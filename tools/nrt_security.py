"""Admission and resource bounds for the native lobby protocol.

Shared servers use a distinct random 16-byte token per permitted Steam ID, carried
in the game's existing login field. Tokens must travel over WireGuard, not public TCP.
This is administrator-provisioned identity binding, not Steam ticket verification.
"""
from collections import deque
import hmac
import json
import os
from pathlib import Path
import threading
import time

MAX_FRAME = 16384
MAX_CONNECTIONS = 32
MAX_PER_ADDRESS = 4
MAX_ROOMS = 128
MAX_MEMBERS = 4
IDLE_SECONDS = 150
FRAME_SECONDS = 15
MAX_PER_SECOND = 120
AUTH_TOKENS = {}
ACTIVE = {}
LOCK = threading.RLock()


class ProtocolError(ValueError):
    pass


def load_auth(filename=None):
    filename = filename or os.environ.get("ROI_AUTH_FILE")
    if not filename:
        return False
    data = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
    tokens = {}
    for key, value in data.items():
        uid = int(key)
        token = bytes.fromhex(value)
        if not 0 < uid < 2 ** 64 or len(token) != 16:
            raise ValueError("Invalid lobby credential record")
        tokens[uid] = token
    if not tokens or len(set(tokens.values())) != len(tokens):
        raise ValueError("Lobby credentials must be nonempty and unique")
    AUTH_TOKENS.clear()
    AUTH_TOKENS.update(tokens)
    return True


def login(session, uid, payload):
    if not 0 < uid < 2 ** 64:
        raise ProtocolError("Invalid identity")
    if AUTH_TOKENS:
        expected = AUTH_TOKENS.get(uid)
        if expected is None or len(payload) != 16 or not hmac.compare_digest(expected, payload):
            raise ProtocolError("Login rejected")
    with LOCK:
        if session.get("uid", uid) != uid:
            raise ProtocolError("Identity cannot change")
        if uid in ACTIVE and ACTIVE[uid] is not session:
            raise ProtocolError("Identity already connected")
        ACTIVE[uid] = session
        session["uid"] = uid


def release(session):
    with LOCK:
        uid = session.get("uid")
        if ACTIVE.get(uid) is session:
            ACTIVE.pop(uid, None)


def check_message(session, uid):
    if session.get("uid") != uid:
        raise ProtocolError("Login required; identity must match")


def rate_limit(session, now=None):
    now = time.monotonic() if now is None else now
    times = session.setdefault("recent_messages", deque())
    while times and times[0] <= now - 1:
        times.popleft()
    if len(times) >= MAX_PER_SECOND:
        raise ProtocolError("Message rate exceeded")
    times.append(now)
