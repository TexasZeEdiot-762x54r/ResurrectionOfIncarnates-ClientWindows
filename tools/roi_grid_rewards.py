"""Bounded original-evidence or explicitly selected revival grid rewards."""
import json
from pathlib import Path

POLICY_FILE = Path(__file__).with_name('grid-reward-policy.json')
MAX_ORIGINAL_EARNED_RIGHTS = 9  # Guide: four initial cells, thirteen total through leveling.
MAX_NATIVE_EXPANSIONS = 12      # Four initial cells plus twelve expansions fill the 4x4 grid.


def level_rewards():
    policy = json.loads(POLICY_FILE.read_text(encoding='utf-8'))
    rows = policy.get('level_rewards')
    provenance = policy.get('level_reward_provenance', 'original_evidence')
    maximum = (MAX_NATIVE_EXPANSIONS if provenance == 'user_selected_revival'
               else MAX_ORIGINAL_EARNED_RIGHTS)
    source = policy.get('level_reward_source')
    if (policy.get('schema') != 1 or provenance not in ('original_evidence', 'user_selected_revival')
            or provenance == 'user_selected_revival'
            and (not isinstance(source, str) or not 1 <= len(source.strip()) <= 2048)
            or not isinstance(rows, list) or len(rows) > maximum):
        raise ValueError('Invalid recovered grid-reward policy')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid recovered grid reward')
        level, rights, evidence = row.get('player_level'), row.get('rights'), row.get('evidence')
        if type(level) is not int or not 5 <= level <= 50 or level in seen:
            raise ValueError('Invalid or duplicated reward level')
        if type(rights) is not int or not 1 <= rights <= maximum:
            raise ValueError('Invalid expansion-right quantity')
        if row.get('verified') is not True or not isinstance(evidence, str) or not 1 <= len(evidence) <= 2048:
            raise ValueError('Unverified grid rewards cannot be activated')
        seen.add(level)
        result.append((level, rights))
    if sum(rights for _, rights in result) > maximum:
        raise ValueError('Level rewards exceed the documented level-earned allowance')
    return sorted(result)


def earned_rights(player_level):
    if type(player_level) is not int or not 1 <= player_level <= 50:
        raise ValueError('Invalid player level')
    return sum(rights for level, rights in level_rewards() if player_level >= level)
