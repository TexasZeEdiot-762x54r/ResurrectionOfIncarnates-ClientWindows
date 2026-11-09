"""Server-selected starting policy; existing saves never choose their own policy."""
import os


def starter(snapshot):
    import roi_skills
    policy = os.environ.get('ROI_PROGRESSION_MODE', 'normal')
    if policy not in ('normal', 'unlocked'):
        raise ValueError('ROI_PROGRESSION_MODE must be normal or unlocked')
    result = dict(snapshot, skillGridRuleset=policy, gamePoint=0,
                  skillCubeSeedInventory=False, diagnosticSkillCube=False,
                  originalSkillCubes=True, skillGridProgression=policy == 'normal')
    if policy == 'unlocked':
        result['skillGrids'] = [roi_skills.empty_grid(i) for i in range(roi_skills.MAX_GRIDS)]
    return result


def unlocked(saved):
    return (saved or {}).get('skillGridRuleset') == 'unlocked'


def lamina_allowed(saved):
    return unlocked(saved) or int((saved or {}).get('playerLevel', 1)) >= 10


def grid_allowed(saved):
    return unlocked(saved) or int((saved or {}).get('playerLevel', 1)) >= 5


def capped(row, rank):
    from roi_cube_synthesis import MAX_LEVEL, TOTAL_EXP, _trigger_limit
    level = MAX_LEVEL[rank]
    row.update(sv_skillLevel=str(level),sv_skillExp=str(TOTAL_EXP[rank][level]),
               sv_levelupCount=str(level-1),sv_triggerCount='0',sv_triggerLimit=str(_trigger_limit(level)))


def insert_starter_inventory(cursor,uid,snapshot):
    import json
    import roi_skills
    rows=roi_skills.inventory(snapshot)
    if rows:
        cursor.executemany('INSERT INTO skill_cube_inventory '
            '(steam_id,hold_id,definition_id,level,xp,durability,max_durability,data) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s,%s)', [(uid,r['sv_skillcubeHoldId'],r['sv_skillcubeId'],
                int(r['sv_skillLevel']),int(r['sv_skillExp']),int(r['sv_triggerLimit']),
                int(r['sv_triggerLimit']),json.dumps(r,separators=(',',':'))) for r in rows])
