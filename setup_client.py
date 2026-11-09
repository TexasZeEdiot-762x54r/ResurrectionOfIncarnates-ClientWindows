"""Set up a separate experimental client. Run from a fresh checkout or extracted copy."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parent
TOOLS = ROOT/'tools'
sys.path.insert(0,str(ROOT/'patch'))
from base_game import build, digest
from asset_patch import install

PUBLIC = 'riseofincarnates.artificerreviver.com'
PIN = 'fc47097a9b167799a4dde9c6610c6c3d84699df7c96442a6dfa9968abbc4a033'


def unpack(entry, dst):
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp)/entry['file']
        cached = ROOT/'vendor'/entry['file']
        if cached.is_file():
            shutil.copyfile(cached,archive)
        else:
            with urllib.request.urlopen(entry['url'],timeout=45) as src,archive.open('xb') as out:
                shutil.copyfileobj(src,out)
        if digest(archive) != entry['sha256']:
            raise ValueError('Runtime hash mismatch: '+entry['file'])
        dst.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            for f in z.infolist():
                if not (dst/f.filename).resolve().is_relative_to(dst.resolve()):
                    raise ValueError('Invalid vendor path')
            z.extractall(dst)


def config(args):
    host = args.host or (PUBLIC if args.mode == 'internet' else '')
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?',host):
        raise ValueError('Provide the gateway hostname or address without a URL scheme.')
    pin = args.pin or (PIN if args.mode == 'internet' and host==PUBLIC else '')
    if not re.fullmatch('[0-9a-fA-F]{64}',pin):
        raise ValueError('Get the 64-digit TLS certificate fingerprint directly from your operator.')
    if args.mode=='vpn' and host==PUBLIC:
        raise ValueError('Use your private gateway endpoint in VPN mode, not the public service.')
    if args.mode=='lan':
        addr=ipaddress.IPv4Address(host)
        if not any(addr in ipaddress.ip_network(net) for net in
                   ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8')):
            raise ValueError('LAN requires an explicit private IPv4 address, never the public server.')
    return host,pin.lower()


def setup(args):
    sys.path.insert(0,str(TOOLS))
    from verify_release import verify
    verify(ROOT)
    if sys.version_info<(3,10) or sys.maxsize < 2**32:
        raise ValueError('Use 64-bit Python 3.10 or later.')
    host,pin=config(args)
    if os.name!='nt' and os.getuid()==0:
        raise ValueError('Do not install the client as root')
    game = ROOT/'game'
    if (game.exists() or (TOOLS/'config.json').exists() or (TOOLS/'player_profile.json').exists()
            or (TOOLS/'account-auth.json').exists()):
        raise ValueError('Use a fresh checkout; do not install over a client, credential or save.')
    files = json.loads((ROOT/'patch/base-manifest.json').read_text())['files']
    if args.steam is None and args.base is None:
        raise ValueError('--depot requires --steam; a complete recovery copy needs only --base.')
    steam = (args.steam or args.base).resolve(strict=True)
    if args.base:
        install(args.base,game,ROOT/'patch')
    else:
        if not args.depot:
            raise ValueError('Pass --base for an offline copy or --depot for the downloaded assets.')
        with tempfile.TemporaryDirectory(prefix='roi-clean-',dir=ROOT) as tmp:
            base = Path(tmp)/'base'
            build([steam,args.depot],files,base)
            install(base,game,ROOT/'patch')
    lock=json.loads((TOOLS/'runtime-lock.json').read_text())
    libs=TOOLS/('libs' if os.name=='nt' else 'win-libs')
    for wheel in lock['windows_wheels']:
        unpack(wheel,libs)
    if os.name!='nt':
        if os.getuid()==0:
            raise ValueError('Do not install the client as root')
        unpack(lock['python_windows'],TOOLS/'python-win')
        venv=ROOT/'.venv'
        subprocess.run([sys.executable,'-m','venv',str(venv)],check=True)
        subprocess.run([str(venv/'bin/python'),'-m','pip','install',*lock['linux_requirements']],check=True)
    cfg=dict(game=str(game),steam=str(steam),depot=str((args.depot or args.base).resolve()),
             libs=str(TOOLS/'libs'),libs_windows=str(libs),lobby_mode='server',lobby_transport='tls-public',
             lobby_listen='127.0.0.1',lobby_server='127.0.0.1:52711',public_lobby_host=host,
             release_mode=args.mode)
    if os.name!='nt':
        cfg['game_windows']='Z:'+str(game).replace('/','\\')
    (TOOLS/'config.json').write_text(json.dumps(cfg,indent=2),encoding='utf-8')
    (TOOLS/'public-lobby.json').write_text(json.dumps(dict(internet_host=host,lan_host=host,port=args.port,
                                          certificate_sha256=pin)),encoding='utf-8')
    (ROOT/'.cachyos-ready.json').write_text(json.dumps(dict(patched_sha256=digest(game/'exe/roi.exe'))))
    print('Experimental assets installed.' + (' Test account login with python tools/lan_login.py; native LAN battle transport is not enabled.'
          if args.mode=='lan' else ' Keep Steam signed in for this profile.'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['internet','vpn','lan'],required=True)
    p.add_argument('--steam',type=Path,help='Original Steam game directory (required with --depot)')
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--depot',type=Path)
    g.add_argument('--base',type=Path)
    p.add_argument('--host')
    p.add_argument('--pin')
    p.add_argument('--port',type=int,choices=range(1024,65536),metavar='PORT',default=52714)
    try:
        setup(p.parse_args())
    except (OSError,ValueError) as exc:
        p.exit(1,str(exc)+'\n')
