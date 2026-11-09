"""Bounded read-only samples of Customize loading; no per-frame breakpoint."""
import json
import struct

ENTRY = 0x140161A20
VTABLE = 0x141856B00


def snapshot(d, scene):
    if d.q(scene) != VTABLE:
        return None
    result = {'scene': {hex(o): d.d32(scene+o) for o in (0x40,0x608,0x60c,0x620)},
              'window_ready': bool(d.q(scene+0x630)), 'pending_assets': []}
    for index in range(6):
        loader = d.q(0x141B941F0 + index*8)
        if not loader:
            continue
        vector = d.q(loader+0x40)
        if not vector:
            continue
        start, end = d.q(vector+8), d.q(vector+16)
        size = end-start
        if not 0 <= size <= 0x138*4000 or size % 0x138:
            continue
        raw = d.read(start,size) if size else b''
        if len(raw) != size:
            continue
        pending = []
        for offset in range(0,size,0x138):
            status, group = struct.unpack_from('<II',raw,offset+0x110)
            if status == 0:
                name = raw[offset:offset+256].split(b'\0')[0].decode('ascii','replace')
                pending.append({'group':group,'asset':name})
        result['pending_assets'].append({'loader':index,'count':len(pending),'first':pending[:8]})
    game = d.q(0x141B87450)
    api = d.q(game+0x170) if game else 0
    result['api'] = [d.d32(api+0x128),d.d32(api+0x12c)] if api else None
    return result


def setup(d):
    state={'scene':None,'samples':0,'budget':36}

    def enter(hit):
        state.update(scene=hit.rcx,samples=0)

    def poll(debugger):
        if not state['scene'] or state['samples'] >= 4 or not state['budget']:
            return
        state['samples'] += 1
        row=snapshot(debugger,state['scene'])
        if row is None:
            state['scene']=None
            return
        state['budget'] -= 1
        debugger.log('CUSTOMIZE_WAIT '+json.dumps(row,separators=(',',':')))

    def arm(debugger):
        if debugger.read(ENTRY,6) != bytes.fromhex('48895c240857'):
            debugger.log('CUSTOMIZE_WAIT disabled: code guard mismatch')
            return
        debugger.bp(ENTRY,enter)
        debugger.log('CUSTOMIZE_WAIT bounded read-only loading trace armed')

    d.on_start(arm)
    d.poll(poll,every=2.0)
