"""Keep unknown-player appearance placeholders small during Steam friend bursts."""
from copy import deepcopy


def compact(value):
    """Accept an already-defaulted guest, never a known peer or the local profile."""
    result = deepcopy(value)
    result.update(sv_rankId='1', sv_rankPoint='0')
    import roi_records
    result['sv_battleRecords'] = roi_records.profile_records({}, 'unknown')
    # Native profile parsing retains these collections for each friend. They are
    # local ownership data, not needed to render a missing peer's default outfit.
    for field in ('sv_skillgrids', 'sv_skillcubes', 'sv_items', 'sv_recipes', 'sv_compositeStacks'):
        result[field] = []
    for character in result.get('sv_characters', []):
        character['sv_charaUsage'] = '0'
        needed = {str(hold) for slot in character.get('sv_customSlots', [])
                  for hold in slot.get('sv_customItemHoldIds', []) if str(hold) != '0'}
        character['sv_customItems'] = [
            item for item in character.get('sv_customItems', [])
            if str(item.get('sv_customItemHoldId')) in needed]
    return result
