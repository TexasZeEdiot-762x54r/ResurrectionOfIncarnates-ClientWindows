"""Native, IV-only health condition. Original low-health behavior 3 is untouched."""
import struct

ENTRY = 0x14062C1D0
RESUME = 0x14062C1E3
EXPECTED = bytes.fromhex('83c2fe83fa1d0f87aa0100004c8d051d3e9dff')
LOW_HP = 0x14062C20E
LOW_HP_EXPECTED = bytes.fromhex(
    'f30f1081880c0000f30f5e81840c00000f2f058b0721010f875b010000b801000000c3')


def jump(address):
    return bytes.fromhex('ff2500000000') + struct.pack('<Q', address)


def trampoline(resume=RESUME):
    code = bytearray()
    labels, references = {}, []

    def branch(opcode, label):
        code.extend(bytes.fromhex(opcode))
        references.append((len(code), label))
        code.extend(b'\0' * 4)

    code.extend(bytes.fromhex('83fa20'))  # cmp edx,32
    branch('0f85', 'original')
    # Reject nonpositive/nonfinite max HP and negative/nonfinite current HP.
    code.extend(bytes.fromhex('8b81840c000085c0'))
    branch('0f8e', 'false')
    code.extend(bytes.fromhex('3d0000807f'))
    branch('0f83', 'false')
    code.extend(bytes.fromhex('8b81880c000085c0'))
    branch('0f88', 'false')
    code.extend(bytes.fromhex('3d0000807f'))
    branch('0f83', 'false')
    code.extend(bytes.fromhex('f30f1081880c0000f30f5e81840c0000'))
    branch('0f2f05', 'half')  # comiss xmm0,[rip+half]
    branch('0f82', 'true')  # strictly below, not equal
    labels['false'] = len(code)
    code.extend(bytes.fromhex('31c0c3'))
    labels['true'] = len(code)
    code.extend(bytes.fromhex('b801000000c3'))
    labels['original'] = len(code)
    code.extend(bytes.fromhex('83c2fe83fa1d'))
    branch('0f87', 'false')
    code.extend(bytes.fromhex('49b8') + struct.pack('<Q', 0x140000000))
    code.extend(jump(resume))
    labels['half'] = len(code)
    code.extend(struct.pack('<f', .5))
    for offset, label in references:
        struct.pack_into('<i', code, offset, labels[label] - offset - 4)
    return bytes(code)


def setup(d):
    def arm(debugger):
        for address, expected in ((ENTRY, EXPECTED), (LOW_HP, LOW_HP_EXPECTED)):
            if debugger.read(address, len(expected)) != expected:
                debugger.log(f'STEEL_SPINE disabled: code guard mismatch at {address:#x}')
                return
        blob = trampoline()
        cave = debugger.alloc(len(blob))
        if not cave or not debugger.write(cave, blob) or debugger.read(cave, len(blob)) != blob:
            raise RuntimeError('Steel spine native condition installation failed')
        patch = jump(cave).ljust(len(EXPECTED), b'\x90')
        try:
            if not debugger.write(ENTRY, patch) or debugger.read(ENTRY, len(patch)) != patch:
                raise RuntimeError('Steel spine guarded entry installation failed')
        except BaseException:
            debugger.write(ENTRY, EXPECTED)
            raise
        debugger.log('STEEL_SPINE armed: IV HP < 50%; I-III original HP <= 30%')

    d.on_start(arm)
