"""Shared replay transport and bounded durable upload outbox for the local client."""
from contextlib import contextmanager
from http.client import IncompleteRead
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import profile_store
import roi_replays as replay

MAX_PENDING = 64
MAX_PENDING_BYTES = 16 * 1024 * 1024
LOCK = threading.Lock()
WORKERS = {}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, token, action, data):
    headers = {'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    body=json.dumps(dict(action=action,data=data),separators=(',',':')).encode()
    if len(body)>384*1024:raise ValueError('Recording request exceeds limit')
    opener=build_opener(ProxyHandler({}),NoRedirect())
    try:
        with opener.open(Request(url,body,headers,method='POST'),timeout=5) as response:
            raw=response.read(512*1024+1)
    except IncompleteRead:
        raise OSError('Incomplete shared replay response') from None
    if len(raw)>512*1024:raise ValueError('Recording response exceeds limit')
    value=json.loads(raw,object_pairs_hook=profile_store._strict_object)
    if type(value) is not dict:raise ValueError('Invalid recording response')
    return value


def validate_row(row, payload=False):
    fields={'sv_stageId','sv_roomType','sv_entries','sv_replayVer','sv_flag',
            'sv_replayId','sv_saveTime'}
    if payload:fields.add('sv_replayData')
    if type(row) is not dict or set(row)!=fields:
        raise ValueError('Invalid shared recording fields')
    if type(row['sv_replayId']) is not str or not re.fullmatch('[0-9a-f]{64}',row['sv_replayId']):
        raise ValueError('Invalid shared recording ID')
    replay.integer(row['sv_saveTime'],0,2**53-1)
    if type(row['sv_entries']) is not list or len(row['sv_entries'])!=4:
        raise ValueError('Invalid shared recording roster')
    owner=row['sv_entries'][0].get('sv_uid') if type(row['sv_entries'][0]) is dict else None
    value={k:v for k,v in row.items() if k not in ('sv_replayId','sv_saveTime')}
    if payload:replay.normalize(value,owner)
    else:replay.normalize_metadata(value,owner)
    return row


def find(url,token,data):
    replay.filters(data)
    value=request(url,token,'find',data)
    if set(value)!={'sv_replays'} or type(value['sv_replays']) is not list or len(value['sv_replays'])>100:
        raise ValueError('Invalid shared recording search')
    seen=set()
    for row in value['sv_replays']:
        validate_row(row)
        if row['sv_replayId'] in seen:raise ValueError('Duplicate shared recording')
        if not replay.matches(row,replay.filters(data)):raise ValueError('Shared recording does not match search')
        seen.add(row['sv_replayId'])
    return value['sv_replays']


def load(url,token,ident):
    value=validate_row(request(url,token,'load',dict(sv_replayId=ident)),True)
    if value['sv_replayId']!=ident:raise ValueError('Shared recording identity changed')
    return value


@contextmanager
def outbox(path):
    db=sqlite3.connect(Path(path),timeout=5)
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA auto_vacuum=FULL')
        db.execute('PRAGMA max_page_count=8192')
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS pending ('
                   'owner TEXT NOT NULL,key TEXT NOT NULL,created INTEGER NOT NULL,'
                   'body TEXT NOT NULL,PRIMARY KEY(owner,key))')
        db.execute('CREATE TABLE IF NOT EXISTS uploaded ('
                   'owner TEXT NOT NULL,local_id TEXT NOT NULL,shared_id TEXT NOT NULL,'
                   'created INTEGER NOT NULL,PRIMARY KEY(owner,local_id))')
        yield db
        db.commit()
    except BaseException:
        db.rollback();raise
    finally:db.close()


def stage(path,owner,key,data):
    owner=str(replay.integer(owner,1,2**64-1))
    if type(key) is not str or not re.fullmatch('[0-9a-f]{32}',key):
        raise ValueError('Invalid replay match key')
    value=replay.normalize(data,owner)
    body=json.dumps(dict(match_key=key,recording=value),separators=(',',':'))
    with outbox(path) as db:
        db.execute('DELETE FROM pending WHERE created<?',(int(time.time())-24*3600,))
        if db.execute('SELECT 1 FROM pending WHERE owner=? AND key=?',(owner,key)).fetchone():return
        count,used=db.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(body)),0) FROM pending').fetchone()
        if count>=MAX_PENDING or used+len(body)>MAX_PENDING_BYTES:
            raise ValueError('Ranked replay upload outbox is full')
        db.execute('INSERT INTO pending VALUES (?,?,?,?)',(owner,key,int(time.time()),body))


def flush(path,owner,url,token):
    """Only remove a queued recording after an explicit durable server receipt."""
    with outbox(path) as db:
        db.execute('DELETE FROM pending WHERE created<?',(int(time.time())-24*3600,))
        rows=db.execute('SELECT key,body FROM pending WHERE owner=? ORDER BY created LIMIT 8',(str(owner),)).fetchall()
    for key,body in rows:
        queued=json.loads(body,object_pairs_hook=profile_store._strict_object)
        value=request(url,token,'save',queued)
        import hashlib
        expected=hashlib.sha256(bytes.fromhex(key)).hexdigest()
        if set(value)!={'sv_replayId','stored'} or value['sv_replayId']!=expected or type(value['stored']) is not bool:
            raise ValueError('Invalid recording upload receipt')
        local_id=replay.identity(str(owner),queued['recording'])
        with outbox(path) as db:
            db.execute('INSERT OR REPLACE INTO uploaded VALUES (?,?,?,?)',
                       (str(owner),local_id,expected,int(time.time())))
            db.execute('DELETE FROM uploaded WHERE owner=? AND local_id NOT IN '
                       '(SELECT local_id FROM uploaded WHERE owner=? ORDER BY created DESC,local_id LIMIT ?)',
                       (str(owner),str(owner),replay.MAX_REPLAYS))
            db.execute('DELETE FROM pending WHERE owner=? AND key=?',(str(owner),key))


def merge(path,owner,local,remote):
    remote_ids={row['sv_replayId'] for row in remote}
    replaced=set()
    if Path(path).exists() and remote_ids:
        with outbox(path) as db:
            replaced={local_id for local_id,shared_id in db.execute(
                'SELECT local_id,shared_id FROM uploaded WHERE owner=?',(str(owner),)) if shared_id in remote_ids}
    rows=[row for row in local if row['sv_replayId'] not in replaced]+remote
    unique={row['sv_replayId']:row for row in rows}
    return sorted(unique.values(),key=lambda row:(-int(row['sv_saveTime']),row['sv_replayId']))[:replay.MAX_RESULTS]


def start(path,owner,url,token):
    identity=(str(Path(path).resolve()),str(owner),url)
    with LOCK:
        if identity in WORKERS:
            WORKERS[identity].set();return
        if len(WORKERS)>=4:return
        wake=threading.Event();WORKERS[identity]=wake
    def run():
        while True:
            try:
                if Path(path).exists():flush(path,owner,url,token)
            except (OSError,ValueError,sqlite3.Error):
                pass  # Retain the recording for a later retry, including after relaunch.
            wake.wait(30);wake.clear()
    threading.Thread(target=run,name='ranked-replay-upload',daemon=True).start()
