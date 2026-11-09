"""Owner-selected post-match cube drops with replay-safe settlement."""
from copy import deepcopy
import secrets


def policy(value):
    import roi_awards as awards
    if value is None:
        return {'enabled': False}
    required = {'enabled', 'provenance', 'source', 'battle_cube_count', 'win_bonus', 'level_up_bonus'}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value) - required - {'victory_boost'} or type(value['enabled']) is not bool
            or value['provenance'] != 'user_selected_revival'
            or not isinstance(value['source'], str) or not 1 <= len(value['source'].strip()) <= 2048
            or awards.integer(value['battle_cube_count'], 1, 1) != 1):
        raise ValueError('Invalid revival cube-drop policy')
    bonus = value['level_up_bonus']
    win_bonus = value['win_bonus']
    if (not isinstance(win_bonus, dict) or set(win_bonus) != {'cube_chance_percent'}
            or awards.integer(win_bonus['cube_chance_percent'], 50, 50) != 50):
        raise ValueError('Invalid win cube bonus policy')
    boost = value.get('victory_boost', {'enabled': False})
    if (not isinstance(boost, dict) or type(boost.get('enabled')) is not bool):
        raise ValueError('Invalid victory boost switch')
    if boost != {'enabled': False} and (
            set(boost) != {'enabled', 'chance_percent'} or boost['enabled'] is not True
            or type(boost['chance_percent']) is not int or boost['chance_percent'] != 1):
        raise ValueError('Invalid victory boost policy')
    boost_enabled = boost.get('enabled') is True
    if bonus == {'enabled': False} and type(bonus.get('enabled')) is bool:
        return dict(enabled=value['enabled'], battle_cube_count=1,
                    win_cube_chance_percent=50, level_up_enabled=False, victory_boost_enabled=boost_enabled)
    base = {'enabled', 'selection', 'cube_weight', 'lp_weight'}
    if (not isinstance(bonus, dict) or set(bonus) not in
            (base | {'lp_amount'}, base | {'lp_amounts'})
            or bonus['enabled'] is not True or bonus['selection'] != 'weighted_random'):
        raise ValueError('Invalid level-up bonus policy')
    cube_weight = awards.integer(bonus['cube_weight'], 1, 10000)
    lp_weight = awards.integer(bonus['lp_weight'], 1, 10000)
    amounts = bonus.get('lp_amounts', [bonus.get('lp_amount')])
    if not isinstance(amounts, list) or not 1 <= len(amounts) <= 16:
        raise ValueError('Invalid equally weighted LP amounts')
    amounts = [awards.integer(amount, 1) for amount in amounts]
    if len(set(amounts)) != len(amounts):
        raise ValueError('Duplicate LP outcomes')
    return dict(enabled=value['enabled'], battle_cube_count=1, win_cube_chance_percent=50,
                level_up_enabled=True, victory_boost_enabled=boost_enabled,
                cube_weight=cube_weight, lp_weight=lp_weight, lp_amounts=amounts)


def _leveled(row):
    return (isinstance(row, dict) and type(row.get('level_before')) is int
            and type(row.get('level_after')) is int
            and row['level_after'] > row['level_before'])


def apply(saved, config, result, xp_receipt, *, randbelow=None):
    """Return atomic profile changes, native result items and an immutable receipt."""
    if not config['enabled'] or result not in (-1, 1):
        return {}, [], None
    import roi_awards as awards
    import roi_skill_catalogue as catalogue
    choose = randbelow or secrets.randbelow
    events = [{'source': 'battle', 'kind': 'cube'}]
    if result == 1 and choose(100) < config['win_cube_chance_percent']:
        events.append({'source': 'win_bonus', 'kind': 'cube'})
    if config['level_up_enabled'] and isinstance(xp_receipt, dict):
        for source, key in (('player_level', 'player'), ('character_level', 'character_xp')):
            if _leveled(xp_receipt.get(key)):
                total = config['cube_weight'] + config['lp_weight']
                kind = 'cube' if choose(total) < config['cube_weight'] else 'lp'
                events.append({'source': source, 'kind': kind})
    if result == 1 and config.get('victory_boost_enabled') and choose(100) == 0:
        import roi_consumables as consumables
        kind = ('lp', 'exp')[choose(2)]
        days = consumables.DAYS[choose(len(consumables.DAYS))]
        item = next(row for row in consumables.CATALOGUE if row['kind'] == kind and row['days'] == days)
        events.append(dict(source='victory_boost', kind='boost', boost_type=kind,
                           days=days, item_id=item['id']))
    cube_events = [row for row in events if row['kind'] == 'cube']
    cube_ids = catalogue.original_cube_ids()
    selected = [cube_ids[choose(len(cube_ids))] for _ in cube_events]
    changes, granted = catalogue.grant(saved, selected)
    for event, row in zip(cube_events, granted):
        event.update(hold_id=row['sv_skillcubeHoldId'], cube_id=row['sv_skillcubeId'])
    lp_events = [row for row in events if row['kind'] == 'lp']
    if lp_events:
        amounts = config['lp_amounts']
        for event in lp_events:
            event['amount'] = amounts[choose(len(amounts))] if len(amounts) > 1 else amounts[0]
        total_lp = awards.integer(sum(row['amount'] for row in lp_events), 1)
        changes['gamePoint'] = awards.integer(awards.integer(saved.get('gamePoint', 0)) + total_lp)
    boost_events = [row for row in events if row['kind'] == 'boost']
    if boost_events:
        boost_changes, granted = consumables.grant(saved, [row['item_id'] for row in boost_events])
        changes.update(boost_changes)
        for event, row in zip(boost_events, granted):
            event['hold_id'] = row['sv_itemHoldId']
    result_items = [dict(sv_itemId=row['cube_id'], sv_count='1', sv_additions=[])
                    if row['kind'] == 'cube' else
                    dict(sv_itemId=row['item_id'], sv_count='1', sv_additions=[])
                    if row['kind'] == 'boost' else awards.native_item('lp', row['amount'])
                    for row in events]
    return changes, result_items, {'events': deepcopy(events)}
