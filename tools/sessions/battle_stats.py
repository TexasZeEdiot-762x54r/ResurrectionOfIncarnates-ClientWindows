"""Opt-in, bounded numeric first-spawn diagnostics on normal client launch."""
import json
from pathlib import Path
import struct

FLAG = Path(__file__).resolve().parent.parent / 'battle-stats-trace.enabled'
HOOKS = {
    'base_copy': (0x140559654, bytes.fromhex('0fb64809388f710c0000'), 'rdi', 0),
    'skill_refresh': (0x14062AD0B, bytes.fromhex('e870faffff'), 'rbx', 0),
    'spawn_before': (0x140634D4B, bytes.fromhex('f30f10aec00d0100'), 'rsi', -0xBF0),
    'spawn_after': (0x140634D6B, bytes.fromhex('c686f2b5000001'), 'rsi', -0xBF0),
}


def snapshot(d, unit):
    def value(offset, fmt):
        size = struct.calcsize(fmt)
        if not 0x10000 <= unit < 0x7FFFFFF00000:
            raise ValueError('Invalid actor')
        return struct.unpack(fmt, d.read(unit + offset, size))[0]

    slot, character = value(0xC71, '<b'), value(0xC74, '<i')
    if not 0 <= slot < 4 or not 1 <= character <= 21:
        raise ValueError('Invalid player actor')
    return dict(slot=slot, character=character,
                base=value(0xC80, '<f'), maximum=value(0xC84, '<f'),
                current=value(0xC88, '<f'), baseline=value(0x1112C, '<f'),
                final=value(0x119B0, '<f'), spawns=value(0xC1F8, '<I'))


def setup(d, *, enabled=None):
    if not (FLAG.is_file() if enabled is None else enabled):
        return
    budget = [40]

    def record(hit, name, register, offset):
        if not budget[0]:
            return
        budget[0] -= 1
        try:
            row = snapshot(d, getattr(hit, register) + offset)
            d.log('BATTLE_STATS ' + name + ' ' + json.dumps(row, allow_nan=False))
        except (OSError, ValueError, TypeError, AttributeError, struct.error):
            d.log('BATTLE_STATS ' + name + ' numeric sample unavailable')

    def arm(debugger):
        for address, expected, _, _ in HOOKS.values():
            if debugger.read(address, len(expected)) != expected:
                debugger.log('BATTLE_STATS disabled: code guard mismatch')
                return
        for name, (address, _, register, offset) in HOOKS.items():
            debugger.bp(address, lambda h, n=name, r=register, o=offset: record(h, n, r, o))
        debugger.log('BATTLE_STATS numeric startup trace armed; no stat changes')

    d.on_start(arm)
