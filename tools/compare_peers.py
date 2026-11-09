"""Compare two friends' native Lamina slot traces. Does not send or print account IDs."""
import argparse
import json
from pathlib import Path


def parse(path):
    rounds={}
    with path.open(encoding='utf-8',errors='replace') as f:
        for line in f:
            if 'LAMINA_PEERS {' not in line:
                continue
            row=json.loads(line.split('LAMINA_PEERS ',1)[1])
            rounds.setdefault(row['round'],[]).append(row['slots'])
    return rounds


def compare(first,second):
    shared=sorted(set(first)&set(second))
    if not shared:
        return ['No shared round with verified native seats. Do not enable remote summons.'],False
    notes=[]
    passed=False
    for key in shared:
        sets=[]
        for snapshots in (first[key],second[key]):
            full=set()
            for rows in snapshots:
                if len(rows)>=2 and all(x[1]==x[2] for x in rows):
                    full.add(tuple(sorted((s,tuple(expected)) for s,expected,actual,active in rows)))
            sets.append(full)
        common=sets[0]&sets[1]
        if common:
            passed=True
            notes.append(f'{key[:8]}: both clients saw matching native selections for {max(len(c) for c in common)} human seats.')
        else:
            notes.append(f'{key[:8]}: native seat choices did not agree on both clients.')
    notes.append('This checks loadouts only. It does not attest summon input timing, actor state, damage or replay determinism.')
    return notes,passed


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('first',type=Path)
    p.add_argument('second',type=Path)
    a=p.parse_args()
    notes,passed=compare(parse(a.first),parse(a.second))
    print('\n'.join(notes))
    raise SystemExit(0 if passed else 1)
