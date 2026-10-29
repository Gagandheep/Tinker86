"""BIOS video/keyboard services and waiting for keyboard input."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Microprocessor import Microprocessor  # noqa: E402
from Microprocessor.bios import BIOS, COLUMNS, VIDEO_SEGMENT  # noqa: E402
from Microprocessor.dos import DOS  # noqa: E402

CODE_SEGMENT = 0x1000


def machine(code):
    cpu = Microprocessor()
    cpu.CS = CODE_SEGMENT
    cpu.DS = cpu.ES = 0x2000
    cpu.SS, cpu.SP = 0x3000, 0x0100
    cpu.memory[CODE_SEGMENT << 4:(CODE_SEGMENT << 4) + len(code)] = bytes(code)
    dos = DOS(cpu)
    return cpu, dos.bios


class Video(unittest.TestCase):

    def test_screen_starts_blank_with_grey_attribute(self):
        cpu, bios = machine([])
        self.assertEqual(bios.cell(0, 0), (0x20, 0x07))
        self.assertEqual(bios.cursor, (0, 0))

    def test_teletype_wraps_and_scrolls(self):
        cpu, bios = machine([])
        bios.teletype(''.join(f'line {i}\r\n' for i in range(26)))
        lines = bios.screen_text().split('\n')
        self.assertEqual(lines[0], 'line 2')
        self.assertEqual(lines[23], 'line 25')
        self.assertEqual(bios.cursor, (24, 0))
        bios.teletype('x' * (COLUMNS + 1))
        self.assertEqual(bios.cursor, (24, 1))

    def test_int10_teletype_and_cursor(self):
        # mov ah,2 ; mov dx,0305h ; int 10h ; mov ax,0E41h ; int 10h
        cpu, bios = machine([0xB4, 0x02, 0xBA, 0x05, 0x03, 0xCD, 0x10, 0xB8, 0x41, 0x0E, 0xCD, 0x10])
        for _ in range(5):
            cpu.step()
        self.assertEqual(bios.cell(3, 5)[0], ord('A'))
        self.assertEqual(bios.cursor, (3, 6))

    def test_write_char_with_attribute_and_count(self):
        # mov ax,0958h ; mov bx,001Eh ; mov cx,3 ; int 10h
        cpu, bios = machine([0xB8, 0x58, 0x09, 0xBB, 0x1E, 0x00, 0xB9, 0x03, 0x00, 0xCD, 0x10])
        cpu.run(max_steps=4)
        self.assertEqual([bios.cell(0, c) for c in range(4)], [(0x58, 0x1E)] * 3 + [(0x20, 0x07)])
        self.assertEqual(bios.cursor, (0, 0))  # AH=09h does not move the cursor

    def test_scroll_window_up(self):
        cpu, bios = machine([])
        bios.teletype('a\r\nb\r\nc')
        cpu.AX, cpu.BX, cpu.CX, cpu.DX = 0x0601, 0x1700, 0x0000, 0x184F  # scroll up 1, attr 17h
        bios._int10(cpu)
        self.assertEqual(bios.screen_text().split('\n')[:2], ['b', 'c'])
        self.assertEqual(bios.cell(24, 0), (0x20, 0x17))

    def test_direct_video_memory_writes_show_up(self):
        # mov ax,0B800h ; mov es,ax ; mov word ptr es:[0], 4E21h
        cpu, bios = machine([0xB8, 0x00, 0xB8, 0x8E, 0xC0, 0x26, 0xC7, 0x06, 0x00, 0x00, 0x21, 0x4E])
        cpu.run(max_steps=3)
        self.assertEqual(bios.cell(0, 0), (0x21, 0x4E))
        self.assertEqual(cpu.read8(VIDEO_SEGMENT, 1), 0x4E)


class KeyboardInput(unittest.TestCase):

    def test_int16_waits_for_a_key_then_resumes(self):
        cpu, bios = machine([0xB4, 0x00, 0xCD, 0x16, 0xF4])  # mov ah,0 ; int 16h ; hlt
        cpu.step()
        self.assertEqual(cpu.step(), 0)
        self.assertTrue(cpu.waiting_for_input)
        self.assertEqual(cpu.IP, 2)  # rewound to the INT
        self.assertEqual(cpu.run(), 0)  # run() stops instead of spinning
        bios.keyboard.push(ord('a'))
        cpu.step()
        self.assertFalse(cpu.waiting_for_input)
        self.assertEqual((cpu.AX, cpu.IP), (0x1E61, 4))

    def test_int16_check_key_sets_zero_flag(self):
        cpu, bios = machine([0xB4, 0x01, 0xCD, 0x16, 0xCD, 0x16])
        cpu.run(max_steps=2)
        self.assertTrue(cpu.get_flag('ZF'))
        bios.keyboard.push(0x0D)
        cpu.step()
        self.assertFalse(cpu.get_flag('ZF'))
        self.assertEqual(cpu.AX, 0x1C0D)
        self.assertEqual(len(bios.keyboard), 1)  # not removed

    def test_dos_line_input_resumes_and_echoes(self):
        # mov dx,100h ; mov ah,0Ah ; int 21h ; hlt
        cpu, bios = machine([0xBA, 0x00, 0x01, 0xB4, 0x0A, 0xCD, 0x21, 0xF4])
        cpu.write8(cpu.DS, 0x100, 20)
        bios.keyboard.push_text('ab')
        cpu.run()
        self.assertTrue(cpu.waiting_for_input)
        self.assertEqual(bios.screen_text(), 'ab')
        bios.keyboard.push_text('x\bc\r')  # typo corrected with backspace
        cpu.run()
        self.assertTrue(cpu.halted)
        self.assertEqual(cpu.read8(cpu.DS, 0x101), 3)
        self.assertEqual(bytes(cpu.read8(cpu.DS, 0x102 + i) for i in range(4)), b'abc\r')
        self.assertEqual(bios.screen_text(), 'abc')
        self.assertEqual(bios.cursor, (1, 0))

    def test_dos_output_reaches_screen_and_output_callback(self):
        printed = []
        cpu = Microprocessor()
        bios = BIOS(cpu)
        DOS(cpu, output=printed.append, bios=bios)
        cpu.DL = ord('Z')
        cpu.AH = 0x02
        cpu.interrupt(0x21)
        self.assertEqual(printed, ['Z'])
        self.assertEqual(bios.cell(0, 0)[0], ord('Z'))


if __name__ == '__main__':
    unittest.main()
