"""Local ticket reservation around the durable, idempotent shared rename operation."""
import json
import urllib.request
import uuid

import profile_store
import roi_consumables
import team_names

TICKET_ID = '1000030020000001'


def request(url, token, action, data):
    body = json.dumps({'call': action, 'data': data}, separators=(',', ':')).encode('utf-8')
    if len(body) > team_names.MAX_REQUEST_BYTES:
        raise ValueError('Team request too large')
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    with opener.open(req, timeout=3) as response:
        raw = response.read(team_names.MAX_RESPONSE_BYTES + 1)
    if len(raw) > team_names.MAX_RESPONSE_BYTES:
        raise ValueError('Team response too large')
    result = json.loads(raw, object_pairs_hook=team_names.strict_object)
    if not isinstance(result, dict):
        raise ValueError('Invalid team response')
    return result


def finish(path, actor, pending, send):
    if (not isinstance(pending, dict) or pending.get('uid') != actor
            or not isinstance(pending.get('request'), dict)):
        raise ValueError('Pending team ticket belongs to a different or invalid identity')
    data = team_names.rename(actor, pending['request'])
    op = team_names.operation(pending.get('operation'))
    result = send('changeTagName', dict(data, operation=op))
    if (not isinstance(result, dict) or result.get('operation') != op
            or result.get('status') not in ('applied', 'unchanged', 'rejected')):
        raise ValueError('Ambiguous team response; ticket reservation retained')
    native = result.get('data')
    if (not isinstance(native, dict) or native.get('sv_partnerUid') != data['sv_partnerUid']
            or native.get('sv_freeTagName') != '' or not isinstance(native.get('sv_paidTagName'), str)
            or len(native['sv_paidTagName'].encode('utf-8')) > 63
            or result['status'] != 'rejected' and native['sv_paidTagName'] != data['sv_tagName']):
        raise ValueError('Invalid team receipt; ticket reservation retained')
    saved = profile_store.read(path)
    # Only an explicit durable rejection/no-op permits a refund. HTTP errors,
    # lost responses and disk failures leave the same operation reserved for retry.
    changes = {}
    if result['status'] != 'applied':
        changes, _ = roi_consumables.grant(saved, [TICKET_ID])
    rows = roi_consumables.owned(dict(saved, **changes))
    changes.update(consumableItems=[r for r in rows if r['sv_itemNum'] != '0'],
                   teamNamePending=None,
                   teamNameReceipt={'uid': actor, 'request': data, **result})
    profile_store.update(path, changes)
    return result


def change(path, actor, data, send):
    actor = team_names.uid(actor)
    data = team_names.rename(actor, data)
    with profile_store.LOCK:
        saved = profile_store.read(path)
        pending = saved.get('teamNamePending')
        if pending is not None:
            result = finish(path, actor, pending, send)
            if pending['request'] == data:
                return result
            saved = profile_store.read(path)
        # Native requests have no operation ID. A read before reserving handles
        # same-name retries and lets either partner make a later distinct change.
        listing = get(actor, {'sv_uids': [data['sv_partnerUid']]}, send)
        current = listing['sv_tagNames'][0]
        if current['sv_paidTagName'] == data['sv_tagName']:
            return {'status': 'unchanged', 'data': {
                'sv_partnerUid': data['sv_partnerUid'], 'sv_freeTagName': '',
                'sv_paidTagName': data['sv_tagName']}}
        rows = roi_consumables.owned(saved)
        ticket = next((r for r in rows if r['sv_itemId'] == TICKET_ID and int(r['sv_itemNum']) > 0), None)
        if ticket is None:
            raise ValueError('A Team Name Ticket is required')
        ticket['sv_itemNum'] = str(int(ticket['sv_itemNum']) - 1)
        pending = {'uid': actor, 'operation': uuid.uuid4().hex, 'request': data}
        profile_store.update(path, {'consumableItems': rows, 'teamNamePending': pending,
                                   'consumableHoldSequence': roi_consumables.hold_sequence(saved, rows)})
        return finish(path, actor, pending, send)


def recover(path, actor, send):
    """Reconcile a reserved ticket on the next native team-list refresh."""
    actor = team_names.uid(actor)
    with profile_store.LOCK:
        pending = profile_store.read(path).get('teamNamePending')
        if pending is None:
            return False
        finish(path, actor, pending, send)
        return True


def get(actor, data, send):
    actor = team_names.uid(actor)
    partners = data.get('sv_uids') if isinstance(data, dict) else None
    if not isinstance(partners, list) or len(partners) > 128:
        raise ValueError('Invalid partner query')
    partners = list(dict.fromkeys(team_names.uid(p) for p in partners))
    result = send('getTagName', {'sv_uids': partners})
    rows = result.get('sv_tagNames') if isinstance(result, dict) else None
    if not isinstance(rows, list) or len(rows) != len(partners):
        raise ValueError('Invalid team listing')
    by_uid = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('sv_uid') not in partners or row['sv_uid'] in by_uid:
            raise ValueError('Invalid team listing identity')
        for key in ('sv_freeTagName', 'sv_paidTagName'):
            if not isinstance(row.get(key), str) or len(row[key].encode('utf-8')) > 63:
                raise ValueError('Invalid team listing name')
        by_uid[row['sv_uid']] = {k: row[k] for k in ('sv_uid', 'sv_freeTagName', 'sv_paidTagName')}
    return {'sv_tagNames': [by_uid[p] for p in partners]}
