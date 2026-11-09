"""Default-off, repeatable Lamina factory experiment. No on-disk game patch.

Only setup(d) is an integration entry point. This is statically validated,
not battle-tested. Breakpoint redirection runs the native call on its original
game thread and preserves its return address and Win64 shadow space.
"""
import hashlib
import json
import math
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PureWindowsPath
import struct
import time
import uuid

ROOT = PureWindowsPath(Path(__file__).resolve().parents[2])
FLAG_NAME = 'lamina-combat.enabled'
OPEN, SUMMON, USED = 0x14069AA70, 0x14069AAC0, 0x14069AAD0
PARAM = 0x14069AAE0
NATIVE_SUMMON = 0x140640540
CREATED_RETURN = 0x1406405BF
INITIALIZED = 0x140520D65
MENU_SCENE = 0x140162BF0
TARGET_SET, TARGET_VALID = 0x14059E310, 0x14059E320
LIFETIME_RETURN = 0x1405330B7
LOA_SHOT_RETURN = 0x1405092D7
LOA_CHILD_READY = 0x140711DFB
DULL_MARK, HIT_HP = 0x14065B7FD, 0x14065983B
SHOT_LOST_AIM, CONTACT_BEGIN, CONTACT_END = 0x1406E2EDE, 0x140658E51, 0x14065A107
SHAPE_PAIR = 0x1408A76E5
# Whole, position-independent instructions replayed by the observer thunks.
GRIFFIN_SITES = {SHAPE_PAIR: bytes.fromhex('4c8b9aa0020000'),
                 SHOT_LOST_AIM: bytes.fromhex('c683f814000000'),
                 CONTACT_BEGIN: bytes.fromhex('0fb6430c'),
                 CONTACT_END: bytes.fromhex('488b96e00d0000')}
HUD_TAIL, HUD_FACTORY = 0x1404A7928, 0x1407D8850
ENEMY_GAUGE_TYPE, ENEMY_GAUGE_VTABLE = 0x141BAE690, 0x1418E5500
DULL_STORES = {DULL_MARK: bytes.fromhex('41c78630f9000001000000'),
               HIT_HP: bytes.fromhex('f30f1186880c0000')}
LOA_CHILD = 97
IFRIT_CHILD = 95
CHILD_ROWS = {LOA_CHILD: (46, b'actor/subcharacter/LoaShot'),
              IFRIT_CHILD: (4, b'actor/subcharacter/IfreShot')}
SUB_MANAGER = 0x141C00528
LOA_CHILD_VTABLE = 0x1419A7C30
SCRIPT_POOL = 0x141C00568
MANAGER, OBJECTS = 0x141C00368, 0x141B87508
ROSTER, REGISTRY, PLAYBACK = 0x141B87458, 0x141B87490, 0x141B874D0
STUBS = {
    OPEN: bytes.fromhex('32c0c3') + b'\xcc' * 13,
    SUMMON: bytes.fromhex('c20000') + b'\xcc' * 13,
    USED: bytes.fromhex('b001c3') + b'\xcc' * 13,
    PARAM: bytes.fromhex('c20000') + b'\xcc' * 13,
}
SITES = {**STUBS, **DULL_STORES, **GRIFFIN_SITES, CREATED_RETURN: bytes.fromhex('488b5c2438488b7424404883c4205fc3'),
         HUD_TAIL: bytes.fromhex('e9230f3300'),
         INITIALIZED: bytes.fromhex('488b5c24304883c4205fc3'),
         LIFETIME_RETURN: b'\xc3' + b'\xcc' * 8,
         LOA_SHOT_RETURN: bytes.fromhex('488bf04885c07434'),
         LOA_CHILD_READY: bytes.fromhex('488bc6488b9c2408'),
         TARGET_SET: bytes.fromhex('889110eb0000c3') + b'\xcc' * 9,
         TARGET_VALID: bytes.fromhex('83b914eb0000007408b1ff84c90f99c0c30fb68910eb000084c90f99c0c3cccc')}
CONTRACTS = {
    0x1408A76D0: (0x13C, 'a9dc75d1d3555cd036b303015fa51c3a80237ed586f98c3126aa477682edb743'),
    0x1404A78F9: (52, 'd4c285f51c77bdb065f1f7393b735d73dc169d4600f9933619938eb63aa1fe03'),
    0x1404B43B0: (57, 'ab3784be1b0812cd40ce49f26989ade0a28c298b94774ae787a8deeebe651fd5'),
    0x1404B5770: (194, '830f63c571fdc1da63e6de709ca1c0b1b50f786265ad28153304156fb023c544'),
    0x1404B5952: (56, 'e587b19b153501c0904dfbe67fde0f70162b19d719ad6107f2590592fa321042'),
    HUD_FACTORY: (70, 'cca888960a56a6944177577d6511e1694610f1ce578b5b14f728ac363a16f892'),
    0x14065981C: (42, '793edb38ee3bebb9ba7991a15f32aa52b1d764f9e178c9e9fff5fcf7e5461127'),
    0x14065B7D3: (53, 'ae08796fc6df862ba13d120a8af33ee911228702d51bd9660334e3b049d6285f'),
    0x14065828C: (25, '1c1263bb6fef629f3bce3f2ab21f12b89fe8ff15531a41cb91560418fefb5a21'),
    0x14073DDD0: (33, '9c7e5066a397cc566c65de01bcaabb9e4eba723b8821a323a7d090748a3b1cce'),
    0x140721A50: (257, '242e477416b1227c2222886a49672a6d93322f2a54d8d091542a729187fbd2f8'),
    LOA_CHILD_VTABLE: (56, 'af4c06f4bc56f0e554368a822f574a20b946e43d41d4ae94515b16e52eb795e7'),
    0x14072BFA0: (58, '0b0cee521c66472e9a9245ae0da4670f6f588d727b17917c9daf1bd784a07674'),
    0x14072B9F3: (57, '8bcd3e1ad2e21ce9c5a502c8feb4a896ba14aa0c4bac8be027b0d5b90909b3d2'),
    0x14050A3D0: (338, 'bb7c80656582f0ecb98220fbf9cc7e65f549c9b370bda4f8ffc647136a8ddb11'),
    0x1405099D0: (86, '66e127a2b774fbb6b1ae87b3548ef60906a8efd402b7f517dcd973b5a0686cd8'),
    0x140509220: (183, '776af1cfc2cdcc0c1e076d8ed759e551f2a928166db8d44c64c416735d6d7478'),
    0x140512800: (76, 'ef1e66f9dfc880067f7b4c6ebb69ddf56f0ada497337f7d2baf9b18ccb3ac2f2'),
    0x140512F70: (79, '966c36d2ec4467f1c0b05523fd03674023dacae7501170473d3db3396846e482'),
    0x140527CA0: (170, '228e34bd84f215ec7845f8892c7eadced47bcf7f3366c3637184cc70a39c1388'),
    0x1405330A0: (24, '04b58582943df3e67b2b8678959fc579c0d7f85f6a61370d7c657ca8a637679e'),
    NATIVE_SUMMON: (143, '780e4f807cce81de028716d494a2db23569d60a357d42368a5faf234441374dc'),
    0x140552F20: (127, '5be3a8c967d94d10bbe26a8c608b271849bf15936f10079592da043a0cf6f3d0'),
    0x14051F340: (440, 'c2c64bc35cee3313c730ad9f65605f91c370b152f4a013e9c36ec4da62c4df1a'),
    0x140521480: (235, '86bd1eb17bcb47c898b5690e7080c4965a4325f7e18e2d6ee50770b72d1b1e2a'),
    0x1405043F0: (44, '019eed8d38cdf724fb59d21213c9f94900808ad8d07df115082567c4e371cddd'),
    0x14052ADB0: (257, '8feec7d32d8a7a8c00a71e32e0313a0c49a6cef7fbdacd88ec2ee76e6c83ff07'),
    0x140538B00: (418, 'f2151dafac70b42d58981f98f2ccb85a0d8dbe8f0438218dc56f620ab181bd64'),
    0x140512770: (13, '0c72df7cfb46b0347ed6487e78c7908d37084e56c371396b8e53d5cb11a5b031'),
    0x140512ED0: (156, '9876f0079a1a445d97e729d87767c23e40ca14281ab0fcd36cd286f5753b6287'),
    0x140512B80: (18, '826768cbc3e05ea8a2ccf372ea1d3ec8271b41d1f673b2c916686b9d69d1c0cc'),
    0x140512BB0: (18, '826768cbc3e05ea8a2ccf372ea1d3ec8271b41d1f673b2c916686b9d69d1c0cc'),
    0x14069F440: (146, '9cdbdadf6d4a421f358b8f38bca186556e766a2a305d8c2ce770f0e93f2c4491'),
    0x1418F87A8: (8, 'f174277c3c92a7baca6f16dbe9122d66563574ea86a77756169e46829b9bfcbb'),
    0x1418F87B8: (8, 'ba2e6fe9d1f7242c57cc95fe05e45120afe63f3483ea348a086f6c4b0334077c'),
    0x1418F87E8: (8, '2261fce6122e288463f047cc30d32f14bfd1852c488bf760518e990287070e50'),
}
READ_ERRORS = (OSError, ValueError, TypeError, AttributeError, struct.error)
# Each derived constructor uses the same 0xE80 layout and native base initializer.
# Verified against its original script binding's allocator, not inferred by ID.
CONSTRUCTORS = {
    1: (0x1405043F0, '019eed8d38cdf724fb59d21213c9f94900808ad8d07df115082567c4e371cddd'),
    2: (0x1405058E0, '44e81f4803527a458875a476d1b305e2005460e8c698a76862224601eefbcbd4'),
    3: (0x140507170, 'eb9b3fb3aeeb396295749effb54eaae0245c44e7fabc1722ab89f53eec10ae47'),
    4: (0x140509530, '44744a410cd071d8056bbc7694c3b59b6cda13232239ab9b474e39528bd6aec0'),
    5: (0x14050B5A0, '3ae461771836c84e260c55138eaab4dae043029dfe0236156cd685fbf1205733'),
    6: (0x14050D840, 'e707d686c8b408810737ff175aeb4ac9affa84bd3b81ff2448bf0801960f3fc3'),
    7: (0x14050EF10, '258c5764d1a2662144b9361ce15c8bf831a0e1f2628abf14ad8d8d32aebbd59c'),
    8: (0x1405105E0, '1a99f2e24843b1bb48a394a03b04e6acf57c9535cf54601b202615068ac69115'),
    9: (0x1405119E0, '0ccb2b4f23d19aeef96ce818651c5b2f4d26de6a828af56f1e0e977f578c2311'),
    10: (0x140513070, '1dd2a4374c99528afe20f786665386b66bf8bc1b3b220563230d30720da7a55e'),
    11: (0x140514B80, '039c9b28fb90b502b5b69d373b089154264c6bbbe2c96dd9314e24005479f1af'),
    12: (0x140517D60, 'f900cea976a6a1a172bd6c7e83d7c0bc2564bcdffabbecea3fd40d28588dd080'),
    13: (0x1405190A0, '0847f4a6f66173542f12193dcf5a8cd839d2e51ede0c189795f24e0252984847'),
    14: (0x14051B220, '89c410bffbca7d67a58e104be871267bf718c12135aab54973d564807a46ac32'),
    15: (0x14051D0E0, 'd8e7a9eb1f9333ecc0141f2115c324ce35bfdad984cf0a1c37f03c442debb310'),
}
CONTRACTS.update({address: (44, digest) for address, digest in CONSTRUCTORS.values()})


class SummonRecorder:
    """One disk writer; submit/drain belong to the debugger thread, never block it.

    Only immutable validated event fields reach the worker, never game pointers
    or debugger operations. Executor shutdown drains accepted work on normal exit.
    """
    def __init__(self, capacity=64):
        self.capacity = capacity
        self.pending = deque()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lamina-save')

    def submit(self, record, store, lamina, launch, event, *, variant):
        if len(self.pending) >= self.capacity:
            raise OverflowError('Lamina save queue full; event was not queued')

        def save():
            started = time.perf_counter()
            result = record(store, lamina, launch, event, validated=True, variant=variant)
            return dict(event_id=event, result=result,
                        save_ms=round((time.perf_counter() - started) * 1000, 3))

        future = self.executor.submit(save)
        self.pending.append((event, future))

    def drain(self):
        completed = []
        while self.pending and self.pending[0][1].done():
            event, future = self.pending.popleft()
            try:
                completed.append(future.result())
            except Exception as exc:
                completed.append(dict(event_id=event, error=type(exc).__name__ + ': ' + str(exc)))
        return completed

    def close(self):
        self.executor.shutdown(wait=True)


def native_gate(budget, roster=ROSTER, playback=PLAYBACK, registry=REGISTRY, *, used=False,
                parameter=False):
    """Win64 leaf: RCX=wrapper, EAX=bool; only volatile GPRs/flags clobbered.

    Addresses are injectable for isolated machine-code tests. Native object
    lifetime remains the engine's responsibility; null checks cannot validate
    a dangling non-null pointer. No calls, stack writes, or debugger crossings.
    Parameter mode consumes EDX=index/XMM2=value and writes only owner+C2A0.
    """
    code, branches = bytearray(), []

    def emit(raw):
        code.extend(bytes.fromhex(raw))

    def imm64(raw, value):
        if type(value) is not int or not 0x10000 <= value < 0x7FFFFFF00000:
            raise ValueError('invalid gate address')
        emit(raw)
        code.extend(struct.pack('<Q', value))

    def reject(condition):
        emit('0f ' + condition)
        branches.append(len(code))
        code.extend(b'\0' * 4)

    if parameter:
        # The sole recovered command is index 0 with a 10-frame shot delay.
        emit('85 d2'); reject('85')           # reject other parameter indices
        emit('66 0f 7e d0 85 c0'); reject('8e')  # movd eax,xmm2; positive bits
        emit('3d 00 00 20 41'); reject('87')  # <=10.0; excludes NaN/Inf
    imm64('48 b8', budget)                    # mov rax, budget
    emit('80 38 01'); reject('85')            # cmp byte [rax], 1; jne fail
    emit('48 85 c9'); reject('84')            # test rcx, rcx
    emit('4c 8b 81 f8 0e 00 00')              # r8 = wrapper->player
    emit('4d 85 c0'); reject('84')
    imm64('48 b8', roster)
    emit('4c 8b 08 4d 85 c9'); reject('84')   # r9 = *roster
    emit('41 83 b9 50 f6 05 00 05'); reject('85')
    emit('41 80 79 10 00'); reject('85')
    imm64('48 b8', playback)
    emit('4c 8b 10 4d 85 d2'); reject('84')   # r10 = *playback
    emit('41 80 7a 08 00'); reject('85')
    emit('41 81 7a 10 f0 05 03 15'); reject('85')
    emit('41 0f be 90 71 0c 00 00')           # edx = signed player slot
    emit('83 fa 03'); reject('87')           # unsigned >3 includes negatives
    emit('41 3a 52 14'); reject('85')
    imm64('48 b8', registry)
    emit('4c 8b 18 4d 85 db'); reject('84')   # r11 = *registry
    emit('48 69 c2 d0 25 00 00')              # rax = slot * 0x25d0
    emit('4d 39 44 03 18'); reject('85')      # registered player identity
    emit('4d 39 80 58 0c 00 00'); reject('85')
    emit('49 8d 80 f0 0b 00 00')
    emit('49 39 80 60 0c 00 00'); reject('85')
    emit('41 8b 80 74 0c 00 00 ff c8 83 f8 14'); reject('87')
    emit('41 8b 80 78 0c 00 00')
    if parameter:
        emit('83 f8 0a'); reject('85')
    else:
        emit('ff c8 83 f8 0e'); reject('87')  # IDs 1..15
    emit('41 80 b8 11 56 00 00 02'); reject('87')  # level byte 0..2
    emit('41 8b 80 88 0c 00 00 85 c0'); reject('8e')
    emit('3d 00 00 80 7f'); reject('83')      # positive finite float bits
    if parameter:
        emit('49 8b 80 88 c2 00 00 48 85 c0'); reject('84')
        emit('4c 39 80 58 0c 00 00'); reject('85')
        emit('83 b8 90 38 00 00 0a'); reject('85')
        emit('48 83 b8 d0 3a 00 00 00'); reject('84')
        emit('41 83 b8 98 c2 00 00 01'); reject('85')
    for offset in ((0xC290,) if parameter else (0xC288, 0xC290)):
        emit('49 83 b8')
        code.extend(struct.pack('<I', offset))
        emit('00'); reject('85')
    for offset in ((0xC29C,) if parameter else (0xC298, 0xC29C)):
        emit('41 83 b8')
        code.extend(struct.pack('<I', offset))
        emit('00'); reject('85')
    if parameter:
        emit('f3 41 0f 11 90 a0 c2 00 00')  # only the local owner's shot field
    emit('b8 ' + ('00' if used or parameter else '01') + ' 00 00 00 c3')
    failure = len(code)
    emit('b8 ' + ('01' if used else '00') + ' 00 00 00 c3')
    for at in branches:
        struct.pack_into('<i', code, at, failure - (at + 4))
    return bytes(code)


def gate_jump(target):
    return bytes.fromhex('ff2500000000') + struct.pack('<Q', target) + b'\xcc\xcc'


def enemy_gauge_thunk(factory=HUD_FACTORY, descriptor=ENEMY_GAUGE_TYPE):
    # At the tail jump the builder has unwound its stack. Preserve the pending
    # factory arguments and give the extra Win64 call aligned shadow space.
    return (bytes.fromhex('51 52 41 50 41 51 48 83 ec 28 49 b9') + struct.pack('<Q', descriptor)
            + b'\x48\xb8' + struct.pack('<Q', factory) + bytes.fromhex('ff d0 48 83 c4 28 41 59 41 58 5a 59')
            + gate_jump(factory))


def gauge_preload_ready():
    path = Path(str(ROOT / 'game/resource/masterWindows/hud/preloadList/PreloadBattleHudList.unk'))
    return (path.resolve() == path and path.stat().st_size == 11571 and hashlib.sha256(path.read_bytes()).hexdigest()
            == '917647bd0c0424e6cdf83e336ac64e7d9cca31e476dceedbe2c9f3b679a8bac2')


def needs_enemy_gauge(d, hit):
    manager = pointer(d, OBJECTS)
    roster, play = pointer(d, ROSTER), pointer(d, PLAYBACK)
    if (hit.rcx != manager or (hit.rdx, hit.r8, hit.r9) != (5, 15, 0x141BAE8C0)
            or number(d, roster + 0x5F650) != 5 or number(d, play + 8, '<B') != 0
            or number(d, ENEMY_GAUGE_TYPE + 0x28, '<Q') != 0x1404B43B0):
        return False
    slot = number(d, play + 0x14, '<B')
    if slot > 3:
        return False
    owner = number(d, pointer(d, REGISTRY) + slot*0x25D0 + 0x18, '<Q')
    if owner:
        if (number(d, owner + 0xC58, '<Q') != owner or number(d, owner + 0xC71, '<B') != slot
                or (number(d, owner + 0xC78), number(d, owner + 0x5611, '<b')) != (12, 2)):
            return False
    elif number(d, roster + 0x3CD18) != 12:
        return False
    # Search the actual layer-5 objects, not just the HUD manager's sort list.
    head = pointer(d, manager + 0x48 + 5*8)
    node = number(d, head + 8, '<Q')
    seen = set()
    while node:
        if node in seen or len(seen) >= 512:
            return False
        seen.add(node)
        obj = number(d, node + 8, '<Q')
        if not obj:
            break
        if number(d, obj, '<Q') == ENEMY_GAUGE_VTABLE:
            return False
        node = number(d, node + 0x20, '<Q')
    return True


def checked_write(d, address, raw):
    if not d.write(address, raw) or d.read(address, len(raw)) != raw:
        raise OSError(f'write did not verify at {address:#x}')


def allowed_environment(exe, module, config, enabled):
    """Pure policy check; callers supply resolved paths to reject junctions."""
    return (enabled is True
            and PureWindowsPath(exe) == ROOT / 'game/exe/roi.exe'
            and PureWindowsPath(module) == ROOT / 'tools/sessions/lamina_combat.py'
            and PureWindowsPath(config.get('game_windows',config.get('game',''))) == ROOT / 'game'
            and config.get('lobby_mode') == 'server'
            and config.get('lobby_transport') == 'tls-public'
            and config.get('lobby_server') == '127.0.0.1:52711'
            and config.get('lobby_listen') == '127.0.0.1'
            and config.get('release_mode') in ('vpn','internet','lan') and bool(config.get('public_lobby_host')))


def environment(d):
    module = Path(__file__).resolve()
    tools = module.parent.parent
    config = json.loads((tools / 'config.json').read_text(encoding='utf-8-sig'))
    return allowed_environment(str(Path(d.exe).resolve()), str(module), config,
                               (tools / FLAG_NAME).is_file())


def read(d, address, size):
    if type(address) is not int or not 0x10000 <= address <= 0x7FFFFFFFFFFF - size:
        raise ValueError('invalid address')
    if not 0 < size <= 1024:
        raise ValueError('unbounded read')
    raw = d.read(address, size)
    if len(raw) != size:
        raise ValueError('incomplete read')
    return raw


def number(d, address, fmt='<I'):
    return struct.unpack(fmt, read(d, address, struct.calcsize(fmt)))[0]


def pointer(d, address):
    value = number(d, address, '<Q')
    if not 0x10000 <= value < 0x7FFFFFF00000:
        raise ValueError('missing native pointer')
    return value


def loa_load_error(d, script):
    """Bounded read of SQVM's last error for the rejected, idle Loa context."""
    try:
        if number(d, script + 0x28, '<B') != 4 or number(d, script + 0x278):
            return ''
        vm = pointer(d, pointer(d, script + 0x10) + 0x18)
        if number(d, vm + 0x70) != 0x08000010:  # OT_STRING
            return ''
        data = read(d, pointer(d, vm + 0x78) + 0x38, 192).split(b'\0', 1)[0]
        return ': ' + ascii(data.decode('utf-8', 'replace')) if data else ''
    except READ_ERRORS:
        return ''


class HandStillActive(ValueError):
    pass


def script_contexts(d, lamina, *, child=False):
    """Bounded read-only scan of the native pool; never manufacture ready flags."""
    if child:
        if lamina not in CHILD_ROWS:
            raise ValueError('Not a reviewed Lamina child')
        kind, folder = CHILD_ROWS[lamina]
        table = pointer(d, SUB_MANAGER)
        row = table + lamina * 0x608
        if (number(d, table + 0x304F8) or number(d, row + 0x40) != kind or
                read(d, row + 0x548, 128).split(b'\0', 1)[0] != folder):
            raise ValueError(f'Child {lamina} resource row unavailable')
    key = (3000 if child else 6000) + lamina
    manager = pointer(d, SCRIPT_POOL)
    if number(d, manager, '<Q') != 0x141900B68:
        raise ValueError('unknown script pool vtable')
    if number(d, manager + 0x38):
        raise ValueError('script pool is busy')
    head = pointer(d, manager + 0x48)
    count = number(d, manager + 0x50, '<Q')
    if count > 4096 or number(d, head + 0x41, '<B') != 1:
        raise ValueError('invalid script pool sentinel/count')
    todo, seen, contexts = [pointer(d, head + 8)], set(), []
    while todo:
        node = todo.pop()
        if node == head:
            continue
        if node in seen or len(seen) >= count:
            raise ValueError('cyclic or inconsistent script pool')
        seen.add(node)
        if number(d, node + 0x41, '<B'):
            raise ValueError('unexpected script pool sentinel')
        todo.extend((pointer(d, node), pointer(d, node + 0x10)))
        if number(d, node + 0x18) != key:
            continue
        script = pointer(d, node + 0x38)
        owner = number(d, node + 0x30, '<Q')
        if child and number(d, script, '<Q') != LOA_CHILD_VTABLE:
            raise ValueError(f'Child {lamina} context has unexpected bindings')
        if child and owner:
            raise HandStillActive(f'Previous child {lamina} is still active')
        if not owner:
            if (number(d, script + 0x28, '<B') != lamina
                    or number(d, script + 8, '<B') != 1
                    or number(d, script + 0x278)):
                detail = loa_load_error(d, script) if lamina == 4 else ''
                raise ValueError('Lamina script exists but is not ready' + detail)
            pointer(d, script + 0x10)
            contexts.append(script)
    if len(seen) != count or number(d, manager + 0x38):
        raise ValueError('script pool changed during inspection')
    if len(set(contexts)) != len(contexts):
        raise ValueError('duplicate Lamina script contexts')
    return contexts


def salamander_duration_compatibility(d, attempt, actor):
    """Restore the recovered time-based script's behavior, not an original quota.

    This executable initializes m_siShotMax to zero and immediately exits when
    m_siShotCount >= m_siShotMax. The older script only sets a lifetime. Supply
    a nonbinding signed shot ceiling for that exact unset state; native lifetime,
    owner loss, attack completion and cleanup checks remain authoritative.
    Called once at validated initialization, never once per frame.
    """
    if attempt['lamina'] != 10:
        raise ValueError('Salamander compatibility applied to another Lamina')
    values = preflight(d, attempt['wrapper'], require_idle=False)
    if any(values[k] != attempt[k] for k in values):
        raise ValueError('Salamander owner selection changed')
    if (pointer(d, actor + 0xC58) != attempt['player']
            or number(d, actor + 0x3890) != 10
            or number(d, actor + 0x3894) != attempt['level']
            or number(d, actor + 0x3994) != 0
            or pointer(d, attempt['player'] + 0xC288) != actor):
        raise ValueError('Salamander actor identity changed')
    script = pointer(d, actor + 0x3AD0)
    if number(d, script + 8, '<B') != 1:
        raise ValueError('Salamander script not ready')
    life, maximum = (number(d, actor + off, '<f') for off in (0x3988, 0x398C))
    quota, shots = (number(d, actor + off, '<i') for off in (0x4324, 0x4328))
    changed = (quota == 0 and shots == 0
               and math.isfinite(life) and math.isfinite(maximum)
               and 0 < life <= maximum <= 36000)
    if changed:
        checked_write(d, actor + 0x4324, struct.pack('<i', 0x7FFFFFFF))
    return dict(life=life, max_life=maximum, shot_limit_before=quota,
                shot_count=shots, duration_only_compatibility=changed)


def target_range_gate(enabled, roster=ROSTER, playback=PLAYBACK, registry=REGISTRY, *, setter=False):
    """Requested experimental range rule, not a recovered original restriction.

    Leaf Win64 replacements for target assignment/validity. All four registered
    actors in local Vs. Bot use their own capped range. Other modes keep native
    behavior. No Python callbacks, nonvolatile registers, or stale distance cache.
    """
    code, labels, jumps = bytearray(), {}, []

    def emit(raw):
        code.extend(bytes.fromhex(raw))

    def label(name):
        labels[name] = len(code)

    def jump(op, name):
        emit(op)
        jumps.append((len(code), name))
        code.extend(b'\0' * 4)

    def address(op, value):
        if not 0x10000 <= value < 0x7FFFFFF00000:
            raise ValueError('invalid targeting global address')
        emit(op)
        code.extend(struct.pack('<Q', value))

    emit('49 89 ca')                         # r10=player, preserve RCX
    address('48 b8', enabled)
    emit('80 38 01'); jump('0f 85', 'original')
    address('48 b8', roster)
    emit('48 8b 00 48 85 c0'); jump('0f 84', 'original')
    emit('83 b8 50 f6 05 00 05'); jump('0f 85', 'original')
    emit('80 78 10 00'); jump('0f 85', 'original')
    address('48 b8', playback)
    emit('48 8b 00 48 85 c0'); jump('0f 84', 'original')
    emit('80 78 08 00'); jump('0f 85', 'original')
    emit('81 78 10 f0 05 03 15'); jump('0f 85', 'original')
    emit('4d 85 d2'); jump('0f 84', 'original')
    emit('49 8b 82 58 0c 00 00 4c 39 d0'); jump('0f 85', 'original')
    emit('41 0f b6 82 71 0c 00 00 83 f8 03'); jump('0f 87', 'original')
    address('49 bb', registry)
    emit('4d 8b 1b 4d 85 db'); jump('0f 84', 'original')
    emit('48 69 c0 d0 25 00 00 49 8b 44 03 18 4c 39 d0'); jump('0f 85', 'original')
    emit('41 83 ba 74 0c 00 00 01'); jump('0f 82', 'original')
    emit('41 83 ba 74 0c 00 00 15'); jump('0f 87', 'original')
    if setter:
        emit('44 0f b6 ca')                  # r9d=DL candidate
    else:
        emit('41 83 ba 14 eb 00 00 00'); jump('0f 85', 'original')
        emit('45 0f b6 8a 10 eb 00 00')
    emit('41 83 f9 03'); jump('0f 87', 'reject')
    emit('49 69 c1 d0 25 00 00 4d 8b 44 03 18') # r8=target
    emit('4d 85 c0'); jump('0f 84', 'reject')
    emit('4d 39 d0'); jump('0f 84', 'reject')
    emit('4d 39 80 58 0c 00 00'); jump('0f 85', 'reject')
    # Positive finite base, multiplier and cap. Do not turn invalid data into
    # infinite lock range; normal zero range is treated as no eligible target.
    for offset, xmm in ((0xC098, 'c0'), (0x11674, 'c8'), (0x11D6C, 'd0')):
        emit('41 8b 82'); code.extend(struct.pack('<I', offset))
        emit('85 c0'); jump('0f 8e', 'reject')
        emit('3d 00 00 80 7f'); jump('0f 83', 'reject')
        emit('66 0f 6e ' + xmm)
    emit('f3 0f 5d ca f3 0f 59 c1')          # base * min(multiplier,cap)
    emit('66 0f 7e c0 3d 00 00 80 7f'); jump('0f 83', 'reject')
    emit('f3 41 0f 10 88 80 00 00 00 f3 41 0f 5c 8a 80 00 00 00 f3 0f 59 c9')
    for offset in (0x84, 0x88):
        emit('f3 41 0f 10 90'); code.extend(struct.pack('<I', offset))
        emit('f3 41 0f 5c 92'); code.extend(struct.pack('<I', offset))
        emit('f3 0f 59 d2 f3 0f 58 ca')
    emit('f3 0f 51 c9 0f 2e c8'); jump('0f 8a', 'reject') # distance, reject NaN
    jump('0f 87', 'reject')                  # distance > range; equality stays locked
    if setter:
        emit('45 88 8a 10 eb 00 00 c3')
    else:
        emit('b0 01 c3')
    label('reject')
    emit('41 c6 82 10 eb 00 00 ff 31 c0 c3')
    label('original')
    if setter:
        emit('88 91 10 eb 00 00 c3')
    else:
        # Original predicate: no-lock flag or negative target ID means false.
        emit('83 b9 14 eb 00 00 00'); jump('0f 85', 'false')
        emit('0f b6 81 10 eb 00 00 84 c0 0f 99 c0 c3')
        label('false'); emit('31 c0 c3')
    for at, name in jumps:
        struct.pack_into('<i', code, at, labels[name] - at - 4)
    return bytes(code)


def script_bootstrap(pool=SCRIPT_POOL, allocate=0x1407B7160,
                    construct=0x1405043F0, configure=0x14052ADB0,
                    register=0x140538B00, *, lamina=1, child=False):
    """Game-thread thunk: native allocation/constructor/load, then guarded return.

    The pool owns the registered object and destroys it on normal battle cleanup.
    RBX is preserved; each call has aligned Win64 shadow space. No actor is made.
    """
    if type(lamina) is not int or (lamina not in CHILD_ROWS if child else lamina not in CONSTRUCTORS):
        raise ValueError('invalid bootstrap Lamina')
    code = bytearray.fromhex('53 48 83 ec 20 b9')
    code.extend(struct.pack('<I', 0xE50 if child else 0xE80))

    def call(address):
        code.extend(b'\x48\xb8' + struct.pack('<Q', address) + b'\xff\xd0')

    call(allocate)
    code.extend(bytes.fromhex('48 85 c0 0f 84'))
    branch = len(code)
    code.extend(b'\0' * 4)
    code.extend(bytes.fromhex('48 89 c3 48 89 c1'))
    call(construct)
    code.extend(bytes.fromhex('48 89 d9 ba') + struct.pack('<I', lamina)
                + bytes.fromhex('45 31 c0'))
    call(configure)
    code.extend(b'\x48\xb8' + struct.pack('<Q', pool))
    code.extend(bytes.fromhex('48 8b 08 48 89 da 41 b8') + struct.pack('<I', 3 if child else 6)
                + bytes.fromhex('41 b9 01 00 00 00'))
    call(register)
    code.extend(bytes.fromhex('48 89 d8'))
    end = len(code)
    code.extend(bytes.fromhex('48 83 c4 20 5b c3'))
    struct.pack_into('<i', code, branch, end - branch - 4)
    return bytes(code)


def preflight(d, wrapper, *, require_idle=True):
    """Read-only evidence of eligibility, not a guarantee resources will load."""
    import release_lamina
    if not release_lamina.allowed():
        raise ValueError("Lamina locked by progression")
    player = pointer(d, wrapper + 0xEF8)
    roster, playback = pointer(d, ROSTER), pointer(d, PLAYBACK)
    if number(d, roster + 0x5F650) != 5:
        raise ValueError('only Vs. Bot mode 5 is permitted')
    if number(d, playback + 8, '<B') != 0:
        raise ValueError('replay is forbidden')
    if number(d, playback + 0x10) != 0x150305F0:
        raise ValueError('battle header unavailable')
    slot = number(d, player + 0xC71, '<b')
    if not 0 <= slot < 4 or slot != number(d, playback + 0x14, '<B'):
        raise ValueError('not the local player')
    registry = pointer(d, REGISTRY)
    if pointer(d, registry + slot * 0x25D0 + 0x18) != player:
        raise ValueError('player is not registered')
    if pointer(d, player + 0xC58) != player or pointer(d, player + 0xC60) != player + 0xBF0:
        raise ValueError('owner links are incomplete')
    if not 1 <= number(d, player + 0xC74) <= 21:
        raise ValueError('invalid player character')
    hp = number(d, player + 0xC88, '<f')
    if not math.isfinite(hp) or hp <= 0:
        raise ValueError('player is not alive')
    if number(d, roster + 0x10, '<B') != 0:
        raise ValueError('native ID-6 override mode is excluded')
    lamina = number(d, player + 0xC78, '<i')
    level = number(d, player + 0x5611, '<b')
    if not 1 <= lamina <= 15 or not 0 <= level <= 2:
        raise ValueError('invalid Lamina ID/zero-based skill level')
    if require_idle and any(number(d, player + off, '<Q') for off in (0xC288, 0xC290)):
        raise ValueError('Lamina actor slot is occupied')
    if require_idle and any(number(d, player + off) for off in (0xC298, 0xC29C)):
        raise ValueError('Lamina active flag is set')
    manager = pointer(d, MANAGER)
    if number(d, manager, '<Q') != 0x141900D38:
        raise ValueError('unknown Lamina manager vtable')
    if number(d, manager + 0x10120) != 0:
        raise ValueError('Lamina manager is busy')
    pointer(d, manager + 0x100C0)
    pointer(d, manager + 0x100D8)
    objects = pointer(d, OBJECTS)
    pointer(d, objects + 0x1A8)
    row = manager + 8 + lamina * 0x804
    scale = number(d, row, '<f')
    if not math.isfinite(scale) or not 0 < scale < 1000:
        raise ValueError('Lamina table row is not populated')
    resource = read(d, row + 4, 256).split(b'\0', 1)[0]
    if not resource or any(c < 32 or c > 126 for c in resource):
        raise ValueError('Lamina resource name is absent')
    # Detect selection/owner replacement while gathering the snapshot.
    if pointer(d, wrapper + 0xEF8) != player or number(d, player + 0xC78, '<i') != lamina:
        raise ValueError('player selection changed during inspection')
    return dict(player=player, slot=slot, lamina=lamina, level=level)


def confirm_created(d, attempt):
    values = preflight(d, attempt['wrapper'], require_idle=False)
    if any(values[k] != attempt[k] for k in values):
        raise ValueError('native return does not match dispatched player/selection')
    player, lamina, level = values['player'], values['lamina'], values['level']
    actors = []
    for index in range(2 if lamina == 3 else 1):
        actor = pointer(d, player + 0xC288 + index * 8)
        if actor == player or actor in actors:
            raise ValueError('invalid/duplicate Lamina actor pointer')
        if number(d, player + 0xC298 + index * 4) != 1:
            raise ValueError('new Lamina active flag missing')
        if pointer(d, actor + 0xC58) != player:
            raise ValueError('new Lamina owner mismatch')
        if any(pointer(d, actor + off) != player + 0xBF0 for off in (0xC60, 0xC68)):
            raise ValueError('new Lamina owner context mismatch')
        if number(d, actor + 0xC71, '<b') != values['slot']:
            raise ValueError('new Lamina player slot mismatch')
        if (number(d, actor + 0x3890) != lamina or number(d, actor + 0x3894) != level
                or number(d, actor + 0x3994) != index):
            raise ValueError('new Lamina identity mismatch')
        actors.append(actor)
    if lamina != 3 and (number(d, player + 0xC290, '<Q') or number(d, player + 0xC29C)):
        raise ValueError('unexpected paired actor')
    return actors


def refill_summon_charges(d, attempt):
    """Experimental local-player refill using the recovered AddLaminaMagazineNum layout."""
    values = preflight(d, attempt['wrapper'], require_idle=False)
    if any(values[k] != attempt[k] for k in values):
        raise ValueError('player/selection changed before test-charge refill')
    magazine = pointer(d, values['player'] + 0x13818)
    capacity = number(d, magazine + 0x24, '<i') + number(d, magazine + 0x28, '<i')
    current = number(d, magazine + 0x4C, '<i')
    if not 1 <= capacity <= 99 or not 0 <= current <= capacity:
        raise ValueError('invalid Lamina magazine capacity/count')
    checked_write(d, magazine + 0x4C, struct.pack('<i', capacity))
    checked_write(d, magazine + 0x58, b'\x01')
    checked_write(d, magazine + 0x5A, b'\x00')
    return capacity


def verify_code(d):
    for address, expected in SITES.items():
        if any(address <= bp < address + len(expected) for bp in getattr(d, 'bps', {})):
            raise ValueError(f'breakpoint already owned at {address:#x}')
        if read(d, address, len(expected)) != expected:
            raise ValueError(f'stub guard mismatch at {address:#x}')
    for address, (size, digest) in CONTRACTS.items():
        if hashlib.sha256(read(d, address, size)).hexdigest() != digest:
            raise ValueError(f'factory code guard mismatch at {address:#x}')


class DullahanLink:
    """One local beta owner's marks. Only ordinary committed hits can produce a copy."""
    def __init__(self, d, attempt):
        self.d, self.owner, self.slot = d, attempt['player'], attempt['slot']
        self.marks = {}

    def fighter(self, actor):
        d = self.d
        slot = number(d, actor + 0xC71, '<b')
        if (not 0 <= slot < 4 or number(d, actor + 0xC58, '<Q') != actor or
                number(d, pointer(d, REGISTRY) + slot * 0x25D0 + 0x18, '<Q') != actor):
            raise ValueError('Unregistered combatant')
        team = number(d, actor + 0xC7C)
        if team not in (0, 1):
            raise ValueError('Unexpected team')
        return slot, team

    def local_team(self):
        d = self.d
        roster, play = pointer(d, ROSTER), pointer(d, PLAYBACK)
        if (number(d, roster + 0x5F650) != 5 or number(d, play + 8, '<B') or
                number(d, play + 0x10) != 0x150305F0 or number(d, play + 0x14, '<B') != self.slot or
                number(d, self.owner + 0xC78) != 14 or number(d, self.owner + 0x5611, '<b') != 1):
            raise ValueError('Not the local Dullahan beta battle')
        slot, team = self.fighter(self.owner)
        if slot != self.slot:
            raise ValueError('Local combatant changed')
        return team

    def mark(self, actor):
        self.marks.pop(actor, None)
        team = self.local_team()
        slot, enemy = self.fighter(actor)
        if (team != enemy and number(self.d, actor + 0x11E30, '<b') == self.slot
                and number(self.d, actor + 0xF938, '<f') > 0):
            # Discard stale addresses when a slot gets a replacement actor.
            self.marks = {a: s for a, s in self.marks.items() if s != slot}
            self.marks[actor] = slot
            self.d.log(f'LAMINA_DULL beta mark slot={slot}')

    def transfer(self, victim, remaining):
        if not self.marks:
            return
        d = self.d
        team = self.local_team()
        source_slot, enemy = self.fighter(victim)
        old = number(d, victim + 0xC88, '<f')
        if team == enemy or not (math.isfinite(old) and 0 <= remaining < old):
            return
        for actor, slot in tuple(self.marks.items()):
            if number(d, pointer(d, REGISTRY) + slot * 0x25D0 + 0x18, '<Q') != actor:
                self.marks.pop(actor)
                continue
            if (self.fighter(actor) != (slot, enemy) or number(d, actor + 0xF930) != 1
                    or not number(d, actor + 0xF938, '<f') > 0 or number(d, actor + 0xC50)):
                self.marks.pop(actor)
                continue
            if actor == victim or number(d, actor + 0xC8C) or number(d, actor + 0xC90):
                continue
            hp = number(d, actor + 0xC88, '<f')
            if not math.isfinite(hp) or hp <= 0:
                continue
            after = max(0.0, hp - (old - remaining))
            checked_write(d, actor + 0xC88, struct.pack('<f', after))
            d.log(f'LAMINA_DULL shared from={source_slot} to={slot} loss={old-remaining:.3f} hp={hp:.3f}->{after:.3f}')


class GriffinContacts:
    """Observe shot guidance and paired defender contact processing; never change combat data."""
    def __init__(self, d, attempt):
        self.d, self.owner, self.slot = d, attempt['player'], attempt['slot']
        self.pending = {}
        self.count = 0
        self.pair_counts = [0, 0]

    def observe(self, site, hit):
        d, owner = self.d, self.owner
        if site == SHAPE_PAIR:
            # The collision manager selected a non-null partner from a 0x50-byte
            # intersection record. No eligibility filter or hit callback has run.
            if (number(d, hit.rdx + 0x138, '<Q') != owner or
                    number(d, hit.rdx + 0x228, '<Q') not in (0x140549220, 0x1407A7170) or
                    number(d, hit.rdx + 0x234) != 2):
                return
        elif self.count >= 400 or (site != SHOT_LOST_AIM and hit.rsi != owner):
            return
        play, roster, registry = (pointer(d, addr) for addr in (PLAYBACK, ROSTER, REGISTRY))
        entry = registry + self.slot * 0x25D0
        if (number(d, play + 8, '<B') or number(d, roster + 0x5F650) != 5
                or number(d, play + 0x14, '<B') != self.slot or number(d, entry + 0x18, '<Q') != owner
                or number(d, owner + 0xC58, '<Q') != owner or number(d, owner + 0xC78) != 11
                or number(d, owner + 0x5611, '<b') != 2):
            return
        key = (hit.rsp, hit.rbx)
        if site == SHAPE_PAIR:
            shell = number(d, hit.rbx + 0x138, '<Q')
            if (not shell or number(d, pointer(d, shell) + 0x2A0, '<Q') != 0x1406E4060 or
                    number(d, hit.rbx + 0x234) != 1):
                return
            team = number(d, owner + 0xC7C)
            if team not in (0, 1) or number(d, shell + 0xB4C) != 1 - team:
                return
            i = hit.rdi
            if (i % 0x50 or not 0 <= i // 0x50 < min(4096, number(d, hit.rsi + 8)) or
                    number(d, hit.rbx + 0x298, '<Q') != hit.rsi or
                    number(d, hit.rsi + i + 0x48, '<Q') != hit.rdx):
                return
            xyz = struct.unpack('<3f', read(d, hit.rsi + i + 0x10, 12))
            if not all(map(math.isfinite, xyz)):
                return
            published = number(d, entry + 0xC0, '<B')
            frames = number(d, owner + 0x10E4, '<i')
            active = int(bool(published or frames > 0))
            if self.pair_counts[active] >= (300 if active else 80):
                return
            self.pair_counts[active] += 1
            d.log('LAMINA_GRIFFIN ' + json.dumps(dict(event='shape_overlap', shell=hex(shell),
                  point=xyz, published_dodge=published, shot_frames=frames,
                  shot_modifier=number(d, owner + 0x119D0, '<f'),
                  invulnerable=number(d, entry + 0x10C, '<B'),
                  hp_before=number(d, owner + 0xC88, '<f'))))
            return
        if site == CONTACT_END:
            row = self.pending.pop(key, None)
            if not row:
                return
            row['hp_after'] = number(d, owner + 0xC88, '<f')
        else:
            if site == CONTACT_BEGIN:
                self.pending.pop(key, None)
                shell = number(d, hit.rbx + 0x18, '<Q')
                source = number(d, hit.rbx + 0xC, '<b')
                if not shell or not 0 <= source < 4:
                    return
                # Only count shells using the reviewed native flight update. Never
                # classify a sub-character or a melee hit as a projectile by proximity.
                if number(d, pointer(d, shell) + 0x2A0, '<Q') != 0x1406E4060:
                    return
            else:
                shell = hit.rbx
                if number(d, shell + 0x14F0, '<b') != self.slot:
                    return
            team = number(d, owner + 0xC7C)
            if team not in (0, 1) or number(d, shell + 0xB4C) != 1 - team:
                return
            row = dict(event='contact' if site == CONTACT_BEGIN else 'lost_tracking',
                       shell=hex(shell), shot_modifier=number(d, owner + 0x119D0, '<f'),
                       shot_frames=number(d, owner + 0x10E4, '<i'),
                       published_dodge=number(d, entry + 0xC0, '<B'),
                       invulnerable=number(d, entry + 0x10C, '<B'),
                       hp_before=number(d, owner + 0xC88, '<f'))
            if site == CONTACT_BEGIN:
                if len(self.pending) < 16:
                    self.pending[key] = dict(row, source=source)
                return
        self.count += 1
        d.log('LAMINA_GRIFFIN ' + json.dumps(row))


def setup(d):
    """Register deferred installation, or return False without touching memory."""
    try:
        if not environment(d):
            return False
    except READ_ERRORS:
        return False
    recorder = SummonRecorder()

    def saved_events(debugger):
        for completion in recorder.drain():
            debugger.log('LAMINA_COMBAT async save ' + json.dumps(completion))

    def arm(debugger):
        try:
            if not environment(debugger):
                return
            verify_code(debugger)
        except READ_ERRORS as exc:
            debugger.log(f'LAMINA_COMBAT disabled: {exc}')
            return
        sequence = [0]
        log_budget = [12]
        pending = [None]
        loading = [None]
        initializing = [None]
        salamander_watch = [None]
        loa_watch = [None]
        dull_link = [None]
        dull_handlers = {}
        dull_returns = {}
        griffin = [None]
        griffin_handlers, griffin_returns = {}, {}
        launch_id = 'launch.' + uuid.uuid4().hex

        def inspect(hit):
            return preflight(debugger, hit.rcx)

        def summon(hit):
            started = time.perf_counter()
            try:
                saved_events(debugger)
                # A closed gate also protects an in-flight attempt from duplicate callbacks.
                if number(debugger, budget, '<B') != 1:
                    hit.skip_call(0)
                    return
                if not environment(debugger):
                    checked_write(debugger, budget, b'\0')
                    raise ValueError('experimental opt-in or isolation was removed')
                values = inspect(hit)
                checked_write(debugger, budget, b'\0')
                resources = [(values['lamina'], 2 if values['lamina'] == 3 else 1)]
                if values['lamina'] == 4:
                    resources.append((LOA_CHILD, 2))
                elif values['lamina'] == 13 and values['level'] == 2:
                    resources.append((IFRIT_CHILD, 4))
                # In-use hands may still be finishing the previous firing cycle.
                # Do not consume the retry gate or register duplicates in that case.
                pools = [(key, required, script_contexts(debugger, key, child=key in CHILD_ROWS))
                         for key, required in resources]
                # Results scripts also call this stub for non-local actors. Reject
                # those before acquiring the gate shared with the next battle.
                # Once accepted, reopen only after successful initialization.
                sequence[0] += 1
                attempt = dict(values, wrapper=hit.rcx, tid=hit.tid,
                               event_id=f'native.{sequence[0]}')
                attempt['missing'] = [(key, required, contexts) for key, required, contexts
                                      in pools if len(contexts) < required]
                if values['lamina'] == 4:
                    debugger.log(f"LAMINA_LOA hand contexts ready before summon={len(pools[-1][2])}")
                elif resources[-1][0] == IFRIT_CHILD:
                    debugger.log(f"LAMINA_IFRIT bomb contexts ready before summon={len(pools[-1][2])}")
                advance(hit, attempt)
            except READ_ERRORS as exc:
                if isinstance(exc, HandStillActive):
                    checked_write(debugger, budget, b'\x01')
                pending[0] = None
                loading[0] = None
                hit.skip_call(0)
                if log_budget[0]:
                    log_budget[0] -= 1
                    debugger.log(f'LAMINA_COMBAT summon rejected: {exc}')
            finally:
                elapsed = (time.perf_counter() - started) * 1000
                if elapsed >= 5:
                    debugger.log(f'LAMINA_TIMING summon_callback_ms={elapsed:.3f}')

        def advance(hit, attempt):
            while attempt['missing']:
                key, required, _ = attempt['missing'].pop(0)
                contexts = script_contexts(debugger, key, child=key in CHILD_ROWS)
                if len(contexts) >= required:
                    continue
                attempt.update(resource=key, required=required, contexts_before=contexts,
                               loads_remaining=required - len(contexts))
                loading[0] = attempt
                hit.setreg('rip', bootstrap_entries[key])
                debugger.log(f"LAMINA_COMBAT loading script for ID {key} before actor creation")
                return
            dispatch(hit, attempt)

        def dispatch(hit, attempt):
            if (attempt['lamina'], attempt['level']) == (14, 1):
                arm_dull(attempt)
            if (attempt['lamina'], attempt['level']) == (11, 2):
                try:
                    arm_griffin(attempt)
                except READ_ERRORS as exc:
                    debugger.log(f'LAMINA_GRIFFIN observer unavailable: {exc}')
            attempt['dispatch_started'] = time.perf_counter()
            attempt['completed'] = []
            pending[0] = attempt
            hit.setreg('rcx', attempt['player'])
            hit.setreg('rip', NATIVE_SUMMON)
            debugger.log('LAMINA_COMBAT native dispatch requested ' + json.dumps(attempt)
                         + '; initialization/animation/damage unverified')

        def bootstrapped(hit):
            attempt = loading[0]
            if attempt is None or hit.tid != attempt['tid']:
                return
            loading[0] = None
            try:
                if not environment(debugger):
                    raise ValueError('experimental isolation was removed')
                values = preflight(debugger, attempt['wrapper'])
                if any(values[k] != attempt[k] for k in values):
                    raise ValueError('selection changed during script loading')
                key = attempt['resource']
                contexts = script_contexts(debugger, key, child=key in CHILD_ROWS)
                before = attempt['contexts_before']
                if (not hit.rax or hit.rax not in contexts or hit.rax in before
                        or not set(before) < set(contexts)):
                    raise ValueError('new Lamina script did not become ready; actor creation cancelled')
                attempt['loads_remaining'] -= 1
                required = attempt['required']
                if len(contexts) < required:
                    if attempt['loads_remaining'] <= 0:
                        raise ValueError('paired Lamina script allocation did not complete')
                    attempt['contexts_before'] = contexts
                    loading[0] = attempt
                    hit.setreg('rip', bootstrap_entries[key])
                else:
                    debugger.log(f"LAMINA_COMBAT ID {key} scripts ready in native pool")
                    advance(hit, attempt)
            except READ_ERRORS as exc:
                hit.skip_call(0)
                debugger.log(f'LAMINA_COMBAT script bootstrap rejected: {exc}')

        def created(hit):
            started = time.perf_counter()
            attempt = pending[0]
            if attempt is None or hit.tid != attempt['tid'] or hit.rdi != attempt['player']:
                return
            pending[0] = None
            try:
                if not environment(debugger):
                    raise ValueError('experimental isolation was removed')
                actors = confirm_created(debugger, attempt)
                initializing[0] = dict(attempt, actors=actors)
                if attempt['lamina'] == 10:
                    salamander_watch[0] = dict(attempt, actor=actors[0])
                elif attempt['lamina'] == 4:
                    loa_watch[0] = dict(attempt, actor=actors[0], samples=0, initialized_hands=0)
                debugger.log('LAMINA_COMBAT actor allocation confirmed; waiting for initialization')
                finish_initialization(initializing[0], started)
            except READ_ERRORS as exc:
                debugger.log(f'LAMINA_COMBAT actor confirmation failed: {exc}')
            finally:
                elapsed = (time.perf_counter() - started) * 1000
                if elapsed >= 5:
                    debugger.log(f'LAMINA_TIMING created_callback_ms={elapsed:.3f}')

        def initialized(hit):
            started = time.perf_counter()
            attempt = initializing[0] or pending[0]
            if attempt is None:
                return
            actor = hit.rdi - 0xBF0
            if actor in attempt['completed']:
                return
            if 'actors' in attempt and actor not in attempt['actors']:
                return
            try:
                if not environment(debugger):
                    raise ValueError('experimental isolation was removed')
                if pointer(debugger, actor + 0xC58) != attempt['player']:
                    return
                index = number(debugger, actor + 0x3994)
                expected = 2 if attempt['lamina'] == 3 else 1
                if (number(debugger, actor + 0x3890) != attempt['lamina']
                        or number(debugger, actor + 0x3894) != attempt['level']
                        or index >= expected or len(attempt['completed']) >= expected):
                    return
                script = pointer(debugger, actor + 0x3AD0)
                if number(debugger, script + 8, '<B') != 1:
                    raise ValueError('initialized actor script is not ready')
                if attempt['lamina'] == 10:
                    debugger.log('LAMINA_SALAMANDER initial ' + json.dumps(dict(
                        event_id=attempt['event_id'],
                        **salamander_duration_compatibility(debugger, attempt, actor),
                        lifetime_cube_rate=number(debugger, attempt['player'] + 0x11D18, '<f'))))
                attempt['completed'].append(actor)
                debugger.log(f"LAMINA_COMBAT initialized actor event={attempt['event_id']} index={index}")
                finish_initialization(attempt, started)
            except (OSError, ValueError, TypeError, AttributeError, struct.error, OverflowError, ImportError) as exc:
                debugger.log(f'LAMINA_COMBAT confirmation/recording failed; no success claim: {exc}')

        def finish_initialization(attempt, started):
            if 'actors' not in attempt or set(attempt['completed']) != set(attempt['actors']):
                return
            try:
                initializing[0] = None
                capacity = refill_summon_charges(debugger, attempt)
                import roi_lamina
                store = Path(__file__).resolve().parent.parent / 'lamina_profile.json'
                expected_store = ROOT / 'tools/lamina_profile.json'
                if PureWindowsPath(str(store.resolve())) != expected_store:
                    raise ValueError('experimental profile path escaped clone')
                recorder.submit(roi_lamina.record_summon, store, attempt['lamina'], launch_id,
                                attempt['event_id'], variant=attempt['level'])
                checked_write(debugger, budget, b'\x01')
                debugger.log('LAMINA_COMBAT native initialization returned ' + json.dumps(dict(
                    actors=attempt['actors'], save_queued=True, event_id=attempt['event_id'],
                    test_charges=capacity,
                    callback_ms=round((time.perf_counter() - started) * 1000, 3),
                    dispatch_to_init_ms=round((started - attempt['dispatch_started']) * 1000, 3))))
            except (OSError, ValueError, TypeError, AttributeError, struct.error, OverflowError, ImportError) as exc:
                debugger.log(f'LAMINA_COMBAT confirmation/recording failed; no success claim: {exc}')

        def persistent_return(address, offset, callback):
            # Both guarded sites begin with mov rbx,[rsp+disp8]. Emulate that
            # flag-neutral load instead of disarming INT3 for a single step:
            # another actor's worker thread can reach the same site meanwhile.
            def handler(hit):
                hit.setreg('rbx', number(debugger, hit.rsp + offset, '<Q'))
                hit.setreg('rip', address + 5)
                callback(hit)
            return handler

        def disarm_dull():
            dull_link[0] = None
            for site, handler in dull_handlers.items():
                installed = debugger.bps.get(site)
                if installed and installed[1] is handler:
                    checked_write(debugger, site, DULL_STORES[site][:1])
                    del debugger.bps[site]

        def arm_dull(attempt):
            link = dull_link[0]
            if link and (link.owner, link.slot) == (attempt['player'], attempt['slot']):
                return
            disarm_dull()
            try:
                for site, handler in dull_handlers.items():
                    raw = DULL_STORES[site]
                    if site in debugger.bps or read(debugger, site, len(raw)) != raw:
                        raise ValueError('Unexpected Dullahan hit site')
                    debugger.bp(site, handler)
                    if read(debugger, site, 1) != b'\xcc':
                        raise OSError('Dullahan observation failed to arm')
                dull_link[0] = DullahanLink(debugger, attempt)
            except Exception:
                disarm_dull()
                raise

        def dull_handler(site):
            def observe(hit):
                # Always execute the displaced native store, including on rejected hits.
                hit.setreg('rip', dull_returns[site])
                link = dull_link[0]
                if not link:
                    return
                try:
                    if site == DULL_MARK:
                        link.mark(hit.r14)
                    elif link.marks:
                        link.transfer(hit.rsi, hit.ctx.get(0x1A0, '<f'))  # Win64 CONTEXT.Xmm0
                except READ_ERRORS as exc:
                    if log_budget[0]:
                        log_budget[0] -= 1
                        debugger.log(f'LAMINA_DULL ignored event: {exc}')
            return observe

        def disarm_griffin():
            griffin[0] = None
            for site, handler in griffin_handlers.items():
                installed = debugger.bps.get(site)
                if installed and installed[1] is handler:
                    checked_write(debugger, site, GRIFFIN_SITES[site][:1])
                    del debugger.bps[site]

        def arm_griffin(attempt):
            current = griffin[0]
            if current and (current.owner, current.slot) == (attempt['player'], attempt['slot']):
                return
            disarm_griffin()
            try:
                for site, handler in griffin_handlers.items():
                    raw = GRIFFIN_SITES[site]
                    if site in debugger.bps or read(debugger, site, len(raw)) != raw:
                        raise ValueError('Griffin site already patched or changed')
                    debugger.bp(site, handler)
                    if read(debugger, site, 1) != b'\xcc':
                        raise OSError('Griffin probe did not arm')
                griffin[0] = GriffinContacts(debugger, attempt)
                debugger.log('LAMINA_GRIFFIN armed; shape overlaps, contacts and lost tracking logged separately')
            except Exception:
                disarm_griffin()
                raise

        def griffin_handler(site):
            def observe(hit):
                hit.setreg('rip', griffin_returns[site])
                try:
                    if griffin[0]:
                        griffin[0].observe(site, hit)
                except READ_ERRORS:
                    pass
            return observe

        def menu_reset(hit):
            disarm_griffin()
            disarm_dull()
            previous_menu(hit)
            pending[0] = loading[0] = initializing[0] = None
            salamander_watch[0] = None
            loa_watch[0] = None
            enabled = environment(debugger)
            checked_write(debugger, budget, b'\x01' if enabled else b'\0')
            debugger.log(f'LAMINA_COMBAT menu reset enabled={enabled}; no XP for unfinished attempts')

        def lifetime_return(hit):
            # Native setter ends in RET with RAX=actor. Preserve that return
            # value and keep INT3 armed; this is observation, not a lifetime fix.
            actor = hit.rax
            hit.skip_call(actor)
            watched = salamander_watch[0]
            if watched is None or watched['actor'] != actor:
                return
            try:
                if (not environment(debugger) or number(debugger, actor + 0x3890) != 10
                        or pointer(debugger, actor + 0xC58) != watched['player']):
                    return
                salamander_watch[0] = None
                debugger.log('LAMINA_SALAMANDER entry_lifetime ' + json.dumps(dict(
                    event_id=watched['event_id'], life=number(debugger, actor + 0x3988, '<f'),
                    max_life=number(debugger, actor + 0x398C, '<f'),
                    lifetime_cube_rate=number(debugger, watched['player'] + 0x11D18, '<f'))))
            except READ_ERRORS as exc:
                debugger.log(f'LAMINA_SALAMANDER lifetime observation unavailable: {exc}')

        def loa_shot_return(hit):
            # Preserve the native return and its following null check. Observe
            # at most the first two hand launches from a confirmed local Loa.
            hit.setreg('rsi', hit.rax)
            hit.setreg('rip', LOA_SHOT_RETURN + 3)
            watch = loa_watch[0]
            if not watch or watch['actor'] != hit.rdi or watch['samples'] >= 2:
                return
            try:
                if (pointer(debugger, hit.rdi + 0xC58) != watch['player']
                        or number(debugger, hit.rdi + 0x3890) != 4):
                    return
                offsets = struct.unpack('<fff', read(debugger, hit.rsp + 0x60, 12))
                watch['samples'] += 1
                debugger.log('LAMINA_LOA hand launch ' + json.dumps(dict(event=watch['event_id'],
                    variant=watch['level'], hand=hit.rbp & 0xffffffff, offsets=offsets,
                    factory_returned_actor=bool(hit.rax))))
            except READ_ERRORS as exc:
                debugger.log(f'LAMINA_LOA launch observation unavailable: {exc}')

        def loa_child_ready(hit):
            hit.setreg('rax', hit.rsi)
            hit.setreg('rip', LOA_CHILD_READY + 3)
            watch = loa_watch[0]
            if not watch or watch['initialized_hands'] >= 2:
                return
            actor = hit.rdi
            try:
                if (number(debugger, actor + 0xC58, '<Q') != watch['player'] or
                        number(debugger, actor + 0xC68, '<Q') != watch['actor'] + 0xBF0):
                    return
                watch['initialized_hands'] += 1
                debugger.log('LAMINA_LOA hand initialized ' + json.dumps(dict(
                    event=watch['event_id'], initialized=number(debugger, hit.rsi),
                    script_attached=bool(number(debugger, actor + 0x39D8, '<Q')),
                    effect_layers=number(debugger, actor + 0x3A50),
                    attack_flag=number(debugger, actor + 0x2CB5, '<B'),
                    native_variant=number(debugger, actor + 0x3A34))))
            except READ_ERRORS as exc:
                debugger.log(f'LAMINA_LOA child observation unavailable: {exc}')

        def build_hud(hit):
            hit.setreg('rip', HUD_FACTORY)
            try:
                if environment(debugger) and gauge_preload_ready() and needs_enemy_gauge(debugger, hit):
                    hit.setreg('rip', gauge_entry)
                    debugger.log('LAMINA_ABAO adding native enemy gauge to Vs. Bot HUD')
            except READ_ERRORS as exc:
                debugger.log(f'LAMINA_ABAO HUD skipped: {exc}')

        handlers = {CREATED_RETURN: persistent_return(CREATED_RETURN, 0x38, created),
                    HUD_TAIL: build_hud,
                    LOA_CHILD_READY: loa_child_ready,
                    LOA_SHOT_RETURN: loa_shot_return,
                    SUMMON: summon,
                    LIFETIME_RETURN: lifetime_return,
                    INITIALIZED: persistent_return(INITIALIZED, 0x30, initialized)}
        installed = []
        patched = []
        budget = debugger.alloc(8192)
        if not budget or not 0x10000 <= budget < 0x7FFFFFF00000:
            debugger.log('LAMINA_COMBAT disabled: native gate allocation failed')
            return
        try:
            checked_write(debugger, budget, b'\0\0')
            open_code = native_gate(budget)
            used_code = native_gate(budget, used=True)
            open_entry, used_entry = budget + 16, budget + 16 + len(open_code)
            parameter_entry = used_entry + len(used_code)
            all_code = bytearray(open_code + used_code + native_gate(budget, parameter=True))
            bootstrap_entries = {}
            for lamina, (constructor, _) in CONSTRUCTORS.items():
                entry = open_entry + len(all_code)
                bootstrap_entries[lamina] = entry
                code = script_bootstrap(construct=constructor, lamina=lamina)
                handlers[entry + len(code) - 1] = bootstrapped
                all_code.extend(code)
            for child in CHILD_ROWS:
                entry = open_entry + len(all_code)
                bootstrap_entries[child] = entry
                code = script_bootstrap(construct=0x14073DDD0, configure=0x140721A50,
                                        lamina=child, child=True)
                handlers[entry + len(code) - 1] = bootstrapped
                all_code.extend(code)
            for site, raw in DULL_STORES.items():
                dull_handlers[site] = dull_handler(site)
                dull_returns[site] = open_entry + len(all_code)
                all_code.extend(raw + gate_jump(site + len(raw)))
            for site, raw in GRIFFIN_SITES.items():
                griffin_handlers[site] = griffin_handler(site)
                griffin_returns[site] = open_entry + len(all_code)
                all_code.extend(raw + gate_jump(site + len(raw)))
            gauge_entry = open_entry + len(all_code)
            all_code.extend(enemy_gauge_thunk())
            # Range rejection alone breaks native target cycling and AI pursuit.
            # Keep the prototype uninstalled until acquisition/navigation agree.
            if 16 + len(all_code) > 8192:
                raise ValueError('native gates exceed allocation')
            checked_write(debugger, open_entry, bytes(all_code))
            for address, handler in handlers.items():
                installed.append(address)
                debugger.bp(address, handler)
                if debugger.read(address, 1) != b'\xcc':
                    raise OSError('breakpoint installation did not verify')
            for address, target in ((USED, used_entry), (OPEN, open_entry), (PARAM, parameter_entry)):
                patched.append(address)
                checked_write(debugger, address, gate_jump(target))
            # Last write enables eligibility only after both gates and handlers verify.
            checked_write(debugger, budget, b'\x01')
        except Exception:
            failures = []
            for address, raw in [(budget, b'\0\0')] + [(a, SITES[a]) for a in patched]:
                try:
                    checked_write(debugger, address, raw)
                except OSError as exc:
                    failures.append(str(exc))
            for address in installed:
                try:
                    checked_write(debugger, address, SITES[address][:1] if address in SITES else b'\xc3')
                    debugger.bps.pop(address, None)
                except OSError as exc:
                    failures.append(str(exc))
            if failures:
                raise RuntimeError('Lamina rollback incomplete; stop this experimental process: '
                                   + '; '.join(failures))
            raise
        # The launcher already owns this scene hook. Preserve its original byte
        # and callback rather than registering a competing breakpoint.
        if MENU_SCENE in debugger.bps:
            previous_menu = debugger.bps[MENU_SCENE][1]
            debugger.bps[MENU_SCENE][1] = menu_reset
        debugger.log('LAMINA_COMBAT armed v14: Dullahan beta teammate damage; native targeting unchanged; '
                     'all 15 script loaders, paired ID3 and bounded ID10 command; '
                     'experimental local Vs. Bot only; unlimited sequential summons with charge refill; '
                     'static validation only, not a working-combat claim')

    d.on_start(arm)
    # Registration must precede Debugger.run, which computes its poll schedule
    # before invoking on_start. Keep other session pollers' existing cadence.
    d.poll(saved_events, every=getattr(d, 'poll_every', 10.0))
    return True
