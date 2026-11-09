"""Fields and named operations owned by an administrator-enrolled account."""
CORE=frozenset(('playerLevel','playerExperience','characterLevels','characterExperience',
    'originalSkillCubes','originalSkillCatalogueVersion','diagnosticSkillCube',
    'gamePoint','consumableItems','consumableGrants','consumableHoldSequence','boostState','storeCart',
    'awardPipeline','battleStatistics','battleCharacterPartners','teamNamePending','teamNameReceipt',
    'circleCapacityPending','circleCapacityReceipt','circleSubLeaderCapacityPending','circleSubLeaderCapacityReceipt'))
PREFIXES=('skillCube','skillGrid','evolutionTestGrant')
ACTIONS=frozenset(('selectLamina','useItem','updateSkillGrid','extendSkillGrid','composeSkillCube','verifyComposing',
    'cutCompositeTime','getMyCart','getStoreItemList','addToCart','updateCart','buyItem','removeFromCart',
    'beginBotBattle','endBotBattle','nativeLP','changeTagName','getTagName','incMaxNumOfMembers','incMaxNumOfSubLeaders'))
# These operations already use durable pending/receipt reservations around a
# different shared service. Preserve their checkpoints instead of buffering them.
CHECKPOINT_ACTIONS=frozenset(('changeTagName','getTagName','incMaxNumOfMembers','incMaxNumOfSubLeaders'))
PUBLISH_ACTIONS=frozenset(('useItem','updateSkillGrid','extendSkillGrid','composeSkillCube','buyItem',
    'endBotBattle','changeTagName','incMaxNumOfMembers','incMaxNumOfSubLeaders'))

def protected(key):
    return key in CORE or key.startswith(PREFIXES)

def select(snapshot):
    return {key:value for key,value in snapshot.items() if protected(key)}

def merge(local,remote):
    return dict({key:value for key,value in local.items() if not protected(key)},**select(remote))
