"""End-to-end: assemble examples/hello.asm with the real TASM/TLINK in DOSBox, run it,
and follow it in the source. Skipped unless DOSBox and the Turbo Assembler binaries
are available (external/tasm/BIN, or TASM_DIR)."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import Assembler  # noqa: E402
from Assembler.listing import load_source_map  # noqa: E402
from Microprocessor import Microprocessor  # noqa: E402
from Microprocessor.dos import DOS  # noqa: E402


def toolchain_available():
    try:
        Assembler.check_toolchain()
        Assembler.dosbox_executable()
    except Assembler.AssemblerError:
        return False
    return True


@unittest.skipUnless(toolchain_available(), 'needs DOSBox and TASM.EXE/TLINK.EXE')
class RealBuild(unittest.TestCase):

    def test_hello_example(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'HELLO.ASM')
            shutil.copy(os.path.join(ROOT, 'examples', 'hello.asm'), source)
            success, log, exe = Assembler.tasm_tlink(source)
            self.assertTrue(success, log)
            self.assertIn('Error messages:    None', log)
            cpu = Microprocessor()
            printed = []
            DOS(cpu, output=printed.append, root=directory)
            program = cpu.load_program(exe)
            source_map = load_source_map(exe, program.load_segment)
            self.assertIsNotNone(source_map)
            with open(source) as f:
                lines = f.read().splitlines()
            first = source_map.line_for_address(cpu.physical_address(cpu.CS, cpu.IP))
            self.assertIn('start:', lines[first - 1])
            # every instruction executed maps to a source line holding an instruction
            while not cpu.halted:
                line = source_map.line_for_address(cpu.physical_address(cpu.CS, cpu.IP))
                code = lines[line - 1].split(';')[0].split(':')[-1].strip().lower()
                self.assertTrue(code and not code.startswith('.'), (line, lines[line - 1]))
                cpu.step()
            self.assertEqual(''.join(printed), 'Hello from the 8086!\r\n')
            self.assertEqual((cpu.exit_code, cpu.CX), (0, 22))
            address, size = source_map.symbol('message')
            self.assertEqual(bytes(cpu.memory[address:address + 5]), b'Hello')


if __name__ == '__main__':
    unittest.main()
