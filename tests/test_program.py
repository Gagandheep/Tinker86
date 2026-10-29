"""Loading DOS programs and running them against the emulated DOS services."""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Microprocessor import Microprocessor  # noqa: E402
from Microprocessor.dos import DOS, DOSServiceNotImplemented  # noqa: E402

# code padded to 32 bytes, followed by the data segment at paragraph 2
HELLO_CODE = bytes([
    0xB8, 0x02, 0x00,  # mov ax, seg data   (relocated: 2 + load segment)
    0x8E, 0xD8,        # mov ds, ax
    0xBA, 0x00, 0x00,  # mov dx, offset msg
    0xB4, 0x09,        # mov ah, 9
    0xCD, 0x21,        # int 21h
    0xB8, 0x07, 0x4C,  # mov ax, 4C07h
    0xCD, 0x21,        # int 21h
]).ljust(32, b'\x90')
HELLO_DATA = b'Hello, 8086!$'


def make_exe(image, relocations=(), ss=0, sp=0x100, cs=0, ip=0):
    header_paragraphs = 2
    header = bytearray(header_paragraphs * 16)
    relocation_offset = 0x1C
    assert relocation_offset + 4 * len(relocations) <= len(header)
    total = len(header) + len(image)
    struct.pack_into('<13H', header, 0, 0x5A4D, total % 512, (total + 511) // 512, len(relocations),
                     header_paragraphs, 0, 0xFFFF, ss, sp, 0, ip, cs, relocation_offset)
    for i, (offset, segment) in enumerate(relocations):
        struct.pack_into('<HH', header, relocation_offset + 4 * i, offset, segment)
    return bytes(header) + image


class ProgramTests(unittest.TestCase):

    def write(self, data, suffix):
        handle, path = tempfile.mkstemp(suffix=suffix)
        os.write(handle, data)
        os.close(handle)
        self.addCleanup(os.remove, path)
        return path

    def test_exe_with_relocation_prints_and_exits(self):
        image = HELLO_CODE + HELLO_DATA
        path = self.write(make_exe(image, relocations=[(1, 0)], ss=4, sp=0x80), '.exe')
        cpu = Microprocessor()
        printed = []
        DOS(cpu, output=printed.append)
        program = cpu.load_program(path)
        self.assertEqual(program.kind, 'EXE')
        self.assertEqual(cpu.CS, program.load_segment)
        self.assertEqual(cpu.SS, program.load_segment + 4)
        self.assertEqual(cpu.DS, program.psp_segment)
        cpu.run()
        self.assertEqual(''.join(printed), 'Hello, 8086!')
        self.assertTrue(cpu.halted)
        self.assertEqual(cpu.exit_code, 7)

    def test_com_program_returns_through_psp(self):
        # mov dl, 'A' ; mov ah, 2 ; int 21h ; ret
        path = self.write(bytes([0xB2, 0x41, 0xB4, 0x02, 0xCD, 0x21, 0xC3]), '.com')
        cpu = Microprocessor()
        printed = []
        DOS(cpu, output=printed.append)
        program = cpu.load_program(path)
        self.assertEqual((program.kind, cpu.IP), ('COM', 0x100))
        cpu.run()
        self.assertEqual(printed, ['A'])
        self.assertEqual(cpu.exit_code, 0)

    def test_buffered_input(self):
        # mov dx, 200h ; mov ah, 0Ah ; int 21h ; int 20h  (buffer at DS:0200)
        path = self.write(bytes([0xBA, 0x00, 0x02, 0xB4, 0x0A, 0xCD, 0x21, 0xCD, 0x20]), '.com')
        cpu = Microprocessor()
        DOS(cpu, input=lambda: 'hi')
        cpu.load_program(path)
        cpu.write8(cpu.DS, 0x200, 10)
        cpu.run()
        self.assertEqual(cpu.read8(cpu.DS, 0x201), 2)
        self.assertEqual(bytes(cpu.read8(cpu.DS, 0x202 + i) for i in range(3)), b'hi\r')

    def test_unsupported_service_is_reported(self):
        path = self.write(bytes([0xB4, 0x99, 0xCD, 0x21]), '.com')
        cpu = Microprocessor()
        DOS(cpu)
        cpu.load_program(path)
        cpu.step()
        with self.assertRaises(DOSServiceNotImplemented):
            cpu.step()


if __name__ == '__main__':
    unittest.main()
