"""Owner-selected one-use wear, committed atomically with the battle receipt."""
from copy import deepcopy

FIELD = 'skillCubeWear'


def state(saved):
    import roi_skill_catalogue as catalogue
    value = deepcopy(saved.get(FIELD, {}))
    if not isinstance(value, dict) or len(value) > catalogue.MAX_HOLDS:
        raise ValueError('Invalid cube wear state')
    for hold, count in value.items():
        if (type(hold) is not str or not hold.isascii() or not hold.isdigit()
                or str(int(hold)) != hold or not 1 <= int(hold) <= catalogue.MAX_HOLDS
                or type(count) is not int or not 0 <= count <= 2147483647):
            raise ValueError('Invalid cube wear entry')
    return value


def normalize(value):
    if (not isinstance(value, dict) or set(value) != {'grid_id', 'holds'}
            or type(value['grid_id']) is not int or not 0 <= value['grid_id'] < 5
            or not isinstance(value['holds'], list) or len(value['holds']) > 7):
        raise ValueError('Invalid battle cube snapshot')
    holds = value['holds']
    if any(type(h) is not str or not h.isascii() or not h.isdigit()
           or not 1 <= int(h) <= 9999 or str(int(h)) != h for h in holds):
        raise ValueError('Invalid battle hold ID')
    if len(set(holds)) != len(holds):
        raise ValueError('Duplicate battle hold')
    return dict(grid_id=value['grid_id'], holds=sorted(holds, key=int))


def snapshot(saved, value):
    """Validate retained native holds against this account's owned loadout."""
    import roi_skills
    value = normalize(value)
    holds = value['holds']
    import release_policy
    if saved.get('skillGridRuleset') and not release_policy.grid_allowed(saved):
        if holds:raise ValueError('Skill system is locked')
        return value
    if not roi_skills.enabled(saved) and not holds:
        return value
    grids = roi_skills.grids(saved)
    grid = next((g for g in grids if int(g['sv_sgridId']) == value['grid_id']), None)
    if grid is None or set(grid['sv_skillcubeHoldIds']) != set(holds):
        raise ValueError('Battle cubes do not match the owned grid')
    return value


def apply(saved, usage, result):
    if usage is None or result not in (-1, 0, 1):
        return {}, None
    import roi_skills
    usage = snapshot(saved, usage)
    import release_policy
    if release_policy.unlocked(saved):
        return {}, dict(grid_id=usage["grid_id"], cubes=[])
    owned = {r['sv_skillcubeHoldId']: r for r in roi_skills.inventory(saved)}
    used = state(saved)
    events = []
    for hold in usage['holds']:
        row = owned[hold]
        limit = int(row['sv_triggerLimit'])
        count = int(row['sv_triggerCount'])
        remaining = max(0, limit - count)
        if remaining:
            used[hold] = used.get(hold, 0) + 1
        events.append(dict(hold_id=hold, cube_id=row['sv_skillcubeId'],
                           before=remaining, after=max(0, remaining - 1)))
    return ({FIELD: used} if events else {}), dict(grid_id=usage['grid_id'], cubes=events)
