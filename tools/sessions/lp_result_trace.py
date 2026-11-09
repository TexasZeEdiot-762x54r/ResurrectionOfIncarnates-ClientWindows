"""Opt-in numeric LP input comparison. No saves, rewards or result-state writes."""
import json
from pathlib import Path
import struct

FLAG = Path(__file__).with_name('lp-result-diagnostics.enabled')
HOOKS = {
    'bot_request': (0x140079680, bytes.fromhex('48895c24104c894c24204c89')),
    'custom_report': (0x1400EA140, bytes.fromhex('48895c240848897424104889')),
    'grade_computed': (0x1404C0007, bytes.fromhex('3bda7507413bdf74343bda7d')),
}
EVENT_LIMIT = 4
SCORE_OFFSETS = (0x04, 0x0C, 0x10, 0x24, 0x30, 0x50, 0x78, 0x8C)
BOT_COUNTERS = (0x18, 0x20, 0x28, 0x58, 0x68, 0x70, 0x80, 0x88, 0x90, 0x98)


def read_exact(reader, address, size):
    if type(address) is not int or address < 0x10000 or not 0 < size <= 0xA0:
        raise ValueError('Invalid numeric snapshot range')
    data = reader.read(address, size)
    if len(data) != size:
        raise ValueError('Unavailable numeric snapshot')
    return data


def pointer(reader, address):
    return struct.unpack('<Q', read_exact(reader, address, 8))[0]


def snapshot(reader):
    state = pointer(reader, 0x141B87440)
    scores = pointer(reader, 0x141B87490)
    result = dict(outcome_internal=struct.unpack('<i', read_exact(reader, state + 0x2C, 4))[0])
    slots = []
    for slot in range(4):
        raw = read_exact(reader, scores + 0xF120 + slot * 0x90, 0x90)
        slots.append({f'{offset:02x}': struct.unpack_from('<i', raw, offset)[0]
                      for offset in SCORE_OFFSETS})
    result['score_slots_i32'] = slots
    return result


def setup(d, *, enabled=None):
    if not (FLAG.is_file() if enabled is None else enabled):
        return
    remaining = {name: EVENT_LIMIT for name in HOOKS}

    def event(name, hit):
        if remaining[name] <= 0:
            return 'remove'
        remaining[name] -= 1
        try:
            value = snapshot(d)
            if name == 'bot_request':
                # 0x140079680 uses r9 for these documented uint64 report counters.
                raw = read_exact(d, hit.r9, 0xA0)
                value['bot_counters_u64'] = {f'{off:02x}': struct.unpack_from('<Q', raw, off)[0]
                                              for off in BOT_COUNTERS}
            if name == 'grade_computed':
                points, grade = struct.unpack('<ii', read_exact(d, hit.rsi + 0x294, 8))
                value['grade_points'], value['grade_index'] = points, grade
            d.log('LP_INPUT_TRACE ' + json.dumps(dict(event=name,
                sample=EVENT_LIMIT - remaining[name], **value), separators=(',', ':')))
        except (ValueError, OSError, struct.error):
            d.log('LP_INPUT_TRACE ' + name + ' numeric snapshot unavailable')
        if remaining[name] == 0:
            return 'remove'

    def arm(debugger):
        for address, expected in HOOKS.values():
            if debugger.read(address, len(expected)) != expected:
                debugger.log('LP_INPUT_TRACE disabled: code guard mismatch')
                return
        for name, (address, _) in HOOKS.items():
            debugger.bp(address, lambda hit, name=name: event(name, hit))
        debugger.log('LP_INPUT_TRACE opt-in numeric comparison armed; no reward/state writes')

    d.on_start(arm)
