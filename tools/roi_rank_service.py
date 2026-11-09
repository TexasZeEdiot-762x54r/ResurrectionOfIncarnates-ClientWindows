"""Read rank from the public authority; never fall back to a local ledger."""
import json
import re
import threading
import time
import urllib.request

LOCK = threading.Lock()
CACHE = {}


def validate(value, uid):
    legacy = {'sv_uid','sv_rankId','sv_rankPoint','sv_revision'}
    streaks = {'sv_winStreakCount','sv_winStreakMax'}
    if not isinstance(value, dict) or set(value) not in (legacy, legacy | streaks) or value['sv_uid'] != uid:
        raise ValueError('Invalid server rank identity')
    value = dict(sv_winStreakCount='0',sv_winStreakMax='0',**value) if set(value)==legacy else value
    for name, minimum, maximum in (('sv_rankId',1,30), ('sv_rankPoint',0,2147483647), ('sv_revision',0,2**63-1),
                                   ('sv_winStreakCount',0,2147483647), ('sv_winStreakMax',0,2147483647)):
        raw = value[name]
        if not isinstance(raw,str) or len(raw)>20 or not re.fullmatch(r'0|[1-9][0-9]*',raw) or not minimum<=int(raw)<=maximum:
            raise ValueError('Invalid server rank value')
    if int(value['sv_winStreakCount']) > int(value['sv_winStreakMax']):
        raise ValueError('Current streak exceeds best streak')
    return {key:value[key] for key in ('sv_rankId','sv_rankPoint','sv_winStreakCount','sv_winStreakMax')}


def profile_fields(url, token, uid):
    uid = str(uid)
    key = (url, uid, token)
    with LOCK:
        cached = CACHE.get(key)
        if cached and time.monotonic() < cached[0]:
            return dict(cached[1])
    value = dict(sv_rankId='1', sv_rankPoint='0', sv_winStreakCount='0', sv_winStreakMax='0')
    headers = {'Authorization':'Bearer '+token} if token else {}
    headers['X-ROI-Rank-Fields'] = 'streaks-v1'
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(url, headers=headers)
        with opener.open(request, timeout=1.5) as response:
            raw = response.read(1025)
        if len(raw)>1024:
            raise ValueError('Rank response too large')
        value = validate(json.loads(raw), uid)
    except (OSError,ValueError,TypeError,RecursionError):
        pass  # Unavailable server never makes edited local points authoritative.
    with LOCK:
        CACHE.clear()
        CACHE[key] = (time.monotonic()+1, dict(value))
    return value
