"""DOS file services (INT 21h 39h-4Fh, 56h) against a temporary directory as drive C:."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Microprocessor import Microprocessor  # noqa: E402
from Microprocessor.dos import DOS  # noqa: E402

DATA = 0x2000
NAME, OTHER, BUFFER = 0x0000, 0x0080, 0x0200


class DOSFiles(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.cpu = Microprocessor()
        self.cpu.DS = self.cpu.ES = DATA
        self.cpu.SS, self.cpu.SP = 0x3000, 0x100
        self.printed = []
        self.dos = DOS(self.cpu, output=self.printed.append, root=self.root)
        self.addCleanup(self.dos.files.close_all)

    def string(self, offset, text):
        for i, byte in enumerate(text.encode('latin-1') + b'\0'):
            self.cpu.write8(DATA, offset + i, byte)

    def call(self, ah, path=None, **registers):
        """INT 21h with AH=ah (and DS:DX -> path). Returns (CF, AX)."""
        if path is not None:
            self.string(NAME, path)
            registers.setdefault('DX', NAME)
        self.cpu.AH = ah
        for name, value in registers.items():
            setattr(self.cpu, name, value)
        self.cpu.interrupt(0x21)
        return self.cpu.get_flag('CF'), self.cpu.AX

    def buffer(self, data=None, length=0):
        if data is not None:
            for i, byte in enumerate(data):
                self.cpu.write8(DATA, BUFFER + i, byte)
        return bytes(self.cpu.read8(DATA, BUFFER + i) for i in range(length))

    def host(self, *parts):
        return os.path.join(self.root, *parts)

    def test_create_write_seek_read_close(self):
        cf, handle = self.call(0x3C, 'notes.txt', CX=0)
        self.assertFalse(cf)
        self.assertGreaterEqual(handle, 5)
        self.buffer(b'hello world')
        cf, written = self.call(0x40, BX=handle, CX=11, DX=BUFFER)
        self.assertEqual((cf, written), (False, 11))
        cf, _ = self.call(0x42, AL=0, BX=handle, CX=0, DX=6)  # seek to offset 6
        self.assertEqual((self.cpu.DX, self.cpu.AX), (0, 6))
        self.buffer(bytes(16))
        cf, count = self.call(0x3F, BX=handle, CX=16, DX=BUFFER)
        self.assertEqual((cf, count, self.buffer(length=5)), (False, 5, b'world'))
        cf, _ = self.call(0x42, AL=2, BX=handle, CX=0xFFFF, DX=0xFFFB)  # 5 bytes before the end
        self.assertEqual(self.cpu.AX, 6)
        self.assertFalse(self.call(0x3E, BX=handle)[0])
        with open(self.host('NOTES.TXT'), 'rb') as f:  # DOS upper-cases new names
            self.assertEqual(f.read(), b'hello world')
        self.assertEqual(self.call(0x3E, BX=handle), (True, 6))  # already closed: invalid handle

    def test_open_existing_file_case_insensitively_and_errors(self):
        with open(self.host('Data.Bin'), 'wb') as f:
            f.write(b'\x01\x02\x03')
        cf, handle = self.call(0x3D, 'C:\\DATA.BIN', AL=0)
        self.assertFalse(cf)
        self.call(0x3F, BX=handle, CX=10, DX=BUFFER)
        self.assertEqual((self.cpu.AX, self.buffer(length=3)), (3, b'\x01\x02\x03'))
        self.assertEqual(self.call(0x40, BX=handle, CX=1, DX=BUFFER), (True, 5))  # opened read-only
        self.assertEqual(self.call(0x3D, 'missing.txt', AL=0), (True, 2))
        self.assertEqual(self.call(0x3D, 'nodir\\x.txt', AL=0), (True, 3))
        self.assertEqual(self.call(0x3D, 'D:\\x.txt', AL=0), (True, 0x0F))

    def test_paths_cannot_escape_the_root(self):
        outside = os.path.join(os.path.dirname(self.root), 'escape.txt')
        self.call(0x3C, '..\\..\\escape.txt', CX=0)
        self.assertFalse(os.path.exists(outside))
        self.assertTrue(os.path.exists(self.host('ESCAPE.TXT')))  # '..' stops at the root
        os.symlink(os.path.dirname(self.root), self.host('link'))
        self.assertEqual(self.call(0x3C, 'link\\x.txt', CX=0), (True, 5))

    def test_directories(self):
        self.assertFalse(self.call(0x39, 'sub')[0])
        self.assertTrue(os.path.isdir(self.host('SUB')))
        self.assertFalse(self.call(0x3B, '\\sub')[0])
        self.call(0x47, DL=0, SI=OTHER)
        self.assertEqual(self.dos._string(DATA, OTHER), 'SUB')
        self.call(0x3C, 'inner.txt', CX=0)
        self.assertTrue(os.path.exists(self.host('SUB', 'INNER.TXT')))
        self.assertEqual(self.call(0x3A, '\\SUB'), (True, 0x10))  # current directory
        self.call(0x3B, '..')
        self.call(0x47, DL=0, SI=OTHER)
        self.assertEqual(self.dos._string(DATA, OTHER), '')
        self.assertEqual(self.call(0x3A, 'sub'), (True, 5))  # not empty
        self.assertEqual(self.call(0x3B, 'missing'), (True, 3))

    def test_delete_rename_and_attributes(self):
        open(self.host('A.TXT'), 'w').close()
        self.string(OTHER, 'B.TXT')
        self.assertFalse(self.call(0x56, 'a.txt', DI=OTHER)[0])
        self.assertTrue(os.path.exists(self.host('B.TXT')))
        self.call(0x43, 'b.txt', AL=0)
        self.assertEqual(self.cpu.CX & 0x10, 0)
        self.call(0x39, 'dir')
        self.call(0x43, 'dir', AL=0)
        self.assertEqual(self.cpu.CX, 0x10)
        self.assertFalse(self.call(0x41, 'b.txt')[0])
        self.assertEqual(self.call(0x41, 'b.txt'), (True, 2))

    def test_find_first_and_next(self):
        for name in ('ONE.ASM', 'TWO.ASM', 'NOTES.TXT', 'long file name.asm'):
            open(self.host(name), 'wb').close()
        os.mkdir(self.host('SRC'))
        self.call(0x1A, DX=0x0300)  # set DTA
        found = []
        cf, _ = self.call(0x4E, '*.ASM', CX=0)
        while not cf:
            found.append(self.dos._string(DATA, 0x0300 + 0x1E))
            cf, ax = self.call(0x4F)
        self.assertEqual(found, ['ONE.ASM', 'TWO.ASM'])  # names without an 8.3 form are skipped
        self.assertEqual(ax, 0x12)
        self.call(0x4E, '*.*', CX=0x10)
        names = [self.dos._string(DATA, 0x0300 + 0x1E)]
        while not self.call(0x4F)[0]:
            names.append(self.dos._string(DATA, 0x0300 + 0x1E))
        self.assertIn('SRC', names)
        self.assertEqual(self.call(0x4E, '*.xyz', CX=0), (True, 0x12))
        self.call(0x2F)
        self.assertEqual((self.cpu.ES, self.cpu.BX), (DATA, 0x0300))

    def test_console_handles(self):
        self.buffer(b'hi\r\n')
        self.assertEqual(self.call(0x40, BX=1, CX=4, DX=BUFFER), (False, 4))
        self.assertEqual(''.join(self.printed), 'hi\r\n')
        self.assertEqual(self.dos.bios.screen_text(), 'hi')
        self.dos.bios.keyboard.push_text('abc\r')
        self.call(0x3F, BX=0, CX=3, DX=BUFFER)  # reads part of the line ...
        self.assertEqual((self.cpu.AX, self.buffer(length=3)), (3, b'abc'))
        self.call(0x3F, BX=0, CX=10, DX=BUFFER)  # ... and the rest next time
        self.assertEqual((self.cpu.AX, self.buffer(length=2)), (2, b'\r\n'))
        self.call(0x44, AL=0, BX=1)
        self.assertEqual(self.cpu.DX & 0x80, 0x80)  # character device

    def test_memory_allocation(self):
        cf, segment = self.call(0x48, BX=0x100)
        self.assertFalse(cf)
        cf, second = self.call(0x48, BX=0x10)
        self.assertEqual(second, segment + 0x100)
        self.assertEqual(self.call(0x48, BX=0xFFFF), (True, 8))
        self.assertFalse(self.call(0x4A, BX=0x10)[0])

    def test_program_using_files_end_to_end(self):
        # create file, write the message, close; then print it back via handle 1
        image = bytes([
            0xB4, 0x3C, 0x31, 0xC9, 0xBA, 0x20, 0x00, 0xCD, 0x21,  # create OUT.TXT -> AX
            0x89, 0xC3,                                            # mov bx, ax
            0xB4, 0x40, 0xB9, 0x05, 0x00, 0xBA, 0x28, 0x00, 0xCD, 0x21,  # write 5 bytes
            0xB4, 0x3E, 0xCD, 0x21,                                # close
            0xB8, 0x00, 0x4C, 0xCD, 0x21,                          # exit
        ]).ljust(0x20, b'\x90') + b'OUT.TXT\0' + b'12345'
        path = os.path.join(self.root, 'PROG.COM')
        # .COM: the image is at offset 100h, so patch the DX offsets
        image = image.replace(b'\xBA\x20\x00', b'\xBA\x20\x01').replace(b'\xBA\x28\x00', b'\xBA\x28\x01')
        with open(path, 'wb') as f:
            f.write(image)
        self.cpu.load_program(path)
        self.cpu.run()
        self.assertEqual(self.cpu.exit_code, 0)
        with open(self.host('OUT.TXT'), 'rb') as f:
            self.assertEqual(f.read(), b'12345')


if __name__ == '__main__':
    unittest.main()
