"""Evidence-gated match awards, native item encoding and replay-safe persistence."""
from bisect import bisect_right
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import profile_store

POLICY_FILE = Path(__file__).with_name('award-policy.json')
MAX_INT = 2147483647
RECEIPT_LIMIT = 128
PLAYER_MAX_LEVEL = 50
CHARACTER_MAX_LEVEL = 30


def integer(value, low=0, high=MAX_INT):
    if type(value) is str:
        digits = value[1:] if low < 0 and value.startswith('-') else value
        if digits.isascii() and digits.isdigit() and len(digits) <= 10:
            value = int(value)
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid award integer')
    return value


def verified(row):
    if not isinstance(row, dict) or row.get('verified') is not True:
        raise ValueError('Unverified award data')
    evidence = row.get('evidence')
    if not isinstance(evidence, str) or not 1 <= len(evidence.strip()) <= 2048:
        raise ValueError('Award data needs source evidence')


def curve(rows, maximum):
    if not isinstance(rows, list) or len(rows) > maximum:
        raise ValueError('Invalid XP curve')
    output = []
    for index, row in enumerate(rows):
        verified(row)
        level, xp = integer(row.get('level'), 0, maximum - 1), integer(row.get('total_xp'))
        if level != index or index == 0 and xp != 0 or output and xp < output[-1]:
            raise ValueError('XP levels must be contiguous and thresholds nondecreasing')
        # Native level lookup counts thresholds <= XP, then subtracts one.
        if index > 1 and xp == output[-1]:
            raise ValueError('Duplicate non-baseline XP threshold')
        output.append(xp)
    if output and len(output) < 2:
        raise ValueError('Incomplete XP curve')
    return output


def policy():
    import roi_match_xp
    if POLICY_FILE.stat().st_size > 512 * 1024:
        raise ValueError('Award policy exceeds limit')
    value = json.loads(POLICY_FILE.read_text(encoding='utf-8'),
                       object_pairs_hook=profile_store._strict_object)
    if (not isinstance(value, dict) or type(value.get('schema')) is not int
            or value['schema'] != 1 or type(value.get('enabled')) is not bool):
        raise ValueError('Invalid award policy')
    player = curve(value.get('player_levels'), PLAYER_MAX_LEVEL + 1)
    character_default = curve(value.get('character_default_levels', []), CHARACTER_MAX_LEVEL + 1)
    character_rows = value.get('character_levels')
    if not isinstance(character_rows, dict) or len(character_rows) > 21:
        raise ValueError('Invalid character XP curves')
    characters = ({str(cid): deepcopy(character_default)
                   for cid in range(1, 22)} if character_default else {})
    for cid, rows in character_rows.items():
        canonical = str(integer(cid, 1, 21))
        if cid != canonical:
            raise ValueError('Noncanonical character ID')
        characters[cid] = curve(rows, CHARACTER_MAX_LEVEL + 1)
    rules, seen = value.get('bot_rewards'), set()
    if not isinstance(rules, list) or len(rules) > 128:
        raise ValueError('Invalid bot reward table')
    normalized = []
    for row in rules:
        verified(row)
        bot, result = integer(row.get('bot_battle_id'), 0), integer(row.get('battle_result'), 0, 3)
        key = bot, result
        if key in seen:
            raise ValueError('Duplicate bot reward rule')
        seen.add(key)
        normalized.append(dict(bot_battle_id=bot, battle_result=result,
            reason_id=integer(row.get('reason_id'), 0, 10000),
            player_xp=integer(row.get('player_xp')), character_xp=integer(row.get('character_xp'))))
    lp = value.get('lp_rewards')
    lp_enabled, battle_bonuses, team_bonuses, components = False, {}, {}, 'battle_only'
    if lp is not None:
        if (not isinstance(lp, dict) or type(lp.get('enabled')) is not bool
                or lp.get('provenance') != 'user_selected_revival'
                or not isinstance(lp.get('source'), str)
                or not 1 <= len(lp['source'].strip()) <= 2048
                or not isinstance(lp.get('battle_bonuses'), dict)
                or set(lp['battle_bonuses']) != {'-1', '1'}):
            raise ValueError('Invalid revival LP policy')
        lp_enabled = lp['enabled']
        battle_bonuses = {key: integer(amount, 1)
                          for key, amount in lp['battle_bonuses'].items()}
        components = lp.get('components', 'battle_only')
        if components not in ('battle_only', 'full'):
            raise ValueError('Invalid LP component policy')
        if components == 'full':
            if type(lp.get('score_percent')) is not int or lp['score_percent'] != 20:
                raise ValueError('Score Bonus must always be twenty percent')
            teams = lp.get('team_bonuses')
            if not isinstance(teams, dict) or set(teams) != {'-1', '1'}:
                raise ValueError('Incomplete Team Bonus table')
            for outcome, grades in teams.items():
                if not isinstance(grades, dict) or set(grades) != {'S', 'A', 'B', 'C'}:
                    raise ValueError('Incomplete Team Bonus grades')
                team_bonuses[outcome] = {grade: integer(amount) for grade, amount in grades.items()}
    match_xp = roi_match_xp.policy(value.get('match_xp'))
    import roi_cube_drops
    cube_drops = roi_cube_drops.policy(value.get('cube_drops'))
    if value['enabled'] and match_xp['enabled']:
        raise ValueError('Select either legacy XP rules or revival match XP, not both')
    return dict(enabled=value['enabled'], player=player, characters=characters, rules=normalized,
                lp_enabled=lp_enabled, battle_bonuses=battle_bonuses,
                lp_components=components, team_bonuses=team_bonuses,
                match_xp=match_xp, cube_drops=cube_drops,
                digest=hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())


def decode_item(item_id):
    """Decimal descriptor groups from native 0x1400fdc60, not a recovered item catalogue."""
    if type(item_id) not in (int, str) or isinstance(item_id, bool):
        raise ValueError('Invalid native reward ID')
    text = str(item_id)
    if not text.isascii() or not text.isdigit() or len(text) > 16:
        raise ValueError('Invalid native reward ID')
    text = text.zfill(16)
    return tuple(int(text[a:b]) for a, b in ((0,1),(1,2),(2,4),(4,6),(6,9),(9,10),(10,13),(13,16)))


def native_item(kind, amount, character=0):
    amount = integer(amount, 1)
    character = integer(character, 0, 21)
    # Protocol-compatible reconstructed IDs; unknown original database suffixes are zero.
    if kind == 'player_xp' and character == 0 or kind == 'character_xp' and character > 0:
        item_id = f'10{character:02d}010000000000'
    elif kind == 'grid_right' and character == 0:
        item_id = '1000020010000000'
    elif kind == 'lp' and character == 0:
        item_id = '1000000000000000'
    else:
        raise ValueError('Unsupported native award type')
    return {'sv_itemId': item_id, 'sv_count': str(amount), 'sv_additions': []}


def experience(saved, character=None):
    if character is None:
        return integer(saved.get('playerExperience', 0))
    return integer(saved.get('characterExperience', {}).get(str(character), 0))


def character_level(saved, cid):
    return integer(saved.get('characterLevels', {}).get(str(cid), 1), 0, CHARACTER_MAX_LEVEL)


def level_for(xp, thresholds):
    if not thresholds:
        raise ValueError('Original XP thresholds have not been recovered')
    return max(0, bisect_right(thresholds, integer(xp)) - 1)


def level_reward_items():
    import roi_grid_rewards
    return {level: [native_item('grid_right', rights)]
            for level, rights in roi_grid_rewards.level_rewards()}


def master_rows(kind, cid=None):
    current = policy()
    thresholds = current['player'] if kind == 'player' else current['characters'].get(str(cid), [])
    if not thresholds:
        return None
    rewards = level_reward_items() if kind == 'player' else {}
    return [dict(sv_level=str(level), sv_totalExp=str(xp), sv_openTime='0',
                 sv_rewardItems=rewards.get(level, []),
                 **({'sv_charaId': str(cid)} if cid is not None else {}))
            for level, xp in enumerate(thresholds)]


def request_digest(data):
    if not isinstance(data, dict):
        raise ValueError('Invalid battle result')
    encoded = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > 65536:
        raise ValueError('Battle result exceeds limit')
    return hashlib.sha256(encoded).hexdigest()


def ledger(saved):
    value = deepcopy(saved.get('awardPipeline', {'schema': 1, 'sequence': 0, 'receipts': []}))
    if not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1:
        raise ValueError('Invalid award ledger')
    integer(value.get('sequence'))
    if not isinstance(value.get('receipts'), list) or len(value['receipts']) > RECEIPT_LIMIT:
        raise ValueError('Invalid award receipts')
    return value


def aligned_experience(saved, thresholds, cid=None):
    xp = experience(saved, cid)
    level = integer(saved.get('playerLevel', 1), 0, PLAYER_MAX_LEVEL) if cid is None else character_level(saved, cid)
    if level_for(xp, thresholds) != level:
        raise ValueError('Manual level override and XP must be aligned before enabling awards')
    return xp


def enabled_for(current, mode):
    if mode not in ('bot', 'custom'):
        raise ValueError('Unsupported match award mode')
    if mode == 'custom':
        return (current['match_xp']['enabled'] or current['cube_drops']['enabled']
                or current['lp_enabled'] and current['lp_components'] == 'full')
    return (current['enabled'] or current['lp_enabled'] or current['match_xp']['enabled']
            or current['cube_drops']['enabled'])


def lp_components(current, result, summary):
    """Consume a normalized internal summary, never invented HTTP field names.

    Native adapters must establish the displayed score/grade before calling this.
    These rates are owner-selected revival rules, not an original economy claim.
    """
    if result not in (-1, 1):
        return []  # No draw/abandonment payout has been selected.
    battle = current['battle_bonuses'][str(result)]
    if current['lp_components'] == 'battle_only':
        return [('6', battle)]
    if not isinstance(summary, dict) or set(summary) != {'total_score', 'total_rank'}:
        raise ValueError('Verified native result summary is required')
    score = integer(summary['total_score'])
    rank = summary['total_rank']
    if type(rank) is not str or rank not in ('S', 'A', 'B', 'C'):
        raise ValueError('Unrecognized native total rank')
    # Integer arithmetic preserves floor(score * 20%) without floating-point drift.
    return [('2', score // 5), ('5', current['team_bonuses'][str(result)][rank]), ('6', battle)]


def begin(path, owner, character, data, *, mode='bot', track_records=False):
    current = policy()
    if not enabled_for(current, mode) and not track_records:
        return None
    fingerprint = request_digest(data)
    bot = integer(data.get('sv_botBattleId'), 0) if mode == 'bot' else None
    context = dict(owner=str(owner), character=integer(character, 1, 21), bot=bot, mode=mode,
                   policy=current['digest'], request=fingerprint,
                   match={key: deepcopy(data[key]) for key in
                          ('sv_sessionId', 'sv_partnerUid', 'sv_stageId') if key in data})
    with profile_store.LOCK:
        saved = profile_store.read(path)
        if current['match_xp']['enabled']:
            import roi_match_xp
            roi_match_xp.baseline(saved, current['player'], None, current['match_xp'])
            roi_match_xp.baseline(saved, current['characters'].get(str(character), []),
                                  character, current['match_xp'])
        candidates = [row for row in current['rules'] if
                      mode == 'bot' and current['enabled'] and row['bot_battle_id'] == bot]
        if any(row['player_xp'] for row in candidates):
            aligned_experience(saved, current['player'])
        if any(row['character_xp'] for row in candidates):
            aligned_experience(saved, current['characters'].get(str(character), []), character)
        state = ledger(saved)
        active = state.get('active')
        if active and all(active.get(key) == value for key, value in context.items()):
            return {'sv_battleId': active['battle_id']}
        state['sequence'] = integer(state['sequence'] + 1, 1)
        state['active'] = dict(context, battle_id=str(state['sequence']))
        profile_store.update(path, {'awardPipeline': state})
        return {'sv_battleId': state['active']['battle_id']}


def finish(path, owner, data, *, mode='bot', lp_summary=None, xp_summary=None, cube_usage=None,
           track_records=False, teammate=None):
    current = policy()
    rewards_enabled = enabled_for(current, mode)
    if not rewards_enabled and not track_records:
        return None
    fingerprint_data = (data if lp_summary is None and mode == 'bot' else
                        dict(data=data, mode=mode, lp_summary=lp_summary))
    if xp_summary is not None:
        import roi_match_xp
        fingerprint_data = dict(data=data, mode=mode, lp_summary=lp_summary,
                                xp_summary=roi_match_xp.summary(xp_summary))
    if cube_usage is not None:
        fingerprint_data = dict(result=fingerprint_data, cube_usage=cube_usage)
    if teammate is not None:
        import roi_partners
        fingerprint_data = dict(result=fingerprint_data, teammate=roi_partners.snapshot(teammate))
    fingerprint = request_digest(fingerprint_data)
    battle_id = str(integer(data.get('sv_battleId'), 1))
    with profile_store.LOCK:
        saved = profile_store.read(path)
        state = ledger(saved)
        for receipt in state['receipts']:
            if receipt['battle_id'] == battle_id:
                if receipt['owner'] != str(owner) or receipt['request'] != fingerprint:
                    raise ValueError('Conflicting completed battle retry')
                return deepcopy(receipt['reply'])
        active = state.get('active')
        if not active or active['battle_id'] != battle_id or active['owner'] != str(owner):
            raise ValueError('Result has no matching active battle')
        if (active.get('mode', 'bot') != mode or active['policy'] != current['digest']
                or mode == 'bot' and integer(data.get('sv_botBattleId'), 0) != active['bot']):
            raise ValueError('Battle reward context changed')
        match = active.get('match', {})
        if request_digest(match) != request_digest({key: data[key] for key in match if key in data}):
            raise ValueError('Battle session, partner or stage changed')
        result = integer(data.get('sv_battleResult'), -1, 3)
        rule = next((row for row in current['rules'] if
                     mode == 'bot' and current['enabled'] and
                     (row['bot_battle_id'], row['battle_result']) == (active['bot'], result)), None)
        import roi_match_xp
        import roi_boosts
        boost = roi_boosts.multipliers(saved)
        selected = roi_match_xp.selected_rule(current, result, xp_summary)
        if selected:
            selected = dict(selected, player_xp=integer(selected['player_xp'] * boost['exp']),
                            character_xp=integer(selected['character_xp'] * boost['exp']))
        elif rule:
            rule = dict(rule, player_xp=integer(rule['player_xp'] * boost['exp']),
                        character_xp=integer(rule['character_xp'] * boost['exp']))
        changes, items, xp_receipt = {}, [], None
        if selected:
            rule = selected
            changes, items, xp_receipt = roi_match_xp.apply(saved, current, active['character'], rule)
        elif rule:
            xp_receipt = {'character': active['character']}
            for kind, cid, field, level_field, thresholds in (
                    ('player_xp', None, 'playerExperience', 'playerLevel', current['player']),
                    ('character_xp', active['character'], 'characterExperience', 'characterLevels',
                     current['characters'].get(str(active['character']), []))):
                amount = rule[kind]
                if not amount:
                    continue
                before = aligned_experience(saved, thresholds, cid)
                after = integer(before + amount)
                level = level_for(after, thresholds)
                if cid is None:
                    changes.update({field: after, level_field: level})
                else:
                    xp_values, levels = deepcopy(saved.get(field, {})), deepcopy(saved.get(level_field, {}))
                    xp_values[str(cid)], levels[str(cid)] = after, level
                    changes.update({field: xp_values, level_field: levels})
                items.append(native_item(kind, amount, cid or 0))
                label = 'player' if cid is None else 'character_xp'
                xp_receipt[label] = dict(stored_before=before, before=before, after=after,
                                         gained=amount, level_before=level_for(before, thresholds),
                                         level_after=level, baseline_adjustment=0)
            if 'playerLevel' in changes:
                import roi_skills
                if roi_skills.progression(saved):
                    changes['skillGrids'] = roi_skills.grids(dict(saved, **changes))
        rewards = [dict(sv_reasonId=str(rule['reason_id']), sv_items=items)] if items else []
        lp = lp_components(current, result, lp_summary) if rewards_enabled and current['lp_enabled'] else []
        lp = [(reason, integer(amount * boost['lp'])) for reason, amount in lp]
        if lp:
            changes['gamePoint'] = integer(integer(saved.get('gamePoint', 0)) + sum(amount for _, amount in lp))
            # Native collector: reason 2 = Score, 5 = Team, 6 = Battle Bonus.
            rewards.extend(dict(sv_reasonId=reason, sv_items=[native_item('lp', amount)])
                           for reason, amount in lp if amount)
        import roi_cube_drops
        drop_changes, result_items, drop_receipt = roi_cube_drops.apply(
            dict(saved, **changes), current['cube_drops'], result, xp_receipt)
        changes.update(drop_changes)
        import roi_cube_wear
        usage = active.get('native_cube_usage') if mode == 'bot' else cube_usage
        if not rewards_enabled:
            usage = None  # Records-only operation must not change inventory.
        wear_changes, wear_receipt = roi_cube_wear.apply(dict(saved, **changes), usage, result)
        changes.update(wear_changes)
        import roi_records
        record_changes, record_receipt = roi_records.apply(
            saved, owner, mode, active['character'], result)
        changes.update(record_changes)
        import roi_partners
        teammate=active.get('native_teammate') if mode=='bot' else teammate
        if record_receipt is not None:
            changes.update(roi_partners.apply(dict(saved,**changes),owner,mode,active['character'],teammate))
        reply = dict(sv_battleId=battle_id, sv_rewards=rewards,
                     sv_items=result_items, sv_mails=[])
        state['active'] = None
        receipt = dict(battle_id=battle_id, owner=str(owner), request=fingerprint, reply=deepcopy(reply))
        if boost != {'lp': 1, 'exp': 1}:
            receipt['boost_multipliers'] = boost
        if record_receipt is not None:
            receipt['statistics'] = record_receipt
        if teammate is not None:
            receipt['teammate'] = deepcopy(teammate)
        if xp_summary is not None:
            receipt['xp_summary'] = deepcopy(xp_summary)
        if xp_receipt is not None:
            receipt['xp'] = xp_receipt
        if drop_receipt is not None:
            receipt['cube_drops'] = drop_receipt
        if usage is not None:
            receipt['cube_usage'] = deepcopy(usage)
            receipt['cube_wear'] = wear_receipt
        if lp_summary is not None:
            receipt.update(mode=mode, lp_summary=deepcopy(lp_summary),
                           begin_request=active['request'],
                           native_report=deepcopy(active.get('native_report')))
        state['receipts'] = (state['receipts'] + [receipt])[-RECEIPT_LIMIT:]
        changes['awardPipeline'] = state
        profile_store.update(path, changes)
        return reply
