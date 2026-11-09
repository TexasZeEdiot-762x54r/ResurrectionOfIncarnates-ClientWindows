"""Settle committed skill-cube synthesis through the native Customize UI."""
import struct


TIMER_GATE_ADDRESS = 0x14035CC6E
TIMER_GATE_NATIVE_RESUME = 0x14035CC7E
TIMER_GATE_VERIFY_RESUME = 0x14035CD25
TIMER_ELIGIBLE = 0x14049EA00
FINISH_PARSE_ADDRESS = 0x1400E21AD
FINISH_PARSE_RESUME = 0x1400E21CA
PARSE_RESULT = 0x1400E2A80
CLIENT_STATE_POINTER = 0x141B87458
COPY_ACTIVE_STACK = 0x140013C00
MEMCPY = 0x140CCB0F0
STACK_RECORD_SIZE = 0xA8
PROFILE_ACTIVE_STACKS = 0xEB18
PROFILE_FINISHED_STACKS = 0xEEF0
PATCH_BLOCK_SIZE = 768

TIMER_GATE_EXPECTED = bytes.fromhex(
    '488bcee88a1d14003c010f85a7000000'
)
FINISH_PARSE_EXPECTED = bytes.fromhex(
    '488b8d801d0000488d542470e8c2080000488d4c2430c7400803000000'
)
# Compatibility aliases retained for focused tooling that imported the old patch.
PATCH_ADDRESS = TIMER_GATE_ADDRESS
RESUME_ADDRESS = TIMER_GATE_VERIFY_RESUME
EXPECTED = TIMER_GATE_EXPECTED


def _mov_rax(value):
    return b'\x48\xb8' + struct.pack('<Q', value)


def _jump(value):
    return _mov_rax(value) + b'\xff\xe0'


def _skip(opcode, body):
    if len(opcode) != 1 or len(body) > 0x7f:
        raise ValueError('Completion guard exceeds short branch')
    return opcode + bytes([len(body)]) + body


def _skip_near(opcode, body):
    if len(opcode) != 2:
        raise ValueError('Near completion guard requires a two-byte opcode')
    return opcode + struct.pack('<i', len(body)) + body


def timer_trampoline(storage):
    # This hook is reached only after the selected quota's native timer expires.
    # Preview and commit records share the active array, but retain isTest at
    # +A4. Force verify only for an occupied, non-preview record; otherwise
    # preserve every original gate.
    force_verify = (
        bytes.fromhex('45896a08')
        + bytes.fromhex('8986280b0000')
        + bytes.fromhex('bf01000000')
        + _jump(TIMER_GATE_VERIFY_RESUME)
    )
    selected_quota = (
        bytes.fromhex('8b86500a0000')
        + bytes.fromhex('4439e8')
        + _skip(b'\x75', force_verify)
    )
    available_slot = (
        bytes.fromhex('83be280b0000ff')
        + _skip(b'\x75', selected_quota)
    )
    committed_record = (
        bytes.fromhex('4183b9a400000000')
        + _skip(b'\x75', available_slot)
    )
    occupied_record = (
        bytes.fromhex('49833900')
        + _skip(b'\x74', committed_record)
    )
    inspect_record = (
        bytes.fromhex('4d8bdd')
        + bytes.fromhex('4d69dba8000000')
        + bytes.fromhex('4e8d8c1858b60400')
        + occupied_record
    )
    active_manager = (
        _mov_rax(CLIENT_STATE_POINTER)
        + bytes.fromhex('488b00')
        + bytes.fromhex('4885c0')
        + _skip(b'\x74', inspect_record)
    )
    original_gate = (
        bytes.fromhex('488bce')
        + _mov_rax(TIMER_ELIGIBLE) + b'\xff\xd0'
        + bytes.fromhex('3c01')
        + _skip(b'\x75', _jump(TIMER_GATE_NATIVE_RESUME))
        + _jump(TIMER_GATE_VERIFY_RESUME)
    )
    return b'\x49\xba' + struct.pack('<Q', storage) + active_manager + original_gate


def empty_stack_record():
    return b'\x00' * 8 + struct.pack('<i', -1) + b'\x00' * (STACK_RECORD_SIZE - 12)


def finish_trampoline(storage, resume=FINISH_PARSE_RESUME):
    # The finished response must remain available for the native frame loop.
    # The parser owns a second per-player active-stack cache. An empty
    # sv_compositeStacks array does not clear it, so a later profile refresh can
    # repopulate the global slot and reserve the inventory row indefinitely.
    # Clear both caches only when the parsed active and finished records agree.
    original = bytes.fromhex('488b8d801d0000488d542470')
    parse = _mov_rax(PARSE_RESULT) + b'\xff\xd0'
    set_status = bytes.fromhex('488d4c2430c7400803000000')

    clear_global_call = (
        bytes.fromhex('498d5310')
        + _mov_rax(COPY_ACTIVE_STACK) + b'\xff\xd0'
    )
    clear_global = (
        b'\x49\xbb' + struct.pack('<Q', storage)
        + bytes.fromhex('458b4308')
        + _mov_rax(CLIENT_STATE_POINTER)
        + bytes.fromhex('488b084885c9')
        + _skip(b'\x74', clear_global_call)
    )

    clear_profile = (
        bytes.fromhex('4c89c9')
        + bytes.fromhex('498d5310')
        + bytes.fromhex('41b8a8000000')
        + _mov_rax(MEMCPY) + b'\xff\xd0'
    )
    actions = clear_profile + clear_global
    # The native finished-stack parser leaves the record's leading qword empty.
    # Both active and finished records carry their parsed hold identity at +0x10.
    matching_records = (
        bytes.fromhex('498b4910')
        + bytes.fromhex('4885c9')
        + _skip(
            b'\x74',
            bytes.fromhex('493b4a10')
            + _skip(
                b'\x75',
                bytes.fromhex('498b4918')
                + bytes.fromhex('493b4a18')
                + _skip(b'\x75', actions),
            ),
        )
    )
    profile = (
        bytes.fromhex('488b07')
        + bytes.fromhex('4885c0')
        + _skip(
            b'\x74',
            bytes.fromhex('4589c2')
            + bytes.fromhex('4d69d2a8000000')
            + bytes.fromhex('4e8d8c1018eb0000')
            + bytes.fromhex('4e8d9410f0ee0000')
            + matching_records,
        )
    )
    clear_active = (
        b'\x49\xbb' + struct.pack('<Q', storage)
        + bytes.fromhex('458b43084183f804')
        + _skip_near(bytes.fromhex('0f87'), profile)
    )
    restore_cleanup_argument = bytes.fromhex('488d4c2430')
    return (
        original + parse + set_status + clear_active
        + restore_cleanup_argument + _jump(resume)
    )


def absolute_jump(target, size):
    jump = _jump(target)
    if len(jump) > size:
        raise ValueError('Completion jump exceeds guarded patch region')
    return jump.ljust(size, b'\x90')


def build_blob(base):
    storage = base
    prefix = b'\x00' * 16 + empty_stack_record()
    timer_address = base + len(prefix)
    timer = timer_trampoline(storage)
    finish_address = timer_address + len(timer)
    finish = finish_trampoline(storage)
    blob = prefix + timer + finish
    return blob, {
        TIMER_GATE_ADDRESS: absolute_jump(timer_address, len(TIMER_GATE_EXPECTED)),
        FINISH_PARSE_ADDRESS: absolute_jump(finish_address, len(FINISH_PARSE_EXPECTED)),
    }


def setup(d):
    d.log('CUBE_COMPLETION registration loaded')

    def arm(debugger):
        guarded = {
            TIMER_GATE_ADDRESS: TIMER_GATE_EXPECTED,
            FINISH_PARSE_ADDRESS: FINISH_PARSE_EXPECTED,
        }
        for address, expected in guarded.items():
            if debugger.read(address, len(expected)) != expected:
                debugger.log(
                    f'CUBE_COMPLETION disabled: native code guard mismatch at {address:#x}'
                )
                return

        cave = debugger.alloc(PATCH_BLOCK_SIZE)
        if not cave:
            debugger.log('CUBE_COMPLETION disabled: could not allocate completion block')
            return
        blob, patches = build_blob(cave)
        if not debugger.write(cave, blob) or debugger.read(cave, len(blob)) != blob:
            debugger.log('CUBE_COMPLETION disabled: could not install completion block')
            return

        installed = []
        try:
            for address, patch in patches.items():
                if not debugger.write(address, patch) or debugger.read(address, len(patch)) != patch:
                    raise RuntimeError(f'Could not install guarded jump at {address:#x}')
                installed.append(address)
        except Exception:
            for address in reversed(installed):
                debugger.write(address, guarded[address])
            raise
        debugger.log('CUBE_COMPLETION guarded native settlement armed')

    d.on_start(arm)
