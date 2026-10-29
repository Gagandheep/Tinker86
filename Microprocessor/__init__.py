"""Intel 8086 CPU core.

Registers, flags and memory are plain Python ints / a bytearray, so all
arithmetic is masked explicitly to 8, 16 or 20 bits. One call to step()
executes one complete instruction, including its prefixes (and every
iteration of a REP-prefixed string instruction).
"""
from .Exceptions import InstructionNotImplemented

CF, PF, AF, ZF, SF, TF, IF, DF, OF = 0x001, 0x004, 0x010, 0x040, 0x080, 0x100, 0x200, 0x400, 0x800
FLAG_BITS = {'CF': CF, 'PF': PF, 'AF': AF, 'ZF': ZF, 'SF': SF, 'TF': TF, 'IF': IF, 'DF': DF, 'OF': OF}
FLAGS_WRITABLE = 0x0FD5  # bits that exist on the 8086
FLAGS_ALWAYS_SET = 0xF002  # bits 1 and 12-15 always read as 1 on the 8086

MEMORY_SIZE = 1 << 20
ADDRESS_MASK = MEMORY_SIZE - 1

PARITY = tuple(bin(i).count('1') % 2 == 0 for i in range(256))

# Register numbers as encoded in instructions
AX, CX, DX, BX, SP, BP, SI, DI = range(8)
ES, CS, SS, DS = range(4)

# ModRM r/m field -> base registers of the effective address
EA_REGISTERS = ((BX, SI), (BX, DI), (BP, SI), (BP, DI), (SI,), (DI,), (BP,), (BX,))

SEGMENT_PREFIXES = {0x26: ES, 0x2E: CS, 0x36: SS, 0x3E: DS}
REP_PREFIXES = (0xF2, 0xF3)
LOCK_PREFIXES = (0xF0, 0xF1)  # F1 is an undocumented alias of LOCK


def _reg16(index):
    def getter(self):
        return self.regs[index]

    def setter(self, value):
        self.regs[index] = int(value) & 0xFFFF

    return property(getter, setter)


def _reg8(index):
    def getter(self):
        return self.get_reg8(index)

    def setter(self, value):
        self.set_reg8(index, int(value))

    return property(getter, setter)


def _sreg(index):
    def getter(self):
        return self.sregs[index]

    def setter(self, value):
        self.sregs[index] = int(value) & 0xFFFF

    return property(getter, setter)


class Microprocessor:

    class Interface:
        segments = {'Code': 'CS', 'Data': 'DS', 'Stack': 'SS', 'Extra': 'ES'}
        registers = ['AX', 'BX', 'CX', 'DX', 'IP', 'SP', 'BP', 'SI', 'DI', 'CS', 'DS', 'SS', 'ES', 'FLAG']

        # memory_callback(addresses): addresses is a list of changed physical
        # addresses, or None when everything may have changed.
        memory_callback = lambda *args: None
        registers_callback = lambda: None
        segments_callback = lambda *args: None  # (segment name, segment register value)

    # opcode -> handler method name; filled in per instruction category below
    OPCODES = {}

    AL, CL, DL, BL, AH, CH, DH, BH = (_reg8(i) for i in range(8))
    AX, CX, DX, BX, SP, BP, SI, DI = (_reg16(i) for i in range(8))
    ES, CS, SS, DS = (_sreg(i) for i in range(4))

    def __init__(self):
        self.memory = bytearray(MEMORY_SIZE)
        # interrupt number -> callable(cpu); used when the vector table entry is
        # empty, e.g. to emulate DOS services without a real DOS in memory.
        self.interrupt_hooks = {}
        # io_read(port, word) -> value; io_write(port, value, word)
        self.io_read = None
        self.io_write = None
        self._dispatch = [self._unimplemented] * 256
        for op_code, name in self.OPCODES.items():
            self._dispatch[op_code] = getattr(self, name)
        # ModRM reg field -> operation for the F6/F7 and FF opcode groups
        self._group3 = [self._test_imm, self._test_imm, self._not, self._neg] + \
            [lambda w: self._unimplemented(0xF6 | w)] * 4
        self._group5 = [lambda: self._inc_dec_rm(1, False), lambda: self._inc_dec_rm(1, True)] + \
            [lambda: self._unimplemented(0xFF)] * 6
        self.reset()

    def reset(self, clear_memory=True):
        self.regs = [0] * 8
        self.sregs = [0] * 4
        self.ip = 0
        self.flags = 0
        self.halted = False
        self.exit_code = None
        self.instruction_length = 0
        if clear_memory:
            self.memory = bytearray(MEMORY_SIZE)
        self._seg_override = None
        self._rep = None
        self._dirty = []

    # ------------------------------------------------------------------ registers

    @property
    def IP(self):
        return self.ip

    @IP.setter
    def IP(self, value):
        self.ip = int(value) & 0xFFFF

    @property
    def FLAG(self):
        return (self.flags & FLAGS_WRITABLE) | FLAGS_ALWAYS_SET

    @FLAG.setter
    def FLAG(self, value):
        self.flags = int(value) & FLAGS_WRITABLE

    def get_reg8(self, index):
        if index < 4:
            return self.regs[index] & 0xFF
        return self.regs[index - 4] >> 8

    def set_reg8(self, index, value):
        value &= 0xFF
        if index < 4:
            self.regs[index] = (self.regs[index] & 0xFF00) | value
        else:
            self.regs[index - 4] = (self.regs[index - 4] & 0x00FF) | (value << 8)

    def get_reg(self, index, w):
        return self.regs[index] if w else self.get_reg8(index)

    def set_reg(self, index, value, w):
        if w:
            self.regs[index] = value & 0xFFFF
        else:
            self.set_reg8(index, value)

    def get_flag(self, flag):
        return bool(self.flags & FLAG_BITS[flag])

    def set_flags(self, **kwargs):
        for name, value in kwargs.items():
            self._set_flag(FLAG_BITS[name], value)

    def _set_flag(self, bit, value):
        if value:
            self.flags |= bit
        else:
            self.flags &= ~bit

    # --------------------------------------------------------------------- memory

    @staticmethod
    def physical_address(segment, offset):
        return ((segment << 4) + (offset & 0xFFFF)) & ADDRESS_MASK

    def read8(self, segment, offset):
        return self.memory[self.physical_address(segment, offset)]

    def read16(self, segment, offset):
        # the second byte wraps around inside the segment
        return self.read8(segment, offset) | (self.read8(segment, offset + 1) << 8)

    def write8(self, segment, offset, value):
        address = self.physical_address(segment, offset)
        self.memory[address] = value & 0xFF
        self._dirty.append(address)

    def write16(self, segment, offset, value):
        self.write8(segment, offset, value)
        self.write8(segment, offset + 1, value >> 8)

    def read_memory(self, address, word=True):
        address &= ADDRESS_MASK
        if word:
            return self.memory[address] | (self.memory[(address + 1) & ADDRESS_MASK] << 8)
        return self.memory[address]

    def write_memory(self, address, data, word=True):
        address &= ADDRESS_MASK
        self.memory[address] = data & 0xFF
        self._dirty.append(address)
        if word:
            self.memory[(address + 1) & ADDRESS_MASK] = (data >> 8) & 0xFF
            self._dirty.append((address + 1) & ADDRESS_MASK)

    # ---------------------------------------------------------------- execution

    def debug(self):
        """Prepare for single stepping after a program has been loaded."""
        self.halted = False
        self.notify_all()
        return 0

    def notify_all(self):
        self.Interface.registers_callback()
        self.Interface.memory_callback(None)
        for name, register in self.Interface.segments.items():
            self.Interface.segments_callback(name, getattr(self, register))

    def step(self):
        """Execute one instruction. Returns its length in bytes (0 when halted)."""
        if self.halted:
            return 0
        segments_before = list(self.sregs)
        self._seg_override = None
        self._rep = None
        self._dirty = []
        self._fetched = 0
        trap = self.flags & TF

        op_code = self.fetch8()
        while True:
            if op_code in SEGMENT_PREFIXES:
                self._seg_override = SEGMENT_PREFIXES[op_code]
            elif op_code in REP_PREFIXES:
                self._rep = op_code
            elif op_code not in LOCK_PREFIXES:
                break
            op_code = self.fetch8()

        self._dispatch[op_code](op_code)
        self.instruction_length = self._fetched

        if trap and self.flags & TF and not self.halted:
            self.interrupt(1)

        self._notify(segments_before)
        return self.instruction_length

    def run(self, max_steps=1_000_000):
        """Step until the CPU halts or max_steps instructions ran. Returns the count."""
        steps = 0
        while not self.halted and steps < max_steps:
            self.step()
            steps += 1
        return steps

    def _notify(self, segments_before):
        self.Interface.registers_callback()
        if self._dirty:
            self.Interface.memory_callback(self._dirty)
        if segments_before != self.sregs:
            for name, register in self.Interface.segments.items():
                index = ('ES', 'CS', 'SS', 'DS').index(register)
                if segments_before[index] != self.sregs[index]:
                    self.Interface.segments_callback(name, self.sregs[index])

    def _unimplemented(self, op_code):
        raise InstructionNotImplemented(op_code)

    def fetch8(self):
        value = self.read8(self.sregs[CS], self.ip)
        self.ip = (self.ip + 1) & 0xFFFF
        self._fetched += 1
        return value

    def fetch16(self):
        low = self.fetch8()
        return low | (self.fetch8() << 8)

    def fetch_signed8(self):
        value = self.fetch8()
        return value - 0x100 if value & 0x80 else value

    def interrupt(self, number):
        hook = self.interrupt_hooks.get(number)
        vector = number * 4
        if hook is not None and self.read16(0, vector) == 0 and self.read16(0, vector + 2) == 0:
            hook(self)
            return
        self.push(self.FLAG)
        self.flags &= ~(IF | TF)
        self.push(self.sregs[CS])
        self.push(self.ip)
        self.ip = self.read16(0, vector)
        self.sregs[CS] = self.read16(0, vector + 2)

    def push(self, value):
        self.regs[SP] = (self.regs[SP] - 2) & 0xFFFF
        self.write16(self.sregs[SS], self.regs[SP], value)

    def pop(self):
        value = self.read16(self.sregs[SS], self.regs[SP])
        self.regs[SP] = (self.regs[SP] + 2) & 0xFFFF
        return value

    # ------------------------------------------------------------ operand decode

    def _segment(self, default):
        return self._seg_override if self._seg_override is not None else default

    def _modrm(self):
        """Decode a ModRM byte (and displacement). Returns the reg field.

        Afterwards the r/m operand is a register (self._ea is None, self._rm is
        its number) or memory at segment register self._ea[0], offset self._ea[1].
        """
        byte = self.fetch8()
        mod, reg, rm = byte >> 6, (byte >> 3) & 7, byte & 7
        self._rm = rm
        if mod == 3:
            self._ea = None
            return reg
        if mod == 0 and rm == 6:
            offset = self.fetch16()
            segment = DS
        else:
            offset = sum(self.regs[r] for r in EA_REGISTERS[rm])
            segment = SS if rm in (2, 3, 6) else DS
            if mod == 1:
                offset += self.fetch_signed8()
            elif mod == 2:
                offset += self.fetch16()
        self._ea = (self._segment(segment), offset & 0xFFFF)
        return reg

    def _get_rm(self, w):
        if self._ea is None:
            return self.get_reg(self._rm, w)
        segment, offset = self._ea
        if w:
            return self.read16(self.sregs[segment], offset)
        return self.read8(self.sregs[segment], offset)

    def _set_rm(self, value, w):
        if self._ea is None:
            self.set_reg(self._rm, value, w)
            return
        segment, offset = self._ea
        if w:
            self.write16(self.sregs[segment], offset, value)
        else:
            self.write8(self.sregs[segment], offset, value)

    def _ea_word(self, displacement=0):
        segment, offset = self._ea
        return self.read16(self.sregs[segment], offset + displacement)

    # ----------------------------------------------------- data transfer group

    def MOV_RM_R(self, op_code):  # 88-8B
        w, d = op_code & 1, op_code & 2
        reg = self._modrm()
        if d:
            self.set_reg(reg, self._get_rm(w), w)
        else:
            self._set_rm(self.get_reg(reg, w), w)

    def MOV_RM_SREG(self, op_code):  # 8C, 8E (the 8086 ignores bit 2 of reg)
        reg = self._modrm() & 3
        if op_code & 2:
            self.sregs[reg] = self._get_rm(1)
        else:
            self._set_rm(self.sregs[reg], 1)

    def MOV_A_M(self, op_code):  # A0-A3
        w = op_code & 1
        offset = self.fetch16()
        segment = self.sregs[self._segment(DS)]
        if op_code & 2:
            if w:
                self.write16(segment, offset, self.regs[AX])
            else:
                self.write8(segment, offset, self.regs[AX])
        else:
            self.set_reg(AX, self.read16(segment, offset) if w else self.read8(segment, offset), w)

    def MOV_R_I(self, op_code):  # B0-BF
        w = op_code & 8
        self.set_reg(op_code & 7, self.fetch16() if w else self.fetch8(), w)

    def MOV_RM_I(self, op_code):  # C6, C7
        w = op_code & 1
        self._modrm()
        self._set_rm(self.fetch16() if w else self.fetch8(), w)

    def LEA(self, op_code):  # 8D
        reg = self._modrm()
        if self._ea is not None:  # register operand is undefined; leave reg unchanged
            self.regs[reg] = self._ea[1]

    def LDS_LES(self, op_code):  # C4 LES, C5 LDS
        reg = self._modrm()
        if self._ea is None:
            return  # undefined form
        self.regs[reg] = self._ea_word()
        self.sregs[DS if op_code & 1 else ES] = self._ea_word(2)

    def XCHG_RM_R(self, op_code):  # 86, 87
        w = op_code & 1
        reg = self._modrm()
        value = self._get_rm(w)
        self._set_rm(self.get_reg(reg, w), w)
        self.set_reg(reg, value, w)

    def XCHG_AX_R(self, op_code):  # 90-97 (90 is NOP)
        reg = op_code & 7
        self.regs[AX], self.regs[reg] = self.regs[reg], self.regs[AX]

    def CBW(self, op_code):  # 98
        al = self.regs[AX] & 0xFF
        self.regs[AX] = al | (0xFF00 if al & 0x80 else 0)

    def CWD(self, op_code):  # 99
        self.regs[DX] = 0xFFFF if self.regs[AX] & 0x8000 else 0

    def PUSH_R(self, op_code):  # 50-57
        reg = op_code & 7
        if reg == SP:  # the 8086 pushes the already decremented SP
            self.regs[SP] = (self.regs[SP] - 2) & 0xFFFF
            self.write16(self.sregs[SS], self.regs[SP], self.regs[SP])
        else:
            self.push(self.regs[reg])

    def POP_R(self, op_code):  # 58-5F
        self.regs[op_code & 7] = self.pop()

    def PUSH_SREG(self, op_code):  # 06 0E 16 1E
        self.push(self.sregs[op_code >> 3])

    def POP_SREG(self, op_code):  # 07 0F 17 1F (POP CS exists on the 8086)
        self.sregs[op_code >> 3] = self.pop()

    def POP_RM(self, op_code):  # 8F
        self._modrm()
        self._set_rm(self.pop(), 1)

    def PUSHF(self, op_code):  # 9C
        self.push(self.FLAG)

    def POPF(self, op_code):  # 9D
        self.FLAG = self.pop()

    def SAHF(self, op_code):  # 9E
        self.flags = (self.flags & 0xFF00) | ((self.regs[AX] >> 8) & (SF | ZF | AF | PF | CF))

    def LAHF(self, op_code):  # 9F
        self.set_reg8(4, self.FLAG & 0xFF)

    def XLAT(self, op_code):  # D7
        offset = self.regs[BX] + (self.regs[AX] & 0xFF)
        self.set_reg8(0, self.read8(self.sregs[self._segment(DS)], offset))

    def IN(self, op_code):  # E4 E5 (imm8 port), EC ED (DX port)
        w = op_code & 1
        port = self.regs[DX] if op_code & 8 else self.fetch8()
        if self.io_read is not None:
            value = self.io_read(port, w)
        else:
            value = 0xFFFF  # nothing connected: the bus floats high
        self.set_reg(AX, value, w)

    def OUT(self, op_code):  # E6 E7 (imm8 port), EE EF (DX port)
        w = op_code & 1
        port = self.regs[DX] if op_code & 8 else self.fetch8()
        if self.io_write is not None:
            self.io_write(port, self.get_reg(AX, w), w)

    # ---------------------------------------------------------- arithmetic/logic

    def _set_szp(self, result, w):
        self._set_flag(ZF, result == 0)
        self._set_flag(SF, result & (0x8000 if w else 0x80))
        self._set_flag(PF, PARITY[result & 0xFF])

    def alu(self, operation, a, b, w):
        """operation: 0 ADD, 1 OR, 2 ADC, 3 SBB, 4 AND, 5 SUB, 6 XOR, 7 CMP. Sets flags."""
        mask, sign = (0xFFFF, 0x8000) if w else (0xFF, 0x80)
        if operation in (0, 2):
            carry = 1 if operation == 2 and self.flags & CF else 0
            result = a + b + carry
            self._set_flag(CF, result > mask)
            self._set_flag(OF, (a ^ result) & (b ^ result) & sign)
            self._set_flag(AF, (a ^ b ^ result) & 0x10)
        elif operation in (3, 5, 7):
            borrow = 1 if operation == 3 and self.flags & CF else 0
            result = a - b - borrow
            self._set_flag(CF, result < 0)
            self._set_flag(OF, (a ^ b) & (a ^ result) & sign)
            self._set_flag(AF, (a ^ b ^ result) & 0x10)
        else:
            result = a | b if operation == 1 else a & b if operation == 4 else a ^ b
            self.flags &= ~(CF | OF | AF)  # AF is undefined; the 8086 clears it
        result &= mask
        self._set_szp(result, w)
        return result

    def ALU_RM_R(self, op_code):  # 00-3D, low three bits 0-3: op r/m,reg / op reg,r/m
        operation, w, d = op_code >> 3, op_code & 1, op_code & 2
        reg = self._modrm()
        if d:
            result = self.alu(operation, self.get_reg(reg, w), self._get_rm(w), w)
            if operation != 7:
                self.set_reg(reg, result, w)
        else:
            result = self.alu(operation, self._get_rm(w), self.get_reg(reg, w), w)
            if operation != 7:
                self._set_rm(result, w)

    def ALU_A_I(self, op_code):  # 04/05, 0C/0D, ... 3C/3D: op al/ax, imm
        operation, w = op_code >> 3, op_code & 1
        immediate = self.fetch16() if w else self.fetch8()
        result = self.alu(operation, self.get_reg(AX, w), immediate, w)
        if operation != 7:
            self.set_reg(AX, result, w)

    def GRP1(self, op_code):  # 80-83: op r/m, imm (83 sign-extends imm8; 82 aliases 80)
        w = op_code & 1
        operation = self._modrm()
        if op_code == 0x81:
            immediate = self.fetch16()
        elif op_code == 0x83:
            immediate = self.fetch_signed8() & 0xFFFF
        else:
            immediate = self.fetch8()
        result = self.alu(operation, self._get_rm(w), immediate, w)
        if operation != 7:
            self._set_rm(result, w)

    def TEST_RM_R(self, op_code):  # 84, 85
        w = op_code & 1
        reg = self._modrm()
        self.alu(4, self._get_rm(w), self.get_reg(reg, w), w)

    def TEST_A_I(self, op_code):  # A8, A9
        w = op_code & 1
        self.alu(4, self.get_reg(AX, w), self.fetch16() if w else self.fetch8(), w)

    def _inc_dec(self, value, w, decrement):
        mask, sign = (0xFFFF, 0x8000) if w else (0xFF, 0x80)
        result = (value + (-1 if decrement else 1)) & mask
        self._set_flag(OF, result == (sign - 1 if decrement else sign))
        self._set_flag(AF, (value ^ result) & 0x10)
        self._set_szp(result, w)  # CF is not affected
        return result

    def INC_DEC_R(self, op_code):  # 40-4F
        reg = op_code & 7
        self.regs[reg] = self._inc_dec(self.regs[reg], 1, op_code & 8)

    def _inc_dec_rm(self, w, decrement):
        self._set_rm(self._inc_dec(self._get_rm(w), w, decrement), w)

    def _not(self, w):
        self._set_rm(~self._get_rm(w) & (0xFFFF if w else 0xFF), w)

    def _neg(self, w):
        self._set_rm(self.alu(5, 0, self._get_rm(w), w), w)

    def _test_imm(self, w):
        value = self._get_rm(w)
        self.alu(4, value, self.fetch16() if w else self.fetch8(), w)

    def DAA(self, op_code):  # 27
        self._decimal_adjust(subtract=False)

    def DAS(self, op_code):  # 2F
        self._decimal_adjust(subtract=True)

    def _decimal_adjust(self, subtract):
        al = old_al = self.regs[AX] & 0xFF
        old_cf, old_af = self.flags & CF, self.flags & AF
        sign = -1 if subtract else 1
        if (al & 0x0F) > 9 or self.flags & AF:
            al = (al + 6 * sign) & 0xFF
            self.flags |= AF
        else:
            self.flags &= ~AF
        # measured on hardware: the second adjustment threshold is 0x9F when AF was set
        if old_al > (0x9F if old_af else 0x99) or old_cf:
            al = (al + 0x60 * sign) & 0xFF
            self.flags |= CF
        else:
            self.flags &= ~CF
        self.set_reg8(0, al)
        self._set_szp(al, 0)

    def AAA(self, op_code):  # 37
        self._ascii_adjust(subtract=False)

    def AAS(self, op_code):  # 3F
        self._ascii_adjust(subtract=True)

    def _ascii_adjust(self, subtract):
        al, ah = self.regs[AX] & 0xFF, self.regs[AX] >> 8
        if (al & 0x0F) > 9 or self.flags & AF:
            al = (al - 6 if subtract else al + 6) & 0xFF
            ah = (ah - 1 if subtract else ah + 1) & 0xFF
            self.flags |= AF | CF
        else:
            self.flags &= ~(AF | CF)
        self.regs[AX] = (ah << 8) | (al & 0x0F)

    def AAM(self, op_code):  # D4 ib
        base = self.fetch8()
        al = self.regs[AX] & 0xFF
        if base == 0:
            # measured on hardware: flags are set as for a zero logic result before the trap
            self.flags &= ~(CF | OF | AF)
            self._set_szp(0, 0)
            self.interrupt(0)
            return
        self.regs[AX] = ((al // base) << 8) | (al % base)
        self._set_szp(al % base, 0)

    def AAD(self, op_code):  # D5 ib
        base = self.fetch8()
        al, ah = self.regs[AX] & 0xFF, self.regs[AX] >> 8
        al = self.alu(0, al, (ah * base) & 0xFF, 0)
        self.regs[AX] = al

    # Group handlers: the ModRM reg field selects the operation.

    def GRP3(self, op_code):  # F6, F7
        w = op_code & 1
        operation = self._modrm()
        self._group3[operation](w)

    def GRP4(self, op_code):  # FE: INC/DEC r/m8
        operation = self._modrm()
        if operation > 1:
            raise InstructionNotImplemented(op_code)  # undefined on the 8086
        self._inc_dec_rm(0, operation)

    def GRP5(self, op_code):  # FF
        operation = self._modrm()
        self._group5[operation]()

    # ------------------------------------------------- processor control group

    def CMC(self, op_code):  # F5
        self.flags ^= CF

    def CLC(self, op_code):  # F8
        self.flags &= ~CF

    def STC(self, op_code):  # F9
        self.flags |= CF

    def CLI(self, op_code):  # FA
        self.flags &= ~IF

    def STI(self, op_code):  # FB
        self.flags |= IF

    def CLD(self, op_code):  # FC
        self.flags &= ~DF

    def STD(self, op_code):  # FD
        self.flags |= DF

    def WAIT(self, op_code):  # 9B: no 8087 attached, TEST# is never asserted
        pass

    def ESC(self, op_code):  # D8-DF: coprocessor escape; decode the operand, no 8087
        self._modrm()
        if self._ea is not None:
            self._get_rm(1)  # the 8086 performs the memory read for the coprocessor

    def HLT(self, op_code):  # F4
        self.halted = True


def _register(names):
    for op_codes, name in names:
        for op_code in op_codes:
            Microprocessor.OPCODES[op_code] = name


_register([
    (range(0x88, 0x8C), 'MOV_RM_R'),
    ((0x8C, 0x8E), 'MOV_RM_SREG'),
    (range(0xA0, 0xA4), 'MOV_A_M'),
    (range(0xB0, 0xC0), 'MOV_R_I'),
    ((0xC6, 0xC7), 'MOV_RM_I'),
    ((0x8D,), 'LEA'),
    ((0xC4, 0xC5), 'LDS_LES'),
    ((0x86, 0x87), 'XCHG_RM_R'),
    (range(0x90, 0x98), 'XCHG_AX_R'),
    ((0x98,), 'CBW'),
    ((0x99,), 'CWD'),
    (range(0x50, 0x58), 'PUSH_R'),
    (range(0x58, 0x60), 'POP_R'),
    ((0x06, 0x0E, 0x16, 0x1E), 'PUSH_SREG'),
    ((0x07, 0x0F, 0x17, 0x1F), 'POP_SREG'),
    ((0x8F,), 'POP_RM'),
    ((0x9C,), 'PUSHF'),
    ((0x9D,), 'POPF'),
    ((0x9E,), 'SAHF'),
    ((0x9F,), 'LAHF'),
    ((0xD7,), 'XLAT'),
    ((0xE4, 0xE5, 0xEC, 0xED), 'IN'),
    ((0xE6, 0xE7, 0xEE, 0xEF), 'OUT'),
    ((0xF5,), 'CMC'),
    ((0xF8,), 'CLC'),
    ((0xF9,), 'STC'),
    ((0xFA,), 'CLI'),
    ((0xFB,), 'STI'),
    ((0xFC,), 'CLD'),
    ((0xFD,), 'STD'),
    ((0x9B,), 'WAIT'),
    (range(0xD8, 0xE0), 'ESC'),
    ((0xF4,), 'HLT'),
    ([op for op in range(0x40) if op & 7 < 4], 'ALU_RM_R'),
    ([op for op in range(0x40) if op & 7 in (4, 5)], 'ALU_A_I'),
    (range(0x80, 0x84), 'GRP1'),
    ((0x84, 0x85), 'TEST_RM_R'),
    ((0xA8, 0xA9), 'TEST_A_I'),
    (range(0x40, 0x50), 'INC_DEC_R'),
    ((0x27,), 'DAA'),
    ((0x2F,), 'DAS'),
    ((0x37,), 'AAA'),
    ((0x3F,), 'AAS'),
    ((0xD4,), 'AAM'),
    ((0xD5,), 'AAD'),
    ((0xF6, 0xF7), 'GRP3'),
    ((0xFE,), 'GRP4'),
    ((0xFF,), 'GRP5'),
])
