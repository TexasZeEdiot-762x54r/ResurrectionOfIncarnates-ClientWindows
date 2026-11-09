"""Inventory catalogue recovered from text, using explicit revival item IDs.

Original names/descriptions: common_text 460484..460514. Original server
database IDs remain unrecovered; roi_boosts implements approved revival use.
Merely owning these
items never changes award policy, currency, XP, or expansion rights.
"""
from copy import deepcopy

DAYS = (1, 3, 7, 14, 30)
CATALOGUE = tuple(
    {'id': f'1400{kind:02d}{days:03d}0200{index+1:03d}',
     'name': f'{label} Boost ({days} '+('day)' if days == 1 else 'days)'),
     'name_quote': 460484 + kind*5 + index,
     'description_quote': 460501 + kind*5 + index,
     'thumbnail': f'Other/Store_Thumbnail_Other_{("LP", "Exp")[kind]}_{index+1:03d}',
     'kind': label.lower(), 'days': days}
    for kind, label in enumerate(('LP', 'EXP')) for index, days in enumerate(DAYS)
) + (
    {'id':'1000030010000001','name':'Tournament Ticket','name_quote':460494,
     'description_quote':460511,'kind':'tournament_ticket',
     'thumbnail':'Other/Store_Thumbnail_Other_Tournament_001'},
    {'id':'1000030020000001','name':'Team Name Ticket','name_quote':460496,
     'description_quote':460513,'kind':'team_name_ticket',
     'thumbnail':'Other/Store_Thumbnail_Other_Teamname_001'},
)
IDS = {row['id'] for row in CATALOGUE}


def master_rows():
    return [{'sv_itemId': row['id'], 'sv_name': str(row['name_quote']),
             'sv_desc': str(row['description_quote']), 'sv_thumbnails': [row['thumbnail']],
             'sv_additions': [], 'sv_sort': str(index+100),
             'sv_createTime':'1388534400', 'sv_hold':'1'}
            for index, row in enumerate(CATALOGUE)]


def owned(snapshot):
    rows = snapshot.get('consumableItems', [])
    if not isinstance(rows, list) or len(rows) > 9999:
        raise ValueError('Invalid consumable inventory')
    result, holds = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid consumable row')
        item, hold, count = (str(row.get(key, '')) for key in ('sv_itemId','sv_itemHoldId','sv_itemNum'))
        if (not item.isascii() or not item.isdigit() or not 0 < int(item) < 2**64
                or not hold.isascii() or not hold.isdigit() or not 0 < int(hold) < 2**64
                or not count.isascii() or not count.isdigit() or not 0 <= int(count) <= 2147483647
                or hold in holds):
            raise ValueError('Invalid consumable identity or quantity')
        holds.add(hold)
        result.append({'sv_itemId':item,'sv_itemHoldId':hold,'sv_itemNum':count})
    return result


def grant_one_each(snapshot, receipt):
    receipts = deepcopy(snapshot.get('consumableGrants', {}))
    if receipt in receipts:
        return {}
    rows = owned(snapshot)
    next_hold = hold_sequence(snapshot, rows) + 1
    for item in CATALOGUE:
        existing = next((row for row in rows if row['sv_itemId'] == item['id']), None)
        if existing:
            if int(existing['sv_itemNum']) == 2147483647:
                raise ValueError('Inventory quantity overflow')
            existing['sv_itemNum'] = str(int(existing['sv_itemNum'])+1)
        else:
            rows.append({'sv_itemHoldId':str(next_hold),'sv_itemId':item['id'],'sv_itemNum':'1'})
            next_hold += 1
    owned({'consumableItems':rows})
    receipts[receipt] = [item['id'] for item in CATALOGUE]
    return {'consumableItems':rows,'consumableGrants':receipts,
            'consumableHoldSequence':next_hold-1}


def hold_sequence(snapshot, rows):
    value = snapshot.get('consumableHoldSequence', 97000000000)
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError('Invalid consumable hold sequence')
    return max([97000000000, value] + [int(row['sv_itemHoldId']) for row in rows])


def grant(snapshot, item_ids):
    """Stage selected items; the caller commits them with its battle receipt."""
    if not isinstance(item_ids, list) or len(item_ids) > 64 or any(item not in IDS for item in item_ids):
        raise ValueError('Unsupported consumable grant')
    if not item_ids:
        return {}, []
    rows = owned(snapshot)
    next_hold = hold_sequence(snapshot, rows) + 1
    granted = []
    for item_id in item_ids:
        existing = next((row for row in rows if row['sv_itemId'] == item_id), None)
        if existing is None:
            if next_hold >= 2**64 or len(rows) >= 9999:
                raise ValueError('Consumable inventory capacity exceeded')
            existing = dict(sv_itemHoldId=str(next_hold), sv_itemId=item_id, sv_itemNum='0')
            rows.append(existing)
            next_hold += 1
        count = int(existing['sv_itemNum'])
        if count >= 2147483647:
            raise ValueError('Consumable quantity overflow')
        existing['sv_itemNum'] = str(count + 1)
        granted.append(deepcopy(existing))
    owned({'consumableItems': rows})
    return {'consumableItems': rows, 'consumableHoldSequence': next_hold-1}, granted
