"""Synchronize loadout selections and compare an authenticated roster with native seats."""
import http.client
import json
import struct
import threading
import time
from pathlib import Path


HERE=Path(__file__).resolve().parent
LOCK=threading.Lock()
PENDING=None
WORKER=None
CACHED=None
LAST_CHECK=0.
READING=False


def credential():
    from roi_account_client import read_credential
    import roi_progression_client
    account=read_credential(HERE/'account-auth.json')
    if not account or roi_progression_client.credential(account['uid'])!=account['token']:
        raise ValueError('No matching account')
    return account


def restore(account, options, exchange):
    """Before startup only, restore an enrolled user's choice on a new installation."""
    import asyncio
    import roi_lamina
    from release_policy import lamina_allowed
    reply=asyncio.run(exchange(options,'GET','/progress/'+account['uid'],account['token']))
    fields=reply.get('fields') if isinstance(reply,dict) else None
    import release_lamina
    release_lamina.publish(fields if isinstance(fields,dict) else {})
    if not isinstance(fields,dict):
        return
    entry=fields.get('skillGridLaminaLoadout')
    if entry and lamina_allowed(fields):
        roi_lamina.select(HERE/'lamina_profile.json',entry['id'],entry['variant'])


def submit(selection,variant):
    """Bound backlog to the latest selection. No network calls on the game thread."""
    global PENDING,WORKER
    if type(selection)!=int or not 1<=selection<=15 or type(variant)!=int or not 0<=variant<=2:
        raise ValueError('Invalid selected Lamina')
    with LOCK:
        PENDING=(selection,variant)
        if WORKER is None:
            WORKER=threading.Thread(target=publish,daemon=True)
            WORKER.start()


def publish():
    global PENDING,WORKER
    import roi_progression_client as api
    while True:
        with LOCK:
            entry,PENDING=PENDING,None
            if entry is None:
                WORKER=None
                return
        try:
            uid=credential()['uid']
            reply=api.forward('http://127.0.0.1:52713/progress/'+uid,uid,1,'Lamina','selectLamina',
                              dict(id=entry[0],variant=entry[1]))
            result='accepted' if reply and reply.get('result')=='OK' else 'not accepted'
        except Exception:
            result='unavailable'
        print('LAMINA_SYNC selection '+result,flush=True)


def fetch():
    global CACHED,READING
    result=None
    try:
        a=credential()
        c=http.client.HTTPConnection('127.0.0.1',52713,timeout=2)
        try:
            c.request('GET','/lamina-roster/'+a['uid'],headers={'Authorization':'Bearer '+a['token']})
            res=c.getresponse()
            if res.status!=200:
                return
            raw=res.read(4097)
            if len(raw)>4096:
                return
            doc=json.loads(raw)
        finally:
            c.close()
        members=doc['members']
        if (not isinstance(doc.get('round'),str) or len(doc['round'])!=32 or not isinstance(members,list)
                or not 1<=len(members)<=4 or any(set(p)!={'id','variant','uid'} or
                type(p['id'])!=int or not 0<=p['id']<=15 or type(p['variant'])!=int or
                not 0<=p['variant']<=2 or not isinstance(p['uid'],str) for p in members)):
            return
        rows={int(p['uid']):(p['id'],p['variant']) for p in members}
        if len(rows)!=len(members) or int(a['uid']) not in rows:
            return
        result=(doc['round'],rows)
    except Exception:
        pass
    finally:
        with LOCK:
            CACHED,READING=result,False


def setup(d):
    # Observe the existing P2P roster. Never write speculative peer actors or
    # replay an input from wall-clock HTTP timing.
    previous=[None]
    def poll(debugger):
        global LAST_CHECK,READING
        try:
            def num(p,fmt):
                size=struct.calcsize(fmt)
                if not 0x10000<=p<0x7fffffff0000:raise ValueError('bad pointer')
                return struct.unpack(fmt,debugger.read(p,size))[0]
            play=num(0x141B874D0,'<Q'); reg=num(0x141B87490,'<Q'); roster=num(0x141B87458,'<Q')
            if num(play+8,'<B') or num(play+0x10,'<I')!=0x150305F0 or num(roster+0x5F650,'<I')==5:
                previous[0]=None
                return
            with LOCK:
                if not READING and time.monotonic()-LAST_CHECK>3:
                    READING=True
                    LAST_CHECK=time.monotonic()
                    threading.Thread(target=fetch,daemon=True).start()
                cached=CACHED
            if not cached:
                return
            key,expected=cached
            rows=[]
            for slot in range(4):
                p=num(reg+slot*0x25D0+0x18,'<Q')
                if not p or num(p+0xC58,'<Q')!=p or num(p+0xC71,'<b')!=slot:continue
                uid=num(play+slot*0x22D8+0x48,'<Q')
                if uid not in expected:continue
                actual=(num(p+0xC78,'<I'),num(p+0x5611,'<b'))
                rows.append((slot,expected[uid],actual,bool(num(p+0xC288,'<Q'))))
            state=(key,rows)
            if rows and state!=previous[0]:
                debugger.log('LAMINA_PEERS '+json.dumps(dict(round=key,slots=rows)))
                previous[0]=state
        except (OSError,ValueError,struct.error):
            return
    if (HERE/'lamina-peer-trace.enabled').is_file():
        d.poll(poll,every=0.25)
