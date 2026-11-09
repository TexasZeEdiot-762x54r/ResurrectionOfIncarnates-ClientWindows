"""Approved account progression over the existing certificate-checked TLS relay."""
from copy import deepcopy
import hashlib
import http.client
import json
import logging
from pathlib import Path
import queue
import re
import threading
import time
from urllib.parse import urlsplit
import uuid

import progression_contract as contract

KEY_FILE=Path(__file__).with_name('account-auth.json')
LOCK=threading.RLock()
CACHE={}
READ_LOCK=threading.Lock()
POOLS={}
CACHE_SECONDS=10
CONNECTION_IDLE_SECONDS=4  # The relay closes HTTP connections after five idle seconds.

class Unavailable(RuntimeError):pass

class ResponseError(Exception):
    def __init__(self,code):self.code=code

def exchange(target,uid,token,body,timeout,fresh=False):
    """Two bounded HTTP/1.1 streams share the already-pinned loopback tunnel."""
    key=(target.netloc,str(uid),hashlib.sha256(token.encode()).digest())
    with LOCK:
        pool=POOLS.get(key)
        if pool is None:
            pool=queue.LifoQueue(maxsize=2);pool.put(None);pool.put(None)
            if len(POOLS)>=2:raise Unavailable('Too many account transports')
            POOLS[key]=pool
    try:connection=pool.get(timeout=min(timeout,1.5))
    except queue.Empty:raise OSError('Progression transport busy') from None
    try:
        if connection is not None and (fresh or
                time.monotonic()-getattr(connection,'_roi_last_used',0)>=CONNECTION_IDLE_SECONDS):
            connection.close();connection=None
        if connection is None:connection=http.client.HTTPConnection(target.hostname,target.port,timeout=timeout)
        connection.timeout=timeout
        if connection.sock is not None:connection.sock.settimeout(timeout)
        connection.request('POST' if body is not None else 'GET',target.path,body=body,
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        response=connection.getresponse();raw=response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise ValueError('Progression response too large')
        if response.status!=200:raise ResponseError(response.status)
        if response.will_close:connection.close();connection=None
        if connection is not None:connection._roi_last_used=time.monotonic()
        return raw
    except BaseException:
        if connection is not None:connection.close()
        connection=None
        raise
    finally:pool.put(connection)

def credential(uid):
    try:
        with KEY_FILE.open('rb') as stream:raw=stream.read(1025)
    except FileNotFoundError:return None
    if len(raw)>1024:raise Unavailable('Invalid account credential file')
    try:value=json.loads(raw)
    except ValueError:raise Unavailable('Invalid account credential file') from None
    if (not isinstance(value,dict) or set(value)!={'schema','uid','issuer','token'} or value['schema']!=1
            or not isinstance(value['token'],str) or not re.fullmatch(r'[0-9a-f]{64}',value['token'])):
        raise Unavailable('Invalid account credential file')
    if value['uid']!=str(uid):return None
    # lobby_server() reloads paths concurrently; its module-level _cfg is briefly
    # empty during reload. Validate against one complete on-disk snapshot instead.
    try:
        with KEY_FILE.with_name('config.json').open('rb') as stream:config_raw=stream.read(16385)
        if len(config_raw)>16384:raise ValueError('Configuration too large')
        config=json.loads(config_raw.decode('utf-8-sig'))
        if not isinstance(config,dict):raise ValueError('Invalid configuration')
    except (OSError,ValueError):raise Unavailable('Account server configuration unavailable') from None
    if (config.get('lobby_transport')!='tls-public'
            or config.get('release_mode') not in ('vpn','internet','lan')
            or config.get('public_lobby_host')!=value['issuer']):
        raise Unavailable('Account credential belongs to a different server')
    return value['token']

def validate(value,uid,reply=False):
    expected={'uid','revision','fields'}|({'reply'} if reply else set())
    if (not isinstance(value,dict) or set(value)!=expected or value['uid']!=str(uid)
            or type(value['revision']) is not int or not 0<=value['revision']<2**63
            or not isinstance(value['fields'],dict) or contract.select(value['fields'])!=value['fields']):
        raise Unavailable('Invalid server progression')
    if reply and not isinstance(value['reply'],dict):raise Unavailable('Invalid operation reply')
    return value

def remember(uid,value):
    with LOCK:
        old=CACHE.get(str(uid))
        if old is None or value['revision']>=old[1]['revision']:
            CACHE.clear();CACHE[str(uid)]=(time.monotonic()+CACHE_SECONDS,deepcopy(value))

def request(url,uid,token,payload=None):
    target=urlsplit(url)
    if (target.scheme!='http' or target.hostname not in ('127.0.0.1','localhost') or target.port!=52713
            or target.username is not None or target.password is not None
            or target.path!='/progress/'+str(uid) or target.query or target.fragment):
        raise Unavailable('Progression must use the fixed local TLS relay')
    body=json.dumps(payload,separators=(',',':')).encode() if payload is not None else None
    if body is not None and len(body)>128*1024:raise ValueError('Progression request too large')
    timeouts=((0.8,2.0) if payload is not None and payload.get('action')=='nativeLP'
              else (6.0,6.0) if payload is not None else (0.8,2.0))
    for attempt,timeout in enumerate(timeouts):
        try:
            raw=exchange(target,uid,token,body,timeout,fresh=attempt>0)
            value=validate(json.loads(raw),uid,reply=payload is not None);remember(uid,value);return value
        except ResponseError as error:
            logging.getLogger('roi-profile').warning('Progression HTTP failure action=%s status=%d attempt=%d',
                payload.get('action') if payload else 'read', error.code, attempt+1)
            if error.code<500 or attempt+1==len(timeouts):raise Unavailable('Progression service rejected the operation') from None
        except (OSError,ValueError,http.client.HTTPException) as error:
            logging.getLogger('roi-profile').warning('Progression transport failure action=%s type=%s attempt=%d',
                payload.get('action') if payload else 'read', type(error).__name__, attempt+1)
            if attempt+1==len(timeouts):raise Unavailable('Progression service unavailable') from None
        except Unavailable:
            logging.getLogger('roi-profile').warning('Progression validation failure action=%s',
                payload.get('action') if payload else 'read')
            raise
        time.sleep(0.15*(attempt+1))

def fields(url,uid):
    token=credential(uid)
    if token is None:return None
    with READ_LOCK:
        with LOCK:
            old=CACHE.get(str(uid))
            if old is not None and time.monotonic()<old[0]:return deepcopy(old[1]['fields'])
        request(url,uid,token)
        with LOCK:return deepcopy(CACHE[str(uid)][1]['fields'])

def forward(url,uid,character,name,action,data):
    token=credential(uid)
    if token is None:return None
    if action not in contract.ACTIONS:raise ValueError('Unsupported progression action')
    data=deepcopy(data)
    if action=='nativeLP':
        import roi_native_lp
        roi_native_lp.authenticate(data)
        data=dict(data,sv_token='0'*64)  # The private loopback credential never leaves this PC.
    payload=dict(action=action,data=data,request_id=uuid.uuid4().hex,character=character,name=name)
    return request(url,uid,token,payload)['reply']
