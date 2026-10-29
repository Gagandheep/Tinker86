"""Disassembler checks: TASM-style output, and the reference style against SingleStepTests names."""
import gzip
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Microprocessor import Microprocessor  # noqa: E402
from Microprocessor.disassembler import disassemble_bytes, disassemble_memory  # noqa: E402

TESTS_DIR = os.path.join(ROOT, 'tests', 'data', '8088', 'v1')
SAMPLE_PER_OPCODE = 50


class TasmStyle(unittest.TestCase):

    def check(self, hex_bytes, text, length=None, offset=0x100):
        instruction = disassemble_bytes(bytes.fromhex(hex_bytes) + bytes(8), offset)
        self.assertEqual(instruction.text, text)
        self.assertEqual(instruction.length, length or len(bytes.fromhex(hex_bytes)))

    def test_examples(self):
        self.check('b8d204', 'mov ax, 4D2h')
        self.check('8b4602', 'mov ax, [bp+2h]')
        self.check('c7470200f0', 'mov word ptr [bx+2h], 0F000h')
        self.check('26880f', 'mov es:[bx], cl')
        self.check('7405', 'je 0107h')
        self.check('ebfe', 'jmp short 0100h')
        self.check('f3a4', 'rep movsb')
        self.check('f2ae', 'repne scasb')
        self.check('cd21', 'int 21h')
        self.check('d1e0', 'shl ax, 1')
        self.check('ff1e3412', 'call dword ptr [1234h]')
        self.check('9a00100020', 'call far 2000h:1000h')
        self.check('800e1000ff', 'or byte ptr [10h], 0FFh')
        self.check('d40a', 'aam')

    def test_from_cpu_memory(self):
        cpu = Microprocessor()
        cpu.write8(0x1000, 0xFFFF, 0xB0)  # mov al, 5 straddling the end of the segment
        cpu.write8(0x1000, 0x0000, 0x05)
        instruction = disassemble_memory(cpu, 0x1000, 0xFFFF)
        self.assertEqual((instruction.text, instruction.length), ('mov al, 5h', 2))


@unittest.skipUnless(os.path.isdir(TESTS_DIR) and os.environ.get('SST_TESTS'),
                     'set SST_TESTS=1 after tests/fetch_sst.sh to check against the SingleStepTests names')
class ReferenceStyle(unittest.TestCase):

    def test_reference_names(self):
        """Every vector's name is its disassembly; compare text and length."""
        limit = int(os.environ.get('DISASSEMBLER_SAMPLE', SAMPLE_PER_OPCODE)) or None
        mismatches = []
        for name in sorted(os.listdir(TESTS_DIR)):
            if not name.endswith('.json.gz'):
                continue
            with gzip.open(os.path.join(TESTS_DIR, name)) as f:
                for test in json.load(f)[:limit]:
                    data = bytes(test['bytes'])
                    instruction = disassemble_bytes(data + b'\x90' * 8, 0, style='reference')
                    if (instruction.text, instruction.length) != (test['name'], len(data)):
                        mismatches.append((test['name'], instruction.text, data.hex()))
        self.assertEqual(mismatches[:10], [])


if __name__ == '__main__':
    unittest.main()
