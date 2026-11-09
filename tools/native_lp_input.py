"""Bounded readers for final Total and the original client Team Rank algorithm.

Offsets/thresholds are native contracts; LP rates are a separate revival policy.
This module neither imports a debugger nor writes game memory.
"""
import struct
import math

MAX_INT = 2147483647


def read(reader, address, size):
    if type(address) is not int or not 0x10000 <= address <= 0x7FFFFFFFFFFF - size or not 0 < size <= 160:
        raise ValueError('Invalid native LP read')
    value = reader.read(address, size)
    if len(value) != size:
        raise ValueError('Unavailable native LP input')
    return value


def number(reader, address, fmt='<i'):
    return struct.unpack(fmt, read(reader, address, struct.calcsize(fmt)))[0]


def pointer(reader, address):
    value = number(reader, address, '<Q')
    if not 0x10000 <= value <= 0x7FFFFFFFFFFF:
        raise ValueError('Invalid native LP pointer')
    return value


def is_replay(reader):
    # Set by playback 0x1403DD668; reset by replay-view constructor 0x1403E9E70.
    manager = pointer(reader, 0x141B874D0)
    value = number(reader, manager + 8, '<B')
    if value not in (0, 1):
        raise ValueError('Unknown native playback state')
    return value == 1


def teammate(reader):
    if is_replay(reader):
        raise ValueError('Replay is not a completed player appearance')
    manager=pointer(reader,0x141B874D0)
    if number(reader,manager+0x10,'<I')!=0x150305F0:
        raise ValueError('Unknown native battle header')
    own=number(reader,manager+0x14,'<B')
    if own>3:raise ValueError('Invalid native player slot')
    participants=[]
    for slot in range(4):
        raw=read(reader,manager+0x30+slot*0x22D8,11)
        side,cid=raw[9:11]
        if side not in (0,1) or not 1<=cid<=21:raise ValueError('Invalid battle teammate roster')
        participants.append((side,cid))
    partners=[cid for slot,(side,cid) in enumerate(participants) if slot!=own and side==participants[own][0]]
    if len(partners)!=1:raise ValueError('Ambiguous battle teammate')
    return dict(character=participants[own][1],partner=partners[0])


def grade(nice, chain, tag):
    if any(type(value) is not int or not 0 <= value <= MAX_INT for value in (nice, chain, tag)):
        raise ValueError('Invalid Team Rank counters')
    indices = [sum(value < cutoff for cutoff in cutoffs)
               for value, cutoffs in ((nice, (1, 2, 3)), (chain, (5, 15, 25)), (tag, (1, 2, 3)))]
    points = 9 - sum(indices)
    return 'SABC'[sum(points < cutoff for cutoff in (2, 5, 8))]


def snapshot(reader):
    state = pointer(reader, 0x141B87440)
    roster = pointer(reader, 0x141B87458)
    scores = pointer(reader, 0x141B87490)
    slots = read(reader, roster + 9, 3)
    own = slots[2] if slots[2] != 255 else slots[0]
    if own > 3:
        raise ValueError('Unsupported local score slot')
    team = number(reader, state + 0x44)
    if number(reader, roster + 0x5F670) != 0:
        selected = slots[1] if slots[1] != 255 else slots[0]
        if selected >= 6:
            raise ValueError('Unsupported native team selector')
        if selected in (0, 2):
            team = 0
        elif selected in (1, 3):
            team = 1
    if team not in (0, 1):
        raise ValueError('Unsupported native team')
    members = read(reader, scores + 0xE2F0 + team * 11, 5)
    count = struct.unpack('<b', members[4:])[0]
    if not 0 <= count <= 4:
        raise ValueError('Invalid native team member count')
    partner = 0  # Exact native 0x1404c1c10 fallback, including own slot zero.
    for slot in members[:count]:
        if slot > 3:
            raise ValueError('Unsupported partner score slot')
        if number(reader, scores + slot * 0x25D0 + 0x18, '<Q') and slot != own:
            partner = slot
            break
    blocks = [read(reader, scores + 0xF120 + slot * 0x90, 0x90) for slot in (own, partner)]
    score = struct.unpack_from('<i', blocks[0], 0x24)[0]
    if not 0 <= score <= MAX_INT:
        raise ValueError('Unsupported final Total score')
    counters = []
    for offset in (0x78, 0x8C, 0x30):
        values = [struct.unpack_from('<i', block, offset)[0] for block in blocks]
        if any(value < 0 for value in values):
            raise ValueError('Negative native Team Rank counter')
        counters.append(sum(values))
    internal = number(reader, state + 0x2C)
    if internal not in (1, 2, 3, 4, 5, 6):
        raise ValueError('Unsupported native match outcome')
    return dict(summary=dict(total_score=score, total_rank=grade(*counters)),
                outcome={1: 1, 2: -1, 3: 0, 4: 1, 5: -1, 6: 0}[internal])


def bot_input(reader, hit):
    raw = read(reader, hit.r8, 0x88)
    outcome = struct.unpack_from('<i', raw, 0x0C)[0]
    if outcome not in (-1, 0, 1):
        raise ValueError('Invalid bot outcome')
    context = read(reader, hit.r9 + 0x0C, 16)
    battle = context.split(b'\0', 1)[0]
    if b'\0' not in context or not battle.isdigit() or not 1 <= len(battle) <= 10:
        raise ValueError('Invalid bot battle context')
    value = snapshot(reader)
    if value['outcome'] != outcome:
        raise ValueError('Bot outcome and native result disagree')
    report = dict(sv_botBattleId=hit.rdx & 0xFFFFFFFF, sv_battleResult=outcome,
                  **{key: struct.unpack_from('<Q', raw, offset)[0] for key, offset in
                     (('sv_kill', 0x68), ('sv_dead', 0x70), ('sv_atBonus', 0x80))})
    return dict(battle_id=battle.decode('ascii'), report=report, summary=value['summary'])


def experience_summary(reader):
    """Native bot wrapper converts score +0x14 float to sv_charaDamage at 0x14011115d.

    Compare actual damage, not Total/Attack Bonus. A tied maximum is still a
    highest value; this interpretation belongs to the custom revival rule.
    """
    roster = pointer(reader, 0x141B87458)
    scores = pointer(reader, 0x141B87490)
    selectors = read(reader, roster + 9, 3)
    own = selectors[2] if selectors[2] != 255 else selectors[0]
    if own > 3:
        raise ValueError('Unsupported XP player slot')
    damage = {}
    for slot in range(4):
        if number(reader, scores + slot * 0x25D0 + 0x18, '<Q'):
            value = number(reader, scores + 0xF120 + slot * 0x90 + 0x14, '<f')
            if not math.isfinite(value) or not 0 <= value <= MAX_INT:
                raise ValueError('Invalid native damage')
            damage[slot] = value
    if own not in damage or len(damage) < 2:
        raise ValueError('Incomplete native damage comparison')
    return dict(highest_damage=damage[own] == max(damage.values()))


def custom_start(reader, address):
    header = read(reader, address, 26)
    outer, size, sequence, tag, kind, status = struct.unpack_from('<HHQHHH', header)
    if outer != size + 4 or size < 22 or kind != 0xE608 or status != 0:
        return None
    if size < 22 + 42:
        raise ValueError('Truncated Custom battle start')
    payload = read(reader, address + 26, 42)
    return dict(sequence=sequence, match_key=payload[2:18].hex())


def custom_key(reader, hit):
    return read(reader, hit.rcx + 0x1ED8, 16).hex()


def battle_cubes(reader):
    """Retained actual battle loadout; never the Customize UI's selected tab."""
    roster = pointer(reader, 0x141B87458)
    selectors = read(reader, roster + 9, 3)
    own = selectors[2] if selectors[2] != 255 else selectors[0]
    if own > 3:
        raise ValueError('Unsupported battle cube slot')
    manager = pointer(reader, 0x141B874D0)
    grid = manager + own * 0x22D8 + 0x20C0
    grid_id, count = struct.unpack('<ii', read(reader, grid + 0xC, 8))
    if not 0 <= grid_id < 5 or not 0 <= count <= 7:
        raise ValueError('Invalid retained battle grid')
    holds = list(struct.unpack('<' + 'Q' * count, read(reader, grid + 0x18, count * 8))) if count else []
    if len(set(holds)) != len(holds) or any(not 1 <= h <= 9999 for h in holds):
        raise ValueError('Invalid retained battle cube holds')
    return dict(grid_id=grid_id, holds=[str(h) for h in holds])
