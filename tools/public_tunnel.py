"""Local-only game/relay listeners forwarding to the pinned public TLS gateway."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from tls_transport import CHANNELS,HEALTH,Limits,bridge,close,pinned_connection


def settings():
    config=json.loads((HERE/'config.json').read_text(encoding='utf-8-sig'))
    public=json.loads((HERE/'public-lobby.json').read_text(encoding='utf-8'))
    if config.get('lobby_transport')!='tls-public':
        raise ValueError('Public TLS transport is not selected')
    host=config.get('public_lobby_host')
    if host not in (public['internet_host'],public['lan_host']):
        raise ValueError('Unexpected public gateway address')
    port=public['port']
    if type(port) is not int or not 1024<=port<=65535:
        raise ValueError('Invalid gateway port')
    return host,port,public['certificate_sha256']


async def check(options):
    reader,writer=await pinned_connection(*options)
    try:
        writer.write(CHANNELS['health'])
        await writer.drain()
        if await asyncio.wait_for(reader.readline(),timeout=5)!=HEALTH:
            raise ValueError('Unexpected TLS gateway health response')
    finally:
        await close(writer)


async def run(options, trace_ranked=False):
    await check(options)
    from roi_account_client import read_credential
    from authenticated_native import Reader as AuthenticatedReader
    import roi_progression_client
    account=read_credential(HERE/'account-auth.json')
    if account is None or roi_progression_client.credential(account['uid'])!=account['token']:
        raise ValueError('Run the account registration launcher first')
    trace = None
    if trace_ranked:
        from ranked_transport_trace import Budget, observe
        trace = Budget(lambda line: print(line, flush=True))
        print('ranked transport trace enabled: numeric shapes only, 10 minutes, 64 records',flush=True)
    limits=Limits(maximum=24,per_ip=24)
    async def accept(reader,writer,channel):
        address=writer.get_extra_info('peername')[0]
        if address!='127.0.0.1':
            await close(writer)
            return
        if not limits.enter(address):
            print('public tunnel: local admission limit reached; connection deferred',flush=True)
            await close(writer)
            return
        remote_writer=None
        try:
            remote,remote_writer=await pinned_connection(*options)
            remote_writer.write(CHANNELS[channel])
            await remote_writer.drain()
            if channel == 'nrt':
                reader=AuthenticatedReader(reader,account['uid'],account['token'])
            if channel == 'nrt' and trace is not None:
                reader=observe(reader,trace,'request')
                remote=observe(remote,trace,'reply')
            result=await bridge(reader,writer,remote,remote_writer)
            if channel=='nrt':
                print('public native stream closed',result,flush=True)
        except (OSError,asyncio.TimeoutError,ValueError):
            print('public tunnel: connection unavailable or certificate check failed',flush=True)
        finally:
            await close(remote_writer)
            await close(writer)
            limits.leave(address)
    native=await asyncio.start_server(lambda r,w:accept(r,w,'nrt'),'127.0.0.1',52711,limit=65536,backlog=16)
    try:
        profile=await asyncio.start_server(lambda r,w:accept(r,w,'profile'),'127.0.0.1',52713,limit=65536,backlog=16)
    except BaseException:
        native.close()
        await native.wait_closed()
        raise
    async with native,profile:
        print('public TLS tunnel ready on loopback 52711/52713',flush=True)
        await asyncio.gather(native.serve_forever(),profile.serve_forever())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    try:
        asyncio.run(check(settings()) if args.check else run(
            settings(), trace_ranked=os.environ.get('ROI_TRACE_RANKED') == '1'))
        if args.check:
            print('Pinned public TLS gateway health check passed')
    except (OSError,ValueError,asyncio.TimeoutError):
        print('Public TLS gateway unavailable, wrong endpoint, or certificate mismatch. Check connectivity and TCP 52714 forwarding.',file=sys.stderr)
        raise SystemExit(1)
