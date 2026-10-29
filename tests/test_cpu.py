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



class ControlFlow(unittest.TestCase):

    def test_jg_uses_sign_overflow_and_zero(self):
        cpu = cpu_with([0x7F, 0x10])  # jg +10h
        cpu.set_flags(SF=True, OF=True, ZF=False)
        cpu.step()
        self.assertEqual(cpu.IP, 0x12)
        cpu = cpu_with([0x7F, 0x10])
        cpu.set_flags(ZF=True)
        cpu.step()
        self.assertEqual(cpu.IP, 0x02)

    def test_call_and_ret(self):
        # call +3 ; hlt ; (pad) ; ret
        cpu = run([0xE8, 0x02, 0x00, 0xF4, 0x90, 0xC3], steps=3)
        self.assertTrue(cpu.halted)
        self.assertEqual(cpu.SP, 0x0100)

    def test_int_uses_vector_table_and_iret_returns(self):
        cpu = cpu_with([0xCD, 0x21, 0xF4])  # int 21h ; hlt
        cpu.write16(0, 0x21 * 4, 0x0000)  # handler at 0500:0000 is a single IRET
        cpu.write16(0, 0x21 * 4 + 2, 0x0500)
        cpu.memory[0x5000] = 0xCF
        cpu.step()
        self.assertEqual((cpu.CS, cpu.IP), (0x0500, 0))
        cpu.step()
        self.assertEqual((cpu.CS, cpu.IP), (CODE_SEGMENT, 2))

    def test_interrupt_hook_for_empty_vector(self):
        cpu = cpu_with([0xCD, 0x21])
        seen = []
        cpu.interrupt_hooks[0x21] = lambda c: seen.append(c.AH)
        cpu.AH = 0x4C
        cpu.step()
        self.assertEqual(seen, [0x4C])
        self.assertEqual(cpu.IP, 2)

    def test_loop(self):
        cpu = run([0xE2, 0xFE], steps=3, CX=3)  # loop $
        self.assertEqual((cpu.CX, cpu.IP), (0, 2))



class ShiftMultiplyDivide(unittest.TestCase):

    def test_shl_by_cl(self):
        cpu = run([0xD3, 0xE0], AX=0x4001, CX=2)  # shl ax, cl
        self.assertEqual(cpu.AX, 0x0004)
        self.assertTrue(cpu.get_flag('CF'))

    def test_rcr_through_carry(self):
        cpu = cpu_with([0xD0, 0xD8], AX=0x0002)  # rcr al, 1
        cpu.set_flags(CF=True)
        cpu.step()
        self.assertEqual(cpu.AL, 0x81)
        self.assertFalse(cpu.get_flag('CF'))

    def test_mul_word(self):
        cpu = run([0xF7, 0xE3], AX=0x1234, BX=0x0100)  # mul bx
        self.assertEqual((cpu.DX, cpu.AX), (0x0012, 0x3400))
        self.assertTrue(cpu.get_flag('CF') and cpu.get_flag('OF'))

    def test_imul_byte(self):
        cpu = run([0xF6, 0xEB], AX=0x00FE, BX=0x0003)  # imul bl: -2 * 3
        self.assertEqual(cpu.AX, 0xFFFA)
        self.assertFalse(cpu.get_flag('CF'))

    def test_idiv_rounds_toward_zero(self):
        cpu = run([0xF6, 0xFB], AX=0xFFF9, BX=0x0002)  # idiv bl: -7 / 2
        self.assertEqual((cpu.AL, cpu.AH), (0xFD, 0xFF))  # -3 remainder -1

    def test_divide_by_zero_raises_interrupt_0(self):
        cpu = cpu_with([0xF6, 0xF3], AX=0x0010, BX=0)  # div bl
        cpu.write16(0, 0, 0x1234)
        cpu.write16(0, 2, 0x0600)
        cpu.step()
        self.assertEqual((cpu.CS, cpu.IP), (0x0600, 0x1234))
        self.assertEqual(cpu.read16(cpu.SS, cpu.SP), 2)  # return address after the DIV


if __name__ == '__main__':
    unittest.main()
