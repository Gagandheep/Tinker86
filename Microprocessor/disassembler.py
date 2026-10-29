"""8086 disassembler.

Two output styles:
- 'tasm' (default): TASM-like syntax for the debugger, e.g. ``mov word ptr es:[bx+2], 0``.
- 'reference': the syntax used by the SingleStepTests suite, e.g.
  ``mov word [es:bx+2h], 0h``; tests/test_disassembler.py checks it
  against every test vector name.
"""

REG8 = ('al', 'cl', 'dl', 'bl', 'ah', 'ch', 'dh', 'bh')
REG16 = ('ax', 'cx', 'dx', 'bx', 'sp', 'bp', 'si', 'di')
SREG = ('es', 'cs', 'ss', 'ds')
EA_BASE = ('bx+si', 'bx+di', 'bp+si', 'bp+di', 'si', 'di', 'bp', 'bx')
ALU = ('add', 'or', 'adc', 'sbb', 'and', 'sub', 'xor', 'cmp')
SHIFTS = ('rol', 'ror', 'rcl', 'rcr', 'shl', 'shr', 'setmo', 'sar')
GROUP3 = ('test', 'test', 'not', 'neg', 'mul', 'imul', 'div', 'idiv')
JCC_REFERENCE = ('jo', 'jno', 'jb', 'jnb', 'jz', 'jnz', 'jbe', 'jnbe',
                 'js', 'jns', 'jp', 'jnp', 'jl', 'jnl', 'jle', 'jnle')
JCC_TASM = ('jo', 'jno', 'jb', 'jae', 'je', 'jne', 'jbe', 'ja',
            'js', 'jns', 'jp', 'jnp', 'jl', 'jge', 'jle', 'jg')
SEGMENT_PREFIXES = {0x26: 0, 0x2E: 1, 0x36: 2, 0x3E: 3}
STRING_OPS = {0xA4: 'movs', 0xA6: 'cmps', 0xAA: 'stos', 0xAC: 'lods', 0xAE: 'scas'}
SIMPLE = {
    0x27: 'daa', 0x2F: 'das', 0x37: 'aaa', 0x3F: 'aas', 0x90: 'nop', 0x98: 'cbw', 0x99: 'cwd',
    0x9B: 'wait', 0x9C: 'pushf', 0x9D: 'popf', 0x9E: 'sahf', 0x9F: 'lahf', 0xCE: 'into', 0xCF: 'iret',
    0xD6: 'salc', 0xD7: 'xlat', 0xF4: 'hlt', 0xF5: 'cmc', 0xF8: 'clc', 0xF9: 'stc', 0xFA: 'cli',
    0xFB: 'sti', 0xFC: 'cld', 0xFD: 'std',
}
MAX_PREFIXES = 6


class Instruction:
    def __init__(self, offset, data, text):
        self.offset = offset
        self.data = data
        self.text = text

    @property
    def length(self):
        return len(self.data)

    def __repr__(self):
        return f'Instruction({self.offset:04X}, {self.data.hex()}, {self.text!r})'


class _Decoder:
    def __init__(self, read, offset, style):
        self.read = read
        self.start = offset
        self.position = offset
        self.reference = style == 'reference'
        self.segment = None
        self.data = bytearray()

    # ------------------------------------------------------------- formatting

    def hex(self, value, digits=0):
        text = format(value, 'X').rjust(digits, '0')
        if not self.reference and text[0] in 'ABCDEF':
            text = '0' + text
        return text + 'h'

    def target(self, value):
        return self.hex(value & 0xFFFF, 4)

    def far(self, segment, offset):
        return f'{self.hex(segment, 4)}:{self.hex(offset, 4)}'

    # --------------------------------------------------------------- fetching

    def byte(self):
        value = self.read(self.position & 0xFFFF)
        self.position += 1
        self.data.append(value)
        return value

    def word(self):
        low = self.byte()
        return low | (self.byte() << 8)

    def signed_byte(self):
        value = self.byte()
        return value - 0x100 if value & 0x80 else value

    def immediate(self, w):
        return self.hex(self.word() if w else self.byte())

    def relative(self, displacement):
        return self.target(self.position + displacement)

    # ------------------------------------------------------------------ ModRM

    def modrm(self, w, size='auto'):
        """Decode ModRM. Returns (reg field, r/m operand text).

        size: 'auto' -> byte/word from w, None -> no size, or an explicit size name.
        In the TASM style the size is only shown when the caller asks with ptr=True.
        """
        value = self.byte()
        mod, reg, rm = value >> 6, (value >> 3) & 7, value & 7
        if mod == 3:
            return reg, (REG16 if w else REG8)[rm], False
        if mod == 0 and rm == 6:
            address = self.hex(self.word())
            default = 'ds'
        else:
            address = EA_BASE[rm]
            default = 'ss' if rm in (2, 3, 6) else 'ds'
            if mod in (1, 2):
                displacement = self.signed_byte() if mod == 1 else self.word()
                if mod == 2 and displacement & 0x8000:
                    displacement -= 0x10000
                sign = '-' if displacement < 0 else '+'
                address += sign + self.hex(abs(displacement))
        segment = SREG[self.segment] if self.segment is not None else default
        if size == 'auto':
            size = 'word' if w else 'byte'
        if self.reference:
            text = f'[{segment}:{address}]'
            if size:
                text = f'{size} {text}'
        else:
            prefix = f'{segment}:' if self.segment is not None else ''
            text = f'{prefix}[{address}]'
            if size:
                text = f'{size} ptr {text}'
        return reg, text, True

    def rm_operand(self, w, ptr):
        """r/m operand: TASM shows 'byte ptr' only when ptr (the size is not implied)."""
        reg, text, memory = self.modrm(w, size='auto' if (ptr or self.reference) else None)
        return reg, text

    # ------------------------------------------------------------- instructions

    def decode(self):
        prefixes = []
        rep = None
        op = self.byte()
        while len(prefixes) < MAX_PREFIXES:
            if op in SEGMENT_PREFIXES:
                self.segment = SEGMENT_PREFIXES[op]
                prefixes.append(('segment', SREG[self.segment]))
            elif op in (0xF2, 0xF3):
                rep = op
                prefixes.append(('rep', op))
            elif op in (0xF0, 0xF1):
                prefixes.append(('lock', 'lock'))
            else:
                break
            op = self.byte()
        text = self.instruction(op, rep)
        return self.decorate(text, op, prefixes)

    def decorate(self, text, op, prefixes):
        """Put prefixes that are not folded into an operand in front of the mnemonic."""
        words = []
        string_op = (op & 0xFE) in STRING_OPS
        for kind, value in prefixes:
            if kind == 'segment':
                # a segment prefix shows up inside a memory operand; otherwise as a word
                if string_op or ('[' not in text and not self.reference):
                    words.append(value)
            elif kind == 'rep':
                if string_op:
                    if op & 0xFE in (0xA6, 0xAE):
                        words.append('repne' if value == 0xF2 else 'repe')
                    else:
                        words.append('rep')
                elif not self.reference:
                    words.append('repne' if value == 0xF2 else 'rep')
            else:
                words.append(value)
        if string_op and words:
            # segment names first, then rep, as in 'cs repne cmpsb'
            words.sort(key=lambda word: 0 if word in SREG else 1)
        return ' '.join(words + [text])

    def instruction(self, op, rep):
        reference = self.reference
        if op < 0x40 and op & 7 < 6:
            name = ALU[op >> 3]
            w = op & 1
            if op & 7 < 4:
                reg, rm = self.rm_operand(w, ptr=False)
                register = (REG16 if w else REG8)[reg]
                return f'{name} {register}, {rm}' if op & 2 else f'{name} {rm}, {register}'
            return f'{name} {"ax" if w else "al"}, {self.immediate(w)}'
        if op < 0x20 and op & 7 in (6, 7):
            return f'{"push" if op & 1 == 0 else "pop"} {SREG[op >> 3]}'
        if op in SIMPLE:
            return SIMPLE[op]
        if 0x40 <= op < 0x60:
            return f'{("inc", "dec", "push", "pop")[(op - 0x40) >> 3]} {REG16[op & 7]}'
        if 0x60 <= op < 0x80:
            displacement = self.signed_byte()
            names = JCC_REFERENCE if reference else JCC_TASM
            return f'{names[op & 0xF]} {self.relative(displacement)}'
        if 0x80 <= op < 0x84:
            w = op & 1
            reg, rm = self.rm_operand(w, ptr=True)
            if op == 0x83:
                immediate = self.hex(self.signed_byte() & 0xFFFF)
            else:
                immediate = self.immediate(w)
            return f'{ALU[reg]} {rm}, {immediate}'
        if op in (0x84, 0x85, 0x86, 0x87):
            w = op & 1
            reg, rm = self.rm_operand(w, ptr=False)
            name = 'test' if op < 0x86 else 'xchg'
            register = (REG16 if w else REG8)[reg]
            if op < 0x86 or reference:
                return f'{name} {rm}, {register}' if op < 0x86 else f'{name} {register}, {rm}'
            return f'{name} {register}, {rm}'
        if 0x88 <= op < 0x8C:
            w = op & 1
            reg, rm = self.rm_operand(w, ptr=False)
            register = (REG16 if w else REG8)[reg]
            return f'mov {register}, {rm}' if op & 2 else f'mov {rm}, {register}'
        if op in (0x8C, 0x8E):
            reg, rm, _ = self.modrm(1, size='word' if reference else None)
            segment = SREG[reg & 3]
            return f'mov {segment}, {rm}' if op & 2 else f'mov {rm}, {segment}'
        if op == 0x8D:
            reg, rm, _ = self.modrm(1, size=None)
            return f'lea {REG16[reg]}, {rm}'
        if op == 0x8F:
            _, rm = self.rm_operand(1, ptr=True)
            return f'pop {rm}'
        if 0x91 <= op < 0x98:
            return f'xchg {REG16[op & 7]}, ax'
        if op == 0x9A:
            offset = self.word()
            segment = self.word()
            return f'callf {self.far(segment, offset)}' if reference else f'call far {self.far(segment, offset)}'
        if 0xA0 <= op < 0xA4:
            w = op & 1
            address = self.hex(self.word())
            segment = SREG[self.segment] if self.segment is not None else 'ds'
            if reference:
                memory = f'{"word" if w else "byte"} [{segment}:{address}]'
            else:
                prefix = f'{segment}:' if self.segment is not None else ''
                memory = f'{prefix}[{address}]'
            register = 'ax' if w else 'al'
            return f'mov {memory}, {register}' if op & 2 else f'mov {register}, {memory}'
        if (op & 0xFE) in STRING_OPS:
            return STRING_OPS[op & 0xFE] + ('w' if op & 1 else 'b')
        if op in (0xA8, 0xA9):
            w = op & 1
            return f'test {"ax" if w else "al"}, {self.immediate(w)}'
        if 0xB0 <= op < 0xC0:
            w = op & 8
            return f'mov {(REG16 if w else REG8)[op & 7]}, {self.immediate(w)}'
        if op in (0xC0, 0xC1, 0xC2, 0xC3, 0xC8, 0xC9, 0xCA, 0xCB):
            far = op & 8
            name = ('retf' if far else 'retn') if reference else ('retf' if far else 'ret')
            return f'{name} {self.hex(self.word())}' if not op & 1 else name
        if op in (0xC4, 0xC5):
            reg, rm, _ = self.modrm(1, size='dword' if reference else None)
            return f'{"lds" if op & 1 else "les"} {REG16[reg]}, {rm}'
        if op in (0xC6, 0xC7):
            w = op & 1
            _, rm = self.rm_operand(w, ptr=True)
            return f'mov {rm}, {self.immediate(w)}'
        if op == 0xCC:
            return 'int3' if reference else 'int 3'
        if op == 0xCD:
            return f'int {self.hex(self.byte())}'
        if 0xD0 <= op < 0xD4:
            w = op & 1
            reg, rm = self.rm_operand(w, ptr=True)
            name = SHIFTS[reg]
            if op & 2:
                return f'{name}c {rm}, cl' if reg == 6 else f'{name} {rm}, cl'
            return f'{name} {rm}' if reference else f'{name} {rm}, 1'
        if op in (0xD4, 0xD5):
            base = self.byte()
            name = 'aam' if op == 0xD4 else 'aad'
            if not reference and base == 10:
                return name
            return f'{name} {self.hex(base)}'
        if 0xD8 <= op < 0xE0:
            _, rm, _ = self.modrm(1, size='word' if reference else None)
            return f'esc {rm}'
        if 0xE0 <= op < 0xE4:
            displacement = self.signed_byte()
            return f'{("loopne", "loope", "loop", "jcxz")[op & 3]} {self.relative(displacement)}'
        if op in (0xE4, 0xE5, 0xE6, 0xE7):
            port = self.hex(self.byte())
            register = 'ax' if op & 1 else 'al'
            return f'in {register}, {port}' if op < 0xE6 else f'out {port}, {register}'
        if op in (0xEC, 0xED, 0xEE, 0xEF):
            register = 'ax' if op & 1 else 'al'
            return f'in {register}, dx' if op < 0xEE else f'out dx, {register}'
        if op in (0xE8, 0xE9):
            displacement = self.word()
            return f'{"call" if op == 0xE8 else "jmp"} {self.relative(displacement)}'
        if op == 0xEA:
            offset = self.word()
            segment = self.word()
            return f'jmpf {self.far(segment, offset)}' if reference else f'jmp far {self.far(segment, offset)}'
        if op == 0xEB:
            displacement = self.signed_byte()
            return f'jmp {self.relative(displacement)}' if reference else f'jmp short {self.relative(displacement)}'
        if op in (0xF6, 0xF7):
            w = op & 1
            reg, rm = self.rm_operand(w, ptr=True)
            if reg < 2:
                return f'test {rm}, {self.immediate(w)}'
            return f'{GROUP3[reg]} {rm}'
        if op == 0xFE:
            reg, rm = self.rm_operand(0, ptr=True)
            if reg > 1:
                return f'db {self.hex(op)}'
            return f'{("inc", "dec")[reg]} {rm}'
        if op == 0xFF:
            peek = self.read(self.position & 0xFFFF)
            reg = (peek >> 3) & 7
            far = reg in (3, 5)
            if reference:
                _, rm, _ = self.modrm(1, size='word')
            else:
                _, rm = self.rm_operand(1, ptr=reg in (0, 1, 6, 7))
                if far:
                    rm = 'dword ptr ' + rm
            names_reference = ('inc', 'dec', 'call', 'callf', 'jmp', 'jmpf', 'push', 'push')
            names_tasm = ('inc', 'dec', 'call', 'call', 'jmp', 'jmp', 'push', 'push')
            return f'{(names_reference if reference else names_tasm)[reg]} {rm}'
        return f'db {self.hex(op)}'


def format_hex(value, style='tasm', digits=0):
    """Hex number as the disassembler writes it (TASM: a leading 0 before A-F digits)."""
    return _Decoder(None, 0, style).hex(value, digits)


def data_byte(offset, value, style='tasm'):
    """A one-byte 'db' pseudo instruction, for bytes that are not code."""
    return Instruction(offset & 0xFFFF, bytes([value]), f'db {format_hex(value, style)}')


def disassemble(read, offset, style='tasm'):
    """Disassemble one instruction. read(offset) returns the byte at that offset
    of the code segment (offsets wrap at 64 KB). Returns an Instruction."""
    decoder = _Decoder(read, offset, style)
    text = decoder.decode()
    return Instruction(offset & 0xFFFF, bytes(decoder.data), text)


def disassemble_bytes(data, offset=0, style='tasm'):
    """Disassemble an instruction from a bytes object placed at offset."""
    return disassemble(lambda o: data[(o - offset) & 0xFFFF] if (o - offset) & 0xFFFF < len(data) else 0,
                       offset, style)


def disassemble_memory(cpu, segment, offset, style='tasm'):
    """Disassemble the instruction at segment:offset of a Microprocessor's memory."""
    return disassemble(lambda o: cpu.read8(segment, o), offset, style)
