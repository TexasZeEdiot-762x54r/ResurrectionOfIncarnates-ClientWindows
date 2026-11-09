"""Local experimental Lamina state, not recovered original progression.

Only imports the standard library and profile_store; no services or HTTP API.
Stores accept a pathlib.Path (atomic replacement) or profile_store.Document
(the caller owns the enclosing transaction). The shared RLock covers the entire
read/modify/write operation; it does not provide cross-process serialization.

Schema under PROFILE_KEY, version 1:
    schema_version: 1
    test_mode: bool (default True: all 15 Laminas and variants 0, 1, 2 enabled)
    policy: {thresholds: [], variant_unlock_levels: None}
    selected_id: integer 1..15
    laminas: {"1".."15": {xp: integer, variant: 0..2, color: 0..255}}
    events: [{match_id: string, event_id: string, lamina_id: int, variant: int}]

XP is cumulative, one per externally confirmed summon. Empty thresholds record
XP only, with a protocol baseline level of 1. Each explicitly supplied positive
threshold advances one level; variant_unlock_levels optionally gives the three
required levels. These are experimental policies, never historical claims.
Color is an opaque local preference; its byte-sized bound is not a claim about
the game's supported palette. Preferences are remembered separately per Lamina.

Event identity is the (match_id, event_id) pair, across all Laminas. A conflicting
reuse fails. The ledger never evicts identities: at MAX_EVENTS, new events fail
closed, while exact retries remain harmless. Do not clear it to reclaim space
unless creating a genuinely separate experimental profile/session namespace.
"""

from bisect import bisect_right
from copy import deepcopy
import re

import profile_store


PROFILE_KEY = "lamina_experimental"
SCHEMA_VERSION = 1
LAMINA_NAMES = (
    "Golem", "Gorgon", "Yazd", "Loa", "Rakshasa", "Sylph", "Manticore",
    "Rukh", "Spriggan", "Salamander", "Griffin", "A Bao A Qu", "Ifrit",
    "Dullahan", "Siren",
)
LAMINA_IDS = tuple(range(1, 16))
VARIANTS = (0, 1, 2)
PERMANENT = "9999999999999"
MAX_EVENTS = 4096
MAX_ID_LENGTH = 128
MAX_THRESHOLDS = 255
MAX_XP = 2**31 - 1
MAX_COLOR = 255
_EVENT_ID = re.compile(r"[A-Za-z0-9_.:-]+", re.ASCII)


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return value


def _keys(value, expected, name):
    if type(value) is not dict or value.keys() != set(expected):
        raise ValueError(f"Invalid {name} fields")


def _event_id(value, name):
    if (type(value) is not str or not 1 <= len(value) <= MAX_ID_LENGTH
            or _EVENT_ID.fullmatch(value) is None):
        raise ValueError(f"{name} must be 1..{MAX_ID_LENGTH} ASCII ID characters")
    return value


def _policy(value, test_mode):
    _keys(value, ("thresholds", "variant_unlock_levels"), "policy")
    thresholds = value["thresholds"]
    if type(thresholds) is not list or len(thresholds) > MAX_THRESHOLDS:
        raise ValueError("thresholds must be a bounded list")
    previous = 0
    for threshold in thresholds:
        _integer(threshold, 1, MAX_XP, "threshold")
        if threshold <= previous:
            raise ValueError("thresholds must be strictly increasing")
        previous = threshold
    unlocks = value["variant_unlock_levels"]
    if unlocks is None:
        if not test_mode:
            raise ValueError("Non-test mode requires an explicit variant unlock policy")
        return
    if type(unlocks) is not list or len(unlocks) != len(VARIANTS):
        raise ValueError("variant_unlock_levels must contain exactly three levels")
    for required in unlocks:
        _integer(required, 1, len(thresholds) + 1, "variant unlock level")
    if unlocks[0] != 1 or unlocks != sorted(unlocks):
        raise ValueError("Variant 0 must start at level 1; unlock levels cannot decrease")


def _default():
    return {
        "schema_version": SCHEMA_VERSION,
        "test_mode": True,
        "policy": {"thresholds": [], "variant_unlock_levels": None},
        "selected_id": 1,
        "laminas": {str(i): {"xp": 0, "variant": 0, "color": 0} for i in LAMINA_IDS},
        "events": [],
    }


def _level(value, lamina_id):
    return 1 + bisect_right(value["policy"]["thresholds"],
                            value["laminas"][str(lamina_id)]["xp"])


def _unlocked(value, lamina_id, variant):
    return (value["test_mode"] or _level(value, lamina_id)
            >= value["policy"]["variant_unlock_levels"][variant])


def _validate(value):
    _keys(value, ("schema_version", "test_mode", "policy", "selected_id",
                  "laminas", "events"), "Lamina state")
    _integer(value["schema_version"], SCHEMA_VERSION, SCHEMA_VERSION, "schema_version")
    if type(value["test_mode"]) is not bool:
        raise ValueError("test_mode must be boolean")
    _policy(value["policy"], value["test_mode"])
    _integer(value["selected_id"], 1, 15, "selected_id")
    _keys(value["laminas"], (str(i) for i in LAMINA_IDS), "laminas")
    for lamina_id in LAMINA_IDS:
        entry = value["laminas"][str(lamina_id)]
        _keys(entry, ("xp", "variant", "color"), "Lamina entry")
        _integer(entry["xp"], 0, MAX_XP, "xp")
        _integer(entry["variant"], 0, 2, "variant")
        _integer(entry["color"], 0, MAX_COLOR, "color")
        if not _unlocked(value, lamina_id, entry["variant"]):
            raise ValueError("A saved variant is locked by the experimental policy")
    events = value["events"]
    if type(events) is not list or len(events) > MAX_EVENTS:
        raise ValueError("Event ledger exceeds its bounded capacity")
    seen = set()
    for event in events:
        _keys(event, ("match_id", "event_id", "lamina_id", "variant"), "event")
        pair = (_event_id(event["match_id"], "match_id"),
                _event_id(event["event_id"], "event_id"))
        _integer(event["lamina_id"], 1, 15, "lamina_id")
        _integer(event["variant"], 0, 2, "variant")
        if pair in seen:
            raise ValueError("Duplicate identity in event ledger")
        seen.add(pair)


def state(profile):
    """Return a validated detached schema snapshot from a profile dict.

    An absent namespace returns defaults without mutating or saving anything.
    Malformed or unsupported saved state raises ValueError, never resets XP.
    """
    if type(profile) is not dict:
        raise ValueError("profile must be a dict")
    if PROFILE_KEY not in profile:
        return _default()
    value = profile[PROFILE_KEY]
    _validate(value)
    return deepcopy(value)


def configure(store, *, test_mode=True, thresholds=None, variant_unlock_levels=None):
    """Replace the experimental policy, preserving XP, events and preferences.

    None thresholds means [], not an original XP curve. Explicit thresholds are
    cumulative XP needed for levels 2 onward. In non-test mode, supply all three
    variant unlock levels. All 15 base Laminas remain available. A policy that
    would lock a remembered variant is rejected; select an allowed one first.
    """
    policy = {"thresholds": thresholds if thresholds is not None else [],
              "variant_unlock_levels": variant_unlock_levels}
    if type(test_mode) is not bool:
        raise ValueError("test_mode must be boolean")
    _policy(policy, test_mode)
    policy = deepcopy(policy)
    with profile_store.LOCK:
        value = state(profile_store.read(store))
        value["test_mode"] = test_mode
        value["policy"] = policy
        _validate(value)
        profile_store.update(store, {PROFILE_KEY: value})
        return deepcopy(value)


def select(store, lamina_id, variant, *, color=None):
    """Select ID 1..15 / combat variant 0..2; optionally set its local color.

    Omitting color preserves that Lamina's previous color. Returns saved state.
    This changes only the experimental namespace, not native game loadouts.
    """
    _integer(lamina_id, 1, 15, "lamina_id")
    _integer(variant, 0, 2, "variant")
    if color is not None:
        _integer(color, 0, MAX_COLOR, "color")
    with profile_store.LOCK:
        value = state(profile_store.read(store))
        if not _unlocked(value, lamina_id, variant):
            raise ValueError("Variant is locked by the experimental policy")
        value["selected_id"] = lamina_id
        entry = value["laminas"][str(lamina_id)]
        entry["variant"] = variant
        if color is not None:
            entry["color"] = color
        profile_store.update(store, {PROFILE_KEY: value})
        return deepcopy(value)


def record_summon(store, lamina_id, match_id, event_id, *, validated=False, variant=0):
    """Record one confirmed summon, awarding exactly 1 experimental XP once.

    Only a trusted local caller may assert validated=True after independently
    checking the summon, Lamina, variant and IDs. This flag is not proof or
    authentication. Selection/UI attempts are not confirmed summons. Variant
    describes the actual event (default 0), not a mutable current selection.

    Returns {recorded, xp_awarded, lamina_id, xp, level}. Exact retries do not
    write; reused identities with a different Lamina/variant raise ValueError.
    At ledger or XP capacity, raises OverflowError without changing the store.
    """
    if validated is not True:
        raise ValueError("A trusted caller must explicitly validate the summon")
    _integer(lamina_id, 1, 15, "lamina_id")
    _integer(variant, 0, 2, "variant")
    _event_id(match_id, "match_id")
    _event_id(event_id, "event_id")
    event = {"match_id": match_id, "event_id": event_id,
             "lamina_id": lamina_id, "variant": variant}
    with profile_store.LOCK:
        value = state(profile_store.read(store))
        entry = value["laminas"][str(lamina_id)]
        for previous in value["events"]:
            if (previous["match_id"], previous["event_id"]) == (match_id, event_id):
                if previous != event:
                    raise ValueError("Event identity reused with conflicting summon data")
                return {"recorded": False, "xp_awarded": 0, "lamina_id": lamina_id,
                        "xp": entry["xp"], "level": _level(value, lamina_id)}
        if not _unlocked(value, lamina_id, variant):
            raise ValueError("Summoned variant is locked by the experimental policy")
        if len(value["events"]) >= MAX_EVENTS:
            raise OverflowError("Event ledger full; identities cannot be safely evicted")
        if entry["xp"] >= MAX_XP:
            raise OverflowError("Experimental XP capacity reached")
        entry["xp"] += 1
        value["events"].append(event)
        profile_store.update(store, {PROFILE_KEY: value})
        return {"recorded": True, "xp_awarded": 1, "lamina_id": lamina_id,
                "xp": entry["xp"], "level": _level(value, lamina_id)}


def profile_rows(profile):
    """Return 15 ordered protocol rows; exactly four fields, all string values.

    EnableTerm matches the existing permanent-ownership convention. Combat
    variants and color remain local state; level is not a variant encoding.
    """
    value = state(profile)
    return [{"sv_supLamId": str(i), "sv_supLamLv": str(_level(value, i)),
             "sv_supLamExp": str(value["laminas"][str(i)]["xp"]),
             "sv_supLamEnableTerm": PERMANENT} for i in LAMINA_IDS]
