"""Display names from the local Steam session, cached briefly per process."""
import time
_cache = {}

def get(uid):
    uid = int(uid)
    if not 0 < uid < 2 ** 64:
        return 'Player'
    cached = _cache.get(uid)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1]
    name = ''
    try:
        import steam_dir
        directory = steam_dir.get()
        # Also supports a server started before persona_name was added.
        if hasattr(directory, 'persona_name'):
            name = directory.persona_name(uid)
        else:
            def job():
                directory.api.SteamFriends.restype = steam_dir.c_vp
                friends = directory.api.SteamFriends()
                if not friends:
                    return ''
                raw = steam_dir._vcall(friends, 7, steam_dir.c_char_p, (steam_dir.c_u64, uid))
                result = (raw or b'').decode('utf-8', 'replace')
                return '' if result == '[unknown]' else result
            name = directory._do(job, timeout=2)
    except (OSError, AttributeError, ValueError, ImportError):
        pass
    name = name.replace('\0', '') if name else 'Player ' + str(uid)[-4:]
    if len(_cache) >= 512 and uid not in _cache:
        _cache.pop(next(iter(_cache)))
    _cache[uid] = (now + (60 if name and not name.startswith('Player ') else 5), name[:255])
    return name
