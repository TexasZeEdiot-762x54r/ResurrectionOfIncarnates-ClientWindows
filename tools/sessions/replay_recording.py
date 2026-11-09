"""End-of-match local recording of the client's existing native input stream.

0x14068db00 records human/network inputs and CPU +0xd6f0 inputs alike.
The original autosave excludes CPUs and Vs. Bot; its lobby-owner wrapper is
not valid for solo bot play. This bridge uses the existing local saveReplay
API instead. It never calls native functions or hooks individual frames.
"""
import base64
import hashlib
from http.client import IncompleteRead
import json
from pathlib import Path
import struct
from threading import BoundedSemaphore, Thread
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
import zlib

import native_lp_input as native
import roi_replays as archive
from sessions.lp_payouts import NoRedirect, strict_object

ROOT = Path(__file__).resolve().parent.parent
FLAG = ROOT / 'replay-recording.enabled'
URL = 'http://127.0.0.1:52710/Dragon/game'
HOOK = 0x1401DE551
FINISH = 0x1401DE5D4
GUARDS = {
    HOOK: bytes.fromhex('8b8850f6050083f905'),
    FINISH: bytes.fromhex('4c8b052d8f9a01'),
    # CPU selection and shared playback/recording branch.
    0x14068DBC7: bytes.fromhex('498b82f0d60000'),
    0x14068DBDF: bytes.fromhex('4c8b15ea984f0141387208746e'),
}
STREAM_OFFSET = 0x8B80
STREAM_CAPACITY = 0x3840
PARTICIPANT_SIZE = 0x22D8


def exact(reader, address, size):
    if (type(address) is not int or not 0 < size <= archive.RAW_SIZE
            or not 0x10000 <= address <= 0x7FFFFFFFFFFF - size):
        raise ValueError('Invalid replay read range')
    value = reader.read(address, size)
    if len(value) != size:
        raise ValueError('Incomplete replay read')
    return value


def ctext(raw):
    if b'\0' not in raw:
        raise ValueError('Unterminated replay name')
    return archive.text(raw.split(b'\0', 1)[0].decode('utf-8'))


def metadata(raw, room_type, flag):
    if len(raw) != archive.RAW_SIZE or struct.unpack_from('<I', raw)[0] != 0x150305F0:
        raise ValueError('Unsupported replay structure')
    local_slot = raw[4]
    if local_slot > 3:
        raise ValueError('Unsupported replay local slot')
    entries = []
    for slot in range(4):
        start = 0x20 + slot * PARTICIPANT_SIZE
        participant = raw[start:start + PARTICIPANT_SIZE]
        human = struct.unpack_from('<b', participant, 1)[0] >= 0
        side, character = participant[9:11]
        if side not in (0, 1):
            raise ValueError('Unsupported replay team')
        # Profile subset copied by 0x1401f6640; use battle-start values, not
        # the currently selected menu character or post-result profile.
        profile = participant[0x18:]
        entries.append(dict(
            sv_uid=str(struct.unpack_from('<Q', profile)[0]) if human else '0',
            sv_playerLv=struct.unpack_from('<i', profile, 0x1AC)[0] if human else 0,
            sv_rankId=struct.unpack_from('<i', profile, 0x1B4)[0] if human else 0,
            sv_charaId=character, sv_side=side + 1,
            sv_userName=ctext(profile[0xAC:0x1AC]) if human else '',
            # These optional social labels are not in the fixed replay header.
            # Leave them empty rather than guessing or reading unrelated memory.
            sv_circleName='', sv_tagName=''))
    owner = entries[local_slot]['sv_uid']
    value = dict(sv_stageId=struct.unpack_from('<i', raw, 8)[0],
                 sv_roomType=room_type, sv_flag=flag, sv_replayVer=archive.VERSION,
                 sv_entries=entries)
    return archive.normalize_metadata(value, owner)


def validate_streams(raw, counters, frame):
    if len(counters) != 4 or not 0 < frame < 0x7FFFFFFF:
        raise ValueError('Invalid replay frame state')
    for slot, count in enumerate(counters):
        # The writer stops at capacity - 1. Do not offer a silently truncated replay.
        if not 0 <= count < STREAM_CAPACITY - 1:
            raise ValueError('Replay input stream reached capacity')
        start = STREAM_OFFSET + slot * STREAM_CAPACITY * 12
        previous = -1
        for index in range(count + 1):
            stamp, buttons = struct.unpack_from('<IQ', raw, start + index * 12)
            if not previous <= stamp <= frame + 1:
                raise ValueError('Invalid replay input ordering')
            if index == count and (stamp == 0 or buttons != 0):
                raise ValueError('Incomplete replay input tail')
            previous = stamp
        if struct.unpack_from('<I', raw, start + (count + 1) * 12)[0] != 0xFFFFFFFF:
            raise ValueError('Replay input count mismatch')


def capture(reader):
    if native.is_replay(reader):
        return None
    roster = native.pointer(reader, 0x141B87458)
    mode = native.number(reader, roster + 0x5F650)
    # Solo/Team Custom lobby transitions set 3/4; Vs. Bot sets 5.
    # Tutorial/training, replay mode 7 and unknown modes are untouched.
    key = None
    if mode in (1, 2):
        try:
            controller = native.pointer(reader, native.pointer(reader, 0x141B87450) + 0x150)
            key = native.read(reader, controller + 0x1ed8, 16).hex()
            if key == '0'*32:return None
        except (OSError,ValueError,struct.error):
            return None
    elif mode not in (3, 4, 5):
        return None
    manager = native.pointer(reader, 0x141B874D0)
    state = native.pointer(reader, 0x141B87440)
    game = native.pointer(reader, 0x141B87450)
    before = native.read(reader, manager + 0x1A2F30, 16)
    frame = native.number(reader, state + 0x18)
    raw = exact(reader, manager + 0x10, archive.RAW_SIZE)
    if (before != native.read(reader, manager + 0x1A2F30, 16)
            or native.is_replay(reader)
            or native.pointer(reader, 0x141B874D0) != manager
            or frame != native.number(reader, state + 0x18)):
        raise ValueError('Replay changed during capture')
    if (struct.unpack_from('<i', raw, 0x14)[0] != mode
            or struct.unpack_from('<i', raw, 8)[0] != native.number(reader, state + 0x48)
            or raw[4] != native.number(reader, roster + 9, '<B')):
        raise ValueError('Replay does not match completed battle')
    counters = struct.unpack('<4i', before)
    validate_streams(raw, counters, frame)
    value = metadata(raw, native.number(reader, game + 0x238),
                     native.number(reader, state + 0x60, '<Q'))
    if key:
        if len({p['sv_uid'] for p in value['sv_entries'] if p['sv_uid']!='0'})!=4:
            return None
        value['revivalRankedMatchKey']=key
    return raw, value, counters


def encode(raw):
    packed = zlib.compress(raw, 7)
    envelope = struct.pack('>4I', 0x42494C5A, len(raw), len(packed), 7) + packed + b'\0'
    digits = base64.b64encode(envelope).decode('ascii').rstrip('=')
    wrapped = '\r\n'.join(digits[i:i+76] for i in range(0, len(digits), 76))
    return archive.validate_payload(wrapped + '=' * (4 - len(wrapped) % 4))


def save_local(raw, value):
    # Same local endpoint and account authority as the native save request.
    # No remote host, redirect, proxy, profile write or reward operation.
    data = dict(value, sv_replayData=encode(raw))
    body = json.dumps(dict(call='saveReplay', data=data), separators=(',', ':')).encode()
    request = Request(URL, body, {'Content-Type': 'application/json'}, method='POST')
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for attempt, timeout in enumerate((1.0, 3.0)):
        try:
            with opener.open(request, timeout=timeout) as response:
                reply = response.read(4097)
            break
        except HTTPError:
            raise
        except (OSError, IncompleteRead):
            if attempt:
                raise
    if len(reply) > 4096:
        raise ValueError('Oversized replay save reply')
    parsed = json.loads(reply, object_pairs_hook=strict_object)
    if not isinstance(parsed, dict) or parsed.get('result') != 'OK' or parsed.get('data') != {}:
        raise ValueError('Local replay archive rejected recording')


class Recorder:
    def __init__(self, debugger, save=save_local):
        self.d, self.save = debugger, save
        self.slots = BoundedSemaphore(2)
        self.last = None

    def persist(self, raw, value, digest):
        try:
            self.save(raw, value)
            self.d.log('REPLAY_RECORD accepted by local View Battle archive/upload outbox')
        except (OSError, ValueError, IncompleteRead):
            if self.last == digest:
                self.last = None
            self.d.log('REPLAY_RECORD save failed; no reward or gameplay state changed')
        finally:
            self.slots.release()

    def event(self, hit):
        try:
            result = capture(self.d)
            if result is None:
                return
            raw, value, counters = result
            digest = hashlib.sha256(raw).digest()
            if self.last != digest:
                if not self.slots.acquire(blocking=False):
                    self.d.log('REPLAY_RECORD busy; native save path retained')
                    return
                previous = self.last
                self.last = digest
                try:
                    Thread(target=self.persist, args=(raw, value, digest),
                           name='local-replay-save', daemon=False).start()
                except Exception:
                    self.last = previous
                    self.slots.release()
                    raise
                self.d.log('REPLAY_RECORD captured inputs=' + repr(counters))
            # Replace only this autosave call chain. Continue at its original
            # common cleanup, avoiding an invalid bot lobby owner or double save.
            hit.setreg('rip', FINISH)
        except (OSError, ValueError, struct.error, RuntimeError) as exc:
            detail = 'invalid text encoding' if isinstance(exc, UnicodeError) else str(exc)
            self.d.log('REPLAY_RECORD capture skipped: ' + detail)


def setup(d, *, enabled=None):
    if not (FLAG.is_file() if enabled is None else enabled):
        return False
    recorder = Recorder(d)

    def arm(debugger):
        if any(debugger.read(address, len(expected)) != expected
               for address, expected in GUARDS.items()):
            debugger.log('REPLAY_RECORD disabled: unexpected native code')
            return
        if HOOK in getattr(debugger, 'bps', {}):
            debugger.log('REPLAY_RECORD disabled: breakpoint already owned')
            return
        debugger.bp(HOOK, recorder.event)
        debugger.log('REPLAY_RECORD end-of-match local recording armed')

    d.on_start(arm)
    return True
