"""Keep character next-level tooltips out of the empty player-unlock fallback."""
GUARDS = {
    0x1403C5151: bytes.fromhex('488d4c2430418bd6e8129c1000488d542430488d8db0010000448bc0e89e080000'),
    0x1403C5172: bytes.fromhex('488b96c8020000'),
    0x1403C85C1: bytes.fromhex('488d4c2430418bd6e8a2671000488d542430488d8db0010000448bc0e89e080000'),
    0x1403C85E2: bytes.fromhex('488b96c8020000'),
}


def correct(d, hit, bonus_enabled):
    if any(type(p) is not int or not 0x10000 <= p < 0x7FFFFFF00000 for p in (hit.rsp, hit.rbp)):
        return False
    if not 0 <= hit.r14 < 30:
        return False
    descriptor = d.read(hit.rsp + 0x30, 0x30)
    if len(descriptor) != 0x30 or descriptor[:0x20] != bytes(0x20) or descriptor[0x28:] != bytes(8):
        return False
    address = hit.rbp + 0x1B0
    before = d.read(address, 0x200)
    if len(before) != 0x200 or b'\0' not in before:
        return False
    text = b'Skill Cube or LP (50/50).\0' if bonus_enabled else b'Character level increases.\0'
    if not d.write(address, text) or d.read(address, len(text)) != text:
        d.write(address, before[:len(text)])
        raise OSError('Character tooltip unavailable')
    return True


def arm(d, bonus_enabled):
    if any(d.read(a, len(b)) != b for a, b in GUARDS.items()):
        d.log('CHARACTER_TOOLTIP disabled: code guard mismatch')
        return
    def formatted(hit):
        try:
            correct(d, hit, bonus_enabled)
        except (OSError, ValueError, TypeError, AttributeError):
            d.log('CHARACTER_TOOLTIP skipped: unavailable text buffer')
    for address in (0x1403C5172, 0x1403C85E2):
        d.bp(address, formatted)
    d.log('CHARACTER_TOOLTIP guarded next-level description armed')
