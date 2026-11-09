"""Local native replay archive. No executable code or real saves are modified.

Envelope: native 0x1401f60c0 -> 0x140807910 -> 0x140b44540.
Response readers: 0x1400cb200, 0x1400cbab0, 0x1400ccd90.
The compressed battle structure remains opaque. Validation here bounds and checks
its envelope, not every internal gameplay field. Native playback is unverified.
"""
import base64
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import struct
import time
import zlib

import profile_store

VERSION = '2.06_150305f0'
RAW_SIZE = 0xb1780
MAX_PAYLOAD = 0x40000 - 1
MAX_REPLAYS = 200
MAX_STORE_BYTES = 64 * 1024 * 1024
MAX_RESULTS = 100  # Native search UI explicitly supports up to 100 hits (quote 450150).
STAGE_IDS = {1, 2, 3, 5, 6}  # Existing stage_master IDs; Paris is 6, not 4.


def integer(value, low=0, high=2147483647):
    if type(value) is str and value.isascii() and value.isdigit() and len(value) <= 20:
        value = int(value)
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid replay integer')
    return value


def text(value, maximum=127):
    if (type(value) is not str or len(value.encode('utf-8')) > maximum
            or any(ord(c) < 32 for c in value)):
        raise ValueError('Invalid replay text')
    return value


def decode_payload(value):
    if (type(value) is not str or not 1 <= len(value) <= MAX_PAYLOAD
            or not re.fullmatch(r'[A-Za-z0-9+/=\r\n]+', value)):
        raise ValueError('Invalid replay encoding')
    compact = value.replace('\r\n', '')
    # Native padding includes line-break bytes in its length calculation and
    # may emit four '=' even for an aligned input. Never rewrite the stored text.
    if not re.fullmatch(r'[A-Za-z0-9+/]+={0,4}', compact):
        raise ValueError('Invalid replay line wrapping or padding')
    digits = compact.rstrip('=')
    try:
        decoded = base64.b64decode(digits + '=' * (-len(digits) % 4), validate=True)
    except ValueError as exc:
        raise ValueError('Invalid replay Base64') from exc
    if base64.b64encode(decoded).decode().rstrip('=') != digits or len(decoded) < 16:
        raise ValueError('Invalid replay Base64 bits')
    magic, size, compressed_size, level = struct.unpack('>4I', decoded[:16])
    if magic != 0x42494c5a or size != RAW_SIZE or not 0 < compressed_size <= RAW_SIZE or level > 9:
        raise ValueError('Invalid native replay envelope')
    end = 16 + compressed_size
    # The encoder passes stream position + 1; its zero byte is not ZLIB data.
    if len(decoded) not in (end, end + 1) or decoded[end:] not in (b'', b'\0'):
        raise ValueError('Invalid replay compressed size')
    packed = decoded[16:end]
    if level:
        try:
            stream = zlib.decompressobj()
            raw = stream.decompress(packed, RAW_SIZE + 1)
        except zlib.error as exc:
            raise ValueError('Invalid replay compression') from exc
        if not stream.eof or stream.unused_data or stream.unconsumed_tail:
            raise ValueError('Incomplete or excessive replay stream')
    else:
        raw = packed
    if len(raw) != RAW_SIZE or raw[:4] != struct.pack('<I', 0x150305f0):
        raise ValueError('Invalid replay structure version or size')
    return raw


def validate_payload(value):
    decode_payload(value)
    return value


def normalize_metadata(data, owner):
    required = {'sv_stageId', 'sv_roomType', 'sv_entries', 'sv_replayVer', 'sv_flag'}
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError('Invalid replay save fields')
    if data['sv_replayVer'] != VERSION:
        raise ValueError('Unsupported replay version')
    owner = str(integer(owner, 1, 2**64 - 1))
    entries = data['sv_entries']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 4:
        raise ValueError('Invalid replay participants')
    players, sides = [], Counter()
    fields = {'sv_uid', 'sv_playerLv', 'sv_charaId', 'sv_userName', 'sv_rankId',
              'sv_circleName', 'sv_side', 'sv_tagName'}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError('Invalid replay participant fields')
        side = integer(entry['sv_side'], 1, 2)
        sides[side] += 1
        players.append(dict(sv_uid=str(integer(entry['sv_uid'], 0, 2**64 - 1)),
                            sv_playerLv=str(integer(entry['sv_playerLv'], 0, 50)),
                            sv_charaId=str(integer(entry['sv_charaId'], 1, 21)),
                            sv_userName=text(entry['sv_userName']),
                            sv_rankId=str(integer(entry['sv_rankId'], 0, 30)),
                            sv_circleName=text(entry['sv_circleName']), sv_side=str(side),
                            sv_tagName=text(entry['sv_tagName'])))
    uids = [row['sv_uid'] for row in players if row['sv_uid'] != '0']
    if owner not in uids or len(uids) != len(set(uids)) or max(sides.values()) > 2:
        raise ValueError('Invalid replay owner or team allocation')
    stage = integer(data['sv_stageId'], 1, 6)
    if stage not in STAGE_IDS:
        raise ValueError('Unknown replay stage')
    return dict(sv_stageId=str(stage),
                sv_roomType=str(integer(data['sv_roomType'], 0, 255)), sv_entries=players,
                sv_replayVer=VERSION,
                sv_flag=str(integer(data['sv_flag'], 0, 2**64 - 1)))


def normalize(data, owner):
    if not isinstance(data, dict) or 'sv_replayData' not in data:
        raise ValueError('Missing replay payload')
    value = normalize_metadata({k: v for k, v in data.items() if k != 'sv_replayData'}, owner)
    value['sv_replayData'] = validate_payload(data['sv_replayData'])
    return value


def identity(owner, value):
    return hashlib.sha256(json.dumps([owner, value], sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def stored_metadata(ident, stamp, metadata, owner):
    if type(ident) is not str or not re.fullmatch('[a-f0-9]{64}', ident):
        raise ValueError('Invalid stored replay ID')
    integer(stamp, 0, 2**53 - 1)
    if type(metadata) is not str or len(metadata) > 8192:
        raise ValueError('Invalid replay metadata size')
    value = json.loads(metadata, object_pairs_hook=profile_store._strict_object)
    return normalize_metadata(value, owner)


@contextmanager
def database(path):
    connection = sqlite3.connect(Path(path), timeout=5)
    try:
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('BEGIN IMMEDIATE')
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version == 0:
            if connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                raise ValueError('Unrecognized replay database')
            connection.execute('CREATE TABLE replays (id TEXT PRIMARY KEY, owner TEXT NOT NULL, '
                               'saved_ms INTEGER NOT NULL, metadata TEXT NOT NULL, payload TEXT NOT NULL)')
            connection.execute('CREATE INDEX replay_owner_time ON replays(owner, saved_ms DESC)')
            connection.execute('PRAGMA user_version=1')
        elif version != 1:
            raise ValueError('Unsupported replay database schema')
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def save(path, owner, data, *, now_ms=None):
    owner = str(integer(owner, 1, 2**64 - 1))
    value = normalize(data, owner)
    digest = identity(owner, value)
    payload = value.pop('sv_replayData')
    metadata = json.dumps(value, separators=(',', ':'))
    stamp = integer(time.time_ns() // 1000000 if now_ms is None else now_ms, 0, 2**53 - 1)
    with database(path) as db:
        if db.execute('SELECT 1 FROM replays WHERE id=? AND owner=?', (digest, owner)).fetchone():
            return digest
        count, used = db.execute('SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)+LENGTH(metadata)),0) FROM replays').fetchone()
        if count >= MAX_REPLAYS or used + len(payload) + len(metadata) > MAX_STORE_BYTES:
            raise ValueError('Replay archive is full; existing replays retained')
        db.execute('INSERT INTO replays VALUES (?,?,?,?,?)', (digest, owner, stamp, metadata, payload))
    return digest


def filters(data):
    allowed = {'sv_replayVer', 'sv_stageId', 'sv_roomType', 'sv_uids', 'sv_minRankId',
               'sv_maxRankId', 'sv_allyCharaIds', 'sv_foeCharaIds', 'sv_circleName',
               'sv_beginTime', 'sv_endTime'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError('Invalid replay search fields')
    out = {}
    for key, value in data.items():
        if key in ('sv_replayVer', 'sv_circleName'):
            out[key] = text(value)
        elif key in ('sv_uids', 'sv_allyCharaIds', 'sv_foeCharaIds'):
            if not isinstance(value, list) or len(value) > (64 if key == 'sv_uids' else 2):
                raise ValueError('Invalid replay search list')
            out[key] = [str(integer(v, 1, 2**64 - 1 if key == 'sv_uids' else 21)) for v in value]
        else:
            out[key] = integer(value, 0, 2**53 - 1 if key.endswith('Time') else 255)
    for start, end in (('sv_beginTime', 'sv_endTime'), ('sv_minRankId', 'sv_maxRankId')):
        if out.get(end) and out.get(start, 0) > out[end]:
            raise ValueError('Reversed replay search interval')
    return out


def matches(row, query):
    for key in ('sv_stageId', 'sv_roomType'):
        if query.get(key) and int(row[key]) != query[key]:
            return False
    if query.get('sv_replayVer') and row['sv_replayVer'] != query['sv_replayVer']:
        return False
    players = row['sv_entries']
    if query.get('sv_uids') and not set(query['sv_uids']) <= {p['sv_uid'] for p in players}:
        return False
    if query.get('sv_circleName') and not any(p['sv_circleName'] == query['sv_circleName'] for p in players):
        return False
    for player in players:
        rank = int(player['sv_rankId'])
        if query.get('sv_minRankId', 0) > rank or query.get('sv_maxRankId') and rank > query['sv_maxRankId']:
            return False
    # Search teams follow native side 1 / side 2; repeated characters need two entries.
    for key, side in (('sv_allyCharaIds', '1'), ('sv_foeCharaIds', '2')):
        have = Counter(p['sv_charaId'] for p in players if p['sv_side'] == side)
        if Counter(query.get(key, [])) - have:
            return False
    stamp = int(row['sv_saveTime'])
    return (stamp >= query.get('sv_beginTime', 0)
            and (not query.get('sv_endTime') or stamp <= query['sv_endTime']))


def find(path, owner, data):
    owner = str(integer(owner, 1, 2**64 - 1))
    query = filters(data)
    found = []
    with database(path) as db:
        rows = db.execute('SELECT id,saved_ms,metadata FROM replays WHERE owner=? '
                          'ORDER BY saved_ms DESC,id LIMIT ?', (owner, MAX_REPLAYS))
        for ident, stamp, metadata in rows:
            row = stored_metadata(ident, stamp, metadata, owner)
            row.update(sv_replayId=ident, sv_saveTime=str(stamp))
            if matches(row, query):
                found.append(row)
                if len(found) == MAX_RESULTS:
                    break
    return found


def load(path, owner, ident):
    owner = str(integer(owner, 1, 2**64 - 1))
    if type(ident) is not str or not re.fullmatch('[a-f0-9]{64}', ident):
        raise ValueError('Invalid replay ID')
    with database(path) as db:
        row = db.execute('SELECT saved_ms,metadata,payload FROM replays WHERE id=? AND owner=?',
                         (ident, owner)).fetchone()
        if row is None:
            raise ValueError('Replay unavailable')
        stamp, metadata, payload = row
        value = stored_metadata(ident, stamp, metadata, owner)
        value['sv_replayData'] = validate_payload(payload)
        if identity(owner, value) != ident:
            raise ValueError('Replay content does not match its ID')
        value.update(sv_replayId=ident, sv_saveTime=str(stamp))
        return value


def decode_request(body):
    """Only saveReplay may contain native raw CRLF inside its payload string."""
    if len(body) > 1024 * 1024:
        raise ValueError('Replay request exceeds limit')
    value = json.loads(body.decode('utf-8'), strict=False, object_pairs_hook=profile_store._strict_object)
    if not isinstance(value, dict) or value.get('call') != 'saveReplay' or not isinstance(value.get('data'), dict):
        raise ValueError('Not a native replay save')
    def check(item, path=()):
        if path == ('data', 'sv_replayData'):
            validate_payload(item)
        elif isinstance(item, dict):
            for key, child in item.items():
                text(key)
                check(child, path + (key,))
        elif isinstance(item, list):
            for child in item:
                check(child, path + ('[]',))
        elif isinstance(item, str):
            text(item, 1024)
    check(value)
    return value


def encode_reply(value):
    """Supply raw-extractable Base64 inside a valid JSON response."""
    data = value.get('data') if isinstance(value, dict) else None
    if not isinstance(data, dict) or 'sv_replayData' not in data:
        return json.dumps(value, separators=(',', ':')).encode()
    # 0x1400cb200 extracts the literal payload before parsing the full JSON.
    # Raw CRLF fails 0x140087655; escaped CRLF would pollute that raw extract.
    # Compact only the wire representation, preserving stored text and its ID.
    payload = validate_payload(data['sv_replayData']).replace('\r\n', '')
    result = deepcopy(value)
    # Version follows payload so 0x1400cb280 always finds the terminator '",'.
    result['data'] = dict(sv_replayData='', **{key: item for key, item in data.items() if key != 'sv_replayData'})
    encoded = json.dumps(result, separators=(',', ':')).encode()
    return encoded.replace(b'"sv_replayData":""', b'"sv_replayData":"' + payload.encode('ascii') + b'"', 1)
