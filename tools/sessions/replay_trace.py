"""Opt-in numeric replay diagnostics; no payload reads or game-state writes."""
from pathlib import Path
import struct

import native_lp_input as native

FLAG = Path(__file__).resolve().parent.parent / 'replay-trace.enabled'
HOOKS = {
    'initialized': (0x1401DBAA1, bytes.fromhex('488b0d9048a201')),
    'autosave': (0x1401DE54A, bytes.fromhex('488b05078f9a01')),
    'encoded': (0x1401DE5BD, bytes.fromhex('85c07413')),
    'connection': (0x1401106F4, bytes.fromhex('0f8568030000')),
    'owner': (0x1401107FE, bytes.fromhex('0f855e020000')),
    'request': (0x14005E450, bytes.fromhex('48895c242048894c2408')),
}


def readiness(reader):
    roster = native.pointer(reader, 0x141B87458)
    manager = native.pointer(reader, 0x141B874D0)
    actors = native.pointer(reader, 0x141B87490)
    playback = native.number(reader, manager + 8, '<B')
    if playback not in (0, 1):
        raise ValueError('Unknown replay flag')
    mode = native.number(reader, roster + 0x5F650)
    # This is only the 32-byte fixed header, never names, inputs or payload.
    header = native.read(reader, manager + 0x10, 32)
    states = []
    for slot in range(6):
        actor = native.number(reader, actors + slot * 0x25D0 + 0x18, '<Q')
        states.append(native.number(reader, actor + 0xC2A4) if actor else None)
    return dict(mode=mode, playback=playback,
                version_ok=struct.unpack_from('<I', header)[0] == 0x150305F0,
                header_counter=struct.unpack_from('<i', header, 0x18)[0],
                actor_states=states, mode_skips_autosave=5 <= mode <= 7)


class Trace:
    def __init__(self, debugger):
        self.d = debugger
        self.remaining = {name: 4 for name in HOOKS}

    def event(self, name, hit):
        if self.remaining[name] == 0:
            return
        self.remaining[name] -= 1
        try:
            if name in ('initialized', 'autosave'):
                fields = readiness(self.d)
            elif name == 'encoded':
                fields = dict(encoded_bytes=hit.rax & 0xFFFFFFFF)
            elif name == 'connection':
                # Original branch tests this field against zero (ESI).
                fields = dict(state=native.number(self.d, hit.rax + 0x158))
            elif name == 'owner':
                # Record equality only, never either player identity.
                owner = native.number(self.d, hit.rcx + 0x10, '<Q')
                fields = dict(is_owner=owner == hit.rax)
            else:
                fields = dict(native_save_request=True)
            self.d.log('REPLAY_TRACE ' + name + ' ' + repr(fields))
        except (OSError, ValueError, TypeError, AttributeError, struct.error):
            self.d.log('REPLAY_TRACE ' + name + ' unavailable')


def setup(d, *, enabled=None):
    if not (FLAG.is_file() if enabled is None else enabled):
        return False
    trace = Trace(d)

    def arm(debugger):
        # Validate the whole set before registering any breakpoint.
        if any(debugger.read(address, len(expected)) != expected
               for address, expected in HOOKS.values()):
            debugger.log('REPLAY_TRACE disabled: unexpected native code')
            return
        if any(address in getattr(debugger, 'bps', {}) for address, _ in HOOKS.values()):
            debugger.log('REPLAY_TRACE disabled: breakpoint already owned')
            return
        for name, (address, _) in HOOKS.items():
            debugger.bp(address, lambda hit, event=name: trace.event(event, hit))
        debugger.log('REPLAY_TRACE bounded read-only readiness diagnostics armed')

    d.on_start(arm)
    return True
