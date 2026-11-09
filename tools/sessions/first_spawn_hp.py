"""Preserve initialized cube HP when the early owner-registration guard skips refresh."""
import math
import struct

HOOK = 0x14062AD10
EXPECTED = bytes.fromhex('488b9dd8f10000')
REFRESH = 0x14062A780
REGISTRY = 0x141B87490


def owner_guard_code(address):
    # Match build.py's existing null-owner guard, including both relative targets.
    raw = bytes.fromhex('0fb6410884c0782a3c067d26480fbec04869c0d02500004c8b15')
    raw += struct.pack('<i', REGISTRY - (address + len(raw) + 4))
    raw += bytes.fromhex('4a837c101800740c488bc448895808e9')
    raw += struct.pack('<i', REFRESH + 7 - (address + len(raw) + 4))
    return raw + b'\xc3'


def seed(d, unit):
    if not isinstance(unit, int) or not 0x10000 <= unit < 0x7FFFFFF00000:
        return False

    def read(address, fmt):
        return struct.unpack(fmt, d.read(address, struct.calcsize(fmt)))

    slot = read(unit + 0xC71, '<b')[0]
    if not 0 <= slot < 4 or not 1 <= read(unit + 0xC74, '<i')[0] <= 21:
        return False
    if read(unit + 0xC1F8, '<I')[0] != 0:
        return False
    registry = read(REGISTRY, '<Q')[0]
    if not 0x10000 <= registry < 0x7FFFFFF00000:
        return False
    if read(registry + slot * 0x25D0 + 0x18, '<Q')[0] != 0:
        return False
    # In this exact early state native refresh would add only the static array.
    # Leave nonempty status lists or dynamic HP to the native calculation.
    start, end = read(unit + 0xFB30, '<QQ')
    if start != end or read(unit + 0x10024, '<f')[0] != 0:
        return False
    if read(unit + 0x119B0, '<f')[0] != 0:
        return False
    raw = d.read(unit + 0x1112C, 4)
    bonus = struct.unpack('<f', raw)[0]
    base = read(unit + 0xC80, '<f')[0]
    total = base + bonus
    if not all(math.isfinite(v) for v in (base, bonus, total)):
        return False
    if bonus == 0 or base <= 0 or total <= 0 or total > 3.4028234663852886e38:
        return False
    # First spawn's original instructions set current/max HP from this value.
    # A later full native refresh clears/rebuilds the array, preventing stacking.
    d.write(unit + 0x119B0, raw)
    d.log(f'FIRST_SPAWN_HP slot={slot} base={base:g} cube_bonus={bonus:g}')
    return True


def setup(d):
    def arm(debugger):
        try:
            if debugger.read(HOOK, len(EXPECTED)) != EXPECTED:
                raise ValueError('initialization hook changed')
            jump = debugger.read(REFRESH, 7)
            if len(jump) != 7 or jump[0] != 0xE9 or jump[5:] != b'\x90\x90':
                raise ValueError('owner guard absent')
            address = REFRESH + 5 + struct.unpack('<i', jump[1:5])[0]
            expected = owner_guard_code(address)
            if debugger.read(address, len(expected)) != expected:
                raise ValueError('owner guard changed')
        except (OSError, ValueError, struct.error):
            debugger.log('FIRST_SPAWN_HP disabled: code guard mismatch')
            return

        def after_static_refresh(hit):
            try:
                seed(debugger, hit.rbx)
            except (OSError, ValueError, TypeError, AttributeError, struct.error):
                debugger.log('FIRST_SPAWN_HP skipped: incomplete actor state')

        debugger.bp(HOOK, after_static_refresh)
        debugger.log('FIRST_SPAWN_HP guarded initialization armed')

    d.on_start(arm)
