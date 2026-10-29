"""A subset of DOS services (INT 20h / INT 21h) implemented in Python.

The emulator has no real DOS in memory, so these run as interrupt hooks
whenever the program executes INT 20h/21h without having installed its
own handler in the vector table. Console output goes through the BIOS
teletype (so it appears on the emulated screen) and keyboard input comes
from the BIOS keyboard queue.
"""
import datetime

from .bios import BIOS
from .Exceptions import WaitingForInput


class DOSServiceNotImplemented(Exception):
    def __init__(self, function):
        self.function = function
        super().__init__(f'INT 21h function {function:02X}h is not supported by the emulator')


class DOS:
    """DOS services for cpu.

    output(text)  optional; receives everything the program prints
    input()       optional; called when the program needs a key and none was
                  typed: returns a line (without newline) to type, or None.
                  Without it the program waits (cpu.waiting_for_input) until
                  keys are added with bios.keyboard.push()/push_text().
    bios          the BIOS to use; one is created if not given
    """

    def __init__(self, cpu, output=None, input=None, bios=None):
        self.cpu = cpu
        if bios is None:
            bios = BIOS(cpu, output=output)
        elif output is not None:
            bios.output = output
        self.bios = bios
        self.input = input
        self._line = None  # characters typed so far for AH=0Ah, kept across retries
        self._services = {
            0x00: self._terminate,
            0x01: self._read_char_echo,
            0x02: self._write_char,
            0x06: self._direct_console_io,
            0x07: self._read_char,
            0x08: self._read_char,
            0x09: self._write_string,
            0x0A: self._buffered_input,
            0x0B: self._input_status,
            0x0C: self._flush_and_input,
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

    def write(self, text):
        self.bios.teletype(text)

    def _key(self, remove=True):
        """Next typed (ASCII, scan) pair, asking input() first when nothing was typed."""
        keyboard = self.bios.keyboard
        if not len(keyboard) and self.input is not None:
            line = self.input()
            if line is not None:
                keyboard.push_text(line + '\r')
        return self.bios.next_key(remove)

    def _next_char(self):
        ascii, scan = self._key()
        if ascii == 0:  # extended key: DOS returns 0, then the scan code
            self.bios.keyboard.keys.appendleft((scan, 0))
        return ascii

    # ------------------------------------------------------------- services

    def _terminate(self, cpu):
        cpu.exit_code = 0
        cpu.halted = True

    def _exit(self, cpu):
        cpu.exit_code = cpu.AL
        cpu.halted = True

    def _read_char_echo(self, cpu):
        char = self._next_char()
        self.write(chr(char))
        cpu.AL = char

    def _read_char(self, cpu):
        cpu.AL = self._next_char()

    def _write_char(self, cpu):
        self.write(chr(cpu.DL))
        cpu.AL = cpu.DL

    def _direct_console_io(self, cpu):
        if cpu.DL != 0xFF:
            self._write_char(cpu)
            return
        if not len(self.bios.keyboard):
            cpu.set_flags(ZF=True)
            cpu.AL = 0
            return
        cpu.AL = self._next_char()
        cpu.set_flags(ZF=False)

    def _input_status(self, cpu):
        cpu.AL = 0xFF if len(self.bios.keyboard) else 0x00

    def _flush_and_input(self, cpu):
        if self._line is None:  # not resuming an interrupted AH=0Ah
            self.bios.keyboard.clear()
        service = cpu.AL
        if service in (0x01, 0x06, 0x07, 0x08, 0x0A):
            cpu.AH = service
            try:
                self._services[service](cpu)
            finally:
                cpu.AH = 0x0C

    def _write_string(self, cpu):
        chars = []
        offset = cpu.DX
        for _ in range(0x10000):
            byte = cpu.read8(cpu.DS, offset)
            if byte == ord('$'):
                break
            chars.append(chr(byte))
            offset += 1
        self.write(''.join(chars))
        cpu.AL = ord('$')

    def _buffered_input(self, cpu):
        """Line input with echo and backspace; resumable when keys run out."""
        capacity = cpu.read8(cpu.DS, cpu.DX)
        if capacity == 0:
            return
        if self._line is None:
            self._line = []
        while True:
            try:
                ascii, _ = self._key()
            except WaitingForInput:
                raise  # keep self._line: the INT is retried when keys arrive
            if ascii == 0x0D:
                break
            if ascii == 0x08:
                if self._line:
                    self._line.pop()
                    self.write('\b \b')
            elif ascii and len(self._line) < capacity - 1:
                self._line.append(ascii)
                self.write(chr(ascii))
        data, self._line = bytes(self._line), None
        self.write('\r\n')
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
