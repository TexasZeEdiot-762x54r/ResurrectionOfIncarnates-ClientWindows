"""Rebuild the experimental assets from a verified base, into a NEW directory."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import zlib
from base_game import build, contained, digest


def unpack(raw):
    pos = raw.find(b'BILZ', 0, 256)
    if pos >= 0:
        try:
            return zlib.decompress(raw[pos + 16:])
        except zlib.error:
            pass
    return raw


def install(base, output, package):
    base, package = base.resolve(strict=True), package.resolve(strict=True)
    files = json.loads((package/'base-manifest.json').read_text())['files']
    plan = json.loads((package/'patch.json').read_text())
    if plan['format'] != 1:
        raise ValueError('Unsupported patch')
    build([base], files, output)
    output = output.resolve()
    for item in plan['changed']:
        dest = contained(output, item['path'])
        with dest.open('r+b') as f, gzip.open(package/'delta'/item['blob'], 'rb') as patch:
            for _ in range(item['runs']):
                at, length = struct.unpack('<QI', patch.read(12))
                if length > 65536 or at + length > item['size']:
                    raise ValueError('Invalid patch bounds')
                f.seek(at)
                original = int.from_bytes(f.read(length), 'little')
                mask = patch.read(length)
                if len(mask) != length:
                    raise ValueError('Truncated patch')
                f.seek(at)
                f.write((original ^ int.from_bytes(mask, 'little')).to_bytes(length, 'little'))
            f.truncate(item['size'])
            if patch.read(1):
                raise ValueError('Trailing patch bytes')
        if digest(dest) != item['sha256']:
            raise ValueError('Experimental file mismatch: ' + item['path'])
    for item in plan['loose']:
        src, dest = contained(output, item['archive']), contained(output, item['path'])
        with src.open('rb') as f:
            f.seek(item['offset'])
            raw = f.read(item['length'])
        raw = unpack(raw) if item['unpack'] else raw
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Loose resource mismatch: '+item['path'])
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('xb') as f:
            f.write(raw)
    return len(plan['changed']), len(plan['loose'])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print('Verified changed files and reconstructed resources:', install(args.base, args.output, Path(__file__).parent))
