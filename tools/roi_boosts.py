"""Timed 2x boosts: original descriptions, owner-selected duration stacking.

UTC seconds are authoritative in the save. Activation, remaining inventory and
retry receipts share one atomic commit. Tickets are deliberately unsupported.
"""
from copy import deepcopy
import time

import profile_store
import roi_consumables

ADDITIONS = {'lp': 111, 'exp': 112}
BOOSTS = {row['id']: row for row in roi_consumables.CATALOGUE if row['kind'] in ADDITIONS}
MAX_TIME = 253402300799
RECEIPT_LIMIT = 256


def number(value, low=0, high=MAX_TIME):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid boost integer')
    return value


def hold_id(value):
    if type(value) is str and value.isascii() and value.isdigit() and len(value) <= 20:
        if value != str(int(value)):
            raise ValueError('Noncanonical consumable hold')
        value = int(value)
    return str(number(value, 1, 2**64-1))


def state(saved):
    value = deepcopy(saved.get('boostState', {'schema': 1, 'active': {}, 'receipts': []}))
    if (not isinstance(value, dict) or set(value) != {'schema', 'active', 'receipts'}
            or type(value['schema']) is not int or value['schema'] != 1
            or not isinstance(value['active'], dict) or value['active'].keys() - ADDITIONS.keys()
            or not isinstance(value['receipts'], list) or len(value['receipts']) > RECEIPT_LIMIT):
        raise ValueError('Invalid boost state')
    for row in value['active'].values():
        if not isinstance(row, dict) or set(row) != {'started', 'expires'}:
            raise ValueError('Invalid boost duration')
        number(row['expires'], number(row['started']) + 1)
    seen = set()
    for receipt in value['receipts']:
        if not isinstance(receipt, dict) or set(receipt) != {'holds', 'used_at'}:
            raise ValueError('Invalid boost receipt')
        holds = receipt['holds']
        if (not isinstance(holds, list) or not 1 <= len(holds) <= 64
                or holds != sorted({hold_id(h) for h in holds}, key=int)
                or seen.intersection(holds)):
            raise ValueError('Invalid spent boost holds')
        seen.update(holds)
        number(receipt['used_at'])
    return value


def multipliers(saved, now=None):
    now = number(int(time.time()) if now is None else now)
    active = state(saved)['active']
    return {kind: 2 if kind in active and active[kind]['started'] <= now < active[kind]['expires'] else 1
            for kind in ADDITIONS}


def native_rows(saved, now=None):
    now = number(int(time.time()) if now is None else now)
    active = state(saved)['active']
    # 0x1400fa891 parses these strings; 0x1403780b0 maps 111=LP, 112=EXP.
    # 0x1404c831d consumes additionValue as percent added to a 1.0 multiplier.
    return [dict(additionId=str(ADDITIONS[kind]), additionValue='100',
                 sv_beginTime=str(active[kind]['started']*1000),
                 sv_endTime=str(active[kind]['expires']*1000))
            for kind in ADDITIONS
            if kind in active and active[kind]['started'] <= now < active[kind]['expires']]


def activate(path, data):
    if not isinstance(data, dict) or set(data) != {'sv_holdIds'}:
        raise ValueError('Invalid useItem request')
    requested = data['sv_holdIds']
    if not isinstance(requested, list) or not 1 <= len(requested) <= 64:
        raise ValueError('Invalid consumable selection')
    holds = sorted([hold_id(value) for value in requested], key=int)
    if len(set(holds)) != len(holds):
        raise ValueError('Duplicate consumable selection')
    with profile_store.LOCK:
        saved = profile_store.read(path)
        current = state(saved)
        if any(receipt['holds'] == holds for receipt in current['receipts']):
            return {}  # Native type 82 consumes an acknowledgement, then reloads.
        spent = {hold for receipt in current['receipts'] for hold in receipt['holds']}
        if spent.intersection(holds):
            raise ValueError('Conflicting activation retry')
        rows = roi_consumables.owned(saved)
        by_hold = {hold_id(row['sv_itemHoldId']): row for row in rows}
        selected = []
        for hold in holds:
            row = by_hold.get(hold)
            if row is None or int(row['sv_itemNum']) < 1 or row['sv_itemId'] not in BOOSTS:
                raise ValueError('Boost is unavailable or item use is unsupported')
            selected.append(row)
        now = number(int(time.time()))
        high = roi_consumables.hold_sequence(saved, rows)
        for row in selected:
            item = BOOSTS[row['sv_itemId']]
            previous = current['active'].get(item['kind'], {})
            if previous.get('started', now) > now:
                raise ValueError('Clock precedes recorded boost activation')
            ongoing = previous.get('expires', 0) > now
            expires = number((previous['expires'] if ongoing else now) + item['days']*86400)
            current['active'][item['kind']] = dict(
                started=previous['started'] if ongoing else now, expires=expires)
            count = int(row['sv_itemNum']) - 1
            if count:
                # A new hold identifies the remainder after refresh. Replaying the
                # old request can never consume the next unit of the same stack.
                high = number(high + 1, 1, 2**64-1)
                row.update(sv_itemNum=str(count), sv_itemHoldId=str(high))
            else:
                rows.remove(row)
        current['receipts'] = (current['receipts'] + [dict(holds=holds, used_at=now)])[-RECEIPT_LIMIT:]
        roi_consumables.owned({'consumableItems': rows})
        profile_store.update(path, dict(consumableItems=rows, consumableHoldSequence=high, boostState=current))
        return {}
