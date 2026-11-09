"""Do not interpret empty character-level rewards as player-system unlocks."""
import struct
from collections import Counter

HOOK = 0x1404CD920
TEXT_HOOK = 0x1404CDCDE
EXPECTED = bytes.fromhex('4c8d2dd926b3ff898798020000')
CALLERS = {0x1404C3D40: 1, 0x1404CACF0: 2}
TEXT_CALLERS = dict(CALLERS)
TEXT_CALLERS.update({0x1404CFFFE: 0, 0x1404D06A6: 0})
GUARDS = {
    HOOK: EXPECTED,
    0x1404CD91B: bytes.fromhex('e850140000'),
    0x1404CED70: bytes.fromhex('4183c8ff8339004c8bc97463'),
    TEXT_HOOK: bytes.fromhex('488b8fa00400004885c97410488b0148'),
    0x1404D06A1: bytes.fromhex('e82ad2ffff'),
}
# The zero-descriptor fallback in 0x1404CED70 chooses these player-only notices.
UNLOCK_TYPES = frozenset((1, 2, 7, 8, 9, 10, 11, 12))


class NoticeState:
    def __init__(self):
        self.clear()

    def clear(self):
        self.bonuses = {}
        self.shown = set()

    def prepare(self, receipt):
        self.clear()
        events, xp = receipt.get('drop_events', []), receipt.get('xp', {})
        if not isinstance(events, list) or len(events) > 64 or not isinstance(xp, dict):
            raise ValueError('Invalid committed bonus notices')
        inventory = Counter()
        for item in receipt.get('result_items', []):
            inventory[(item.get('sv_itemId'), item.get('sv_count'))] += 1
        bonuses = {}
        for event in events:
            if not isinstance(event, dict):
                raise ValueError('Invalid committed bonus event')
            source = event.get('source')
            if source not in ('player_level', 'character_level'):
                continue
            if source in bonuses:
                raise ValueError('Duplicate level track bonus')
            key = 'player' if source == 'player_level' else 'character_xp'
            track = xp.get(key, {})
            before, after = track.get('level_before'), track.get('level_after')
            maximum = 50 if key == 'player' else 30
            if (type(before) is not int or type(after) is not int or not 0 <= before < after <= maximum):
                raise ValueError('Bonus without a committed level increase')
            if event.get('kind') == 'cube':
                item_id = event.get('cube_id')
                if (type(item_id) is not str or len(item_id) != 16 or not item_id.startswith('12')
                        or not item_id.isascii() or not item_id.isdigit()):
                    raise ValueError('Invalid committed bonus cube')
                identity, label = (item_id, '1'), '+1 Skill Cube'
            elif event.get('kind') == 'lp':
                amount = event.get('amount')
                if type(amount) is not int or not 1 <= amount <= 2147483647:
                    raise ValueError('Invalid committed LP bonus')
                identity, label = ('1000000000000000', str(amount)), f'+{amount:,} LP'
            else:
                raise ValueError('Unknown committed level bonus')
            if inventory[identity] < 1:
                raise ValueError('Bonus notice not present in result receipt')
            inventory[identity] -= 1
            bonuses[source] = (after, label)
        self.bonuses = bonuses


def state(d):
    if not hasattr(d, 'level_reward_notice_state'):
        d.level_reward_notice_state = NoticeState()
    return d.level_reward_notice_state


def empty_character_reward(d, hit):
    def u32(address):
        return struct.unpack('<I', d.read(address, 4))[0]

    for pointer in (hit.rsp, hit.rdi, hit.rbx):
        if not isinstance(pointer, int) or not 0x10000 <= pointer < 0x7FFFFFF00000:
            return False
    caller = struct.unpack('<Q', d.read(hit.rsp + 0x6C8, 8))[0]
    context = CALLERS.get(caller)
    if context is None or u32(hit.rdi + 0x290) != context:
        return False
    # A real descriptor has a category and ID. Never suppress an actual item.
    descriptor = d.read(hit.rbx, 0x30)
    # +0x20..+0x27 is padding, not part of the decoded descriptor.
    if (len(descriptor) != 0x30 or descriptor[:0x20] != bytes(0x20)
            or descriptor[0x28:0x30] != bytes(8)):
        return False
    return True


def correct(d, hit):
    if (hit.rax & 0xFFFFFFFF) not in UNLOCK_TYPES or not empty_character_reward(d, hit):
        return False
    hit.setreg('rax', 0xFFFFFFFF)
    d.log('LEVEL_REWARD_NOTICE omitted empty character reward unlock text')
    return True


def fill_empty(d, hit):
    if any(type(p) is not int or not 0x10000 <= p < 0x7FFFFFF00000 for p in (hit.rsp, hit.rdi)):
        return False
    caller = struct.unpack('<Q', d.read(hit.rsp + 0x6C8, 8))[0]
    context = TEXT_CALLERS.get(caller)
    if context is None or d.read(hit.rdi + 0x290, 4) != struct.pack('<I', context):
        return False
    # Fifth native argument remains on the caller's stack throughout this function.
    level = struct.unpack('<I', d.read(hit.rsp + 0x6F0, 4))[0]
    if not 1 <= level <= (50 if context == 0 else 30):
        return False
    address = hit.rdi + 0x2A0  # Native 0x200-byte text buffer, before its UI setter.
    before = d.read(address, 0x200)
    if len(before) != 0x200 or b'\0' not in before:
        return False
    body = before.split(b'\0', 1)[0]
    notices = state(d)
    source = 'player_level' if context == 0 else 'character_level'
    bonus = notices.bonuses.get(source)
    has_bonus = source not in notices.shown and bonus is not None and bonus[0] == level
    if has_bonus:
        prefix = body + b' Bonus: ' if body else ('Player' if context == 0 else 'Character').encode() + f' Lv{level}: '.encode()
        text = prefix + bonus[1].encode('ascii') + b'.\0'
    else:
        if (context == 0 or body or d.read(hit.rdi + 0x298, 4) != b'\xff' * 4
                or not empty_character_reward(d, hit)):
            return False
        text = ('Character reached level ' + str(level) + '.').encode('ascii') + b'\0'
    if len(text) > 0x200:
        return False
    if not d.write(address, text) or d.read(address, len(text)) != text:
        d.write(address, before[:len(text)])
        raise OSError('Level notice text unavailable')
    if has_bonus:
        notices.shown.add(source)
    d.log('LEVEL_REWARD_NOTICE displayed ' + source + '=' + str(level) + ' bonus=' + str(has_bonus))
    return True


def setup(d):
    state(d)
    def arm(debugger):
        from sessions import character_tooltip
        import roi_awards
        try:
            drops = roi_awards.policy()['cube_drops']
            character_tooltip.arm(debugger, drops.get('enabled') and drops.get('level_up_enabled'))
        except (OSError, ValueError):
            debugger.log('CHARACTER_TOOLTIP disabled: reward policy unavailable')
        try:
            if any(debugger.read(a, len(raw)) != raw for a, raw in GUARDS.items()):
                raise ValueError('Unexpected notification code')
        except (OSError, ValueError):
            debugger.log('LEVEL_REWARD_NOTICE disabled: code guard mismatch')
            return

        def classified(hit):
            try:
                correct(debugger, hit)
            except (OSError, ValueError, TypeError, AttributeError, struct.error):
                debugger.log('LEVEL_REWARD_NOTICE skipped: unavailable notification state')

        debugger.bp(HOOK, classified)

        def text_ready(hit):
            try:
                fill_empty(debugger, hit)
            except (OSError, ValueError, TypeError, AttributeError, struct.error):
                debugger.log('LEVEL_REWARD_NOTICE text unavailable; rewards unchanged')

        debugger.bp(TEXT_HOOK, text_ready)
        debugger.log('LEVEL_REWARD_NOTICE guarded character notification correction armed')

    d.on_start(arm)
