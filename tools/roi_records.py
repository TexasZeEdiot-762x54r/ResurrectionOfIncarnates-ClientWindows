"""Completed-battle counters, committed by the award transaction exactly once.

Only future settled results are counted. A bounded award receipt history cannot
reconstruct lifetime statistics. Bot and Custom remain separate; neither grants
ranked points or implies an original ranking formula.
"""
from copy import deepcopy

FIELD = 'battleStatistics'
MAX_COUNT = 2147483647
MODES = ('custom', 'bot')
OUTCOMES = {1: 'wins', -1: 'losses', 0: 'draws'}


def counter(value):
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise ValueError('Invalid battle statistic')
    return value


def empty():
    return dict(wins=0, losses=0, draws=0)


def counts(value):
    if not isinstance(value, dict) or set(value) != set(empty()):
        raise ValueError('Invalid battle outcome counters')
    for amount in value.values():
        counter(amount)
    counter(sum(value.values()))
    return value


def state(saved, owner):
    value = deepcopy(saved.get(FIELD, dict(schema=1, owner=str(owner), modes={})))
    if (not isinstance(value, dict) or set(value) != {'schema', 'owner', 'modes'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['owner'] != str(owner) or not isinstance(value['modes'], dict)
            or set(value['modes']) - set(MODES)):
        raise ValueError('Invalid battle statistics owner or schema')
    for mode in value['modes'].values():
        if not isinstance(mode, dict) or set(mode) != {'total', 'characters'}:
            raise ValueError('Invalid battle statistics mode')
        total = counts(mode['total'])
        characters = mode['characters']
        if not isinstance(characters, dict) or len(characters) > 21:
            raise ValueError('Invalid character statistics')
        for cid, row in characters.items():
            if cid not in {str(i) for i in range(1, 22)}:
                raise ValueError('Invalid statistics character')
            counts(row)
        if any(total[key] != sum(row[key] for row in characters.values()) for key in total):
            raise ValueError('Battle statistics totals disagree')
    return value


def apply(saved, owner, mode, character, result):
    if mode not in MODES or type(character) is not int or not 1 <= character <= 21:
        raise ValueError('Invalid statistics battle context')
    if type(result) is not int or result not in OUTCOMES:
        return {}, None
    value = state(saved, owner)
    bucket = value['modes'].setdefault(mode, dict(total=empty(), characters={}))
    row = bucket['characters'].setdefault(str(character), empty())
    outcome = OUTCOMES[result]
    for target in (bucket['total'], row):
        target[outcome] = counter(target[outcome] + 1)
        counts(target)
    return {FIELD: value}, dict(mode=mode, character=character, outcome=outcome)


def profile_records(saved, owner, good_count=0):
    value = display_state(saved, owner)
    custom = value['modes'].get('custom', {}).get('total', empty())
    # Profile parser 0x1400fd386: index 1 ranked, 2 custom. Numbers are strings.
    def native(row, good=0):
        return dict(sv_winCount=str(row['wins']), sv_loseCount=str(row['losses']),
                    sv_drawCount=str(row['draws']), sv_goodCount=str(counter(good)))
    return [{}, native(empty()), native(custom, good_count)]


def character_usage(saved, owner, character):
    value = display_state(saved, owner)
    return counter(sum(sum(mode['characters'].get(str(character), {}).values())
                       for mode in value['modes'].values()))


def display_state(saved, owner):
    # A different Steam login must not inherit the previous account's counters.
    # Retain the stored data; settlement still refuses an owner mismatch.
    value = saved.get(FIELD)
    if isinstance(value, dict) and value.get('owner') != str(owner):
        return state({}, owner)
    return state(saved, owner)
