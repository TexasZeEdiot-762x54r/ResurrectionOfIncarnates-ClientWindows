"""Opt-in numeric-only Ranked transport trace; never modifies forwarded bytes."""
import json
import struct
import time


KINDS = frozenset((0xE620, 0xE621, 0xE622, 0xE623, 0xE652, 0xE653))
MAX_FRAME = 65537
MAX_CHUNK = 16384


class Budget:
    def __init__(self, emit, clock=time.monotonic):
        self.emit = emit
        self.clock = clock
        self.started = clock()
        self.records = 64
        self.bytes = 4 * 1024 * 1024

    def active(self):
        return self.records > 0 and self.bytes > 0 and self.clock() - self.started < 600

    def write(self, fields):
        if self.active():
            self.records -= 1
            self.emit('ranked transport ' + json.dumps(
                dict(fields, elapsed_seconds=round(self.clock() - self.started, 3)),
                sort_keys=True))


def describe(body, direction):
    header = 20 if direction == 'request' else 22
    if len(body) < header:
        raise ValueError('Short native header')
    kind = struct.unpack_from('<H', body, 10)[0]
    if kind not in KINDS:
        return None
    payload = body[header:]
    fields = {'direction': direction, 'kind': f'{kind:04x}', 'payload_bytes': len(payload)}
    if direction == 'reply':
        fields['result'] = struct.unpack_from('<H', body, 12)[0]
        # The native E620 handler reads exactly one u32 (0x1400e906e).
        if kind == 0xE620 and fields['result'] == 0:
            fields['complete_u32_reply'] = len(payload) == 4
    elif kind == 0xE620:
        # Builder 0x1400e96e0: 3 zero bytes, u16 connection size (0 or 21),
        # opaque connection bytes, u16 count and up to ten opaque u64s.
        # Only lengths/validity are logged, never connection data or identities.
        fields['shape_valid'] = False
        if len(payload) >= 7:
            size = struct.unpack_from('<H', payload, 3)[0]
            if size in (0, 21) and len(payload) >= 7 + size:
                count = struct.unpack_from('<H', payload, 5 + size)[0]
                fields.update(connection_bytes=size, list_count=count,
                              shape_valid=(payload[:3] == bytes(3) and count <= 10
                                           and len(payload) == 7 + size + 8 * count))
    elif kind == 0xE652 and len(payload) == 18:
        fields['operation'] = struct.unpack_from('<H', payload)[0]
    return fields


class Observer:
    def __init__(self, budget, direction):
        if direction not in ('request', 'reply'):
            raise ValueError('Invalid trace direction')
        self.budget = budget
        self.direction = direction
        self.buffer = bytearray()
        self.disabled = False

    def disable(self):
        self.disabled = True
        self.buffer.clear()

    def feed(self, data):
        if self.disabled:
            return
        if not self.budget.active() or len(data) > MAX_CHUNK or len(data) >= self.budget.bytes:
            self.disable()
            return
        self.budget.bytes -= len(data)
        self.buffer.extend(data)
        header = 20 if self.direction == 'request' else 22
        while len(self.buffer) >= 2:
            length = struct.unpack_from('<H', self.buffer)[0]
            if length < header + 4:
                raise ValueError('Invalid native frame')
            total = length + 2
            if len(self.buffer) < total:
                break
            body_length = struct.unpack_from('<H', self.buffer, 2)[0]
            if body_length != length - 4:
                raise ValueError('Invalid native body length')
            fields = describe(self.buffer[4:4 + body_length], self.direction)
            del self.buffer[:total]
            if fields is not None:
                self.budget.write(fields)
            if not self.budget.active():
                self.disable()
                return
        if len(self.buffer) > MAX_FRAME:
            self.disable()


class Reader:
    """The existing TLS bridge only calls read; observer failures cannot break it."""
    def __init__(self, reader, observer):
        self.reader = reader
        self.observer = observer

    async def read(self, size):
        data = await self.reader.read(size)
        try:
            if data:
                self.observer.feed(data)
            else:
                self.observer.disable()
        except Exception:
            self.observer.disable()
        return data


def observe(reader, budget, direction):
    return reader if budget is None else Reader(reader, Observer(budget, direction))
