"""A small subset of DOS services (INT 20h / INT 21h) implemented in Python.

The emulator has no real DOS in memory, so these run as interrupt hooks
whenever the program executes INT 20h/21h without having installed its
own handler in the vector table.
"""
import datetime


class DOSServiceNotImplemented(Exception):
    def __init__(self, function):
        self.function = function
        super().__init__(f'INT 21h function {function:02X}h is not supported by the emulator')


class DOS:
    """Console I/O goes through two callables:

    output(text)  receives everything the program prints
    input()       returns a line typed by the user (without newline), or None
    """

    def __init__(self, cpu, output=None, input=None):
        self.cpu = cpu
        self.output = output or (lambda text: None)
        self.input = input or (lambda: None)
        self._typed = ''
        self._services = {
            0x00: self._terminate,
            0x01: self._read_char_echo,
            0x02: self._write_char,
            0x06: self._direct_console_io,
            0x07: self._read_char,
            0x08: self._read_char,
            0x09: self._write_string,
            0x0A: self._buffered_input,
            0x25: self._set_vector,
            0x2A: self._get_date,
            0x2C: self._get_time,
            0x30: self._get_version,
            0x35: self._get_vector,
            0x4C: self._exit,
        }
        cpu.interrupt_hooks[0x20] = self._int20
        cpu.interrupt_hooks[0x21] = self._int21

    def _int20(self, cpu):
        self._terminate(cpu)

    def _int21(self, cpu):
        service = self._services.get(cpu.AH)
        if service is None:
            raise DOSServiceNotImplemented(cpu.AH)
        service(cpu)

    # -------------------------------------------------------------- helpers

    def _next_char(self, block=True):
        if not self._typed and block:
            line = self.input()
            self._typed = '\r' if line is None else line + '\r'
        if not self._typed:
            return None
        char, self._typed = self._typed[0], self._typed[1:]
        return ord(char) & 0xFF

    # ------------------------------------------------------------- services

    def _terminate(self, cpu):
        cpu.exit_code = 0
        cpu.halted = True

    def _exit(self, cpu):
        cpu.exit_code = cpu.AL
        cpu.halted = True

    def _read_char_echo(self, cpu):
        char = self._next_char()
        self.output('\n' if char == 0x0D else chr(char))
        cpu.AL = char

    def _read_char(self, cpu):
        cpu.AL = self._next_char()

    def _write_char(self, cpu):
        self.output(chr(cpu.DL))
        cpu.AL = cpu.DL

    def _direct_console_io(self, cpu):
        if cpu.DL != 0xFF:
            self._write_char(cpu)
            return
        char = self._next_char(block=False)
        cpu.set_flags(ZF=char is None)
        cpu.AL = char or 0

    def _write_string(self, cpu):
        chars = []
        offset = cpu.DX
        for _ in range(0x10000):
            byte = cpu.read8(cpu.DS, offset)
            if byte == ord('$'):
                break
            chars.append(chr(byte))
            offset += 1
        self.output(''.join(chars))
        cpu.AL = ord('$')

    def _buffered_input(self, cpu):
        capacity = cpu.read8(cpu.DS, cpu.DX)
        if capacity == 0:
            return
        line = self._typed.split('\r', 1)[0] if self._typed else (self.input() or '')
        self._typed = ''
        data = line.encode('latin-1', 'replace')[:capacity - 1]
        self.output(data.decode('latin-1') + '\n')
        cpu.write8(cpu.DS, cpu.DX + 1, len(data))
        for i, byte in enumerate(data + b'\r'):
            cpu.write8(cpu.DS, cpu.DX + 2 + i, byte)

    def _set_vector(self, cpu):
        cpu.write16(0, cpu.AL * 4, cpu.DX)
        cpu.write16(0, cpu.AL * 4 + 2, cpu.DS)

    def _get_vector(self, cpu):
        cpu.BX = cpu.read16(0, cpu.AL * 4)
        cpu.ES = cpu.read16(0, cpu.AL * 4 + 2)

    def _get_date(self, cpu):
        today = datetime.date.today()
        cpu.CX, cpu.DH, cpu.DL = today.year, today.month, today.day
        cpu.AL = (today.weekday() + 1) % 7  # DOS: 0 = Sunday

    def _get_time(self, cpu):
        now = datetime.datetime.now()
        cpu.CH, cpu.CL, cpu.DH, cpu.DL = now.hour, now.minute, now.second, now.microsecond // 10000

    def _get_version(self, cpu):
        cpu.AX = 0x0005  # DOS 5.0
        cpu.BX = cpu.CX = 0
