"""Hand-written checks of the 8086 core. Run with: python -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Microprocessor import Microprocessor, InstructionNotImplemented  # noqa: E402

CODE_SEGMENT = 0x1000


def cpu_with(code, **regs):
    cpu = Microprocessor()
    cpu.CS = CODE_SEGMENT
    cpu.DS = cpu.ES = 0x2000
    cpu.SS = 0x3000
    cpu.SP = 0x0100
    for name, value in regs.items():
        setattr(cpu, name, value)
    start = cpu.physical_address(cpu.CS, cpu.IP)
    cpu.memory[start:start + len(code)] = bytes(code)
    return cpu


def run(code, steps=1, **regs):
    cpu = cpu_with(code, **regs)
    for _ in range(steps):
        cpu.step()
    return cpu


class Foundation(unittest.TestCase):

    def test_16_bit_registers_keep_both_bytes(self):
        cpu = Microprocessor()
        cpu.AX = 0x1234
        self.assertEqual((cpu.AX, cpu.AH, cpu.AL), (0x1234, 0x12, 0x34))
        cpu.BH = 0xAB
        self.assertEqual(cpu.BX, 0xAB00)

    def test_physical_address_uses_20_bits_and_wraps(self):
        self.assertEqual(Microprocessor.physical_address(0x5FFF, 0x0010), 0x60000)
        self.assertEqual(Microprocessor.physical_address(0xFFFF, 0x0010), 0x00000)

    def test_flags_reserved_bits(self):
        cpu = Microprocessor()
        cpu.FLAG = 0
        self.assertEqual(cpu.FLAG, 0xF002)

    def test_instruction_length_and_ip(self):
        cpu = run([0xB8, 0x34, 0x12])  # mov ax, 1234h
        self.assertEqual((cpu.AX, cpu.IP, cpu.instruction_length), (0x1234, 3, 3))

    def test_modrm_disp8_is_sign_extended(self):
        # mov [bx-2], al
        cpu = run([0x88, 0x47, 0xFE], BX=0x0010, AX=0x0055)
        self.assertEqual(cpu.memory[cpu.physical_address(0x2000, 0x000E)], 0x55)
        self.assertEqual(cpu.IP, 3)

    def test_bp_addressing_defaults_to_stack_segment(self):
        cpu = run([0x8A, 0x46, 0x02], BP=0x0010)  # mov al, [bp+2]
        cpu2 = cpu_with([0x8A, 0x46, 0x02], BP=0x0010)
        cpu2.memory[cpu2.physical_address(0x3000, 0x12)] = 0x77
        cpu2.step()
        self.assertEqual(cpu2.AL, 0x77)
        self.assertEqual(cpu.AL, 0)

    def test_segment_override_prefix(self):
        cpu = cpu_with([0x26, 0x8A, 0x07], BX=0x0004)  # mov al, es:[bx]
        cpu.ES = 0x4000
        cpu.memory[cpu.physical_address(0x4000, 4)] = 0x99
        cpu.step()
        self.assertEqual((cpu.AL, cpu.IP), (0x99, 3))

    def test_push_pop(self):
        cpu = run([0x50, 0x5B], steps=2, AX=0xBEEF)  # push ax; pop bx
        self.assertEqual((cpu.BX, cpu.SP), (0xBEEF, 0x100))

    def test_push_sp_pushes_decremented_value(self):
        cpu = run([0x54], SP=0x0100)
        self.assertEqual(cpu.read16(cpu.SS, 0x00FE), 0x00FE)

    def test_hlt_stops_stepping(self):
        cpu = run([0xF4, 0x90], steps=2)
        self.assertTrue(cpu.halted)
        self.assertEqual(cpu.IP, 1)

    def test_unknown_opcode_raises(self):
        cpu = Microprocessor()
        prefixes = {0x26, 0x2E, 0x36, 0x3E, 0xF0, 0xF1, 0xF2, 0xF3}
        unknown = [op for op in range(256) if cpu._dispatch[op] == cpu._unimplemented and op not in prefixes]
        if not unknown:
            self.skipTest('every opcode is implemented')
        cpu = cpu_with([unknown[0]])
        with self.assertRaises(InstructionNotImplemented):
            cpu.step()



class Arithmetic(unittest.TestCase):

    def test_or_is_bitwise(self):
        cpu = run([0x0C, 0x0C], AX=0x0003)  # or al, 0Ch
        self.assertEqual(cpu.AL, 0x0F)
        self.assertFalse(cpu.get_flag('CF') or cpu.get_flag('OF'))

    def test_add_signed_overflow(self):
        cpu = run([0x04, 0x01], AX=0x007F)  # add al, 1
        self.assertEqual(cpu.AL, 0x80)
        self.assertTrue(cpu.get_flag('OF'))
        self.assertTrue(cpu.get_flag('SF'))
        self.assertTrue(cpu.get_flag('AF'))
        self.assertFalse(cpu.get_flag('CF'))

    def test_sub_borrow(self):
        cpu = run([0x2D, 0x01, 0x00], AX=0x0000)  # sub ax, 1
        self.assertEqual(cpu.AX, 0xFFFF)
        self.assertTrue(cpu.get_flag('CF'))
        self.assertFalse(cpu.get_flag('OF'))

    def test_inc_preserves_carry(self):
        cpu = cpu_with([0x40], AX=0xFFFF)  # inc ax
        cpu.set_flags(CF=True)
        cpu.step()
        self.assertEqual(cpu.AX, 0)
        self.assertTrue(cpu.get_flag('CF') and cpu.get_flag('ZF'))

    def test_add_word_memory_with_disp16(self):
        cpu = cpu_with([0x01, 0x87, 0x00, 0x01], BX=0x0010, AX=0x0101)  # add [bx+100h], ax
        cpu.write16(cpu.DS, 0x0110, 0x1000)
        cpu.step()
        self.assertEqual(cpu.read16(cpu.DS, 0x0110), 0x1101)

    def test_daa(self):
        cpu = run([0x27], AX=0x009D)
        self.assertEqual(cpu.AL, 0x03)
        self.assertTrue(cpu.get_flag('CF'))


if __name__ == '__main__':
    unittest.main()
