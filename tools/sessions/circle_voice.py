"""Redirect eligible fighter voice cues only, before the native audio lock.

Intro/outro queued dialogue and normal combat dialogue both reach this component
dispatcher. Animation selection, priorities, SFX, and native playback lifetime
remain untouched. Eligibility is fetched during profile loading, never here.
"""
import json
from pathlib import Path
import struct
import time

import native_lp_input as native
import roi_circle_voice as policy

READY = Path(__file__).resolve().parent.parent / 'circle-voice-asset.json'
ASSET_SHA = '7f1c14c2f382f3d091798094e05c63084ca5d81b7522bcda698905ef1b3a77c1'
SECONDS = 1.9
HOOK = 0x140B6E7A0
GUARDS = {
    HOOK: bytes.fromhex('488bc44155415641574883ec6048c744'),
    0x140B6E8CD: bytes.fromhex('498bd748c1e20648035708'),
    0x140675A07: bytes.fromhex('488b8fa80a0000ba02000000'),
    0x14067C03D: bytes.fromhex('488b89a80a0000418d5501'),
}


class CachedPolicy:
    def __init__(self, path=None):
        self.path = policy.CACHE if path is None else Path(path)
        self.checked = float('-inf')
        self.document = {}

    def allows(self, uid):
        now = time.monotonic()
        if now - self.checked >= 1:
            self.checked = now
            self.document = {}
            try:
                with self.path.open('rb') as stream:
                    raw = stream.read(16385)
                if len(raw) <= 16384:
                    self.document = json.loads(raw)
            except (OSError, ValueError):
                pass
        return policy.allowed(self.document, uid)


def eligible(reader, component, cue, allowed):
    if not 0 <= cue < 491 or native.is_replay(reader):
        return None
    registry = native.pointer(reader, 0x141B87490)
    manager = native.pointer(reader, 0x141B874D0)
    for slot in range(4):
        actor = native.number(reader, registry + slot * 0x25D0 + 0x18, '<Q')
        if actor < 0x10000:
            continue
        if native.number(reader, actor + 0xAA8, '<Q') != component:
            continue
        if native.number(reader, actor + 0xC74) != 1 or native.number(reader, actor + 0xC71, '<b') != slot:
            return None
        # Replay header starts at manager+0x10; participant 0 starts a further
        # 0x20 bytes in. The retained grid selection has its own manager offset.
        grid_row = manager + slot * 0x22D8
        row = grid_row + 0x30
        if native.number(reader, row + 1, '<b') < 0 or native.number(reader, row + 10, '<B') != 1:
            return None
        if native.number(reader, grid_row + 0x20CC) != 4:
            return None
        uid = str(native.number(reader, row + 0x18, '<Q'))
        return dict(slot=slot, grid=5, original=cue) if allowed(uid) else None
    return None


def asset_ready(path=None):
    try:
        with (READY if path is None else Path(path)).open('rb') as stream:
            raw = stream.read(4097)
        value = json.loads(raw) if len(raw) <= 4096 else {}
        return value.get('sha256') == ASSET_SHA and value.get('seconds') == SECONDS and value.get('installed') is True
    except (OSError, ValueError, AttributeError):
        return False


def setup(d):
    if not asset_ready():
        return

    def arm(debugger):
        try:
            if any(debugger.read(address, len(raw)) != raw for address, raw in GUARDS.items()):
                raise ValueError('Voice code changed')
            if HOOK in getattr(debugger, 'bps', {}):
                raise ValueError('Voice breakpoint already owned')
        except (OSError, ValueError):
            debugger.log('CIRCLE_VOICE disabled: code guard or breakpoint mismatch')
            return
        cache = CachedPolicy()
        logged = 0

        def voice(hit):
            nonlocal logged
            if hit.rdx & 0xFFFFFFFF != 2:
                return
            try:
                value = eligible(debugger, hit.rcx, hit.r8 & 0xFFFFFFFF, cache.allows)
                if value is not None:
                    hit.setreg('r8', 0)
                    if logged < 12:
                        debugger.log(f'CIRCLE_VOICE {SECONDS}-second cue {value}')
                        logged += 1
            except (OSError, ValueError, TypeError, AttributeError, struct.error):
                pass

        debugger.bp(HOOK, voice)
        debugger.log('CIRCLE_VOICE guarded Mephistopheles/Grid 5 voice routing armed')

    d.on_start(arm)
