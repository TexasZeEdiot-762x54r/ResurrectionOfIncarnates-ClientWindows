"""Materialize changed game resources from the installed depot, never download retail files."""
import gzip
import json
import shutil
import struct
import zlib

from roi_update import digest,filehash,path,require,safe_name


def xor(p,delta,row):
    with p.open('r+b') as f,gzip.open(delta,'rb') as src:
        for _ in range(row['runs']):
            at,n=struct.unpack('<QI',src.read(12))
            require(0<n<=65536 and at+n<=row['size'],'Invalid delta range')
            mask=src.read(n)
            require(len(mask)==n,'Incomplete delta')
            f.seek(at)
            bits=int.from_bytes(f.read(n),'little')^int.from_bytes(mask,'little')
            f.seek(at)
            f.write(bits.to_bytes(n,'little'))
        require(not src.read(1),'Trailing delta')


def prepare(root,stage,old,new):
    def release(n,current):
        p=path(stage,n) if current and path(stage,n).is_file() else path(root,n)
        require(filehash(p)==(new if current else old)['files'][n][0],'Damaged recipe: '+n)
        return p
    def spec(current):
        obj=json.loads(release('patch/patch.json',current).read_bytes())
        require(obj['format']==1,'Unknown asset recipe')
        return tuple({safe_name(r['path']):r for r in obj[k]} for k in ('changed','loose'))
    require(old['files']['patch/base-manifest.json']==new['files']['patch/base-manifest.json'],
            'New base depot required; use the manual installer')
    base={r['path']:r for r in json.loads(release('patch/base-manifest.json',False).read_bytes())['files']}
    before,lo=spec(False); after,ln=spec(True)
    todo=[]
    def target(n):
        require(n.startswith(('resource/','exe/')),'Invalid game asset')
        dest=path(stage,'game/'+n)
        dest.parent.mkdir(parents=True,exist_ok=True)
        todo.append('game/'+n)
        return dest
    for n in sorted(before.keys()|after.keys()):
        b,a=before.get(n),after.get(n)
        if a==b: continue
        src=path(root,'game/'+n)
        br=base[n]
        require(filehash(src)==(b['sha256'] if b else br['sha256'].lower()),'Modified base asset: '+n)
        dst=target(n)
        shutil.copyfile(src,dst)
        if b:
            require(b['size']>=br['bytes'],'Cannot reverse truncated old delta; manual rebuild required')
            xor(dst,release('patch/delta/'+safe_name(b['blob']),False),b)
            with dst.open('r+b') as f: f.truncate(br['bytes'])
            require(filehash(dst)==br['sha256'].lower(),'Base reconstruction failed: '+n)
        if a:
            xor(dst,release('patch/delta/'+safe_name(a['blob']),True),a)
            with dst.open('r+b') as f: f.truncate(a['size'])
            require(filehash(dst)==a['sha256'],'Patched asset mismatch: '+n)
    for n in sorted(lo.keys()|ln.keys()):
        b,a=lo.get(n),ln.get(n)
        if a==b: continue
        require(n not in base and n.startswith('resource/'),'Loose recipe overlaps original game')
        src=path(root,'game/'+n)
        require(not src.exists() or (b and filehash(src)==b['sha256']), 'Loose asset conflict: '+n)
        dst=target(n)
        if not a: continue
        archive=safe_name(a['archive'])
        require(archive in base and 0<=a['length']<=64*1024**2,'Invalid archive span')
        p=path(stage,'game/'+archive)
        with (p if p.exists() else path(root,'game/'+archive)).open('rb') as f:
            f.seek(a['offset'])
            data=f.read(a['length'])
        if a['unpack']:
            off=data.find(b'BILZ',0,256)
            if off>=0:
                data=zlib.decompress(data[off+16:])
        require(digest(data)==a['sha256'],'Loose resource mismatch: '+n)
        dst.write_bytes(data)
    return todo
