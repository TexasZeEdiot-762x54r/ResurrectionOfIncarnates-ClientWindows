"""Ranked-only streak changes for a trusted, atomic result-settlement adapter.

This module never accepts network requests or writes profiles independently.
The caller must validate the match identity and completed outcome, and commit
these changes in the same transaction as its durable match receipt.
"""
from copy import deepcopy

FIELD = 'rankedWinStreak'
MAX_COUNT = 2147483647
MAX_SEQUENCE = 9223372036854775807


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid ranked streak integer')
    return value


def state(saved, owner):
    owner = str(owner)
    if not owner or len(owner) > 64:
        raise ValueError('Invalid ranked streak owner')
    value = deepcopy(saved.get(FIELD, dict(schema=1, owner=owner,
                     current=0, best=0, sequence=0, outcome=None)))
    if (type(value) is not dict or set(value) != {
            'schema', 'owner', 'current', 'best', 'sequence', 'outcome'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['owner'] != owner):
        raise ValueError('Invalid ranked streak owner or schema')
    integer(value['best'], 0, MAX_COUNT)
    integer(value['current'], 0, value['best'])
    integer(value['sequence'], 0, MAX_SEQUENCE)
    if value['sequence'] == 0:
        if value['outcome'] is not None or value['current'] or value['best']:
            raise ValueError('Invalid initial ranked streak')
    else:
        integer(value['outcome'], -1, 1)
        if (value['outcome'] != 1 and value['current']) or (
                value['outcome'] == 1 and not value['current']):
            raise ValueError('Ranked streak and outcome disagree')
    return value


def apply(saved, owner, mode, sequence, outcome):
    if mode != 'ranked':
        return {}
    integer(sequence, 1, MAX_SEQUENCE)
    integer(outcome, -1, 1)
    value = state(saved, owner)
    if sequence < value['sequence']:
        raise ValueError('Stale ranked streak result')
    if sequence == value['sequence']:
        if outcome != value['outcome']:
            raise ValueError('Conflicting ranked streak retry')
        return {}
    value['current'] = min(MAX_COUNT, value['current'] + 1) if outcome == 1 else 0
    value['best'] = max(value['best'], value['current'])
    value.update(sequence=sequence, outcome=outcome)
    return {FIELD: value}


def profile_fields(saved, owner):
    stored = saved.get(FIELD)
    if isinstance(stored, dict) and stored.get('owner') != str(owner):
        saved = {}
    value = state(saved, owner)
    return dict(sv_winStreakCount=str(value['current']), sv_winStreakMax=str(value['best']))
