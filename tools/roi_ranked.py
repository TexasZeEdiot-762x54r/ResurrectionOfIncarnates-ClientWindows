"""User-selected Option 2 rank policy and atomic, Ranked-only settlement.

This is a revival rule, not a recovered production formula. No HTTP route or
native hook calls begin/finish yet: Ranked matchmaking is implemented, but its
result transport does not attest a winner. The adapter must supply a trusted four-human roster,
persistently increasing per-owner match sequence and completed match winner.
Never trust a result request's claimed mode, ranks or winner as that authority.
"""
from copy import deepcopy

import profile_store
import roi_ranked_streaks

POLICY = 'revival-ranked-option2-v1'
MAX_RANK = 30
MAX_POINTS = 2147483647
MAX_SEQUENCE = 9223372036854775807
RECEIPT_LIMIT = 128
WIN_OVERRIDES = {
    (6,6,3,3):14,
    (6,1,1,1):14,
    (7,2,1,1):16,
    (8,1,1,1):16,
}


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid ranked integer')
    return value


def rank_id(value):
    return integer(value,1,MAX_RANK)


def up_threshold(rank):
    rank_id(rank)
    if rank <= 5: return 10*rank
    if rank <= 7: return 50*rank-200
    if rank <= 13: return 100*rank-550
    if rank <= 19: return 200*rank-1850
    if rank <= 25: return 300*rank-3750
    if rank < MAX_RANK: return 400*rank-6250
    return None


def down_threshold(rank):
    rank_id(rank)
    return up_threshold(rank-1)-50 if rank >= 10 else None


def points(mode, rank, partner, enemies, won):
    if mode != 'ranked':
        return 0
    rank_id(rank)
    if type(won) is not bool:
        raise ValueError('Ranked policy requires a completed win or loss')
    if rank <= 5:
        return 10 if won else 5
    if not won and rank <= 9:
        return 0
    rank_id(partner)
    if not isinstance(enemies,(tuple,list)) or len(enemies) != 2:
        raise ValueError('Missing opposing ranks')
    enemies = sorted(rank_id(r) for r in enemies)
    # twice_difference = 2 * (enemy average - own team average).
    # Integer floor preserves the proposed .5 rounding without float drift.
    twice_difference = sum(enemies)-rank-partner
    if won:
        key = (rank,partner,*enemies)
        return WIN_OVERRIDES.get(key,max(1,min(32,(25+3*twice_difference)//2)))
    return -1 if twice_difference > 0 else -max(1,min(32,12-3*twice_difference))


def validate_progress(rank, total):
    rank_id(rank)
    integer(total,0,MAX_POINTS)
    lower = (down_threshold(rank) if rank >= 10 else up_threshold(rank-1)) if rank > 1 else 0
    upper = up_threshold(rank)
    if total < lower or upper is not None and total >= upper:
        raise ValueError('Rank and points disagree; explicit migration required')


def advance(rank, total, delta):
    validate_progress(rank,total)
    integer(delta,-32,32)
    if rank < 10 and delta < 0:
        raise ValueError('Protected ranks cannot lose points')
    total = max(0,min(MAX_POINTS,total+delta))
    while rank < MAX_RANK and total >= up_threshold(rank):
        rank += 1
    # Equality at the demotion boundary is retained; demote only below it.
    while rank >= 10 and total < down_threshold(rank):
        rank -= 1
    return rank,total


def uid(value):
    if (type(value) is not str or not value.isascii() or not value.isdigit()
            or not 1 <= len(value) <= 20 or value[0] == '0'):
        raise ValueError('Invalid ranked owner')
    return value


def roster(teams, owner):
    if type(teams) is not list or len(teams) != 2:
        raise ValueError('Ranked requires two teams')
    seen,own = set(),None
    for team_index,team in enumerate(teams):
        if type(team) is not list or len(team) != 2:
            raise ValueError('Ranked requires four human participants')
        for slot,member in enumerate(team):
            if type(member) is not dict or set(member) != {'uid','rank'}:
                raise ValueError('Invalid ranked participant')
            member_id = uid(member['uid'])
            rank_id(member['rank'])
            if member_id in seen:
                raise ValueError('Duplicate ranked participant')
            seen.add(member_id)
            if member_id == owner:
                own = team_index,slot
    if own is None:
        raise ValueError('Ranked owner absent from roster')
    return own


def ledger(saved, owner):
    uid(owner)
    state = deepcopy(saved.get('rankedProgress',dict(schema=1,policy=POLICY,owner=owner,
                     rank=1,points=0,sequence=0,pending=None,receipts=[])))
    fields = {'schema','policy','owner','rank','points','sequence','pending','receipts'}
    if (type(state) is not dict or set(state) != fields or type(state['schema']) is not int
            or state['schema'] != 1 or state['policy'] != POLICY or state['owner'] != owner):
        raise ValueError('Invalid ranked ledger or owner')
    validate_progress(state['rank'],state['points'])
    integer(state['sequence'],0,MAX_SEQUENCE)
    if type(state['receipts']) is not list or len(state['receipts']) > RECEIPT_LIMIT:
        raise ValueError('Invalid ranked receipts')
    previous = 0
    for row in state['receipts']:
        if type(row) is not dict or set(row) != {'sequence','winning_team','rank_before',
                'points_before','rank','points','delta','policy'} or row['policy'] != POLICY:
            raise ValueError('Invalid ranked receipt')
        previous = integer(row['sequence'],previous+1,state['sequence'])
        integer(row['winning_team'],0,1)
        validate_progress(row['rank_before'],row['points_before'])
        validate_progress(row['rank'],row['points'])
        if (row['rank'],row['points']) != advance(row['rank_before'],row['points_before'],row['delta']):
            raise ValueError('Ranked receipt does not reconcile')
    if state['receipts'] and any(state[key] != state['receipts'][-1][key] for key in ('rank','points')):
        raise ValueError('Saved rank differs from latest ranked receipt')
    pending = state['pending']
    if pending is not None:
        if (type(pending) is not dict or set(pending) != {'sequence','teams','rank','points'}
                or integer(pending['sequence'],1,MAX_SEQUENCE) != state['sequence']
                or pending['sequence'] <= previous
                or pending['rank'] != state['rank'] or pending['points'] != state['points']):
            raise ValueError('Invalid pending ranked match')
        validate_progress(pending['rank'],pending['points'])
        team,slot = roster(pending['teams'],owner)
        if pending['teams'][team][slot]['rank'] != pending['rank']:
            raise ValueError('Ranked snapshot disagrees with saved rank')
    return state


def profile_fields(saved, owner):
    if 'rankedProgress' not in saved:
        return {'sv_rankId':'1','sv_rankPoint':'0'}
    state = ledger(saved,owner)
    return {'sv_rankId':str(state['rank']),'sv_rankPoint':str(state['points'])}


def begin(path, owner, mode, sequence, teams):
    """Internal adapter boundary, not a remotely callable authorization check."""
    if mode != 'ranked':
        return None
    integer(sequence,1,MAX_SEQUENCE)
    with profile_store.LOCK:
        state = ledger(profile_store.read(path),owner)
        team,slot = roster(teams,owner)
        if teams[team][slot]['rank'] != state['rank']:
            raise ValueError('Start rank differs from authoritative local progress')
        pending = dict(sequence=sequence,teams=deepcopy(teams),rank=state['rank'],points=state['points'])
        if state['pending'] == pending:
            return deepcopy(pending)
        if state['pending'] is not None or sequence <= state['sequence']:
            raise ValueError('Unfinished, conflicting or stale ranked start')
        state.update(sequence=sequence,pending=pending)
        profile_store.update(path,{'rankedProgress':state})
        return deepcopy(pending)


def finish(path, owner, mode, sequence, winning_team):
    if mode != 'ranked':
        return None
    integer(sequence,1,MAX_SEQUENCE)
    integer(winning_team,0,1)
    with profile_store.LOCK:
        saved = profile_store.read(path)
        state = ledger(saved,owner)
        for row in state['receipts']:
            if row['sequence'] == sequence:
                if row['winning_team'] != winning_team:
                    raise ValueError('Conflicting ranked result retry')
                return deepcopy(row)
        pending = state['pending']
        if pending is None or pending['sequence'] != sequence:
            raise ValueError('No matching ranked start')
        teams = pending['teams']
        team,slot = roster(teams,owner)
        change = points('ranked',state['rank'],teams[team][1-slot]['rank'],
                        [p['rank'] for p in teams[1-team]],team == winning_team)
        rank,total = advance(state['rank'],state['points'],change)
        row = dict(sequence=sequence,winning_team=winning_team,rank_before=state['rank'],
                   points_before=state['points'],rank=rank,points=total,
                   delta=total-state['points'],policy=POLICY)
        state.update(rank=rank,points=total,pending=None)
        state['receipts'] = (state['receipts']+[row])[-RECEIPT_LIMIT:]
        streak = roi_ranked_streaks.apply(saved, owner, 'ranked', sequence,
                                        1 if team == winning_team else -1)
        profile_store.update(path,dict(rankedProgress=state, **streak))
        return deepcopy(row)


def cancel(path, owner, mode, sequence):
    """Abandon without inventing disconnect penalties or reusing its sequence."""
    if mode != 'ranked':
        return None
    integer(sequence,1,MAX_SEQUENCE)
    with profile_store.LOCK:
        state = ledger(profile_store.read(path),owner)
        if state['pending'] is None or state['pending']['sequence'] != sequence:
            raise ValueError('No matching ranked start to cancel')
        state['pending'] = None
        profile_store.update(path,{'rankedProgress':state})
