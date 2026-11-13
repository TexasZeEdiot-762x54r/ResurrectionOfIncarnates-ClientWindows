"""Read this installation's local update receipt, never the newest version on the web."""
import base64
import json
from pathlib import Path


def label(root=None):
    root=Path(root) if root is not None else Path(__file__).resolve().parents[1]
    def read(path):
        with path.open('rb') as f: raw=f.read(262145)
        if len(raw)>262144: raise ValueError('Oversized receipt')
        return json.loads(raw)
    try:
        base=read(root/'roi-update-base.json')
        try: installed=read(root/'.roi-update/installed.json')
        except FileNotFoundError: installed=base['release']
        data=json.loads(base64.b64decode(installed['payload'],validate=True))
        rev=data['revision']
        if (data['game']=='rise-of-incarnates/2.06' and data['format']==1
                and data['stream']==base['stream'] and type(rev) is int and 1<=rev<=999999999):
            return f'2.06 - R{rev}'
    except (OSError,KeyError,TypeError,ValueError,RecursionError):
        pass
    return None
