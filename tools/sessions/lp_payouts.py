"""Normal-launch receipt bridge; LP/XP/cube presentation never grants inventory."""
import json
from http.client import IncompleteRead
from pathlib import Path
import re
import struct
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.error import HTTPError
from uuid import uuid4

import native_lp_input as native
from sessions.result_cube_display import CubeDisplay
from sessions.level_reward_notice import state as notice_state

ROOT = Path(__file__).resolve().parent.parent
FLAG = ROOT / 'lp-payouts.enabled'
TOKEN_FILE = ROOT / 'lp-bridge-token.txt'
URL = 'http://127.0.0.1:52710/Dragon/game'
HOOKS = {
    'bot': (0x140079680, bytes.fromhex('48895c24104c894c24204c89')),
    'start': (0x1400E8160, bytes.fromhex('4c8bdc49895b1049896b1849')),
    'custom': (0x1400EA140, bytes.fromhex('48895c240848897424104889')),
    'hud': (0x1404C8030, bytes.fromhex('488bc456574881ece8000000')),
}
LP_FIELDS = ((0x84, '2'), (0xA8, '5'), (0xAC, '6'))
RPC_ACTIONS = frozenset(('open', 'begin_custom', 'stage_bot', 'settle_custom', 'receipt'))
RPC_TIMEOUTS = (1.0, 3.0)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate bridge response field')
        result[key] = value
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TransportFailure(OSError):
    def __init__(self, action):
        super().__init__('Local reward bridge transport failed')
        self.action = action


class RPC:
    def __init__(self, token, *, deferred_bot=False):
        if type(token) is not str or not re.fullmatch('[0-9a-f]{64}', token):
            raise ValueError('Invalid local bridge credential')
        self.token = token
        self.launch = uuid4().hex
        self.opened = False
        self.deferred_bot = deferred_bot
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def send(self, action, *, deferred=False, **fields):
        if action not in RPC_ACTIONS:
            raise ValueError('Unknown local reward operation')
        body = json.dumps(dict(call='nativeLPDeferred' if deferred else 'nativeLP', data=dict(sv_token=self.token, action=action,
                          launch=self.launch, **fields)), allow_nan=False).encode('ascii')
        request = Request(URL, body, {'Content-Type': 'application/json'}, method='POST')
        # A timed-out reply can follow a durable write. Reuse the exact request;
        # these operations are idempotent under the same launch/round identifiers.
        for attempt, timeout in enumerate(RPC_TIMEOUTS):
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    raw = response.read(16385)
                break
            except HTTPError:
                raise
            except (OSError, IncompleteRead):
                if attempt == len(RPC_TIMEOUTS) - 1:
                    raise TransportFailure(action) from None
        if len(raw) > 16384:
            raise ValueError('Bridge response exceeds limit')
        value = json.loads(raw, object_pairs_hook=strict_object)
        if (not isinstance(value, dict) or value.get('result') != 'OK'
                or not isinstance(value.get('data'), dict) or value['data'].get('enabled') is not True):
            raise ValueError('Local LP bridge unavailable')
        return value['data']

    def __call__(self, action, **fields):
        if action == 'stage_bot' and self.deferred_bot:
            reply = self.send(action, deferred=True, **fields)
            if reply.get('queued') is not True:
                raise ValueError('Deferred staging was not acknowledged')
            self.opened = True  # The HTTP worker opens before staging this launch.
            return reply
        if not self.opened:
            self.send('open')
            self.opened = True
        return self.send(action, **fields)


def xp_display_fields(reader, xp, hud, api):
    if type(hud) is not int or hud < 0x10000:
        raise ValueError('Unavailable native XP HUD')
    if not isinstance(xp, dict) or type(xp.get('character')) is not int or not 1 <= xp['character'] <= 21:
        raise ValueError('Invalid XP receipt character')
    cid = xp['character']
    if native.number(reader, hud + 0x9C) != cid:
        raise ValueError('XP receipt character does not match the HUD')
    roster = native.pointer(reader, 0x141B87458)
    fields = []
    for label, gain_offset, total_address in (
            ('player', 0x28, roster + 0x3CCFC),
            ('character_xp', 0x2C + cid * 4, roster + 0x3CEBC + cid * 0x138)):
        values = xp.get(label)
        keys = {'stored_before', 'before', 'after', 'gained', 'level_before', 'level_after', 'baseline_adjustment'}
        if (not isinstance(values, dict) or set(values) != keys
                or any(type(value) is not int or not 0 <= value <= native.MAX_INT for value in values.values())
                or values['before'] + values['gained'] != values['after']
                or values['before'] - values['stored_before'] != values['baseline_adjustment']
                or values['level_after'] < values['level_before']):
            raise ValueError('Invalid committed XP totals')
        if native.number(reader, total_address) not in (values['stored_before'], values['before'], values['after']):
            raise ValueError('Cached XP changed since this result')
        fields.extend(((api + gain_offset, values['gained']), (total_address, values['after'])))
    return fields


def display(reader, row, context, current, *, hud=None):
    if (not isinstance(row, dict) or row.get('enabled') is not True
            or row.get('battle_id') != context['battle_id'] or row.get('mode') != context['mode']
            or row.get('summary') != current['summary'] or current['summary'] != context['summary']):
        raise ValueError('Committed LP receipt and native result disagree')
    values = row.get('components')
    if (not isinstance(values, dict) or set(values) != {'2', '5', '6'}
            or any(type(value) is not int or not 0 <= value <= native.MAX_INT for value in values.values())
            or type(row.get('total')) is not int or row['total'] != sum(values.values())
            or not 0 <= row['total'] <= native.MAX_INT):
        raise ValueError('Invalid committed LP totals')
    api = native.pointer(reader, native.pointer(reader, 0x141B87450) + 0x170)
    fields = [(api + offset, values[reason]) for offset, reason in LP_FIELDS]
    if 'xp' in row:
        fields.extend(xp_display_fields(reader, row['xp'], hud, api))
    originals = [(address, native.read(reader, address, 4)) for address, _ in fields]
    try:
        for address, amount in fields:
            value = struct.pack('<i', amount)
            if not reader.write(address, value) or native.read(reader, address, 4) != value:
                raise OSError('Native award display write failed')
    except (OSError, ValueError):
        restored = [reader.write(address, raw) for address, raw in originals]
        if not all(restored):
            reader.log('LP_PAYOUT display rollback incomplete; persistent receipt retained')
        raise


class Bridge:
    def __init__(self, debugger, rpc):
        self.d, self.rpc = debugger, rpc
        self.context = None
        self.custom = None
        self.budget = 30
        self.timing_budget = 24
        self.cubes = CubeDisplay(debugger)
        self.notices = notice_state(debugger)

    def log(self, text):
        if self.budget:
            self.budget -= 1
            self.d.log('LP_PAYOUT ' + text)

    def xp_fields(self):
        try:
            return {'xp_summary': native.experience_summary(self.d)}
        except (OSError, ValueError, struct.error):
            return {}  # Keep the existing LP-only adapter independent of extra XP reads.

    def cube_fields(self):
        try:
            return {'cube_usage': native.battle_cubes(self.d)}
        except (OSError, ValueError, struct.error):
            self.log('battle cube snapshot unavailable; durability unchanged')
            return {}

    def teammate_fields(self):
        try:
            return {'teammate': native.teammate(self.d)}
        except (OSError, ValueError, struct.error):
            self.log('teammate character unavailable; partner counts unchanged')
            return {}

    def event(self, name, hit):
        started, cpu_started = time.perf_counter(), time.thread_time()
        try:
            return self._event(name, hit)
        finally:
            if self.timing_budget and name in HOOKS:
                self.timing_budget -= 1
                self.d.log('LP_PAYOUT_TIMING '+name
                           +' wall_ms='+format(1000*(time.perf_counter()-started), '.3f')
                           +' thread_cpu_ms='+format(1000*(time.thread_time()-cpu_started), '.3f'))

    def _event(self, name, hit):
        try:
            playing = native.is_replay(self.d)
        except (OSError, ValueError, struct.error):
            playing = True
            self.log('playback state unavailable; reward hook skipped')
        if playing:
            # Only discard Python-side context; never write into replay state.
            self.context = self.custom = None
            self.cubes.clear()
            self.notices.clear()
            return
        try:
            if name == 'start':
                value = native.custom_start(self.d, hit.rdx)
                if value is None:
                    return
                if self.custom and value['sequence'] < self.custom['sequence']:
                    return
                if (self.custom and 'battle_id' in self.custom
                        and all(self.custom[key] == item for key, item in value.items())):
                    return
                self.context = None
                self.cubes.clear()
                self.notices.clear()
                self.custom = dict(value)
                row = self.rpc('begin_custom', **value)
                self.custom = dict(value, battle_id=row['battle_id'])
                self.log('Custom round registered')
            elif name == 'bot':
                self.context = None
                self.cubes.clear()
                self.notices.clear()
                self.custom = None
                value = native.bot_input(self.d, hit)
                self.context = dict(mode='bot', battle_id=value['battle_id'], summary=value['summary'])
                self.rpc('stage_bot', **value, **self.xp_fields(), **self.cube_fields(), **self.teammate_fields())
                self.log('Vs. Bot final score and Team Rank submitted')
            elif name == 'custom':
                if not self.custom:
                    return  # Vs. Bot may use other NRT notifications; retain its HTTP context.
                self.context = None
                self.cubes.clear()
                self.notices.clear()
                if native.custom_key(self.d, hit) != self.custom['match_key']:
                    raise ValueError('Custom round context missing')
                if 'battle_id' not in self.custom:
                    row = self.rpc('begin_custom', **self.custom)
                    self.custom['battle_id'] = row['battle_id']
                value = native.snapshot(self.d)
                self.context = dict(mode='custom', battle_id=self.custom['battle_id'], summary=value['summary'])
                row = self.rpc('settle_custom', **self.custom, **value, **self.xp_fields(), **self.cube_fields(), **self.teammate_fields())
                self.log('Custom LP committed total=' + str(row['total']))
            elif name == 'hud' and self.context is not None:
                self.cubes.clear()
                self.notices.clear()
                row = self.rpc('receipt', mode=self.context['mode'], battle_id=self.context['battle_id'])
                display(self.d, row, self.context, native.snapshot(self.d), hud=getattr(hit, 'rcx', None))
                values = row['components']
                self.log('Displayed Score=' + str(values['2']) + ' Team=' + str(values['5'])
                         + ' Battle=' + str(values['6']) + ' Total=' + str(row['total']))
                if 'xp' in row:
                    self.log('Displayed player XP=' + str(row['xp']['player']['gained'])
                             + ' character XP=' + str(row['xp']['character_xp']['gained']))
                self.cubes.prepare(row, hit)
                self.notices.prepare(row)
                if isinstance(row.get('cube_wear'), dict):
                    wear = row['cube_wear']
                    self.log('Cube durability committed grid=' + str(wear['grid_id'])
                             + ' cubes=' + str(len(wear['cubes'])))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, struct.error) as error:
            # Never expose credentials/pointers, or alter native control flow on a bridge failure.
            detail = (' transport during ' + error.action if isinstance(error, TransportFailure)
                      else ' ' + type(error).__name__)
            self.log(name + ' unavailable;' + detail + '; normal match completion preserved')


def setup(d, *, enabled=None, rpc=None):
    if not (FLAG.is_file() if enabled is None else enabled):
        return False
    if rpc is None:
        try:
            if TOKEN_FILE.stat().st_size > 128:
                raise ValueError('Invalid bridge credential file')
            rpc = RPC(TOKEN_FILE.read_text(encoding='ascii').strip(), deferred_bot=True)
        except (OSError, ValueError):
            d.log('LP_PAYOUT disabled: local bridge credential unavailable')
            return True
    bridge = Bridge(d, rpc)

    def arm(debugger):
        for address, expected in HOOKS.values():
            if debugger.read(address, len(expected)) != expected:
                debugger.log('LP_PAYOUT disabled: native code guard mismatch')
                return
        for name, (address, _) in HOOKS.items():
            debugger.bp(address, lambda hit, name=name: bridge.event(name, hit))
        bridge.cubes.arm()
        debugger.log('LP_PAYOUT guarded committed-reward presentation armed')

    d.on_start(arm)
    return True
