"""Realtime-server ("NRT") message handling (reloaded on every received chunk).

Framing (roi.exe 0x1400e83e0 / 0x1400e8160):
  u16 L (= bytes that follow), u16 body_len, body[body_len], u16 trailer (not checked by the client)
client -> server body: u64 seq, u16 session, u16 type, u64 uid, payload
server -> client body: u64 seq, u16 session, u16 type, u16 result, u64 uid, payload
  The client handles a message directly when result == 0 (0x1400e8e50): type 0x9001 marks it logged in.
"""
import struct
import time

import nrt_rooms
import nrt_security as security

TYPE_LOGIN = 0x9001
# one-way notifications (0x1400f6a10 returns true): never answered, never awaited
NOTIFY = {0xE605, 0xE60C, 0xE60D, 0xE60E, 0xE60F, 0xE617, 0xE61F, 0xE623, 0xE641, 0xE642, 0xE653, 0xE661,
          0xE663, 0xE665}
# types with a reply handler in 0x1400e8e50 (anything else received makes the client drop the connection)
HANDLED = {0x9001, 0xE605, 0xE608, 0xE609, 0xE60A, 0xE60B, 0xE60C, 0xE60D, 0xE60E, 0xE60F, 0xE610, 0xE611,
           0xE612, 0xE613, 0xE614, 0xE616, 0xE617, 0xE61F, 0xE620, 0xE621, 0xE622, 0xE623, 0xE640, 0xE641,
           0xE652, 0xE653, 0xE660, 0xE661, 0xE662, 0xE663, 0xE664, 0xE665, 0xF000}


def parse(buf):
    """Split complete client packets off buf -> ([(seq, session, type, uid, payload)], rest)."""
    out = []
    while len(buf) >= 2:
        (L,) = struct.unpack_from("<H", buf, 0)
        if not 24 <= L <= security.MAX_FRAME - 2:
            raise security.ProtocolError("Invalid outer packet length")
        if len(buf) < L + 2:
            break
        (blen,) = struct.unpack_from("<H", buf, 2)
        if blen != L - 4:
            raise security.ProtocolError("Inconsistent packet lengths")
        pkt, buf = buf[:L + 2], buf[L + 2:]
        body = pkt[4:4 + blen]
        seq, session, typ, uid = struct.unpack_from("<QHHQ", body, 0)
        out.append((seq, session, typ, uid, body[20:]))
        if len(out) > security.MAX_PER_SECOND:
            raise security.ProtocolError("Too many coalesced messages")
    return out, buf


def packet(seq, typ, uid, payload=b"", result=0, session=0):
    if typ in {0xE608, 0xE60A, 0xE610, 0xE611, 0xE612, 0xE613, 0xE614, 0xE616}:
        nrt_rooms.LOG('lobby reply', f'{typ:04x}', 'uid', uid, 'seq', seq,
                      'result', result, 'payload_bytes', len(payload))
    body = struct.pack("<QHHHQ", seq, session, typ, result, uid) + payload
    return struct.pack("<HH", 2 + len(body) + 2, len(body)) + body + struct.pack("<H", 0)


FRIEND_STATE = 0xE641   # server -> client, one-way: u16 state, u64 Steam id, u32 (handler 0x1400eed80)
FRIEND_POLL_SECONDS = 10
# Lobby/battle-lifecycle messages: nrt_server marks session['room_activity_at'] when one of these is seen, and
# tick() holds off its unsolicited 0xE641 push for a while after, so it never lands on top of a create, join,
# leave or battle-start reply. We don't have a real 2015 server trace to confirm these pushes are even timed
# the way we're guessing (0x1400eed80 was reverse-engineered from the client's parser, not observed live), and
# a lobby match on 2026-10-17 ended with "Failed to connect to the server (9008)" ~12s after one of these
# pushes went out right on the heels of the client's own E614 leave -- not proven to be the cause (the same
# push had already gone out dozens of times that session with no issue), but cheap to guard against.
ROOM_LIFECYCLE = {0xE608, 0xE60A, 0xE610, 0xE612, 0xE613, 0xE614, 0xE616}
ROOM_QUIET_SECONDS = 20
RESULT_REPORT = 0xE60C
RESULT_READY = 0xE60D
RESULT_REPORT_BYTES = 76 + 4 * 70
RESULT_READY_BYTES = 10 + 8 + 44 + 4 * 54


def result_ready(session, payload):
    """Complete only this connection's pending battle; never grant rewards here.

    E60C builder 0x1400ea140: 76-byte header, four 70-byte player records.
    E60D parser 0x1400ecbb0: skip 10, read two u64s (low 32 bits used),
    then skip the remaining header/player records through byte 278.
    Its success event advances controller 0x140101d00 through 0x140103620,
    avoiding the 3,000-update fallback before the normal getItemMail request.
    """
    context = session.get('result_context')
    reason = None
    if len(payload) != RESULT_REPORT_BYTES:
        reason = 'invalid-length'
    elif context is None or context.get('done'):
        reason = 'no-pending-battle'
    elif payload[2:18] != context['match_key']:
        reason = 'different-battle'
    if reason:
        nrt_rooms.LOG('results report ignored', reason, 'payload_bytes', len(payload))
        return None
    context['done'] = True
    session['room_activity_at'] = time.time()
    nrt_rooms.LOG('results report accepted; ready notification queued',
                  'payload_bytes', len(payload), 'response_bytes', RESULT_READY_BYTES)
    # The two parsed result values are zero in the no-award revival policy.
    # No profile fields or battle counters are overwritten or fabricated.
    return payload[:10] + bytes(RESULT_READY_BYTES - 10)


def tick(session):
    """Called every few seconds per connection by nrt_server (packets it returns are pushed to the game).
    Tell the game who is online (1), in a lobby (5), or gone (0). Shared servers
    use authenticated live sessions; Steam mode uses the local Steam directory.
    The native client needs this even when its search room record is valid."""
    uid = session.get("uid")
    if (not uid or time.time() < session.get("friends_next", 0)
            or time.time() < session.get("room_activity_at", 0) + ROOM_QUIET_SECONDS):
        return []
    session["friends_next"] = time.time() + FRIEND_POLL_SECONDS
    if nrt_rooms.steam_mode():
        now = {sid: (5 if in_lobby else 1) for sid, in_lobby in nrt_rooms.steam().friends_in_game().items()}
    else:
        # Do not hold both locks, or advertise provisioned but disconnected users.
        with security.LOCK:
            peers = [(sid, peer.get('room_id')) for sid, peer in security.ACTIVE.items() if sid != uid]
        with nrt_rooms.LOCK:
            now = {sid: (5 if sid in nrt_rooms.ROOMS.get(rid, {}).get('members', ()) else 1)
                   for sid, rid in peers}
    before = session.setdefault("friend_states", {})
    out = []
    for sid in set(before) | set(now):
        state = now.get(sid, 0)
        if before.get(sid, 0) != state:
            out.append(packet(0, FRIEND_STATE, uid, struct.pack("<HQI", state, sid, 0)))
    session["friend_states"] = {sid: st for sid, st in now.items()}
    return out


def handle(session, buf):
    msgs, rest = parse(buf)
    replies = []
    for seq, sess, typ, uid, payload in msgs:
        security.rate_limit(session)
        if typ == TYPE_LOGIN:
            security.login(session, uid, payload)
            replies.append(packet(seq, TYPE_LOGIN, uid, session=sess))
            continue
        security.check_message(session, uid)
        if typ in ROOM_LIFECYCLE or typ == 0xE611:
            nrt_rooms.LOG('lobby request', f'{typ:04x}', 'uid', uid, 'seq', seq,
                          'room', session.get('room_id', 0), 'payload_bytes', len(payload))
        if typ in ROOM_LIFECYCLE:
            session["room_activity_at"] = time.time()
        if typ == 0xE610:
            try:
                rec = nrt_rooms.create(session, uid, payload)
            except ValueError:
                rec = None
            replies.append(packet(seq, typ, uid, rec or b"", result=0 if rec else 1, session=sess))
        elif typ == 0xE60A:
            # Native requests use a 16-byte hex field, null-padded when shorter.
            # Reply handler 0x1400ECC20 skips 10 payload bytes,
            # then parses 16 hex chars into the active room's endpoint (+0x200) and hands it to the platform
            # layer (vfunc 0x118); the session it opens next connects there (0x14011F300). Without the 10-byte
            # skip the game parsed garbage, got an invalid Steam ID and failed with "Connection Error".
            token = payload.rstrip(b'\0')
            if len(payload) == 16 and token and all(c in b'0123456789abcdefABCDEF' for c in token):
                endpoint = int(token, 16)
                if endpoint and nrt_rooms.set_endpoint(session, endpoint):
                    session['platform_lobby_id'] = endpoint
                    replies.append(packet(seq, typ, uid, bytes(10) + f'{endpoint:016x}'.encode(), session=sess))
                else:
                    replies.append(packet(seq, typ, uid, result=1, session=sess))
            else:
                replies.append(packet(seq, typ, uid, result=1, session=sess))
        elif typ == 0xE611:
            replies.append(packet(seq, typ, uid, nrt_rooms.search(uid), session=sess))
        elif typ == 0xE613:
            try:
                rec = nrt_rooms.update_settings(session, uid, payload)
            except ValueError:
                rec = None
            replies.append(packet(seq, typ, uid, rec, session=sess) if rec is not None
                           else packet(seq, typ, uid, result=1, session=sess))
        elif typ == 0xE612:
            # Join: payload u64 room id (builder 0x1400ebc40); the reply's room record becomes the active room
            room_id = struct.unpack_from("<Q", payload)[0] if len(payload) >= 8 else 0
            rec = nrt_rooms.join(session, uid, room_id)
            replies.append(packet(seq, typ, uid, rec, session=sess) if rec is not None
                           else packet(seq, typ, uid, result=1, session=sess))
        elif typ == 0xE608:
            # Lobby battle starting (request builder 0x1400ec290: u8, u16). The reply carries match info + the room
            # record (nrt_rooms.battle_info). An empty reply crashed the game: the parser took a member-name length
            # from stale buffer bytes and overflowed a stack buffer (memcpy of 0xDB91 bytes).
            kind = struct.unpack_from("<H", payload, 1)[0] if len(payload) >= 3 else 0
            session.pop('result_context', None)
            info = nrt_rooms.battle_info(session, uid, kind)
            if info is not None:
                session['result_context'] = {'match_key': info[2:18], 'done': False}
            replies.append(packet(seq, typ, uid, info, session=sess) if info is not None
                           else packet(seq, typ, uid, result=1, session=sess))
        elif typ == 0xE60C:
            ready = result_ready(session, payload)
            if ready is not None:
                replies.append(packet(0, RESULT_READY, uid, ready, session=sess))
        elif typ == 0xE616:
            # Lobby invite (builder 0x1400ebcc0): u64 room id (low 16 bits), u16 mode, u64 Steam id.
            #   mode 0: the host invites that friend to its room (friend menu "Invite to Lobby", 0x14038bb1e);
            #   mode 2: an invited friend accepted (0x140121060; room id from the host game's Steam lobby "room")
            #           and joins next with 0xE612.
            # Reply handler 0x1400e9001 only sets a flag: empty reply = OK, result 1 = room not found.
            room_id = struct.unpack_from("<Q", payload)[0] if len(payload) >= 8 else 0
            found = nrt_rooms.find_invited(uid, room_id)
            replies.append(packet(seq, typ, uid, session=sess) if found else packet(seq, typ, uid, result=1, session=sess))
        elif typ == 0xE614:
            nrt_rooms.leave(session)
            replies.append(packet(seq, typ, uid, session=sess))
        elif typ in NOTIFY:
            pass
        elif typ in HANDLED:
            # generic acknowledgement: same type and sequence number, result 0, no payload
            replies.append(packet(seq, typ, uid, session=sess))
    return replies, rest
