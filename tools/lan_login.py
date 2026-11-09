"""Login over the private pinned gateway; never send a LAN password to a Steam endpoint."""
import asyncio
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))


def main():
    cfg=json.loads((HERE/'config.json').read_text())
    if cfg.get('release_mode')!='lan':
        raise ValueError('LAN login is disabled for this installation')
    from roi_account_client import exchange,private_create
    from public_tunnel import settings
    options=settings()
    addr=ipaddress.IPv4Address(options[0])
    if not any(addr in ipaddress.ip_network(n) for n in
               ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8')):
        raise ValueError('Refusing non-LAN endpoint')
    request=dict(username=input('Operator-created LAN username: ').strip(),password=getpass.getpass('LAN password: '))
    value=asyncio.run(exchange(options,'POST','/registration',body=request))
    if (not isinstance(value,dict) or set(value)!={'uid','token','progression'} or value['progression']!='server'
            or not re.fullmatch('[1-9][0-9]{0,19}',value['uid']) or not re.fullmatch('[0-9a-f]{64}',value['token'])):
        raise ValueError('Login refused')
    path=HERE/'account-auth.lan-pending.json'
    private_create(path,dict(schema=1,uid=value['uid'],issuer=str(addr),token=value['token']))
    os.replace(path,HERE/'account-auth.json')
    print('LAN account authenticated. No password was saved.')


if __name__=='__main__':
    try:
        main()
    except Exception:
        sys.exit('LAN login failed. Check the private address, certificate pin and operator-issued account.')
