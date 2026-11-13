"""Pre-launch cumulative updater. No third-party Python packages required."""
import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

URL = 'https://riseofincarnates.artificerreviver.com/updates/v1/'
KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBoZJQqy/R+3QhWQKi4YNT45ykECfr4C3dneehDyD1TE'
STREAMS = {'dev/windows', 'dev/linux', 'dev/workbench-windows', 'stable/windows', 'stable/linux'}
MAX = 16 * 1024 * 1024
BASE = 'roi-update-base.json'
STATE = '.roi-update'


def require(ok, msg):
    if not ok:
        raise ValueError(msg)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def filehash(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for part in iter(lambda: f.read(1048576), b''):
            h.update(part)
    return h.hexdigest()


def safe_name(n):
    # No device names, ADS, case aliases, or application-owned data directories.
    parts = n.split('/')
    require(all(re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', p)
                and not p.endswith('.') and not re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])',
                p.split('.')[0],re.I) for p in parts), 'Unsafe file name')
    return n


def path(root, n):
    p = root
    for s in n.split('/'):
        require(s not in ('', '.', '..') and '/' not in s and '\\' not in s and ':' not in s, 'Unsafe path')
        p = p / s
        if p.is_symlink() or (p.exists() and getattr(p.lstat(), 'st_file_attributes', 0) & 1024):
            raise ValueError('Refusing linked path: ' + n)
    return p


def managed(n):
    return (n == 'RELEASE-FILES.json' or n in ('README.md','FRIEND-TEST.md','setup_client.py',
            'tools/play.ps1','tools/RELEASE-CLIENT','tools/award-policy.json','tools/circle-voice-asset.json',
            'tools/customize-labels.json','tools/customize-parts.json','tools/customize-thumb-plan.json',
            'tools/grid-reward-policy.json','tools/lamina_abilities.json','tools/recovered-skillcube-master.json',
            'tools/revival-five-block-master.json','tools/lp-payouts.enabled','tools/lamina-peer-trace.enabled',
            'tools/lamina-combat.enabled','tools/replay-recording.enabled','tools/runtime-lock.json')
            or bool(re.fullmatch(r'tools/(?:sessions/)?[A-Za-z0-9_]+\.py', n))
            or bool(re.fullmatch(r'patch/(?:[a-z_]+\.py|base-manifest\.json|patch\.json|delta/[0-9]+\.xor\.gz)', n)))


def save(p, obj):
    tmp = p.with_name(p.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(obj, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def verify(raw, key=KEY):
    envelope = json.loads(raw)
    msg = base64.b64decode(envelope['payload'], validate=True)
    sig = base64.b64decode(envelope['signature'], validate=True)
    exe = (Path(os.environ['SystemRoot'])/'System32/OpenSSH/ssh-keygen.exe'
           if os.name == 'nt' else Path('/usr/bin/ssh-keygen'))
    with tempfile.TemporaryDirectory() as tmp:
        allowed, signature = Path(tmp)/'signers', Path(tmp)/'sig'
        allowed.write_bytes(('roi '+key+'\n').encode('ascii'))
        signature.write_bytes(sig)
        result = subprocess.run([str(exe), '-Y','verify','-f',str(allowed),'-I','roi','-n',
                                 'roi-client-update-v1','-s',str(signature)], input=msg,
                                capture_output=True, timeout=8)
        require(result.returncode == 0, 'Release signature is not trusted')
    return json.loads(msg)


def validate(m, stream):
    require(stream in STREAMS and m['stream']==stream and m['format']==1 and
            m['game']=='rise-of-incarnates/2.06', 'Wrong feed, game, or updater format')
    require(type(m['revision']) is int and m['revision']>=1 and isinstance(m['notes'],str)
            and len(m['notes'])<=3000, 'Invalid revision')
    files = m['files']
    require(isinstance(files,dict) and 0<len(files)<=2000, 'Invalid inventory')
    require(len({n.lower() for n in files})==len(files), 'Duplicate paths')
    total=0
    for n,(h,size) in files.items():
        require(managed(safe_name(n)) and re.fullmatch('[a-f0-9]{64}',h) and type(size) is int
                and 0<=size<=MAX, 'Unexpected release file: '+n)
        total+=size
    require(total<=256*1024*1024,'Oversized release')
    return m


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):
        raise ValueError('Unexpected update redirect')


def get(name, limit, timeout):
    if os.name=='nt':
        # Use inbox curl/Schannel: Windows builds the chain against its native trust store.
        exe=Path(os.environ['SystemRoot'])/'System32/curl.exe'
        result=subprocess.run([str(exe),'-q','--proto','=https','--fail','--silent','--show-error',
            '--max-time',str(timeout),'--max-filesize',str(max(1,limit)),'--write-out','%{http_code}',
            URL+name],capture_output=True,timeout=timeout+2)
        require(result.returncode==0 and result.stdout[-3:]==b'200','HTTPS update download failed')
        data=result.stdout[:-3]
        require(len(data)<=limit,'Oversized update response')
        return data
    opener=urllib.request.build_opener(NoRedirect())
    with opener.open(URL+name,timeout=timeout) as r:
        require(r.status==200,'Update server did not return a file')
        data=r.read(limit+1)
    require(len(data)<=limit,'Oversized update response')
    return data


def ask(text):
    if os.name=='nt':
        import ctypes
        return ctypes.windll.user32.MessageBoxW(None,text,'Rise of Incarnates update',0x24)==6
    if shutil.which('zenity'):
        return subprocess.run(['zenity','--question','--no-markup','--title=Rise of Incarnates update',
            '--ok-label=Update and play','--cancel-label=Not now','--text='+text]).returncode==0
    if shutil.which('kdialog'):
        return subprocess.run(['kdialog','--yesno',text]).returncode==0
    if sys.stdin.isatty():
        return input(text+' [y/N] ').lower() in ('y','yes')
    print(text+' Run python3 roi_update.py --apply to install.')
    return False


@contextlib.contextmanager
def lock(p):
    with p.open('a+b') as f:
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def recover(root, work):
    journal=work/'journal.json'
    if journal.exists():
        for n,existed in reversed(json.loads(journal.read_text())):
            require(n==STATE+'/installed.json' or managed(safe_name(n)) or game_path(n), 'Bad recovery path')
            dest=path(root,n)
            backup=path(work/'backup',n)
            if existed and backup.is_file():
                os.replace(backup,dest)
            elif not existed and dest.exists():
                dest.unlink()
        journal.unlink()
    for name in ('stage','backup'):
        p=path(work,name)
        if p.exists():
            shutil.rmtree(p)


def game_path(n):
    return n.startswith(('game/exe/','game/resource/')) and safe_name(n)==n


def update(root,mode,fetch=get,confirm=ask,verifier=verify,platform=None):
    base=json.loads((root/BASE).read_text())
    stream=base['stream']
    platform=platform or ('windows' if os.name=='nt' else 'linux')
    require(stream in STREAMS and stream.split('/')[1] in (platform,'workbench-'+platform),'Wrong platform')
    work=path(root,STATE)
    work.mkdir(exist_ok=True)
    with lock(path(work,'lock')):
        recover(root,work)
        installed=path(work,'installed.json')
        old=validate(verifier(installed.read_bytes() if installed.exists()
                              else json.dumps(base['release']).encode()),stream)
        cfg=json.loads((root/'tools/config.json').read_text(encoding='utf-8-sig'))
        # Unknown/offline installs never silently contact the Internet.
        if not ((cfg.get('release_mode')=='internet' and cfg.get('public_lobby_host')=='riseofincarnates.artificerreviver.com')
                or (stream=='dev/workbench-windows' and cfg.get('lobby_transport')=='experimental-local')):
            print('Public updates disabled for this installation.')
            return
        try:
            raw=fetch(stream+'/latest.json',2*1024*1024,3)
            new=validate(verifier(raw),stream)
            require(type(new['expires']) is int and new['expires']>time.time(),'Expired update metadata')
            require(new['revision']>=old['revision'],'Older release rejected')
            if new['revision']==old['revision']:
                require(old['files']==new['files'],'Conflicting content at installed revision')
                print('Up to date: 2.06 - R'+str(old['revision']))
                return
            changed={}
            for n,row in new['files'].items():
                p=path(root,n)
                local=filehash(p) if p.exists() else None
                if local==row[0]: continue
                require(local is None or (n in old['files'] and local==old['files'][n][0]),
                        'Local file differs; refusing to overwrite '+n)
                changed[n]=row
            removed=[]
            for n in old['files'].keys()-new['files'].keys():
                p=path(root,n)
                if p.exists():
                    require(filehash(p)==old['files'][n][0],'Locally modified obsolete file: '+n)
                    removed.append(n)
            total=sum(v[1] for v in changed.values())
            text=f"2.06 - R{new['revision']} is available (installed R{old['revision']}).\nDownload: {total:,} bytes.\n\n{new['notes']}\n\nUpdate before playing?"
            if mode=='check' or (mode!='apply' and not confirm(text)):
                print(text)
                return
            if os.name=='nt':
                running=subprocess.check_output([str(Path(os.environ['SystemRoot'])/'System32/tasklist.exe'),
                    '/FI','IMAGENAME eq roi.exe','/NH'],timeout=10)
                require(b'roi.exe' not in running.lower(),'Close the game before updating')
            elif Path('/proc').exists():
                for p in Path('/proc').glob('[0-9]*/comm'):
                    try: name=p.read_text().strip().lower()
                    except (OSError,UnicodeError): continue
                    require(name!='roi.exe','Close the game before updating')
            stage=work/'stage'
            stage.mkdir()
            for n,(h,size) in changed.items():
                data=fetch('objects/'+h,size,30)
                require(len(data)==size and digest(data)==h,'Invalid download: '+n)
                dest=path(stage,n)
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(data)
            todo=list(changed)+removed
            if old['files'].get('patch/patch.json')!=new['files'].get('patch/patch.json'):
                from roi_update_assets import prepare
                todo+=prepare(root,stage,old,new)
            if old['files'].get('tools/runtime-lock.json')!=new['files'].get('tools/runtime-lock.json'):
                raise ValueError('New Python runtime required; use the manual installer')
            marker=STATE+'/installed.json'
            (stage/STATE).mkdir()
            (stage/marker).write_bytes(raw)
            todo.append(marker)
            ops=[(n,path(root,n).exists()) for n in todo]
            save(work/'journal.json',ops)
            for n,exists in ops:
                dest=path(root,n)
                if exists:
                    backup=path(work/'backup',n)
                    backup.parent.mkdir(parents=True,exist_ok=True)
                    os.replace(dest,backup)
                source=path(stage,n)
                if source.exists():
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    os.replace(source,dest)
            (work/'journal.json').unlink()
            print('Installed 2.06 - R'+str(new['revision']))
        finally:
            recover(root,work)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    for flag in ('launch','check','apply'):
        group.add_argument('--'+flag,action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    try:
        # -I isolates untrusted import paths. Only this installation's bootstrap modules are used.
        sys.path.insert(0,str(root))
        update(root,'launch' if args.launch else 'apply' if args.apply else 'check')
    except Exception as exc:
        print('Update unavailable: '+str(exc),file=sys.stderr)
        # Never launch through an incomplete recovery, or alongside another update.
        if (root/STATE/'journal.json').exists() or not args.launch or isinstance(exc,BlockingIOError):
            return 1
        if isinstance(exc,OSError) and getattr(exc,'errno',None) in (11,13):
            return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
