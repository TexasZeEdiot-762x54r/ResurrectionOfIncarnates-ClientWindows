"""Authenticated local revival bridge; native transports retain their existing replies."""
from copy import deepcopy
import hmac
from pathlib import Path
import re

import profile_store
import roi_awards as awards

TOKEN_FILE = Path(__file__).with_name('lp-bridge-token.txt')
REPORT_FIELDS = ('sv_botBattleId', 'sv_battleResult', 'sv_kill', 'sv_dead', 'sv_atBonus')


def hex_id(value, length=32):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{' + str(length) + '}', value):
        raise ValueError('Invalid native bridge identifier')
    return value


def sequence_id(value):
    if type(value) is str and value.isascii() and value.isdigit() and len(value) <= 20:
        value = int(value)
    if type(value) is not int or not 1 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError('Invalid native sequence')
    return value


def summary(value):
    if not isinstance(value, dict) or set(value) != {'total_score', 'total_rank'}:
        raise ValueError('Invalid native result summary')
    score = awards.integer(value['total_score'])
    rank = value['total_rank']
    if type(rank) is not str or rank not in ('S', 'A', 'B', 'C'):
        raise ValueError('Invalid native Total Rank')
    return dict(total_score=score, total_rank=rank)


def bot_report(value):
    if not isinstance(value, dict) or not all(key in value for key in REPORT_FIELDS):
        raise ValueError('Missing bot report counters')
    return {key: awards.integer(value[key], -1 if key == 'sv_battleResult' else 0,
                               1 if key == 'sv_battleResult' else awards.MAX_INT)
            for key in REPORT_FIELDS}


def receipt(state, owner, battle_id, mode):
    return next((row for row in state['receipts'] if row.get('battle_id') == battle_id
                 and row.get('owner') == str(owner) and row.get('mode') == mode), None)


def result(saved, row):
    components = {'2': 0, '5': 0, '6': 0}
    for group in row['reply']['sv_rewards']:
        reason = group['sv_reasonId']
        if reason in components:
            components[reason] = sum(awards.integer(item['sv_count']) for item in group['sv_items'])
    value = dict(enabled=True, battle_id=row['battle_id'], mode=row['mode'],
                summary=deepcopy(row['lp_summary']), components=components,
                total=sum(components.values()), balance=awards.integer(saved.get('gamePoint', 0)))
    if 'xp' in row:
        value['xp'] = deepcopy(row['xp'])
    value['result_items'] = deepcopy(row['reply'].get('sv_items', []))
    value['drop_events'] = deepcopy(row.get('cube_drops', {}).get('events', []))
    if row.get('cube_wear') is not None:
        value['cube_wear'] = deepcopy(row['cube_wear'])
    return value


def bot_summary(path, owner, data):
    """Only supply a staged native summary if its battle and actual HTTP counters match."""
    battle_id = str(awards.integer(data.get('sv_battleId'), 1))
    report = bot_report(data)
    with profile_store.LOCK:
        state = awards.ledger(profile_store.read(path))
        previous = receipt(state, owner, battle_id, 'bot')
        if previous is not None:
            return deepcopy(previous['lp_summary']) if previous.get('native_report') == report else None
        active = state.get('active')
        if (not active or active.get('owner') != str(owner) or active.get('mode') != 'bot'
                or active.get('battle_id') != battle_id or active.get('native_report') != report):
            return None
        return deepcopy(active.get('native_summary'))


def bot_xp_summary(path, owner, data):
    with profile_store.LOCK:
        if bot_summary(path, owner, data) is None:
            return None
        state = awards.ledger(profile_store.read(path))
        row = receipt(state, owner, str(awards.integer(data['sv_battleId'], 1)), 'bot')
        return deepcopy(row.get('xp_summary') if row is not None else
                        (state.get('active') or {}).get('native_xp_summary'))


def authenticate(data):
    if not isinstance(data, dict):
        raise ValueError('Expected native bridge object')
    token = hex_id(data.get('sv_token'), 64)
    if TOKEN_FILE.stat().st_size > 128:
        raise PermissionError('Invalid local bridge credential')
    expected = TOKEN_FILE.read_text(encoding='ascii').strip()
    if not hmac.compare_digest(hex_id(expected, 64), token):
        raise PermissionError('Invalid local bridge credential')


def handle(path, owner, character, data, *, trusted=False):
    if not isinstance(data, dict):
        raise ValueError('Expected native bridge object')
    if not trusted:
        authenticate(data)
    else:
        hex_id(data.get('sv_token'),64)
    current = awards.policy()
    if not (current['match_xp']['enabled'] or current['cube_drops']['enabled']
            or current['lp_enabled'] and current['lp_components'] == 'full'):
        return {'enabled': False}
    action = data.get('action')
    fields = {
        'open': set(), 'begin_custom': {'sequence', 'match_key'},
        'stage_bot': {'battle_id', 'report', 'summary'},
        'settle_custom': {'sequence', 'match_key', 'battle_id', 'outcome', 'summary'},
        'receipt': {'battle_id', 'mode'},
    }
    required = fields.get(action, set()) | {'sv_token', 'action', 'launch'}
    optional = {'xp_summary', 'cube_usage', 'teammate'} if action in ('stage_bot', 'settle_custom') else set()
    if action not in fields or not required <= set(data) or set(data) - required - optional:
        raise ValueError('Invalid native bridge operation')
    import roi_partners
    teammate=roi_partners.snapshot(data['teammate']) if 'teammate' in data else None
    xp_summary = None
    if current['match_xp']['enabled'] and 'xp_summary' in data:
        import roi_match_xp
        xp_summary = roi_match_xp.summary(data['xp_summary'])
    launch = hex_id(data.get('launch'))
    with profile_store.LOCK:
        saved = profile_store.read(path)
        cube_usage = None
        if 'cube_usage' in data:
            import roi_cube_wear
            cube_usage = roi_cube_wear.normalize(data['cube_usage'])
        state = awards.ledger(saved)
        bridge = state.get('native_bridge', {})
        if action == 'open':
            if bridge.get('launch') != launch or bridge.get('owner') != str(owner):
                state['native_bridge'] = dict(launch=launch, owner=str(owner), sequence=0)
                profile_store.update(path, {'awardPipeline': state})
            return {'enabled': True}
        if bridge.get('launch') != launch or bridge.get('owner') != str(owner):
            raise ValueError('Native bridge session changed')
        if action == 'receipt':
            battle_id = str(awards.integer(data['battle_id'], 1))
            if data['mode'] not in ('bot', 'custom'):
                raise ValueError('Invalid native match mode')
            row = receipt(state, owner, battle_id, data['mode'])
            if row is None:
                raise ValueError('No completed native award receipt')
            return result(saved, row)
        if action == 'stage_bot':
            battle_id = str(awards.integer(data['battle_id'], 1))
            native = summary(data['summary'])
            report = bot_report(data['report'])
            # The native Total score floors death-penalty deficits at zero.
            expected_score = max(0, report['sv_kill'] + report['sv_atBonus'] - report['sv_dead'])
            if native['total_score'] != expected_score:
                raise ValueError('Native score and report components disagree')
            previous = receipt(state, owner, battle_id, 'bot')
            if previous is not None:
                if (previous['lp_summary'] != native or previous.get('native_report') != report
                        or previous.get('xp_summary') != xp_summary or previous.get('cube_usage') != cube_usage
                        or previous.get('teammate') != teammate):
                    raise ValueError('Conflicting native result retry')
                return {'enabled': True, 'battle_id': battle_id}
            active = state.get('active')
            if (not active or active.get('owner') != str(owner) or active.get('mode') != 'bot'
                    or active.get('battle_id') != battle_id or active.get('bot') != report['sv_botBattleId']
                    or active.get('policy') != current['digest']):
                raise ValueError('Native bot result has no matching begin')
            if 'native_summary' in active:
                if (active['native_summary'] != native or active['native_report'] != report
                        or active.get('native_xp_summary') != xp_summary or active.get('native_cube_usage') != cube_usage
                        or active.get('native_teammate') != teammate):
                    raise ValueError('Conflicting staged native result')
            else:
                if cube_usage is not None:
                    roi_cube_wear.snapshot(saved, cube_usage)
                active.update(native_summary=native, native_report=report, native_xp_summary=xp_summary,
                              native_cube_usage=cube_usage,native_teammate=teammate)
                profile_store.update(path, {'awardPipeline': state})
            return {'enabled': True, 'battle_id': battle_id}
        sequence = sequence_id(data['sequence'])
        match_key = hex_id(data['match_key'])
        start = dict(sv_sessionId=launch + ':' + str(sequence), native_match_key=match_key)
        if action == 'begin_custom':
            if sequence < bridge['sequence']:
                raise ValueError('Stale Custom start')
            if sequence == bridge['sequence']:
                if bridge.get('start') != start:
                    raise ValueError('Conflicting Custom start')
                return dict(enabled=True, battle_id=bridge['battle_id'])
            begin = awards.begin(path, owner, character, start, mode='custom')
            state = awards.ledger(profile_store.read(path))
            state['native_bridge'].update(sequence=sequence, start=start, battle_id=begin['sv_battleId'])
            profile_store.update(path, {'awardPipeline': state})
            return dict(enabled=True, battle_id=begin['sv_battleId'])
        battle_id = str(awards.integer(data['battle_id'], 1))
        if (bridge.get('sequence') != sequence or bridge.get('start') != start
                or bridge.get('battle_id') != battle_id):
            raise ValueError('Custom result has no matching native start')
        native = summary(data['summary'])
        outcome = awards.integer(data['outcome'], -1, 1)
        packet = dict(start, sv_battleId=battle_id, sv_battleResult=outcome)
        awards.finish(path, owner, packet, mode='custom', lp_summary=native, xp_summary=xp_summary,
                      cube_usage=cube_usage,teammate=teammate)
        saved = profile_store.read(path)
        return result(saved, receipt(awards.ledger(saved), owner, battle_id, 'custom'))
