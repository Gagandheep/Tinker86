"""TASM listing / TLINK map parsing and the source map built from them."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Assembler.listing import (ListingError, SourceMap, find_companion, load_source_map,  # noqa: E402
                               parse_listing, parse_map)

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
        text = ('      1\t    0000\t\t\t .code\n'
                '      2\t    0000  B8 0001\t\t mov ax, 1\n'
                '1     3\t    0003  90\t\t\t nop  ; from an INCLUDE file\n'
                '\t\t  90 90\n'
                '      4\t    0004  C3\t\t\t ret\n')
        with tempfile.NamedTemporaryFile('w', suffix='.lst', delete=False) as f:
            f.write(text)
        self.addCleanup(os.remove, f.name)
        listing = parse_listing(f.name)
        self.assertEqual([(line.number, line.offset) for line in listing.lines], [(1, 0), (2, 0), (4, 4)])
        self.assertEqual(listing.lines[1].segment, '_TEXT')

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


if __name__ == '__main__':
    unittest.main()
