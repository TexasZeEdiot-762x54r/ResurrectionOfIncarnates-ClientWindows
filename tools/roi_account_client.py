"""No-browser first-launch registration over the existing certificate-pinned channel."""
import asyncio
import csv
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ISSUER = json.loads((HERE/'config.json').read_text('utf-8-sig'))['public_lobby_host']


class RegistrationError(ValueError):
    pass


def read_credential(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(1025)
    except FileNotFoundError:
        return None
    if len(raw) > 1024:
        raise RegistrationError('Invalid account credential file')
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {'schema','uid','issuer','token'}
            or type(value['schema']) is not int or value['schema'] != 1 or value['issuer'] != ISSUER
            or not isinstance(value['uid'], str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value['uid'])
            or int(value['uid']) >= 2**64 or not isinstance(value['token'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', value['token'])):
        raise RegistrationError('Invalid account credential file')
    return value


def private_create(path, value):
    """Set a private ACL while empty, then persist the retry credential before sending."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.name == 'nt':
            output = subprocess.check_output(['whoami','/user','/fo','csv','/nh'], text=True)
            sid = next(csv.reader(io.StringIO(output)))[1]
            if not re.fullmatch(r'S-1-[0-9-]+', sid):
                raise RegistrationError('Cannot secure the account credential')
            result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                                     '*'+sid+':F', '*S-1-5-18:F'], capture_output=True)
            if result.returncode:
                raise RegistrationError('Cannot secure the account credential')
        else:
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            fd = None
            json.dump(value, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if fd is not None:
            os.close(fd)
        path.unlink(missing_ok=True)
        raise


async def exchange(options, method, path, token=None, body=None):
    from tls_transport import pinned_connection, close, CHANNELS
    if options[0] != ISSUER:
        raise RegistrationError('Unexpected account server')
    reader, writer = await pinned_connection(*options)
    try:
        raw = json.dumps(body, separators=(',', ':')).encode() if body is not None else b''
        if len(raw) > 16384:
            raise RegistrationError('Registration request too large')
        headers = [method+' '+path+' HTTP/1.1', 'Host: '+ISSUER, 'Connection: close',
                   'Content-Type: application/json', 'Content-Length: '+str(len(raw))]
        if path == '/registration' or path.startswith('/registration/'):
            from client_version import label
            version = label()
            if version:
                headers.append('X-ROI-Client-Version: '+version)
        if token is not None:
            if not re.fullmatch(r'[0-9a-f]{64}', token):
                raise RegistrationError('Invalid account credential')
            headers.append('Authorization: Bearer '+token)
        writer.write(CHANNELS['profile'] + ('\r\n'.join(headers)+'\r\n\r\n').encode()+raw)
        await asyncio.wait_for(writer.drain(), 5)
        block = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 12)
        if len(block) > 8192:
            raise RegistrationError('Invalid registration response')
        lines = block.decode('ascii').split('\r\n')
        code = int(lines[0].split()[1])
        values = {}
        for line in lines[1:]:
            if not line:
                continue
            name, value = line.split(':', 1)
            name = name.lower()
            if name in values:
                raise RegistrationError('Duplicate response header')
            values[name] = value.strip()
        size = int(values.get('content-length', '-1'))
        if not 0 <= size <= 16384 or 'transfer-encoding' in values:
            raise RegistrationError('Invalid registration response framing')
        raw = await asyncio.wait_for(reader.readexactly(size), 5)
        if code == 401:
            return None
        if code == 409:
            raise RegistrationError('Account already registered. Restore its account-auth.json or ask the administrator for recovery; no progress was reset.')
        if code != 200:
            raise RegistrationError('Account service unavailable; registration will retry on the next launch')
        return json.loads(raw)
    finally:
        await close(writer)


def validate_status(value, uid):
    if (not isinstance(value, dict) or set(value) != {'uid','progression'} or value['uid'] != uid
            or value['progression'] not in ('server','import_pending')):
        raise RegistrationError('Server did not confirm this account')
    return value


def ensure(identity, options, directory=HERE):
    final = directory/'account-auth.json'
    pending = directory/'account-auth.pending.json'
    value = read_credential(final)
    if value is not None:
        if value['uid'] != identity.uid:
            raise RegistrationError('This installation belongs to another Steam account; use a separate client folder')
        status = asyncio.run(exchange(options, 'GET', '/registration/'+identity.uid, value['token']))
        return validate_status(status, identity.uid)
    value = read_credential(pending)
    if value is None:
        value = dict(schema=1, uid=identity.uid, issuer=ISSUER, token=secrets.token_hex(32))
        private_create(pending, value)
    if value['uid'] != identity.uid:
        raise RegistrationError('Pending registration belongs to another Steam account')
    # A prior request may have committed even if its response was lost.
    status = asyncio.run(exchange(options, 'GET', '/registration/'+identity.uid, value['token']))
    if status is None:
        status = asyncio.run(exchange(options, 'POST', '/registration', body=dict(
            uid=identity.uid, token=value['token'], ticket=identity.ticket())))
    result = validate_status(status, identity.uid)
    if final.exists():
        raise RegistrationError('Account credential changed during registration; restart the launcher')
    # Rename, not replace: never overwrite another launch's credential.
    pending.rename(final)
    return result


def main():
    import paths
    from public_tunnel import settings
    from steam_identity import Identity
    try:
        with Identity(paths.GAME_EXE.parent/'steam_api64.dll') as identity:
            result = ensure(identity, settings())
        from match_lamina import restore
        if result.get('progression')=='server':
            restore(read_credential(HERE/'account-auth.json'),settings(),exchange)
        print('Account authenticated. Existing local-save import remains pending.' if result['progression']=='import_pending'
              else 'Account authenticated; progression is server-owned.')
    except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        # Never include transport exception strings, which might contain a ticket.
        print('Account registration/login failed. Check Steam and connectivity. Keep account-auth.pending.json for a safe retry; do not delete existing account-auth.json.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
