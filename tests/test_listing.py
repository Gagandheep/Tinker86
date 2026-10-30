"""TASM listing / TLINK map parsing and the source map built from them."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Assembler.listing import (ListingError, SourceMap, find_companion, is_jwasm_listing,  # noqa: E402
                               load_source_map, parse_jwasm_listing, parse_listing, parse_map)
from Microprocessor import Microprocessor  # noqa: E402
from Microprocessor.dos import DOS  # noqa: E402

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures')
LOAD_SEGMENT = 0x0810
BASE = LOAD_SEGMENT << 4


class ListingTests(unittest.TestCase):

    def setUp(self):
        self.listing = parse_listing(os.path.join(FIXTURES, 'HELLO.LST'))
        self.segments = parse_map(os.path.join(FIXTURES, 'HELLO.MAP'))
        self.map = SourceMap(self.listing, self.segments, LOAD_SEGMENT)

    def test_lines_and_segments(self):
        by_number = {line.number: line for line in self.listing.lines}
        self.assertEqual((by_number[4].segment, by_number[4].offset, by_number[4].has_bytes), ('_DATA', 0, True))
        self.assertEqual((by_number[8].segment, by_number[8].offset), ('_TEXT', 5))
        # '.code' shows the offset reached in _DATA, the segment it leaves
        self.assertEqual((by_number[5].segment, by_number[5].offset, by_number[5].has_bytes), ('_DATA', 0xD, False))
        self.assertNotIn(13, by_number)  # 'end start' has no offset

    def test_map_segments(self):
        self.assertEqual(self.segments['_DATA'], (0x20, 0x0D, 'DATA'))
        self.assertEqual(self.segments['_TEXT'][2], 'CODE')

    def test_symbols(self):
        self.assertEqual(self.listing.symbols['MSG'], ('DGROUP', 0, 1))
        self.assertEqual(self.listing.groups['DGROUP'], ['STACK', '_DATA'])
        self.assertEqual(self.map.symbol('msg'), (BASE + 0x20, 1))  # DGROUP frame is _DATA's paragraph
        self.assertEqual(self.map.symbol('START'), (BASE, 1))
        self.assertIsNone(self.map.symbol('nothing'))

    def test_line_address_lookups(self):
        self.assertEqual(self.map.address_for_line(6), BASE)
        self.assertEqual(self.map.address_for_line(10), BASE + 0x0A)
        self.assertEqual(self.map.address_for_line(1), BASE)  # the next line with code
        self.assertIsNone(self.map.address_for_line(13))
        self.assertEqual(self.map.line_for_address(BASE + 0x0A), 10)
        self.assertEqual(self.map.line_for_address(BASE + 0x0B), 10)  # inside the instruction
        self.assertEqual(self.map.line_for_address(BASE + 0x0F), 12)
        self.assertIsNone(self.map.line_for_address(BASE - 1))
        self.assertEqual(self.map.line_address[4], BASE + 0x20)  # data lines have addresses too

    def test_include_and_continuation_lines_are_skipped(self):
        text = ('      1\t0000\t\t\t\t .code\n'
                '      2\t0000  B8 0001\t\t\t mov ax, 1\n'
                '      3\t\t\t\t\t INCLUDE extra.inc\n'
                '1     4\t0003  90\t\t\t nop  ; from the INCLUDEd file\n'
                '      5\t0004  C3\t\t\t ret\n')
        with tempfile.NamedTemporaryFile('w', suffix='.lst', delete=False) as f:
            f.write(text)
        self.addCleanup(os.remove, f.name)
        listing = parse_listing(f.name)
        # the included line is skipped and does not count as a line of the main file
        self.assertEqual([(line.number, line.offset) for line in listing.lines], [(1, 0), (2, 0), (4, 4)])
        self.assertEqual(listing.lines[1].segment, '_TEXT')

    def test_numbered_continuation_lines_do_not_shift_source_lines(self):
        # real TASM output: the bytes of line 4 overflow onto listing line 5, so the
        # listing's numbers run one ahead of the source after it
        with open(os.path.join(FIXTURES, 'HELLO.LST'), encoding='latin-1') as f:
            self.assertIn('      6\t000D', f.read())  # '.code' is listing line 6 ...
        by_number = {line.number: line for line in self.listing.lines}
        self.assertTrue(by_number[5].source.lower().startswith('.code'))  # ... and source line 5

    def test_wide_offsets_and_wrapped_source_lines(self):
        # 8-wide offset field (32-bit capable segments) and a source line wrapped at 132 columns
        long_comment = '; ' + 'x' * 90
        text = ('      1\t    0000\t\t\t .code\n'
                '      2\t    0000  B8 0001\t\t mov ax, 1 ' + long_comment[:83] + '+\n'
                '      3\t\t\t\t\t ' + long_comment[83:] + '\n'
                '      4\t    0003  C3\t\t\t ret\n')
        with tempfile.NamedTemporaryFile('w', suffix='.lst', delete=False) as f:
            f.write(text)
        self.addCleanup(os.remove, f.name)
        listing = parse_listing(f.name)
        self.assertEqual([(line.number, line.offset, line.has_bytes) for line in listing.lines],
                         [(1, 0, False), (2, 0, True), (3, 3, True)])

    def test_garbage_is_rejected(self):
        with tempfile.NamedTemporaryFile('w', suffix='.lst', delete=False) as f:
            f.write('not a listing\n')
        self.addCleanup(os.remove, f.name)
        with self.assertRaises(ListingError):
            parse_listing(f.name)
        with self.assertRaises(ListingError):
            parse_map(f.name)

    def test_companion_files(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = os.path.join(directory, 'HELLO.EXE')
            open(exe, 'wb').close()
            self.assertIsNone(load_source_map(exe, LOAD_SEGMENT))
            for name in ('hello.lst', 'HELLO.MAP'):
                with open(os.path.join(FIXTURES, name.upper()), 'rb') as src, \
                        open(os.path.join(directory, name), 'wb') as dst:
                    dst.write(src.read())
            self.assertTrue(find_companion(exe, '.lst').endswith('hello.lst'))
            self.assertEqual(load_source_map(exe, LOAD_SEGMENT).address_for_line(7), BASE + 3)



class JWasmListingTests(unittest.TestCase):
    """Listings written by JWasm (tests/fixtures/jwasm, built with jwasm -mz -Sx -Fl)."""

    DIRECTORY = os.path.join(FIXTURES, 'jwasm')

    def trace(self, name):
        """Run a fixture program; return (source lines executed, output, cpu, source map)."""
        cpu = Microprocessor()
        printed = []
        DOS(cpu, output=printed.append, root=self.DIRECTORY)
        exe = os.path.join(self.DIRECTORY, name + '.EXE')
        program = cpu.load_program(exe)
        source_map = load_source_map(exe, program.load_segment)
        lines = []
        while not cpu.halted:
            lines.append(source_map.line_for_address(cpu.physical_address(cpu.CS, cpu.IP)))
            cpu.step()
        return lines, ''.join(printed), cpu, source_map

    def test_format_detection(self):
        self.assertTrue(is_jwasm_listing(os.path.join(self.DIRECTORY, 'HELLO.LST')))
        self.assertFalse(is_jwasm_listing(os.path.join(FIXTURES, 'HELLO.LST')))  # TASM

    def test_hello_lines_symbols_and_segments(self):
        listing, segments = parse_jwasm_listing(os.path.join(self.DIRECTORY, 'HELLO.LST'))
        self.assertEqual(segments['_TEXT'], (0, 0x11, 'CODE'))
        self.assertEqual(segments['_DATA'][2], 'DATA')
        self.assertIn('STACK', segments)  # no file data: placed from its group's frame
        self.assertEqual(listing.symbols['MSG'], ('_DATA', 0, 1))
        lines, output, cpu, source_map = self.trace('HELLO')  # no .MAP file needed
        self.assertEqual(lines, [6, 7, 8, 9, 10, 11, 12])
        self.assertEqual((output, cpu.exit_code), ('Hello, 8086!', 7))
        address, _ = source_map.symbol('msg')
        self.assertEqual(bytes(cpu.memory[address:address + 5]), b'Hello')

    def test_macros_includes_and_false_conditionals(self):
        lines, output, cpu, source_map = self.trace('FEATURES')
        self.assertEqual(output, 'AB')
        # 16: the putc macro call (its expansion), 25: the INCLUDE (helper inside it),
        # 21: the else branch; the false IF block keeps its line numbers (-Sx)
        self.assertEqual(lines, [14, 15, 16, 16, 16, 17, 25, 25, 25, 25, 21, 23, 24])
        address, size = source_map.symbol('count')
        self.assertEqual((size, cpu.memory[address]), (2, 5))


if __name__ == '__main__':
    unittest.main()
