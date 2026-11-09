import hashlib
import json
from pathlib import Path


def verify(root):
    root = root.resolve()
    for n,h in json.loads((root/'RELEASE-FILES.json').read_text()).items():
        p=(root/n).resolve()
        if not p.is_relative_to(root) or hashlib.sha256(p.read_bytes()).hexdigest()!=h:
            raise ValueError('Release file differs: '+n)


if __name__=='__main__':
    verify(Path(__file__).resolve().parents[1])
    print('All packaged files verified.')
