"""Shared paid pair names. Native fields, explicitly bounded revival storage policy.

Wallets and tickets remain client-local in this rebuild. This service authenticates
the caller using the existing relay boundary; it is not a server-owned economy.
Circle/free naming is deliberately not implemented by the ticket endpoint.
"""
from contextlib import contextmanager
import json
import re
import unicodedata

MAX_REQUEST_BYTES = 16384
MAX_RESPONSE_BYTES = 65536
MAX_RECEIPTS = 65536
MAX_PAIRS = 16384


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def uid(value):
    if type(value) not in (str, int) or not re.fullmatch(r'[1-9][0-9]{0,19}', str(value)):
        raise ValueError('Invalid player ID')
    if int(value) >= 2**64:
        raise ValueError('Player ID out of range')
    return str(value)


def rename(actor, data):
    actor = uid(actor)
    if not isinstance(data, dict) or type(data.get('sv_isCircle')) not in (int, str):
        raise ValueError('Invalid naming request')
    if data['sv_isCircle'] not in (0, '0'):
        raise ValueError('Circle naming is not ticket naming')
    partner = uid(data.get('sv_partnerUid'))
    name = data.get('sv_tagName')
    if (partner == actor or not isinstance(name, str) or not name.strip()
            or len(name.encode('utf-8')) > 63
            or any(c in '<>' or unicodedata.category(c).startswith('C') for c in name)):
        raise ValueError('Invalid partner or team name')
    return {'sv_partnerUid': partner, 'sv_isCircle': '0', 'sv_tagName': name}


def operation(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Invalid operation ID')
    return value


@contextmanager
def database():
    import commendations
    with commendations.database() as db:
        db.execute('CREATE TABLE IF NOT EXISTS team_names ('
                   'first TEXT NOT NULL, second TEXT NOT NULL, paid TEXT NOT NULL, '
                   'PRIMARY KEY(first,second))')
        db.execute('CREATE TABLE IF NOT EXISTS team_name_receipts ('
                   'actor TEXT NOT NULL, operation TEXT NOT NULL, request TEXT NOT NULL, '
                   'response TEXT NOT NULL, PRIMARY KEY(actor,operation))')
        yield db


def names(db, actor, partner):
    pair = sorted((actor, partner), key=int)
    row = db.execute('SELECT paid FROM team_names WHERE first=? AND second=?', pair).fetchone()
    return {'sv_partnerUid': partner, 'sv_freeTagName': '', 'sv_paidTagName': row[0] if row else ''}


def perform(actor, action, data, known_partner=lambda value: False):
    actor = uid(actor)
    if not isinstance(data, dict):
        raise ValueError('Expected request object')
    if action == 'getTagName':
        partners = data.get('sv_uids')
        if not isinstance(partners, list) or len(partners) > 128:
            raise ValueError('Invalid partner query')
        partners = list(dict.fromkeys(uid(p) for p in partners))
        with database() as db:
            result = []
            for partner in partners:
                row = names(db, actor, partner)
                result.append({'sv_uid': partner, 'sv_freeTagName': row['sv_freeTagName'],
                               'sv_paidTagName': row['sv_paidTagName']})
            return {'sv_tagNames': result}
    if action != 'changeTagName':
        raise ValueError('Unsupported team operation')
    request = rename(actor, data)
    op = operation(data.get('operation'))
    payload = json.dumps(request, sort_keys=True, separators=(',', ':'))
    partner = request['sv_partnerUid']
    with database() as db, db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT request,response FROM team_name_receipts '
                              'WHERE actor=? AND operation=?', (actor, op)).fetchone()
        if previous:
            if previous[0] != payload:
                raise ValueError('Operation reused for a different name')
            return json.loads(previous[1], object_pairs_hook=strict_object)
        if db.execute('SELECT COUNT(*) FROM team_name_receipts').fetchone()[0] >= MAX_RECEIPTS:
            raise OSError('Team receipt capacity reached; retain pending client operation')
        current = names(db, actor, partner)
        status = 'unchanged'
        if current['sv_paidTagName'] != request['sv_tagName']:
            if not known_partner(int(partner)):
                status = 'rejected'
            else:
                pair = sorted((actor, partner), key=int)
                exists = db.execute('SELECT 1 FROM team_names WHERE first=? AND second=?', pair).fetchone()
                if not exists and db.execute('SELECT COUNT(*) FROM team_names').fetchone()[0] >= MAX_PAIRS:
                    raise OSError('Team capacity reached; retain pending client operation')
                db.execute('INSERT OR REPLACE INTO team_names VALUES (?,?,?)', pair + [request['sv_tagName']])
                current['sv_paidTagName'] = request['sv_tagName']
                status = 'applied'
        result = {'operation': op, 'status': status, 'data': current}
        db.execute('INSERT INTO team_name_receipts VALUES (?,?,?,?)',
                   (actor, op, payload, json.dumps(result, separators=(',', ':'))))
        return result
