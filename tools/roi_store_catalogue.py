"""Shared store contract and task-local authoritative checkout catalogue."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
import re

CURRENT = ContextVar('roi_store_catalogue', default=None)
ACTIONS = frozenset(('getMyCart','getStoreItemList','addToCart','updateCart','buyItem','removeFromCart'))


def validate(rows):
    if not isinstance(rows,list) or not 1 <= len(rows) <= 1024:
        raise ValueError('Invalid store catalogue size')
    result={}
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'store_id','item_id','kind','price_lp','max_quantity','revision','master'}:
            raise ValueError('Invalid store product')
        for key in ('store_id','item_id'):
            if not isinstance(row[key],str) or not re.fullmatch(r'[1-9][0-9]{15}',row[key]):
                raise ValueError('Invalid store identity')
        if row['store_id'][12]=='0' or row['kind'] not in ('grid','ticket','cube') or row['store_id'] in result:
            raise ValueError('Invalid or duplicate LP product')
        for key,low,high in (('price_lp',1,2147483647),('max_quantity',1,99),('revision',1,2**63-1)):
            if type(row[key]) is not int or not low<=row[key]<=high:
                raise ValueError('Invalid product value')
        master=row['master']
        if not isinstance(master,dict) or len(json.dumps(master))>4096:
            raise ValueError('Invalid store metadata')
        if (master.get('_id')!=row['store_id'] or master.get('sv_price')!=str(row['price_lp'])
                or master.get('sv_basisPrice')!=str(row['price_lp'])
                or master.get('sv_items')!=[{'sv_itemId':row['item_id'],'sv_itemCount':'1'}]):
            raise ValueError('Store metadata and authoritative product disagree')
        result[row['store_id']]=deepcopy(row)
    return result


@contextmanager
def use(rows):
    token=CURRENT.set(validate(rows))
    try:yield
    finally:CURRENT.reset(token)


def product(store_id):
    current=CURRENT.get()
    if current is None:return None
    if store_id not in current:raise ValueError('Product is not for sale')
    return current[store_id]


def master(store_id):
    row=product(store_id)
    return deepcopy(row['master']) if row is not None else None


def quote(store_id):
    row=product(store_id)
    return {'revivalStoreRevision':row['revision'],'revivalPriceLP':row['price_lp']} if row else {}


def verify_quote(cart):
    expected=quote(cart['sv_storeItemId'])
    if expected and any(cart.get(key)!=value for key,value in expected.items()):
        raise ValueError('Store price changed; add the product to the cart again')


def remote(url):
    """Public catalogue over the fixed, pinned local TLS tunnel. No stale fallback."""
    from urllib.parse import urlsplit
    from urllib.request import Request,ProxyHandler,build_opener
    import time
    target=urlsplit(url)
    if (target.scheme!='http' or target.hostname not in ('127.0.0.1','localhost') or target.port!=52713
            or target.path!='/store/catalogue' or target.username or target.password or target.query or target.fragment):
        raise ValueError('Store catalogue must use the local TLS tunnel')
    for attempt in range(2):
        try:
            with build_opener(ProxyHandler({})).open(Request(url,headers={'Connection':'close'}),timeout=6) as response:
                raw=response.read(1024*1024+1)
            if len(raw)>1024*1024:raise ValueError('Catalogue response too large')
            rows=json.loads(raw)['products']
            validate(rows)
            return rows
        except OSError:
            if attempt:raise
            time.sleep(.15)
