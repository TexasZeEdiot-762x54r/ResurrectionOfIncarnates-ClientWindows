"""Short-lived, local cosmetic eligibility derived from shared Circle responses."""
import logging
from pathlib import Path
import time

import profile_store

import os
LEADERS = frozenset(os.environ.get('ROI_VOICE_CIRCLE_LEADERS', '').split(',')) - {''}
CACHE = Path(__file__).resolve().parent / 'circle-voice-policy.json'
TTL = 3600
MAX_PLAYERS = 32


def steam_id(value):
    return isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) == 17


def allowed(document, uid, now=None):
    now = time.time() if now is None else now
    if not steam_id(uid) or not isinstance(document, dict) or document.get('schema') != 1:
        return False
    rows = document.get('players')
    if not isinstance(rows, dict) or len(rows) > MAX_PLAYERS:
        return False
    row = rows.get(uid)
    if not isinstance(row, dict) or not isinstance(row.get('leader'), str) or row['leader'] not in LEADERS:
        return False
    checked = row.get('checked')
    return type(checked) in (int, float) and now - TTL <= checked <= now


def record(uid, leader=None, path=None, now=None):
    try:
        _record(uid, leader, path, now)
    except (OSError, ValueError, TypeError):
        logging.getLogger('roi-profile').debug('Voice eligibility cache unavailable')


def _record(uid, leader, path, now):
    if not steam_id(uid):
        return
    path = CACHE if path is None else Path(path)
    now = time.time() if now is None else now
    # Share the existing process lock with profile writes, but never change a save.
    with profile_store.LOCK:
        document = profile_store.read(path)
        players = document.get('players', {})
        players = {key: value for key, value in players.items()
                   if allowed(dict(schema=1, players={key: value}), key, now)} if isinstance(players, dict) else {}
        players.pop(uid, None)
        if isinstance(leader, str) and leader in LEADERS:
            players[uid] = dict(leader=leader, checked=now)
        players = dict(sorted(players.items(), key=lambda pair: pair[1]['checked'], reverse=True)[:MAX_PLAYERS])
        profile_store.update(path, dict(schema=1, players=players))


def observe_circle(uid, circle, path=None):
    leader = None
    cid = circle.get('sv_circleId') if isinstance(circle, dict) else None
    if isinstance(cid, str) and cid.isascii() and cid.isdecimal() and 0 < int(cid) < 2**63:
        leader = circle.get('sv_leaderUid')
    record(uid, leader, path)


def refresh_remote(uid, profile, request, path=None):
    """request must run as the local account; never impersonate the viewed player."""
    try:
        cid = profile.get('sv_circleId', '0')
        if not isinstance(cid, str) or not cid.isascii() or not cid.isdecimal() or not 0 < int(cid) < 2**63:
            record(uid, path=path)
            return
        result = request('findCircle', {'sv_circleIds': [int(cid)]})
        circles = result['data']['sv_circles']
        if not isinstance(circles, list) or len(circles) != 1 or circles[0].get('sv_circleId') != cid:
            raise ValueError('Circle summary mismatch')
        observe_circle(uid, circles[0], path)
    except Exception as exc:
        record(uid, path=path)
        logging.getLogger('roi-profile').debug('Voice eligibility unavailable: %s', type(exc).__name__)
