"""Explicit owner-selected match XP, separate from unrecovered original progression."""
from copy import deepcopy


def policy(value):
    import roi_awards as awards
    if value is None:
        return {'enabled': False}
    if (not isinstance(value, dict) or set(value) != {'enabled', 'provenance', 'source',
            'loss', 'win', 'highest_damage_win', 'baseline'}
            or type(value['enabled']) is not bool or value['provenance'] != 'user_selected_revival'
            or not isinstance(value['source'], str) or not 1 <= len(value['source'].strip()) <= 2048
            or value['baseline'] not in ('require_aligned', 'preserve_current_levels')):
        raise ValueError('Invalid revival match XP policy')
    keys = ('loss', 'win', 'highest_damage_win')
    rates = tuple(awards.integer(value[key]) for key in keys)
    # Keep the previous approved policy readable; shipped policy selects 2/4/4.
    if rates not in ((8, 10, 16), (2, 4, 4)):
        raise ValueError('Match XP must use an owner-selected 8/10/16 or 2/4/4 rule')
    return dict(deepcopy(value), **dict(zip(keys, rates)))


def summary(value):
    if (not isinstance(value, dict) or set(value) != {'highest_damage'}
            or type(value['highest_damage']) is not bool):
        raise ValueError('Verified native highest-damage summary is required')
    return deepcopy(value)


def baseline(saved, thresholds, cid, rule):
    import roi_awards as awards
    xp = awards.experience(saved, cid)
    level = (awards.integer(saved.get('playerLevel', 1), 0, awards.PLAYER_MAX_LEVEL) if cid is None
             else awards.character_level(saved, cid))
    if not thresholds or level >= len(thresholds):
        raise ValueError('Selected level has no approved XP threshold')
    inferred = awards.level_for(xp, thresholds)
    if inferred == level:
        return xp
    if rule['baseline'] == 'preserve_current_levels' and inferred < level:
        return thresholds[level]
    raise ValueError('Manual level override needs an explicit XP baseline decision')


def selected_rule(current, result, native):
    config = current['match_xp']
    if not config['enabled'] or result not in (-1, 1):
        return None
    highest = summary(native)['highest_damage'] if result == 1 else False
    amount = config['highest_damage_win'] if highest else config['win'] if result == 1 else config['loss']
    return dict(player_xp=amount, character_xp=amount, reason_id=1)


def apply(saved, current, character, rule):
    import roi_awards as awards
    changes, items = {}, []
    receipt = {'character': character}
    for kind, cid, field, level_field, thresholds, label in (
            ('player_xp', None, 'playerExperience', 'playerLevel', current['player'], 'player'),
            ('character_xp', character, 'characterExperience', 'characterLevels',
             current['characters'].get(str(character), []), 'character_xp')):
        stored = awards.experience(saved, cid)
        before = baseline(saved, thresholds, cid, current['match_xp'])
        configured = rule[kind]
        level_before = awards.level_for(before, thresholds)
        after = before if level_before == len(thresholds) - 1 else min(before + configured, thresholds[-1])
        amount = after - before
        level_after = awards.level_for(after, thresholds)
        if cid is None:
            changes.update({field: after, level_field: level_after})
        else:
            xps, levels = deepcopy(saved.get(field, {})), deepcopy(saved.get(level_field, {}))
            xps[str(cid)], levels[str(cid)] = after, level_after
            changes.update({field: xps, level_field: levels})
        if amount:
            items.append(awards.native_item(kind, amount, cid or 0))
        receipt[label] = dict(stored_before=stored, before=before, after=after, gained=amount,
                              level_before=level_before, level_after=level_after,
                              baseline_adjustment=before - stored)
    if changes.get('playerLevel') != saved.get('playerLevel'):
        import roi_skills
        if roi_skills.progression(saved):
            changes['skillGrids'] = roi_skills.grids(dict(saved, **changes))
    return changes, items, receipt
