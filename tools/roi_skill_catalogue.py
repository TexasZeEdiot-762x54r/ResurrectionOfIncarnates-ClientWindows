"""Original enabled cube definitions; level-one local revival grants only.

The data retains shipped-client rates, not presumed final live-server overrides.
Leveling, consumption and synthesis are not restored by these grants.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

DATA = Path(__file__).with_name('recovered-skillcube-master.json')
REVIVAL_DATA = Path(__file__).with_name('revival-five-block-master.json')
DROP_FIELD = 'skillCubeDrops'
STATE_FIELD = 'skillCubeState'
CONSUMED_FIELD = 'skillCubeConsumedHolds'
MAX_HOLDS = 9999
STEEL_SPINE_IV_BEHAVIOR = 32
STEEL_SPINE_IV_DESCRIPTION = '251002'


def enabled(saved):
    return saved.get('originalSkillCubes') is True


@lru_cache(maxsize=1)
def _seed_rows():
    if DATA.stat().st_size > 2 * 1024**2:
        raise ValueError('Unexpected cube catalogue size')
    raw = DATA.read_bytes()
    if hashlib.sha256(raw).hexdigest() != 'c24ae4c64bcc5dc13ca223e5fca73769727ea5ff1f076dda35108fbbe5ae97ad':
        raise ValueError('Recovered catalogue changed; review before loading')
    rows = json.loads(raw)
    if len(rows) != 346 or len({row['sv_skillcubeId'] for row in rows}) != 346:
        raise ValueError('Unexpected enabled cube catalogue')
    for row in rows:
        if row['sv_skillcubeStatus'] != '1' or row['sv_skillcubeRank'] not in ('D', 'C', 'B', 'A'):
            raise ValueError('Invalid released cube record')
        if not 2 <= int(row['sv_cobeCount']) <= 5 or len(row['sv_effects']) != 3:
            raise ValueError('Invalid original cube shape/effect count')
        if not 1 <= int(row['sv_effects'][2]['sv_effectId']) <= 8:
            raise ValueError('Invalid original summary category')
    return rows


@lru_cache(maxsize=1)
def _rows():
    if REVIVAL_DATA.stat().st_size > 512 * 1024:
        raise ValueError('Unexpected revival catalogue size')
    raw = REVIVAL_DATA.read_bytes()
    if hashlib.sha256(raw).hexdigest() != '4fa583753f75f862e5153a6f311f6e6b26c14cd3a12dc73162c074f73d6d5762':
        raise ValueError('Revival catalogue changed; review before loading')
    added = json.loads(raw)
    rows = _seed_rows() + added
    if len(added) != 272 or len({r['sv_skillcubeId'] for r in rows}) != 618:
        raise ValueError('Unexpected revival cube identities')
    for row in added:
        if (row['sv_skillcubeStatus'], row['sv_skillcubeRank'], row['sv_cobeCount']) != ('1', 'A', '5'):
            raise ValueError('Invalid approved five-block cube')
        if len(row['sv_effects']) != 3 or not 1 <= int(row['sv_effects'][2]['sv_effectId']) <= 8:
            raise ValueError('Invalid revival effects')
    # Keep recovered evidence intact; IV has a separately reviewed revival rule.
    steel_spine = [row for row in added if row['sv_skillcubeName'] == '250388']
    if len(steel_spine) != 17 or any(
            row['sv_effects'][0]['sv_effectId'] != '50'
            or row['sv_effects'][0]['sv_behavior'] != '0'
            or row['sv_skillcubeDesc'] != '260602' for row in steel_spine):
        raise ValueError('Unexpected Steel spine IV definitions')
    if any(effect['sv_behavior'] == str(STEEL_SPINE_IV_BEHAVIOR)
           for row in rows for effect in row['sv_effects']):
        raise ValueError('Steel spine revival condition is already assigned')
    for row in steel_spine:
        row['sv_effects'][0]['sv_behavior'] = str(STEEL_SPINE_IV_BEHAVIOR)
        row['sv_skillcubeDesc'] = STEEL_SPINE_IV_DESCRIPTION
    return rows


def original_cube_ids():
    """Stable seed/drop/evolution pool; store releases do not grant every player cubes."""
    return tuple(row['sv_skillcubeId'] for row in _seed_rows())


def master():
    return deepcopy(_rows())


def _inventory_row(hold_id, cube_id):
    import roi_cube_synthesis
    return dict(sv_skillcubeHoldId=str(hold_id), sv_skillcubeId=str(cube_id),
                sv_triggerCount='0', sv_triggerLimit=str(roi_cube_synthesis._trigger_limit(1)), sv_levelupCount='0',
                sv_evolutionCount='0', sv_modificationCount='0', sv_recoveryCount='0',
                sv_skillLevel='1', sv_skillExp='0', sv_status='0')


def _drop_rows(saved):
    value = deepcopy(saved.get(DROP_FIELD, [])) if saved else []
    if not isinstance(value, list) or len(value) > MAX_HOLDS - len(_seed_rows()) - 1:
        raise ValueError('Invalid dropped-cube inventory')
    valid_ids = {row['sv_skillcubeId'] for row in _rows()}
    required = set(_inventory_row(0, '0'))
    minimum_hold = len(_seed_rows()) + 2
    previous, seen = minimum_hold - 1, set()
    for row in value:
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError('Invalid dropped-cube record')
        hold = row['sv_skillcubeHoldId']
        if (not isinstance(hold, str) or not hold.isascii() or not hold.isdigit()
                or not minimum_hold <= int(hold) <= MAX_HOLDS or int(hold) <= previous
                or hold in seen or row['sv_skillcubeId'] not in valid_ids):
            raise ValueError('Invalid dropped-cube identity')
        previous, seen = int(hold), seen | {hold}
        for key in required - {'sv_skillcubeHoldId', 'sv_skillcubeId'}:
            field = row[key]
            if not isinstance(field, str) or not field.isascii() or not field.isdigit() or len(field) > 10:
                raise ValueError('Invalid dropped-cube state')
    return value


def _consumed_holds(saved):
    value = deepcopy(saved.get(CONSUMED_FIELD, [])) if saved else []
    if not isinstance(value, list) or len(value) > MAX_HOLDS:
        raise ValueError('Invalid consumed-cube inventory')
    result = []
    for hold in value:
        if (not isinstance(hold, str) or not hold.isascii() or not hold.isdigit()
                or not 1 <= int(hold) <= MAX_HOLDS):
            raise ValueError('Invalid consumed-cube identity')
        result.append(int(hold))
    if result != sorted(set(result)):
        raise ValueError('Consumed-cube identities must be unique and increasing')
    return frozenset(result)


def _cube_state(saved):
    value = deepcopy(saved.get(STATE_FIELD, {})) if saved else {}
    if not isinstance(value, dict) or len(value) > MAX_HOLDS:
        raise ValueError('Invalid skill-cube state')
    required = {'sv_skillLevel', 'sv_skillExp', 'sv_levelupCount'}
    optional = {'sv_triggerCount', 'sv_recoveryCount'}
    result = {}
    for hold, state in value.items():
        if (not isinstance(hold, str) or not hold.isascii() or not hold.isdigit()
                or not 1 <= int(hold) <= MAX_HOLDS or not isinstance(state, dict)
                or not required <= set(state) or set(state) - required - optional):
            raise ValueError('Invalid skill-cube state record')
        for field in state:
            item = state[field]
            if not isinstance(item, str) or not item.isascii() or not item.isdigit() or len(item) > 10:
                raise ValueError('Invalid skill-cube state value')
            if field in optional and int(item) > 2147483647:
                raise ValueError('Invalid skill-cube restoration state')
        if not 1 <= int(state['sv_skillLevel']) <= 40:
            raise ValueError('Invalid skill-cube level')
        if int(hold) == 1 and (saved.get('diagnosticSkillCube') is not True
                or (state['sv_skillLevel'], state['sv_skillExp'], state['sv_levelupCount']) != ('1', '0', '0')):
            raise ValueError('Invalid diagnostic restoration state')
        result[int(hold)] = state
    return result


def cube_ids():
    return tuple(row['sv_skillcubeId'] for row in _rows())


def inventory(saved=None):
    # Hold IDs are revival inventory keys, separate from the recovered item IDs.
    import release_policy
    full = release_policy.unlocked(saved)
    seed = (saved or {}).get('skillCubeSeedInventory', True)
    if type(seed) is not bool:
        raise ValueError('Invalid starter inventory policy')
    rows = ([_inventory_row(index, row['sv_skillcubeId']) for index, row in enumerate(_seed_rows(), 2)]
            if seed else [])
    if full:
        rows = [_inventory_row(index, r['sv_skillcubeId']) for index,r in enumerate(_rows(), 2)]
        ranks = {r['sv_skillcubeId']: r['sv_skillcubeRank'] for r in _rows()}
    rows += _drop_rows(saved)
    consumed = _consumed_holds(saved)
    states = _cube_state(saved)
    import roi_cube_wear
    import roi_cube_synthesis
    wear = roi_cube_wear.state(saved or {})
    known = {int(row['sv_skillcubeHoldId']) for row in rows}
    state_holds = known | ({1} if saved and saved.get('diagnosticSkillCube') is True else set())
    if not consumed <= known or not set(states) <= state_holds or consumed.intersection(states) or not set(map(int, wear)) <= known | {1}:
        raise ValueError('Skill-cube state references an invalid hold')
    result = []
    for row in rows:
        hold = int(row['sv_skillcubeHoldId'])
        if hold in consumed:
            continue
        row.update(states.get(hold, {}))
        count = int(row['sv_triggerCount']) + wear.get(str(hold), 0)
        if count > 2147483647:
            raise ValueError('Cube wear overflow')
        row['sv_triggerCount'] = str(count)
        # Combat reads this held-item limit, while the UI reads the level master.
        # Recompute after applying level state, including legacy/evolved drops.
        row['sv_triggerLimit'] = str(roi_cube_synthesis._trigger_limit(int(row['sv_skillLevel'])))
        if full:
            release_policy.capped(row,ranks[row['sv_skillcubeId']])
        result.append(row)
    return result


def grant(saved, cube_ids_to_add):
    if saved.get('originalSkillCubes') is not True or not isinstance(cube_ids_to_add, list):
        raise ValueError('Skill-cube drops require the recovered catalogue')
    valid = set(cube_ids())
    if not 1 <= len(cube_ids_to_add) <= 4 or any(cid not in valid for cid in cube_ids_to_add):
        raise ValueError('Invalid skill-cube drop')
    drops = _drop_rows(saved)
    import release_policy
    reserve = len(_rows() if release_policy.unlocked(saved) else _seed_rows())
    next_hold = int(drops[-1]['sv_skillcubeHoldId']) + 1 if drops else reserve + 2
    if next_hold + len(cube_ids_to_add) - 1 > MAX_HOLDS:
        raise ValueError('Skill-cube inventory is full')
    granted = [_inventory_row(next_hold + offset, cid)
               for offset, cid in enumerate(cube_ids_to_add)]
    return {DROP_FIELD: drops + granted}, granted


def block_counts(saved=None):
    counts = {row['sv_skillcubeId']: int(row['sv_cobeCount']) for row in _rows()}
    return {int(row['sv_skillcubeHoldId']): counts[row['sv_skillcubeId']]
            for row in inventory(saved)}


def base_level_rows():
    import roi_cube_synthesis
    return roi_cube_synthesis.master_rows()
