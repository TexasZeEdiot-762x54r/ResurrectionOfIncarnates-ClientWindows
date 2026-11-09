"""Verify or assemble the clean retail client from the user's two Steam inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import shutil


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def contained(root, name):
    p = PureWindowsPath(name)
    if (p.is_absolute() or p.drive or len(p.parts) < 2 or p.parts[0] not in {'exe', 'resource'}
            or '..' in p.parts or ':' in name):
        raise ValueError(f'Invalid manifest path: {name}')
    file = root.joinpath(*p.parts)
    if not file.resolve().is_relative_to(root):
        raise ValueError(f'Path escapes installation: {name}')
    return file


def build(roots, files, destination):
    roots = [r.resolve(strict=True) for r in roots]
    if destination is not None:
        destination = destination.resolve()
        if destination.exists() or any(destination.is_relative_to(r) or r.is_relative_to(destination) for r in roots):
            raise ValueError('Choose a new destination outside both Steam inputs.')
    plan = {}
    seen = set()
    for row in files:
        name = row['path']
        if name.casefold() in seen:
            raise ValueError(f'Duplicate manifest path: {name}')
        seen.add(name.casefold())
        for root in roots:
            file = contained(root, name)
            if file.is_file() and file.stat().st_size == row['bytes'] and digest(file) == row['sha256'].lower():
                plan[name] = (file, row['sha256'].lower())
                break
        else:
            raise ValueError(f'Neither Steam input has the verified original: {name}')
    if destination is not None:
        size = sum(row['bytes'] for row in files)
        if shutil.disk_usage(destination.parent).free < size:
            raise ValueError(f'Need at least {size} free bytes at the destination.')
        destination.mkdir()
        for name, (file, expected) in plan.items():
            result = contained(destination, name)
            result.parent.mkdir(parents=True, exist_ok=True)
            with file.open('rb') as src, result.open('xb') as dst:
                shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
            if digest(result) != expected:
                raise ValueError(f'Copy verification failed: {name}. Destination is incomplete.')
    return len(plan)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steam', type=Path, required=True)
    parser.add_argument('--depot', type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--output', type=Path, help='New directory under an existing parent')
    args = parser.parse_args()
    manifest = Path(__file__).with_name('base-manifest.json')
    try:
        count = build([args.steam, args.depot], json.loads(manifest.read_text('utf-8'))['files'], args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'Base game verification failed: {exc}{os.linesep}')
    print(f'Verified {count} original files' + (f' in {args.output}' if args.output else ' in the Steam inputs'))
