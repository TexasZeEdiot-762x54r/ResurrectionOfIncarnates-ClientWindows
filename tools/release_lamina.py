"""Local UI mirror of the last served account entitlement, not an authorization credential."""
import json
import os
from pathlib import Path
import threading


HERE = Path(__file__).resolve().parent
FILE = HERE/'lamina-access.local.json'
LOCK = threading.Lock()


def publish(saved):
    import release_policy
    available=release_policy.lamina_allowed(saved)
    with LOCK:
        if not FILE.is_file() or available != allowed():
            tmp=FILE.with_suffix('.tmp')
            tmp.write_text(json.dumps({'allowed':available}),encoding='ascii')
            os.replace(tmp,FILE)
    return available


def allowed():
    try:
        return json.loads(FILE.read_text('ascii')) == {'allowed':True}
    except (OSError,ValueError):
        return False
