"""Feed committed cube drops to the native results copy, never to inventory APIs."""
from collections import Counter
import struct

import native_lp_input as native

HOOK = 0x1404C8488
GUARDS = {
    0x1404C847A: bytes.fromhex('488b93700100004881c2b0000000e8230c0000'),
    0x1400D13A7: bytes.fromhex('488b4328488d542420488d8fb0000000'),
    0x1404C9770: bytes.fromhex('4c8bc9483bca74440f1f840000000000'),
}
MAX_ROWS = 64
STRIDE = 0x30
SCRATCH_SIZE = 24 + MAX_ROWS * STRIDE


def record(cube_id):
    # Native producer 0x1400D13A7: original ID, result ID, count, three zeros.
    return struct.pack('<QQQQQQ', cube_id, cube_id, 1, 0, 0, 0)


def valid_pointer(value):
    return type(value) is int and 0x10000 <= value < 0x7FFFFFF00000


def read_block(reader, address, size):
    if not valid_pointer(address) or not 0 <= size <= SCRATCH_SIZE:
        raise ValueError('Invalid cube display block')
    return b''.join(native.read(reader, address + i, min(144, size - i))
                    for i in range(0, size, 144))


def cube_ids(items):
    if type(items) is not list or len(items) > MAX_ROWS:
        raise ValueError('Invalid committed result items')
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Invalid committed item')
        value = item.get('sv_itemId')
        if (type(value) is not str or len(value) != 16
                or not value.isascii() or not value.isdigit()):
            raise ValueError('Invalid committed item ID')
        if not value.startswith('12'):
            continue
        count = item.get('sv_count')
        if type(count) is str and count.isascii() and count.isdigit() and len(count) <= 2:
            count = int(count)
        if (type(count) is not int or not 1 <= count <= MAX_ROWS
                or item.get('sv_additions') != [] or len(result) + count > MAX_ROWS):
            raise ValueError('Unsupported committed cube display')
        result.extend([int(value)] * count)
    return result


class CubeDisplay:
    def __init__(self, debugger):
        self.d = debugger
        self.pending = None
        self.scratch = None
        self.budget = 20

    def log(self, text):
        if self.budget:
            self.budget -= 1
            self.d.log('RESULT_CUBES ' + text)

    def clear(self):
        self.pending = None

    def prepare(self, receipt, hit):
        self.clear()
        ids = cube_ids(receipt.get('result_items', []))
        if not ids:
            return
        hud, tid = getattr(hit, 'rcx', None), getattr(hit, 'tid', None)
        if not valid_pointer(hud) or type(tid) is not int or tid <= 0:
            raise ValueError('Unavailable result HUD')
        api = native.pointer(self.d, native.pointer(self.d, 0x141B87450) + 0x170)
        self.pending = hud, tid, api, ids

    def copy(self, hit):
        pending, self.pending = self.pending, None
        if pending is None:
            return False
        hud, tid, api, ids = pending
        if (hit.tid != tid or hit.rdi != hud or hit.rcx != hud + 0xF8
                or hit.rdx != api + 0xB0):
            return False
        start, end, capacity = struct.unpack('<QQQ', native.read(self.d, hit.rdx, 24))
        if (start, end, capacity) == (0, 0, 0):
            raw = b''
        else:
            if (not valid_pointer(start) or not start <= end <= capacity
                    or capacity - start > MAX_ROWS * STRIDE
                    or (end - start) % STRIDE or (capacity - start) % STRIDE):
                raise ValueError('Invalid native cube vector')
            raw = read_block(self.d, start, end - start)
        records = [raw[i:i + STRIDE] for i in range(0, len(raw), STRIDE)]
        available = Counter()
        for row in records:
            original, result, count = struct.unpack_from('<QQQ', row)
            if original == result and count <= MAX_ROWS:
                available[result] += count
        missing = []
        for cube_id in ids:
            if available[cube_id]:
                available[cube_id] -= 1
            else:
                missing.append(record(cube_id))
        if not missing:
            return False
        if len(records) + len(missing) > MAX_ROWS:
            raise ValueError('Native cube display limit exceeded')
        if self.scratch is None:
            address = self.d.alloc(SCRATCH_SIZE)
            if not valid_pointer(address):
                raise OSError('Cube display allocation failed')
            self.scratch = address
        body = b''.join(records + missing)
        begin = self.scratch + 24
        data = struct.pack('<QQQ', begin, begin + len(body), begin + len(body)) + body
        if not self.d.write(self.scratch, data) or read_block(self.d, self.scratch, len(data)) != data:
            raise OSError('Cube display staging failed')
        # Only replace the source argument to the original POD vector-copy call.
        # The native allocator owns its destination; the API's vector is untouched.
        hit.setreg('rdx', self.scratch)
        self.log('queued committed drops=' + str(len(ids)) + ' added=' + str(len(missing)))
        return True

    def arm(self):
        if any(self.d.read(a, len(raw)) != raw for a, raw in GUARDS.items()):
            self.log('disabled: native copy guard mismatch')
            return False

        def copying(hit):
            try:
                self.copy(hit)
            except (OSError, ValueError, TypeError, AttributeError, struct.error):
                self.log('display unavailable; inventory receipt unchanged')

        self.d.bp(HOOK, copying)
        self.log('guarded committed-drop presentation armed')
        return True
