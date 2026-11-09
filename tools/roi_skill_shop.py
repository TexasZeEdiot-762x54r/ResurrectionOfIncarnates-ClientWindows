"""LP checkout for extra grids, Team Name Tickets and released skill cubes."""
from copy import deepcopy
from functools import lru_cache
import roi_skills
import roi_consumables
import roi_skill_catalogue
import roi_store_catalogue as catalogue

# Native decoder 0x1400fdc60: second digit 0, digits 7..9 = 002 identify an extra grid.
# Revival identifiers, not original server database IDs.
ITEM_ID = '1000000020000001'
STORE_ID = '1000000000001001'  # Native currency discriminator: LP, never Steam/IP.
NAME_QUOTE = '240118'
TICKET_ID = '1000030020000001'
TICKET_STORE_ID = '1000030020001001'
TICKET_PRICE = 100000  # Explicit user-selected revival price.
TICKET_QUOTE = '460496'
MAX_TICKETS = 99  # Native quantity chooser clamps at 99 (0x14044479f).
CUBE_PRICES = {'D': 100000, 'C': 500000, 'B': 1000000, 'A': 2500000}


@lru_cache(maxsize=1)
def _cube_products():
    # Dedicated LP SKUs must not reuse the cube definition's shape/variant digits.
    return {f'1200000000001{index:03d}': row
            for index,row in enumerate(roi_skill_catalogue.master(),1)}


def cube_products():
    current=catalogue.CURRENT.get()
    if current is None:return _cube_products()
    definitions={row['sv_skillcubeId']:row for row in _cube_products().values()}
    return {key:definitions[row['item_id']] for key,row in current.items() if row['kind']=='cube'}


def cube_masters(saved):
    if not roi_skill_catalogue.enabled(saved):
        return []
    current=catalogue.CURRENT.get()
    if current is not None:
        return [deepcopy(row['master']) for row in current.values() if row['kind']=='cube']
    rows = []
    for index, (store_id, cube) in enumerate(cube_products().items(), 3):
        row = store_master(saved)
        price = str(CUBE_PRICES[cube['sv_skillcubeRank']])
        row.update(_id=store_id, sv_items=[{'sv_itemId': cube['sv_skillcubeId'], 'sv_itemCount': '1'}],
                   sv_price=price, sv_basisPrice=price, sv_sort=str(index),
                   sv_storeItemDesc=cube['sv_skillcubeDesc'], sv_itemNameId=cube['sv_skillcubeName'])
        rows.append(row)
    return rows


def regular_product(store_id):
    row=catalogue.product(store_id)
    if row is not None:
        if row['kind'] not in ('cube','ticket'):raise ValueError('Not a regular store product')
        return row['item_id'],row['master']['sv_storeItemDesc'],row['price_lp']
    if store_id == TICKET_STORE_ID:
        return TICKET_ID, TICKET_QUOTE, TICKET_PRICE
    cube = cube_products().get(store_id)
    if cube is None:
        raise ValueError('Unsupported shop item')
    return cube['sv_skillcubeId'], cube['sv_skillcubeDesc'], CUBE_PRICES[cube['sv_skillcubeRank']]


def is_regular_cart(rows):
    return (isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict)
            and (rows[0].get('sv_storeItemId') == TICKET_STORE_ID
                 or rows[0].get('sv_storeItemId') in cube_products()))


def cost(saved):
    row=catalogue.product(STORE_ID)
    if row is not None:return row['price_lp']
    return roi_skills.integer(saved.get('skillGridPriceLP', 1), 1, 100000000)


def item_master():
    return {'sv_itemId': ITEM_ID, 'sv_name': NAME_QUOTE, 'sv_desc': NAME_QUOTE,
            'sv_thumbnails': [], 'sv_additions': [], 'sv_sort': '1',
            'sv_createTime': '1388534400', 'sv_hold': '0'}


def store_master(saved):
    row=catalogue.master(STORE_ID)
    if row is not None:return row
    return {'_id': STORE_ID, 'sv_items': [{'sv_itemId': ITEM_ID, 'sv_itemCount': '1'}],
            'sv_price': str(cost(saved)), 'sv_basisPrice': str(cost(saved)), 'sv_sort': '1',
            'sv_beginTime': '1388534400', 'sv_thumbnail': 'NULL', 'sv_thumbnails': [],
            'sv_feature': '0', 'sv_storeItemDesc': NAME_QUOTE, 'sv_itemNameId': NAME_QUOTE}


def ticket_master():
    row=catalogue.master(TICKET_STORE_ID)
    if row is not None:return row
    row = store_master({})
    row.update(_id=TICKET_STORE_ID,
               sv_items=[{'sv_itemId': TICKET_ID, 'sv_itemCount': '1'}],
               sv_price=str(TICKET_PRICE), sv_basisPrice=str(TICKET_PRICE), sv_sort='2',
               sv_thumbnails=['Other/Store_Thumbnail_Other_Teamname_001'],
               sv_storeItemDesc=TICKET_QUOTE, sv_itemNameId=TICKET_QUOTE)
    return row


def cart_type(data):
    # Ticket quantity popup adds with 0; checkout prepares and buys with 1.
    # These are request contexts, not a restriction to a particular product.
    return roi_skills.integer(data.get('sv_type'), 0, 1)


def cart_field(kind):
    # Keep the legacy name for the type-1 checkout, also used by direct grids.
    return ('storeCart', 'skillGridCart')[roi_skills.integer(kind, 0, 1)]


def cart(saved, kind=1):
    return deepcopy(saved.get(cart_field(kind), []))


def is_ticket_cart(rows):
    return (isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict)
            and rows[0].get('sv_storeItemId') == TICKET_STORE_ID)


def reply(saved, rows=None, kind=1):
    store = []
    current=catalogue.CURRENT.get()
    if kind == 1 and roi_skills.enabled(saved) and roi_skills.progression(saved) and (current is None or STORE_ID in current):
        remaining = roi_skills.MAX_GRIDS - len(roi_skills.grids(saved))
        store.append({'sv_storeItemId': STORE_ID, 'sv_buyableCount': str(remaining),
                      'sv_items': [{'sv_itemId': ITEM_ID}], 'sv_salesRank': '0'})
    # Native chooser subtracts already-owned and cart quantities from this total.
    if kind == 0 or is_regular_cart(cart(saved, kind) if rows is None else rows):
        if current is None or TICKET_STORE_ID in current:
            store.append({'sv_storeItemId': TICKET_STORE_ID, 'sv_buyableCount': '2147483647',
                          'sv_items': [{'sv_itemId': TICKET_ID}], 'sv_salesRank': '0'})
        if roi_skill_catalogue.enabled(saved):
            store.extend({'sv_storeItemId': key, 'sv_buyableCount': '2147483647',
                          'sv_items': [{'sv_itemId': cube['sv_skillcubeId']}], 'sv_salesRank': '0'}
                         for key, cube in cube_products().items())
    return {'sv_cartItems': cart(saved, kind) if rows is None else rows,
            'sv_storeItems': store, 'sv_items': []}


def add(saved, data):
    kind = cart_type(data)
    items = data.get('sv_storeItems')
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ValueError('Only one product per checkout is supported')
    item = items[0]
    store_id = str(item.get('sv_storeItemId'))
    if store_id == TICKET_STORE_ID or store_id in cube_products():
        if store_id == TICKET_STORE_ID and kind != 0:
            raise ValueError('Tickets use the regular store cart')
        if store_id != TICKET_STORE_ID and not roi_skill_catalogue.enabled(saved):
            raise ValueError('Skill cube purchasing is unavailable')
        item_id, quote, _ = regular_product(store_id)
        product=catalogue.product(store_id)
        count = roi_skills.integer(item.get('sv_storeItemCount'), 1, product['max_quantity'] if product else MAX_TICKETS)
        changes = {'storeCart': [
            {'sv_storeItemId': store_id, 'sv_storeOrderId': '1', 'sv_resultCode': '0',
             'sv_storeItemDesc': quote, 'sv_storeItemCount': str(count),
             'sv_items': [{'sv_itemId': item_id}], **catalogue.quote(store_id)}]}
        # LP cube buttons add directly to type 1; quantity popups use type 0.
        if kind == 1:
            changes['skillGridCart'] = deepcopy(changes['storeCart'])
        elif is_regular_cart(cart(saved, 1)) and changes['storeCart'] != cart(saved, 0):
            changes['skillGridCart'] = []
        return changes
    if kind != 1:
        raise ValueError('Grid purchases use the direct cart')
    if not roi_skills.enabled(saved) or not roi_skills.progression(saved):
        raise ValueError('Grid purchasing is unavailable')
    if str(item.get('sv_storeItemId')) != STORE_ID:
        raise ValueError('Unsupported shop item')
    roi_skills.integer(item.get('sv_storeItemCount'), 1, 1)
    if len(roi_skills.grids(saved)) >= roi_skills.MAX_GRIDS:
        raise ValueError('All loadouts are owned')
    # Retried addToCart replaces the single-item cart rather than duplicating it.
    rows = [{'sv_storeItemId': STORE_ID, 'sv_storeOrderId': '1', 'sv_resultCode': '0',
             'sv_storeItemDesc': NAME_QUOTE, 'sv_storeItemCount': '1',
             'sv_items': [{'sv_itemId': ITEM_ID}], **catalogue.quote(STORE_ID)}]
    return {'skillGridCart': rows}


def update(saved, data):
    kind = cart_type(data)
    items = data.get('sv_storeItems')
    if (kind == 1 and isinstance(items, list) and len(items) == 1
            and isinstance(items[0], dict)
            and (str(items[0].get('sv_storeItemId')) == TICKET_STORE_ID
                 or str(items[0].get('sv_storeItemId')) in cube_products())):
        # Checkout 0x14040428b -> 0x14010e7e0 sends the selected basket snapshot.
        # Checkout carries the final quantity, which can differ from addToCart.
        # Require an existing order so an update after payment cannot
        # recreate it, then synchronize both copies without charging.
        rows = add(saved, dict(data, sv_type=0))['storeCart']
        if (not is_regular_cart(cart(saved, 0))
                or rows[0]['sv_storeItemId'] != cart(saved, 0)[0]['sv_storeItemId']):
            raise ValueError('No matching pending basket to update')
        catalogue.verify_quote(cart(saved,0)[0])
        return {'storeCart': rows, 'skillGridCart': deepcopy(rows)}
    return add(saved, data)


def remove(saved, data):
    kind = cart_type(data)
    orders = data.get('sv_storeOrderIds', [])
    if not isinstance(orders, list) or len(orders) > 99:
        raise ValueError('Invalid cart order list')
    if '1' not in [str(v) for v in orders]:
        return {}
    changes = {cart_field(kind): []}
    if kind == 0 and is_regular_cart(cart(saved, 1)):
        changes['skillGridCart'] = []
    return changes


def buy(saved, data):
    kind = cart_type(data)
    rows = cart(saved, kind)
    if is_regular_cart(rows):
        if kind == 1 and rows != cart(saved, 0):
            raise ValueError('Checkout no longer matches the pending basket')
        store_id = rows[0]['sv_storeItemId']
        catalogue.verify_quote(rows[0])
        item_id, _, unit_price = regular_product(store_id)
        product=catalogue.product(store_id)
        count = roi_skills.integer(rows[0].get('sv_storeItemCount'), 1, product['max_quantity'] if product else MAX_TICKETS)
        balance = roi_skills.integer(saved.get('gamePoint', 0), 0, 2147483647)
        price = count * unit_price
        if balance < price:
            raise ValueError('Insufficient LP')
        changes = {}
        is_cube = store_id != TICKET_STORE_ID
        grant = roi_skill_catalogue.grant if is_cube else roi_consumables.grant
        batch_size = 4 if is_cube else 64
        for offset in range(0, count, batch_size):
            batch, _ = grant(dict(saved, **changes), [item_id] * min(batch_size, count-offset))
            changes.update(batch)
        changes.update(gamePoint=balance-price, storeCart=[])
        if is_regular_cart(cart(saved, 1)):
            changes['skillGridCart'] = []
        return changes, rows
    if kind != 1:
        raise ValueError('No supported regular store order to purchase')
    if not roi_skills.enabled(saved) or not roi_skills.progression(saved):
        raise ValueError('Grid purchasing is unavailable')
    if len(rows) != 1 or rows[0]['sv_storeItemId'] != STORE_ID:
        raise ValueError('No grid order to purchase')
    catalogue.verify_quote(rows[0])
    balance = roi_skills.integer(saved.get('gamePoint', 0), 0, 2147483647)
    price = cost(saved)
    if balance < price:
        raise ValueError('Insufficient LP')
    return {'skillGrids': roi_skills.add_grid(saved), 'gamePoint': balance - price,
            'skillGridCart': []}, rows
