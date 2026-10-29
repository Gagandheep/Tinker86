"""Load DOS .EXE (MZ) and .COM programs into a Microprocessor, as DOS would."""
import struct

DEFAULT_PSP_SEGMENT = 0x0800
TOP_OF_MEMORY_SEGMENT = 0xA000


class ProgramFormatError(Exception):
    pass


class LoadedProgram:
    def __init__(self, kind, psp_segment, load_segment, size):
        self.kind = kind
        self.psp_segment = psp_segment
        self.load_segment = load_segment
        self.size = size

    @property
    def image_range(self):
        """Physical (start, end) of the loaded image."""
        start = (self.load_segment << 4) + (0x100 if self.kind == 'COM' else 0)
        return start, start + self.size

    def __repr__(self):
        return (f'LoadedProgram({self.kind}, psp={self.psp_segment:04X}, '
                f'load={self.load_segment:04X}, size={self.size})')


def load_program(cpu, path, psp_segment=DEFAULT_PSP_SEGMENT, command_tail=''):
    """Reset the CPU registers and load the program at path. Memory is cleared."""
    with open(path, 'rb') as f:
        data = f.read()
    cpu.reset(clear_memory=True)
    if data[:2] in (b'MZ', b'ZM'):
        program = _load_exe(cpu, data, psp_segment)
    else:
        program = _load_com(cpu, data, psp_segment)
    _build_psp(cpu, psp_segment, command_tail)
    return program


def _build_psp(cpu, segment, command_tail):
    base = cpu.physical_address(segment, 0)
    psp = bytearray(0x100)
    psp[0:2] = b'\xCD\x20'  # INT 20h, reached by a RET from a .COM program
    struct.pack_into('<H', psp, 2, TOP_OF_MEMORY_SEGMENT)
    tail = command_tail.encode('ascii', 'replace')[:126]
    psp[0x80] = len(tail)
    psp[0x81:0x81 + len(tail)] = tail
    psp[0x81 + len(tail)] = 0x0D
    cpu.memory[base:base + 0x100] = psp


def _load_com(cpu, data, psp_segment):
    if len(data) > 0xFF00:
        raise ProgramFormatError('a .COM program must be smaller than 65280 bytes')
    start = cpu.physical_address(psp_segment, 0x100)
    cpu.memory[start:start + len(data)] = data
    cpu.CS = cpu.DS = cpu.ES = cpu.SS = psp_segment
    cpu.IP = 0x100
    cpu.SP = 0xFFFE
    cpu.write16(psp_segment, 0xFFFE, 0x0000)  # RET jumps to the INT 20h at PSP:0000
    return LoadedProgram('COM', psp_segment, psp_segment, len(data))


def _load_exe(cpu, data, psp_segment):
    if len(data) < 0x1C:
        raise ProgramFormatError('truncated MZ header')
    (_, last_page_bytes, pages, relocation_count, header_paragraphs, _, _,
     ss, sp, _, ip, cs, relocation_offset) = struct.unpack_from('<13H', data, 0)
    image_end = pages * 512 - ((512 - last_page_bytes) if last_page_bytes else 0)
    image_end = min(image_end, len(data))
    image = data[header_paragraphs * 16:image_end]

    load_segment = psp_segment + 0x10  # the image follows the 256-byte PSP
    start = cpu.physical_address(load_segment, 0)
    if start + len(image) > len(cpu.memory):
        raise ProgramFormatError('program does not fit in memory')
    cpu.memory[start:start + len(image)] = image

    for i in range(relocation_count):
        offset, segment = struct.unpack_from('<HH', data, relocation_offset + 4 * i)
        target = (load_segment + segment) & 0xFFFF
        cpu.write16(target, offset, cpu.read16(target, offset) + load_segment)

    cpu.CS = load_segment + cs
    cpu.IP = ip
    cpu.SS = load_segment + ss
    cpu.SP = sp
    cpu.DS = cpu.ES = psp_segment
    return LoadedProgram('EXE', psp_segment, load_segment, len(image))
