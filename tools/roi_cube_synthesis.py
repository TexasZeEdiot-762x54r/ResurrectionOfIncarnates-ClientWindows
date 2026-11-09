"""Recovered enhancement progression and owner-selected cube transactions.

The shipped table establishes rank/level XP thresholds and material XP values. The
original queue duration unit and payment rule remain unknown, so this supported
subset completes in one second without charging currency. Restore adds the
materials' remaining uses, capped at the main cube's maximum; this is a revival
rule selected by the owner, not a recovered original restoration formula.
Evolution uses the native checked successor, starting at level one as described
by the original help text, with full durability under the owner's revival policy.
"""
from copy import deepcopy
import time

import roi_skill_catalogue

STACK_FIELD = 'skillCubeCompositeStacks'
RANKS = ('D', 'C', 'B', 'A')
MAX_LEVEL = {'D': 10, 'C': 20, 'B': 30, 'A': 40}

# Exact sv_totalExp values recovered from the shipped client table. Index zero is
# unused so native one-based levels can be used directly.
TOTAL_EXP = {
    'D': (None, 50, 100, 150, 200, 300, 400, 500, 600, 700, 700),
    'C': (None, 100, 200, 300, 400, 500, 600, 700, 800, 1000, 1200,
          1400, 1600, 1800, 2000, 2200, 2400, 2600, 2800, 3000, 3000),
    'B': (None, 150, 300, 450, 600, 750, 900, 1050, 1200, 1400, 1600,
          1800, 2000, 2200, 2400, 2600, 2800, 3100, 3400, 3700, 4000,
          4300, 4600, 4900, 5200, 5500, 5800, 6100, 6400, 6700, 6700),
    'A': (None, 200, 400, 600, 800, 1000, 1200, 1400, 1600, 1850, 2100,
          2350, 2600, 2850, 3100, 3350, 3600, 3900, 4200, 4500, 4800,
          5100, 5400, 5700, 6000, 6400, 6800, 7200, 7600, 8000, 8400,
          8800, 9200, 9600, 10000, 10400, 10800, 11200, 11600, 12000, 12000),
}
GAIN_PER_LEVEL = {'D': 20, 'C': 30, 'B': 40, 'A': 50}


def _trigger_limit(level):
    # The recovered usage-limit columns use the same 5-point progression for
    # every rank through that rank's final supported level.
    if type(level) is not int or not 1 <= level <= 40:
        raise ValueError('Invalid cube durability level')
    return 5 * (level + 1)


def master_rows():
    rows = []
    for rank_number, rank in enumerate(RANKS, 1):
        for level in range(1, MAX_LEVEL[rank] + 1):
            rows.append(dict(sv_rank=str(rank_number), sv_level=str(level),
                sv_totalExp=str(TOTAL_EXP[rank][level]),
                sv_gainExp=str(GAIN_PER_LEVEL[rank] * level),
                sv_triggerLimit=str(_trigger_limit(level))))
    return rows


def _integer(value, minimum, maximum, name):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid ' + name)
    return value


def _catalogue_by_id():
    return {row['sv_skillcubeId']: row for row in roi_skill_catalogue.master()}


def _owned(saved, diagnostic=False):
    if diagnostic:
        import roi_skills
        return {int(row['sv_skillcubeHoldId']): row for row in roi_skills.inventory(saved)}
    return {int(row['sv_skillcubeHoldId']): row for row in roi_skill_catalogue.inventory(saved)}


def _equipped(saved):
    result = set()
    for grid in saved.get('skillGrids', []):
        if not isinstance(grid, dict):
            raise ValueError('Invalid skill-grid state')
        holds = grid.get('sv_skillcubeHoldIds', [])
        if not isinstance(holds, list):
            raise ValueError('Invalid equipped cubes')
        for hold in holds:
            if not isinstance(hold, str) or not hold.isascii() or not hold.isdigit():
                raise ValueError('Invalid equipped cube')
            result.add(int(hold))
    return result


def _normalized_request(data):
    if not isinstance(data, dict):
        raise ValueError('Invalid compose request')
    stack = _integer(data.get('stackNum'), 0, 4, 'composite stack')
    mode = _integer(data.get('mode'), 0, 4, 'compose mode')
    test = _integer(data.get('isTest'), 0, 1, 'test flag')
    # The request builder maps the client's internal Enhance enum (zero) to one.
    if mode not in (1, 2, 4):
        raise ValueError('Only Enhance, Evolve and Restore modes are supported')
    main = _integer(data.get('mainMaterial'), 1, roi_skill_catalogue.MAX_HOLDS, 'main material')
    subs = data.get('subMaterials')
    if not isinstance(subs, list) or not 1 <= len(subs) <= 5:
        raise ValueError('Invalid sub materials')
    subs = tuple(_integer(value, 1, roi_skill_catalogue.MAX_HOLDS, 'sub material') for value in subs)
    if main in subs or len(set(subs)) != len(subs):
        raise ValueError('Cube materials must be distinct')
    if mode == 2 and (len(subs) != 1 or type(data.get('recipe', 0)) is not int
                      or data.get('recipe', 0) != 0):
        raise ValueError('Evolve requires exactly two cubes and no variant recipe')
    return stack, mode, main, subs, test


def _rank(row, catalogue):
    try:
        return catalogue[row['sv_skillcubeId']]['sv_skillcubeRank']
    except KeyError as exc:
        raise ValueError('Unknown skill cube') from exc


def evolution_target(cube_id):
    catalogue = _catalogue_by_id()
    if cube_id not in catalogue:
        raise ValueError('Unknown evolution input')
    source = catalogue[cube_id]
    rank_index = RANKS.index(source['sv_skillcubeRank'])
    # Native Evolve eligibility at 0x140354c52 checks this exact successor ID.
    target_id = str(int(cube_id) + 1000000)
    target = catalogue.get(target_id)
    if (target_id not in roi_skill_catalogue.original_cube_ids()
            or rank_index == len(RANKS) - 1 or target is None
            or target['sv_skillcubeStatus'] != '1'
            or target['sv_skillcubeRank'] != RANKS[rank_index + 1]
            or target['sv_skillcubeCategory'] != source['sv_skillcubeCategory']
            or [(e['sv_effectId'], e['sv_behavior']) for e in target['sv_effects']]
                != [(e['sv_effectId'], e['sv_behavior']) for e in source['sv_effects']]):
        raise ValueError('No supported next-rank cube in the same effect family')
    return target


def _evolve(saved, data):
    stack, mode, main, subs, test = _normalized_request(data)
    selected = {main, *subs}
    owned = _owned(saved)
    if not roi_skill_catalogue.enabled(saved) or not selected <= owned.keys():
        raise ValueError('Selected evolution cubes are not owned')
    if _equipped(saved).intersection(selected):
        raise ValueError('Equipped cubes cannot be evolved or consumed')
    first, second = owned[main], owned[subs[0]]
    if first['sv_skillcubeId'] != second['sv_skillcubeId']:
        raise ValueError('Evolution requires identical cube definitions including shape')
    rank = _rank(first, _catalogue_by_id())
    levels = (int(first['sv_skillLevel']), int(second['sv_skillLevel']))
    if any(level != MAX_LEVEL[rank] for level in levels):
        raise ValueError('Both evolution inputs must be at maximum level')
    now = int(time.time())
    for journal in saved.get(STACK_FIELD, {}).values():
        if not isinstance(journal, dict) or not isinstance(journal.get('result'), dict):
            raise ValueError('Invalid synthesis journal entry')
        result = journal['result']
        end_time, hold = result.get('sv_endTime'), result.get('sv_skillcubeHoldId')
        if any(not isinstance(value, str) or not value.isascii() or not value.isdigit()
               for value in (end_time, hold)):
            raise ValueError('Invalid synthesis journal identity or completion time')
        if int(end_time) > now and int(hold) in selected:
            raise ValueError('An evolution input is still synthesizing')
    target = evolution_target(first['sv_skillcubeId'])
    level, experience = 1, 0
    changes, granted = roi_skill_catalogue.grant(saved, [target['sv_skillcubeId']])
    output = granted[0]
    output.update(sv_skillLevel=str(level), sv_skillExp=str(experience),
                  sv_triggerCount='0', sv_triggerLimit=str(_trigger_limit(level)),
                  sv_evolutionCount='1')
    consumed = sorted(set(roi_skill_catalogue._consumed_holds(saved)) | selected)
    states = {str(hold): state for hold, state in roi_skill_catalogue._cube_state(saved).items()
              if hold not in selected}
    import roi_cube_wear
    used = roi_cube_wear.state(saved)
    for hold in selected:
        used.pop(str(hold), None)
    changes.update({roi_skill_catalogue.CONSUMED_FIELD: [str(hold) for hold in consumed],
                    roi_skill_catalogue.STATE_FIELD: states, roi_cube_wear.FIELD: used})
    result = dict(sv_skillcubeHoldId=output['sv_skillcubeHoldId'], sv_compositeStackNum=str(stack),
        sv_mode=str(mode), sv_beginTime=str(now), sv_endTime=str(now + 1),
        sv_skillcubeId=output['sv_skillcubeId'], sv_triggerCount='0',
        sv_triggerLimit=output['sv_triggerLimit'], sv_skillLevel=output['sv_skillLevel'],
        sv_cost='0', sv_skillExp=output['sv_skillExp'])
    return changes, result


def _result(saved, data):
    stack, mode, main_hold, sub_holds, test = _normalized_request(data)
    owned = _owned(saved, diagnostic=mode == 4)
    if 1 in sub_holds:
        raise ValueError('The diagnostic cube cannot be consumed')
    selected = (main_hold,) + sub_holds
    if any(hold not in owned for hold in selected):
        raise ValueError('Selected cube is not owned')
    if _equipped(saved).intersection(sub_holds):
        raise ValueError('Equipped cubes cannot be consumed')
    catalogue = _catalogue_by_id()
    if mode == 4 and saved.get('diagnosticSkillCube') is True:
        import roi_skills
        catalogue['1'] = roi_skills.master()[0]
    main = owned[main_hold]
    rank = _rank(main, catalogue)
    level = int(main['sv_skillLevel'])
    experience = int(main['sv_skillExp'])
    if not 1 <= level <= MAX_LEVEL[rank]:
        raise ValueError('Invalid main material level')
    if mode == 1 and level == MAX_LEVEL[rank]:
        raise ValueError('Skill cube is already at maximum level')
    gained = 0
    for hold in sub_holds:
        material = owned[hold]
        material_rank = _rank(material, catalogue)
        material_level = int(material['sv_skillLevel'])
        if not 1 <= material_level <= MAX_LEVEL[material_rank]:
            raise ValueError('Invalid material level')
        if mode == 1:
            gained += GAIN_PER_LEVEL[material_rank] * material_level
        else:
            gained += max(0, _trigger_limit(material_level) - int(material['sv_triggerCount']))
    count, limit = main['sv_triggerCount'], main['sv_triggerLimit']
    if mode == 1:
        experience = min(experience + gained, TOTAL_EXP[rank][MAX_LEVEL[rank]])
        while level < MAX_LEVEL[rank] and experience >= TOTAL_EXP[rank][level]:
            level += 1
        limit = str(_trigger_limit(level))
    else:
        limit = str(_trigger_limit(level))
        remaining = max(0, int(limit) - int(count))
        restored = min(int(limit) - remaining, gained)
        if restored:
            count = str(int(limit) - remaining - restored)
        elif not test:
            raise ValueError('Materials would not restore any uses')
    now = int(time.time())
    result = dict(sv_skillcubeHoldId=str(main_hold), sv_compositeStackNum=str(stack),
        sv_mode=str(mode), sv_beginTime=str(now), sv_endTime=str(now + 1),
        sv_skillcubeId=main['sv_skillcubeId'], sv_triggerCount=count,
        sv_triggerLimit=limit, sv_skillLevel=str(level),
        sv_cost='0', sv_skillExp=str(experience))
    normalized = dict(stackNum=stack, mode=mode, mainMaterial=main_hold,
                      subMaterials=list(sub_holds), isTest=test)
    return normalized, result


def compose(saved, data):
    requested = _normalized_request(data)
    stack, mode, main, subs, test = requested
    normalized = dict(stackNum=stack, mode=mode, mainMaterial=main,
                      subMaterials=list(subs), isTest=test)
    stack_key = str(stack)
    journals = deepcopy(saved.get(STACK_FIELD, {}))
    if not isinstance(journals, dict) or len(journals) > 5:
        raise ValueError('Invalid synthesis journal')
    request_key = {key: value for key, value in normalized.items() if key != 'isTest'}
    previous = journals.get(stack_key)
    if not test and previous is not None:
        if not isinstance(previous, dict) or not isinstance(previous.get('result'), dict):
            raise ValueError('Invalid synthesis journal entry')
        if previous.get('request') == request_key:
            return {}, deepcopy(previous['result'])
        end_time = previous['result'].get('sv_endTime')
        if not isinstance(end_time, str) or not end_time.isascii() or not end_time.isdigit():
            raise ValueError('Invalid synthesis completion time')
        if int(end_time) > int(time.time()):
            raise ValueError('Composite stack is already in use')

    if mode == 2:
        changes, result = _evolve(saved, data)
        if test:
            return {}, result
        journals[stack_key] = {'request': request_key, 'result': deepcopy(result)}
        changes[STACK_FIELD] = journals
        return changes, result

    normalized, result = _result(saved, data)
    if normalized['isTest']:
        return {}, result

    consumed = sorted(set(roi_skill_catalogue._consumed_holds(saved))
                      | set(normalized['subMaterials']))
    states = {str(hold): state for hold, state in roi_skill_catalogue._cube_state(saved).items()}
    for hold in normalized['subMaterials']:
        states.pop(str(hold), None)
    main = str(normalized['mainMaterial'])
    current = _owned(saved, diagnostic=mode == 4)[normalized['mainMaterial']]
    states[main] = dict(states.get(main, {}), sv_skillLevel=result['sv_skillLevel'],
        sv_skillExp=result['sv_skillExp'],
        sv_levelupCount=str(int(current['sv_levelupCount']) + (mode == 1)))
    if mode == 4:
        recovery = _integer(int(current['sv_recoveryCount']) + 1, 1, 2147483647, 'recovery count')
        states[main].update(sv_triggerCount=result['sv_triggerCount'], sv_recoveryCount=str(recovery))
    journals[stack_key] = {'request': request_key, 'result': deepcopy(result)}
    changes = {
        roi_skill_catalogue.CONSUMED_FIELD: [str(hold) for hold in consumed],
        roi_skill_catalogue.STATE_FIELD: states,
        STACK_FIELD: journals,
    }
    if mode == 4:
        import roi_cube_wear
        used = roi_cube_wear.state(saved)
        # The restored count is the new baseline. Subsequent battle wear is
        # still added normally, without reapplying the wear already restored.
        used.pop(main, None)
        changes[roi_cube_wear.FIELD] = used
    return changes, result


def verify(saved, data):
    if not isinstance(data, dict):
        raise ValueError('Invalid verification request')
    stack = _integer(data.get('sv_stackNum'), 0, 4, 'composite stack')
    journals = saved.get(STACK_FIELD, {})
    if not isinstance(journals, dict):
        raise ValueError('Invalid synthesis journal')
    row = journals.get(str(stack))
    finished = [] if row is None else [deepcopy(row['result'])]
    return {'sv_compositeStacks': [], 'sv_finishedCompositeStacks': finished}


def cut_time(saved, data):
    if not isinstance(data, dict):
        raise ValueError('Invalid cut-time request')
    query = _integer(data.get('sv_isQuery'), 0, 1, 'cut-time query flag')
    stack = _integer(data.get('sv_stackNum'), 0, 4, 'composite stack')
    journals = saved.get(STACK_FIELD, {})
    if not isinstance(journals, dict) or str(stack) not in journals:
        raise ValueError('Composite stack is not active')
    # The restored queue completes after one second and never charges currency.
    # Both native type 15 (commit) and type 16 (query) require these fields.
    return {'sv_stackNum': str(stack), 'sv_virtualCurrency': '0'}
