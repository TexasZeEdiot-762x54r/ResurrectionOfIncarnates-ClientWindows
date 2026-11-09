"""Future completed-battle teammate-character counts, never inferred history."""
from copy import deepcopy
import roi_records

FIELD='battleCharacterPartners'
EXTENSION='revivalCharacterPartners'
CHARACTERS={str(i) for i in range(1,22)}

def snapshot(value):
    if not isinstance(value,dict) or set(value)!={'character','partner'}:
        raise ValueError('Invalid teammate snapshot')
    if any(type(v) is not int or not 1<=v<=21 for v in value.values()):
        raise ValueError('Invalid teammate character')
    return dict(value)

def state(saved,owner,statistics=None):
    value=deepcopy(saved.get(FIELD,dict(schema=1,owner=str(owner),modes={})))
    if (not isinstance(value,dict) or set(value)!={'schema','owner','modes'} or type(value['schema']) is not int
            or value['schema']!=1 or value['owner']!=str(owner) or not isinstance(value['modes'],dict)
            or set(value['modes'])-set(roi_records.MODES)):
        raise ValueError('Invalid teammate history')
    statistics=roi_records.state(saved,owner) if statistics is None else statistics
    for mode,characters in value['modes'].items():
        if not isinstance(characters,dict) or set(characters)-CHARACTERS:raise ValueError('Invalid teammate owner characters')
        for cid,partners in characters.items():
            if not isinstance(partners,dict) or set(partners)-CHARACTERS:raise ValueError('Invalid teammate characters')
            total=sum(roi_records.counter(count) for count in partners.values())
            games=sum(statistics['modes'].get(mode,{}).get('characters',{}).get(cid,roi_records.empty()).values())
            if total>games:raise ValueError('Teammate history exceeds recorded appearances')
    return value

def apply(saved,owner,mode,character,teammate):
    if teammate is None:return {}
    teammate=snapshot(teammate)
    if mode not in roi_records.MODES or teammate['character']!=character:
        return {}
    value=deepcopy(saved.get(FIELD,dict(schema=1,owner=str(owner),modes={})))
    # Validate existing counters before incrementing against the newly committed
    # appearance count; legacy appearances intentionally have no partner entry.
    state(dict(saved,**{FIELD:value}),owner)
    row=value['modes'].setdefault(mode,{}).setdefault(str(character),{})
    partner=str(teammate['partner']);row[partner]=roi_records.counter(row.get(partner,0)+1)
    state(dict(saved,**{FIELD:value}),owner)
    return {FIELD:value}

def display_state(saved,owner):
    stored=saved.get(FIELD)
    if isinstance(stored,dict) and stored.get('owner')!=str(owner):
        return dict(schema=1,owner=str(owner),modes={})
    return state(saved,owner)
