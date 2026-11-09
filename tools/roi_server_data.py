"""Responses of the local stand-in server (restart it after changing handlers).

Protocol (from roi.exe):
  POST /Dragon/authorize  body {"os":"steam","api_version":"0.8","steamId":..,"region":..,"login":1,"ticket":..}
      reply: JSON {"result":"OK","time":..,"uid":"<id>","storageID":".."} + headers oauth_token / oauth_token_secret
  POST /Dragon/game       body {"version":0.8,"call":"<name>","data":{...}}
      reply: {"result":"OK","time":..,"data":...}
  The client reads most numbers with strtoui64 on *string* values, so values are sent as strings.
"""
import json
import time
from copy import deepcopy

from server_state import STATE  # persists across reloads of this module

TOKEN = "local-token"
SECRET = "local-secret"
CALLS = {}


def call(name):
    def deco(fn):
        CALLS[name] = fn
        return fn
    return deco


def ok(data=None):
    out = {"result": "OK", "time": str(int(time.time()))}
    if data is not None:
        out["data"] = data
    return out


def pid():
    return str(STATE.get("player_id", "1"))


# --- cross-player profile sharing -----------------------------------------------------------------
# server.py only binds to 127.0.0.1, so each player's "Dragon" API is local to their own PC; a lobby
# partner's real customization has to come from somewhere shared. profile_relay.py runs a small in-memory
# store inside nrt_server.py (the one networked component both players already point at, see lobby_server()
# below). publish_profile() pushes this player's own profile() there on login/customize changes; loadPlayer
# fetches a requested partner's real profile from there instead of falling back to a copy of our own.
# Best-effort throughout: any failure (solo play, relay not up yet, timeout) just means no partner data is
# available, never blocks the caller.
def relay_url(path):
    host, port = lobby_server()
    return f"http://{host}:{port + 2}{path}"


def publish_profile():
    # Fire-and-forget in a background thread: the game client waits synchronously for our HTTP response, so
    # this must never add relay latency (or a relay hang) to that wait.
    import paths
    import profile_relay
    import roi_records
    url = relay_url(f'/profile/{pid()}')
    value = profile()
    record_snapshot = saved()
    value['revivalBattleStatistics'] = roi_records.display_state(record_snapshot, pid())
    import roi_partners
    value[roi_partners.EXTENSION] = roi_partners.display_state(record_snapshot, pid())
    profile_relay.publish(url, relay_credential(), value)
    if STATE.get('background_commendations'):
        import commendation_sync
        commendation_sync.start(relay_url(f'/commendations/{pid()}'), relay_credential(), pid(), SAVE_FILE)
        import roi_shared_replays
        roi_shared_replays.start(replay_outbox(),pid(),relay_url(f'/replays/{pid()}'),relay_credential())


def relay_credential():
    import paths
    import roi_progression_client
    account=roi_progression_client.credential(pid())
    if account is not None:
        return account
    import roi_progression_client
    account=roi_progression_client.credential(pid())
    if account is not None:
        return account
    # Never leak an old WireGuard invitation to the anonymous public service.
    return '' if paths._cfg.get('lobby_transport') == 'tls-public' else paths._cfg.get('lobby_token', '')


def fetch_profile(uid):
    import paths
    import profile_reads
    return profile_reads.CACHE.get(relay_url(f'/profile/{uid}'), relay_credential(), uid)


# --- login ---------------------------------------------------------------------------------------

def authorize(req):
    STATE["player_id"] = str(req.get("steamId", "1"))
    # generic reply handler 0x14009a710: root "uid" (string) -> status 1, "storageID"
    out = ok()
    out["uid"] = pid()
    out["storageID"] = "local"
    return out


@call("load")
def load(data):
    name = data.get("name")
    if name == 'circle':
        # Invitation delivery is not implemented; do not synthesize invitations.
        return ok([])
    if name == "player":
        # 0x14009a710: data.sv_onlineId -> status 2 (existing player), data._id stored
        return ok({"_id": pid(), "sv_onlineId": pid()})
    import importlib
    import roi_masters
    importlib.reload(roi_masters)
    return ok(roi_masters.master(name))


PERMANENT = "9999999999999"   # sv_charaEnableTerm >= 0x9184e729fff means owned for good
SAVE_FILE = __import__("pathlib").Path(__file__).resolve().parent / "player_profile.json"


def saved():
    """What the player changed in game (kept across server restarts): active character, tutorial flags."""
    import profile_store
    local=profile_store.read(SAVE_FILE)
    import roi_progression_client
    import progression_contract
    remote=roi_progression_client.fields(relay_url(f'/progress/{pid()}'),pid())
    return local if remote is None else progression_contract.merge(local,remote)


def save(**changes):
    import profile_store
    profile_store.update(SAVE_FILE, changes)


def own_circle_id():
    # This independent Lamina sandbox has no public Circle membership.
    return '0'


def circle_action(action, data):
    if action == 'getCircleInfo':
        import circles
        return ok({'sv_circle': circles.snapshot(None, None),
                   'sv_leaderName': '', 'sv_invitings': []})
    import roi_circles
    import roi_circle_voice
    try:
        result = roi_circles.request(relay_url(f'/circle/{pid()}'), relay_credential(), pid(),
                                    STATE.get('name', 'LocalPlayer'), action, data)
        save(circleMembership={'uid': pid(), 'id': result['circle_id']})
        if action in ('getCircleInfo', 'setupCircle'):
            roi_circle_voice.observe_circle(pid(), result['data'].get('sv_circle'))
        elif action in ('leaveCircle', 'breakUpCircle'):
            roi_circle_voice.record(pid())
        return ok(result['data'])
    except Exception as exc:
        if action == 'getCircleInfo':
            roi_circle_voice.record(pid())
        import logging
        logging.getLogger('roi-profile').warning('Circle %s failed: %s HTTP=%s',
            action, type(exc).__name__, getattr(exc, 'code', 'n/a'))
        # Native Circle parser 0x14008acc0 recognizes NG before dereferencing data.
        return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}


def refresh_circle():
    if STATE.get('circle_service'):
        circle_action('getCircleInfo', {})


@call('setupCircle')
def setup_circle(data):
    return circle_action('setupCircle', data)


@call('getCircleInfo')
def get_circle_info(data):
    return circle_action('getCircleInfo', data)


@call('getCircleMemberList')
def get_circle_members(data):
    return circle_action('getCircleMemberList', data)


@call('getTagName')
def get_tag_names(data):
    import roi_team_names
    try:
        if roi_team_names.recover(SAVE_FILE, pid(), team_request):
            publish_profile()
        return ok(roi_team_names.get(pid(), data, team_request))
    except (OSError, ValueError, TypeError) as exc:
        import logging
        logging.getLogger('roi-profile').warning('Team lookup failed: %s', type(exc).__name__)
        # Type 53 unconditionally copies this array, including when offline.
        return ok({'sv_tagNames': []})


def team_request(action, data):
    import roi_team_names
    return roi_team_names.request(relay_url(f'/team/{pid()}'), relay_credential(), action, data)


@call('changeTagName')
def change_tag_name(data):
    import roi_team_names
    try:
        result = roi_team_names.change(SAVE_FILE, pid(), data, team_request)
        if result['status'] == 'rejected':
            return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}
    except (OSError, ValueError, TypeError) as exc:
        import logging
        logging.getLogger('roi-profile').warning('Team rename failed: %s', type(exc).__name__)
        return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}
    publish_profile()
    # Native types 54/55 parse these fields at 0x140091b60. Type 55 then reloads inventory.
    return ok(result['data'])


@call('getPlayerState')
def get_player_states(data):
    # Type 50 reads sv_states at 0x1400e21dd. No presence records are tracked yet.
    return ok({'sv_states': []})


@call('findCircle')
def find_circle(data):
    return circle_action('findCircle', data)


@call('getCircleName')
def get_circle_name(data):
    return circle_action('getCircleName', data)


@call('leaveCircle')
def leave_circle(data):
    return circle_action('leaveCircle', data)


@call('breakUpCircle')
def break_up_circle(data):
    return circle_action('breakUpCircle', data)


@call('changeComment')
def circle_comment(data):
    return circle_action('changeComment', data)


@call('changeCircleIcon')
def circle_icon(data):
    return circle_action('changeCircleIcon', data)


@call('changeEntryQualifications')
def circle_qualifications(data):
    return circle_action('changeEntryQualifications', data)


@call('changeCircleArea')
def circle_area(data):
    return circle_action('changeCircleArea', data)


@call('incMaxNumOfMembers')
def expand_circle_members(data):
    return circle_capacity_purchase('members')


@call('incMaxNumOfSubLeaders')
def expand_circle_subleaders(data):
    return circle_capacity_purchase('subleaders')


def circle_capacity_purchase(kind):
    import logging
    import roi_circle_capacity
    try:
        result = roi_circle_capacity.expand(SAVE_FILE, relay_url(f'/circle/{pid()}'),
            relay_credential(), pid(), STATE.get('name', 'LocalPlayer'), kind=kind)
        return ok(result)
    except Exception as exc:
        logging.getLogger('roi-profile').warning('Circle expansion failed: %s; check pending purchase before retrying',
                                               type(exc).__name__)
        return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}


def active_chara():
    """The character picked in the menu (updateProfile charaId); the profile's sv_activeCharaId decides who you play."""
    import roi_data
    cid = saved().get("activeChara", 1)
    return cid if cid in roi_data.CHARACTERS else 1


def player_level():
    """Persisted account-level override, within the current player-level master."""
    value = saved().get("playerLevel", 1)
    return value if type(value) is int and 1 <= value < 50 else 1


def merge_tutorial_map(m):
    """sv_tutorialMap updates are bit flags per key (e.g. chara: 1 << charaId once that character's tutorial was seen)."""
    if not isinstance(m, dict):
        return
    stored = saved().get("tutorialMap", {})
    for k, v in m.items():
        try:
            stored[k] = int(stored.get(k, 0)) | int(v)
        except (TypeError, ValueError):
            pass
    save(tutorialMap=stored)


def hold_id(value):
    if type(value) not in (int, str):
        return '0'
    try:
        number = int(value)
    except ValueError:
        return '0'
    return str(number) if 0 <= number < 2 ** 64 else '0'


def explicit_custom_slot(cid, values, items):
    """Resolve base-item defaults using THIS character/profile's hold-ID map.

    Zero accessory slots mean none. Zero base slots mean the base model/colour,
    not permission to reuse a different player's equipped head/costume/weapon.
    """
    values = values if isinstance(values, list) else []
    holds = ([hold_id(value) for value in values[:9]] + ['0'] * 9)[:9]
    inventory = {hold_id(item.get('sv_customItemId')): hold_id(item.get('sv_customItemHoldId'))
                 for item in items if isinstance(item, dict)}
    for index in range(3):
        if holds[index] == '0':
            base_item = str(cid * 1000000 + (index + 1) * 100000)
            holds[index] = inventory.get(base_item, '0')
    return {'sv_customItemHoldIds': holds}


def explicit_profile_outfits(value, defaults_only=False):
    """Copy a remote snapshot without sharing or inheriting local loadouts."""
    result = deepcopy(value)
    for character in result.get('sv_characters', []):
        if not isinstance(character, dict):
            continue
        cid = int(hold_id(character.get('sv_charaId')))
        items = character.get('sv_customItems')
        items = items if isinstance(items, list) else []
        slots = character.get('sv_customSlots')
        slots = slots if isinstance(slots, list) and slots and not defaults_only else [{}]
        character['sv_customSlots'] = [
            dict(slot, **explicit_custom_slot(cid, slot.get('sv_customItemHoldIds'), items))
            for slot in (slot if isinstance(slot, dict) else {} for slot in slots)]
        active = int(hold_id(character.get('sv_activeCustomSlotNum')))
        character['sv_activeCustomSlotNum'] = str(active if not defaults_only and active < len(slots) else 0)
    return result


def custom_slots(cid, snapshot=None, items=None):
    """Saved outfits of a character (updateCustomize), as the profile's sv_customSlots list (index = slot number)."""
    import roi_custom
    current = saved() if snapshot is None else snapshot
    items = roi_custom.owned(cid) if items is None else items
    slots = current.get("customSlots", {}).get(str(cid), {})
    count = max(int(k) for k in slots) + 1 if slots else 1
    active = int(hold_id(current.get('activeCustomSlot', {}).get(str(cid), 0)))
    # Bound synthesis of empty default slots; this is not a recovered UI limit.
    if active < 256:
        count = max(count, active + 1)
    return [explicit_custom_slot(cid, slots.get(str(i), [0] * 9), items) for i in range(count)]


@call("updateCustomize")
def update_customize(data):
    # {"sv_charaId": 1, "sv_slotNum": 0, "sv_customItemHoldIds": [head, costume, weapon, acce x3, 0, 0, 0]}
    cid, slot = str(data.get("sv_charaId")), str(data.get("sv_slotNum", 0))
    allslots = saved().get("customSlots", {})
    allslots.setdefault(cid, {})[slot] = [int(x) for x in data.get("sv_customItemHoldIds", [])]
    save(customSlots=allslots)
    publish_profile()
    return ok({})


@call("changeActiveCustomizeSlot")
def change_active_customize_slot(data):
    active = saved().get("activeCustomSlot", {})
    active[str(data.get("sv_charaId"))] = int(data.get("sv_slotNum", data.get("sv_activeCustomSlotNum", 0)))
    save(activeCustomSlot=active)
    publish_profile()
    return ok({})


def tutorial_map():
    return {k: str(v) for k, v in saved().get("tutorialMap", {}).items()}
ALL_TUTORIALS = "2147483647"  # sv_userTutorial is a bitmask (0x1f = main tutorial; higher bits = hint popups)


def profile():
    import importlib
    import roi_custom
    import roi_data
    import roi_skills
    import roi_awards
    import commendation_sync
    import roi_consumables
    import roi_boosts
    import roi_rank_service
    import roi_records
    importlib.reload(roi_custom)
    import profile_store
    with profile_store.LOCK:
        importlib.reload(roi_skills)
        skill_profile = saved()
        skill_grids = roi_skills.grids(skill_profile)
        skill_inventory = roi_skills.inventory(skill_profile)
    good_counts = commendation_sync.counts(skill_profile, pid())
    chars = []
    for cid in sorted(roi_data.CHARACTERS):
        items = roi_custom.owned(cid) if skill_profile.get('skillGridRuleset') == 'unlocked' else []
        slots = custom_slots(cid, skill_profile, items)
        active = int(hold_id(skill_profile.get("activeCustomSlot", {}).get(str(cid), 0)))
        chars.append({
            "sv_charaId": str(cid), "sv_charaLv": str(roi_awards.character_level(skill_profile, cid)), "sv_charaEnabled": True, "sv_charaEnableTerm": PERMANENT,
            "sv_charaTutorial": ALL_TUTORIALS, "sv_charaExp": str(roi_awards.experience(skill_profile, cid)),
            "sv_charaUsage": str(roi_records.character_usage(skill_profile, pid(), cid)),
            "sv_customItems": items, "sv_customSlots": slots,
            "sv_activeCustomSlotNum": str(active if active < len(slots) else 0),
        })
    import roi_lamina
    lamina_profile = profile_store.read(SAVE_FILE.parent / 'lamina_profile.json')
    lamina_state = roi_lamina.state(lamina_profile)
    import release_lamina
    lamina_access = release_lamina.publish(skill_profile)
    laminas = [dict(row, sv_customSlot=[]) for row in roi_lamina.profile_rows(lamina_profile)] if lamina_access else []
    return {
        "_id": pid(), "sv_uid": pid(), "sv_onlineId": pid(),
        "sv_userName": STATE.get("name", "Player"), "sv_userRegion": STATE.get("region", "SE"),
        "sv_gameRegion": STATE.get("region", "SE"),
        "sv_userTutorial": ALL_TUTORIALS, "sv_tutorialMap": tutorial_map(),
        **roi_rank_service.profile_fields(relay_url(f'/rank/{pid()}'), relay_credential(), pid()),
        "sv_playerLv": str(player_level()), "sv_playerExp": str(roi_awards.experience(skill_profile)), "sv_playerStatus": "0",
        "sv_virtualCurrency": "0", "sv_userCountry": "SE", "sv_userCountryState": "", "sv_gamePoint": str(skill_profile.get('gamePoint', 0)),
        "sv_userCurrency": "EUR", "sv_itemsHoldLimit": "9999", "sv_allCharaFreeTerm": "0",
        "sv_freeCharaGainTime": "0", "sv_selectedStageId": "1",
        "sv_skillgrids": skill_grids, "sv_skillcubeHoldLimit": str(max(100, len(skill_inventory))),
        "sv_skillcubes": skill_inventory, "sv_items": roi_consumables.owned(skill_profile),
        "sv_onBoostList": roi_boosts.native_rows(skill_profile),
        "sv_disconnNum": "0", "sv_circleId": own_circle_id(), "sv_activeCharaId": str(active_chara()), "sv_activeSupLamId": str(lamina_state['selected_id'] if lamina_access else 0),
        "sv_characters": chars, "sv_supLams": laminas,
        "sv_enteredRoomId": "0", "sv_tagPartner": "0", "sv_recipes": [], "sv_compositeStackLimit": "1",
        "sv_compositeStacks": [], "sv_battleRecords": roi_records.profile_records(skill_profile, pid(), sum(good_counts.values())),
        "sv_disconnectLog": "0", "sv_disconnCriminal": "0",
    }


@call("useItem")
def use_item(data):
    import roi_boosts
    try:
        return ok(roi_boosts.activate(SAVE_FILE, data))
    except (TypeError, ValueError):
        return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}


@call("updateProfile")
def update_profile(data):
    # user parser 0x1400df720: data.sv_onlineId -> profile parser 0x1400f7e00, user entry state 3
    if 'supLamId' in data:
        try:
            raw_lamina = data['supLamId']
            if type(raw_lamina) not in (int, str):
                raise ValueError('Invalid Lamina type')
            lamina_id = int(raw_lamina)
            if not 1 <= lamina_id <= 15:
                raise ValueError('Invalid Lamina')
            # The native selector hook owns the sidecar transaction. HTTP only reads.
        except (TypeError, ValueError):
            return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}
    if data.get("name"):
        STATE["name"] = data["name"]
    if data.get("steamRegion"):
        STATE["region"] = data["steamRegion"]
    if "charaId" in data:  # character picked in the menu
        import roi_data
        try:
            cid = int(data["charaId"])
        except (TypeError, ValueError):
            cid = 0
        if cid in roi_data.CHARACTERS:
            save(activeChara=cid)
    merge_tutorial_map(data.get("sv_tutorialMap"))
    publish_profile()
    return ok(profile())


@call("updateSkillGrid")
def update_skill_grid(data):
    import importlib
    import profile_store
    import roi_skills
    with profile_store.LOCK:
        importlib.reload(roi_skills)
        if not roi_skills.enabled(saved()):
            return {"result": "ERROR", "time": str(int(time.time())), "data": {}}
        try:
            value = roi_skills.update_grids(data.get("sv_grids"), saved())
        except (TypeError, ValueError):
            return {"result": "ERROR", "time": str(int(time.time())), "data": {}}
        save(skillGrids=value)
    publish_profile()
    # Native request 13 clears profile-pending only when sv_grids is an array.
    return ok({'sv_grids': value})


@call("extendSkillGrid")
def extend_skill_grid(data):
    import profile_store
    import roi_skills
    with profile_store.LOCK:
        current = saved()
        try:
            value = roi_skills.extend(current, data.get('openGrids'))
        except (TypeError, ValueError):
            return {'result': 'ERROR', 'time': str(int(time.time())), 'data': {}}
        save(skillGrids=value)
    publish_profile()
    # Native request 12 uses the same array gate before invalidating its cache.
    return ok({'sv_grids': value})


@call('composeSkillCube')
def compose_skill_cube(data):
    import importlib
    import profile_store
    import roi_cube_synthesis
    import roi_skill_catalogue
    with profile_store.LOCK:
        importlib.reload(roi_skill_catalogue)
        importlib.reload(roi_cube_synthesis)
        current = saved()
        try:
            changes, result = roi_cube_synthesis.compose(current, data)
        except (TypeError, ValueError):
            return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}
        if changes:
            save(**changes)
    if changes:
        publish_profile()
        # The native client schedules the completion timer by comparing the
        # result timestamps with this response clock. Keep both on the same
        # one-second boundary even if profile persistence crosses a wall-clock
        # second; otherwise the modal can open after its own deadline and never
        # register its completion callback.
        return {'result': 'OK', 'time': result['sv_beginTime'], 'data': result}
    return ok(result)


@call('verifyComposing')
def verify_composing(data):
    import importlib
    import roi_cube_synthesis
    importlib.reload(roi_cube_synthesis)
    try:
        return ok(roi_cube_synthesis.verify(saved(), data))
    except (TypeError, ValueError):
        return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}


@call('cutCompositeTime')
def cut_composite_time(data):
    import importlib
    import roi_cube_synthesis
    importlib.reload(roi_cube_synthesis)
    try:
        return ok(roi_cube_synthesis.cut_time(saved(), data))
    except (TypeError, ValueError):
        return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}


@call('getMyCart')
@call('getStoreItemList')
def get_grid_cart(data):
    import roi_skill_shop
    try:
        return ok(roi_skill_shop.reply(saved(), kind=roi_skill_shop.cart_type(data)))
    except (TypeError, ValueError):
        return {'result': 'ERROR', 'time': str(int(time.time())), 'data': {}}


@call('addToCart')
def add_grid_to_cart(data):
    return change_grid_cart(data, 'add')


@call('updateCart')
def update_grid_cart(data):
    return change_grid_cart(data, 'update')


def change_grid_cart(data, operation):
    import profile_store
    import roi_skill_shop
    with profile_store.LOCK:
        try:
            changes = getattr(roi_skill_shop, operation)(saved(), data)
        except (TypeError, ValueError):
            return {'result': 'ERROR', 'time': str(int(time.time())), 'data': {}}
        save(**changes)
        return ok(roi_skill_shop.reply(saved(), kind=roi_skill_shop.cart_type(data)))


@call('buyItem')
def buy_grid(data):
    import profile_store
    import roi_skill_shop
    with profile_store.LOCK:
        try:
            changes, receipt = roi_skill_shop.buy(saved(), data)
        except (TypeError, ValueError):
            return {'result': 'ERROR', 'time': str(int(time.time())), 'data': {}}
        save(**changes)
        result = roi_skill_shop.reply(saved(), receipt, kind=roi_skill_shop.cart_type(data))
    publish_profile()
    return ok(result)


@call('removeFromCart')
def remove_grid_from_cart(data):
    import profile_store
    import roi_skill_shop
    with profile_store.LOCK:
        try:
            kind = roi_skill_shop.cart_type(data)
            changes = roi_skill_shop.remove(saved(), data)
            if changes:
                save(**changes)
            return ok(roi_skill_shop.reply(saved(), kind=kind))
        except (TypeError, ValueError):
            return {'result': 'ERROR', 'time': str(int(time.time())), 'data': {}}


@call("sendTutorialResult")
def send_tutorial_result(data):
    merge_tutorial_map(data.get("sv_tutorialMap"))
    # Requests 98..103 use the profile parser, not an empty acknowledgement.
    return ok(profile())


@call("updateFriends")
def update_friends(data):
    # request: data.sv_onlineIds = Steam ids of this player's Steam friends who own the game.
    # reply parser 0x140098c80 (request type 0x33): data.sv_friends = [{sv_uid, sv_onlineId, sv_userName}].
    # Every listed friend counts as a registered player, so the in-game friend list (and its Invite) shows them.
    import player_names
    friends = []
    for sid in data.get("sv_onlineIds") or []:
        sid = str(sid)
        if sid.isdigit() and sid != pid():
            friends.append({"sv_uid": sid, "sv_onlineId": sid, "sv_userName": player_names.get(sid)})
    return ok({"sv_friends": friends})


@call("beginBotBattle")
def begin_bot_battle(data):
    import importlib
    import profile_store
    import roi_awards
    try:
        with profile_store.LOCK:
            importlib.reload(roi_awards)
            response = roi_awards.begin(SAVE_FILE, pid(), active_chara(), data, track_records=True)
    except (TypeError, ValueError):
        return {"result": "ERROR", "time": str(int(time.time())), "data": {}}
    if response is not None:
        return ok(response)
    # Retain the legacy protocol fallback if an adapter explicitly declines tracking.
    STATE["battle_seq"] = STATE.get("battle_seq", 0) + 1
    STATE["battle"] = dict(data)
    return ok({"sv_battleId": str(STATE["battle_seq"])})


@call("endBotBattle")
def end_bot_battle(data):
    import importlib
    import profile_store
    import roi_awards
    full_lp = needs_native = False
    try:
        with profile_store.LOCK:
            importlib.reload(roi_awards)
            current = roi_awards.policy()
            full_lp = current['lp_enabled'] and current['lp_components'] == 'full'
            xp_enabled = current['match_xp']['enabled']
            needs_native = full_lp or xp_enabled
            native_summary = None
            native_xp = None
            if needs_native:
                import roi_native_lp
                native_summary = roi_native_lp.bot_summary(SAVE_FILE, pid(), data) if full_lp else None
                native_xp = roi_native_lp.bot_xp_summary(SAVE_FILE, pid(), data) if xp_enabled else None
            # Unsupported/unavailable native input must not turn completion into error 9009.
            response = (None if full_lp and native_summary is None or xp_enabled and native_xp is None else
                        roi_awards.finish(SAVE_FILE, pid(), data, lp_summary=native_summary,
                                          xp_summary=native_xp, track_records=True))
    except (OSError, TypeError, ValueError):
        if needs_native:
            response = None
        else:
            return {"result": "ERROR", "time": str(int(time.time())), "data": {}}
    if response is not None:
        publish_profile()
        return ok(response)
    STATE.setdefault("results", []).append({k: data.get(k) for k in ("sv_battleResult", "sv_stageId", "sv_enemyCharaIds")})
    STATE['results'] = STATE['results'][-128:]
    return ok({"sv_battleId": str(data.get('sv_battleId', STATE.get("battle_seq", 1))),
               "sv_rewards": [], "sv_items": [], "sv_mails": []})


@call('nativeLP')
def native_lp(data):
    """Authenticated loopback revival extension, not an original Dragon request."""
    import roi_native_lp
    try:
        if 'player_id' not in STATE:
            raise ValueError('Player is not authorized yet')
        response = roi_native_lp.handle(SAVE_FILE, pid(), active_chara(), data)
    except (OSError, TypeError, ValueError):
        return {"result": "ERROR", "time": str(int(time.time())), "data": {}}
    if data.get('action') == 'settle_custom' and response.get('enabled'):
        publish_profile()
    return ok(response)


@call("getItemMail")
def get_item_mail(data):
    # Post-match request type 18 uses 0x1400cf370, not the legacy message parser.
    # Its sv_mails array check at 0x1400d05b3 gates API state=2 at 0x1400d0da1;
    # omitting the array leaves the results controller waiting until error 9008.
    # Keep the previous empty fields for compatibility; no rewards are granted.
    return ok({"sv_mails": [], "sv_messages": [], "sv_itemMails": []})


@call("loadPlayer")
def load_player(data):
    q = data.get("query") or {}
    who = str(q.get("_id") or q.get("sv_onlineId") or "")
    if who == pid():
        refresh_circle()
        return ok(profile())
    if who.isdigit():
        # another player (lobby member / host in online play). Each PC's own server.py only binds to
        # 127.0.0.1 -- the other player's real data lives on THEIR PC, not ours -- so ask profile_relay
        # (shared via nrt_server.py, which both clients already point at) for what they last published.
        # 2026-10-17: this used to just copy OUR OWN profile() with the id swapped in, so a lobby partner
        # always appeared to be wearing whatever customization we had equipped -- see MODLOG.
        real = fetch_profile(who)
        if real is not None:
            import roi_circle_voice
            import roi_circles
            roi_circle_voice.refresh_remote(who, real, lambda action, payload:
                roi_circles.request(relay_url(f'/circle/{pid()}'), relay_credential(), pid(),
                                    STATE.get('name', 'LocalPlayer'), action, payload))
            return ok(explicit_profile_outfits(real))
        import roi_circle_voice
        roi_circle_voice.record(who)
        # Unknown remote appearance must never borrow our equipped cosmetics.
        # Retain the compatible legacy placeholder shape, but use explicit base
        # outfits and no accessories. Do not persist this as the remote profile.
        guest = explicit_profile_outfits(profile(), defaults_only=True)
        guest['sv_onBoostList'] = []
        import guest_profiles
        guest = guest_profiles.compact(guest)
        import player_names
        guest.update({"_id": who, "sv_uid": who, "sv_onlineId": who, "sv_userName": player_names.get(who), "sv_activeCharaId": "1", "sv_circleId": "0"})
        return ok(guest)
    return ok({})


@call("registerProfile")
def register_profile(data):
    refresh_circle()
    publish_profile()
    return ok(profile())


def lobby_server():
    """(host, port) of the lobby server from config.json "lobby_server" (default this PC)."""
    import importlib
    import paths
    importlib.reload(paths)
    host, _, port = paths.LOBBY_SERVER.rpartition(":")
    return (host or paths.LOBBY_SERVER), int(port or 52711)


@call("getLobbyList")
def get_lobby_list(data):
    # login step 4: handler 0x14009a710 wants data.sv_aid (-> status 6, stored at req+0x18)
    return ok({"sv_aid": "1"})


@call("enterLobby")
def get_nrt_token(data):
    # login step 5: data.sv_token (+ sv_nrt_host / sv_nrt_port) -> status 7. The game then connects to that
    # lobby server; online play = everyone's game pointing at the same one.
    host, port = lobby_server()
    import paths
    token = paths._cfg.get('lobby_token', '')
    if paths.LOBBY_MODE == 'server' and host not in ('127.0.0.1', 'localhost'):
        try:
            if len(token) != 32 or len(bytes.fromhex(token)) != 16:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError('Remote lobby requires the provisioned player token') from None
    else:
        token = '00112233445566778899aabbccddeeff'
    return ok({'sv_token': token, 'sv_nrt_host': host, 'sv_nrt_port': str(port)})


def ranking_response(name, data):
    import roi_rankings
    try:
        if roi_rankings.supported(name, data):
            return ok(roi_rankings.request(relay_url(f'/rankings/{pid()}'),
                                           relay_credential(), name, data))
        return ok(roi_rankings.empty_reply(name, data))
    except ValueError:
        return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}


@call('getRankmatchRanking')
def rankmatch_ranking(data):
    return ranking_response('getRankmatchRanking', data)


@call('getCharaUsageRanking')
def character_usage_ranking(data):
    return ranking_response('getCharaUsageRanking', data)


@call('getCharaRanking')
def character_ranking(data):
    return ranking_response('getCharaRanking', data)


@call('getCircleRanking')
def circle_ranking(data):
    return ranking_response('getCircleRanking', data)


@call('getTagRanking')
def tag_ranking(data):
    return ranking_response('getTagRanking', data)


@call('getGoodRanking')
def good_ranking(data):
    return ranking_response('getGoodRanking', data)


def replay_archive():
    return SAVE_FILE.with_name('replays.sqlite3')


def replay_outbox():
    return SAVE_FILE.with_name('ranked-replay-uploads.sqlite3')



@call('saveReplay')
def save_replay(data):
    import roi_replays
    import sqlite3
    try:
        if isinstance(data,dict) and 'revivalRankedMatchKey' in data:
            import roi_shared_replays
            data=dict(data)
            key=data.pop('revivalRankedMatchKey')
            roi_shared_replays.stage(replay_outbox(),pid(),key,data)
            roi_shared_replays.start(replay_outbox(),pid(),relay_url(f'/replays/{pid()}'),relay_credential())
            # Local archive capacity must not discard an already queued upload.
            try:roi_replays.save(replay_archive(), pid(), data)
            except (OSError,ValueError,sqlite3.Error):pass
        else:
            roi_replays.save(replay_archive(), pid(), data)
        return ok({})
    except (OSError, ValueError, sqlite3.Error):
        return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}


@call('findReplay')
def find_replay(data):
    import roi_replays
    import sqlite3
    try:
        rows=roi_replays.find(replay_archive(), pid(), data)
        import roi_shared_replays
        remote=[]
        try:
            remote=roi_shared_replays.find(relay_url(f'/replays/{pid()}'),relay_credential(),data)
        except (OSError,ValueError,sqlite3.Error):pass
        rows=roi_shared_replays.merge(replay_outbox(),pid(),rows,remote)
        return ok({'sv_replays':rows})
    except (OSError, ValueError, sqlite3.Error):
        return {'result': 'NG', 'time': str(int(time.time())), 'data': {'sv_replays': []}}


@call('loadReplay')
def load_replay(data):
    import roi_replays
    import sqlite3
    try:
        if not isinstance(data, dict) or set(data) != {'sv_replayId'}:
            raise ValueError('Invalid replay load request')
        try:
            row=roi_replays.load(replay_archive(), pid(), data['sv_replayId'])
        except ValueError:
            import roi_shared_replays
            row=roi_shared_replays.load(relay_url(f'/replays/{pid()}'),relay_credential(),data['sv_replayId'])
        return ok(row)
    except (OSError, ValueError, sqlite3.Error):
        return {'result': 'NG', 'time': str(int(time.time())), 'data': {}}


def respond(method, path, headers, body):
    import profile_store
    try:
        try:
            req = json.loads(body.decode("utf-8"), object_pairs_hook=profile_store._strict_object) if body else {}
        except json.JSONDecodeError:
            if not path.endswith('/Dragon/game'):
                raise
            import roi_replays
            req = roi_replays.decode_request(body)
        if not isinstance(req, dict):
            raise ValueError('Expected request object')
    except ValueError:
        return 400, {}, {}
    if path.endswith("/Dragon/authorize"):
        return 200, {"oauth_token": TOKEN, "oauth_token_secret": SECRET}, authorize(req)
    if path.endswith("/Dragon/game"):
        name = req.get("call")
        if type(name) is not str:
            return 400, {}, {}
        fn = CALLS.get(name)
        if fn:
            import progression_contract
            import roi_progression_client
            if name in progression_contract.ACTIONS:
                try:
                    selected=profile_store.read(SAVE_FILE).get('activeChara',1)
                    reply=roi_progression_client.forward(relay_url(f'/progress/{pid()}'),pid(),selected,
                        STATE.get('name','LocalPlayer'),name,req.get('data') or {})
                except (roi_progression_client.Unavailable,ValueError,PermissionError):
                    return 200, {}, {'result':'NG','time':str(int(time.time())),'data':{}}
                if reply is not None:
                    if (name in progression_contract.PUBLISH_ACTIONS
                            or name=='nativeLP' and (req.get('data') or {}).get('action')=='settle_custom'):
                        import threading
                        def publish_committed():
                            try:publish_profile()
                            except (OSError,ValueError):
                                import logging
                                logging.getLogger('roi-profile').warning('Committed progression publication deferred')
                        threading.Thread(target=publish_committed,daemon=True).start()
                    return 200, {}, reply
            if name in {'saveReplay', 'findReplay', 'loadReplay', 'getRankmatchRanking',
                        'getCharaUsageRanking', 'getCharaRanking', 'getCircleRanking',
                        'getTagRanking', 'getGoodRanking'} and not isinstance(req.get('data'), dict):
                return 200, {}, {'result': 'NG', 'time': str(int(time.time())), 'data': {}}
            return 200, {}, fn(req.get("data") or {})
        import circles
        if req.get('call') in circles.UNSUPPORTED | getattr(circles,'NATIVE_GATED',set()):
            return 200, {}, {'result': 'NG', 'time': str(int(time.time())), 'data': {}}
        return 200, {}, ok({})
    return 404, {}, {}
