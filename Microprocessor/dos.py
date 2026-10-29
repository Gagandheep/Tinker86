"""A subset of DOS services (INT 20h / INT 21h) implemented in Python.

The emulator has no real DOS in memory, so these run as interrupt hooks
whenever the program executes INT 20h/21h without having installed its
own handler in the vector table. Console output goes through the BIOS
teletype (so it appears on the emulated screen) and keyboard input comes
from the BIOS keyboard queue.
"""
import datetime
import struct

from . import dosfs
from .bios import BIOS
from .dosfs import DOSError, DOSFileSystem


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
    root          host directory that is drive C: for the file services
                  (default: the current directory; see set_root())
    """

    # conventional memory handed out by AH=48h: from here up to the end of 640 KB
    HEAP_START_SEGMENT = 0x7000
    HEAP_END_SEGMENT = 0xA000

    def __init__(self, cpu, output=None, input=None, bios=None, root=None):
        self.cpu = cpu
        if bios is None:
            bios = BIOS(cpu, output=output)
        elif output is not None:
            bios.output = output
        self.bios = bios
        self.input = input
        self._line = None  # characters typed so far for line input, kept across retries
        self._stdin = b''  # rest of a line read from handle 0
        self.files = DOSFileSystem(root)
        self._dta = (0, 0x80)
        self._searches = {}  # DTA address -> remaining find-first/next matches
        self._heap = self.HEAP_START_SEGMENT
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
            0x0E: self._select_drive,
            0x19: self._current_drive,
            0x1A: self._set_dta,
            0x25: self._set_vector,
            0x2A: self._get_date,
            0x2C: self._get_time,
            0x2F: self._get_dta,
            0x30: self._get_version,
            0x35: self._get_vector,
            0x39: self._file_service(self._make_directory),
            0x3A: self._file_service(self._remove_directory),
            0x3B: self._file_service(self._change_directory),
            0x3C: self._file_service(self._create),
            0x3D: self._file_service(self._open),
            0x3E: self._file_service(self._close),
            0x3F: self._file_service(self._read),
            0x40: self._file_service(self._write),
            0x41: self._file_service(self._delete),
            0x42: self._file_service(self._seek),
            0x43: self._file_service(self._attributes),
            0x44: self._file_service(self._ioctl),
            0x47: self._file_service(self._get_directory),
            0x48: self._file_service(self._allocate),
            0x49: self._file_service(self._free),
            0x4A: self._file_service(self._resize),
            0x4C: self._exit,
            0x4E: self._file_service(self._find_first),
            0x4F: self._file_service(self._find_next),
            0x56: self._file_service(self._rename),
        }
        cpu.interrupt_hooks[0x20] = self._int20
        cpu.interrupt_hooks[0x21] = self._int21
        cpu.load_listeners.append(self._program_loaded)

    def set_root(self, root):
        """Use host directory root as drive C: (closes open files)."""
        self.files.set_root(root)

    def _program_loaded(self, cpu):
        self.files.close_all()
        self.files.cwd = []
        self._dta = (cpu.DS, 0x80)  # DS points at the PSP after loading
        self._searches = {}
        self._stdin = b''
        self._line = None
        self._heap = self.HEAP_START_SEGMENT

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

    def _string(self, segment, offset, limit=128):
        """ASCIIZ string at segment:offset."""
        chars = []
        for i in range(limit):
            byte = self.cpu.read8(segment, offset + i)
            if byte == 0:
                break
            chars.append(chr(byte))
        return ''.join(chars)

    def _edit_line(self, capacity):
        """Read a line with echo and backspace (without the CR); resumable when keys run out."""
        if self._line is None:
            self._line = []
        while True:
            ascii, _ = self._key()  # WaitingForInput keeps self._line for the retry
            if ascii == 0x0D:
                break
            if ascii == 0x08:
                if self._line:
                    self._line.pop()
                    self.write('\b \b')
            elif ascii and len(self._line) < capacity:
                self._line.append(ascii)
                self.write(chr(ascii))
        data, self._line = bytes(self._line), None
        self.write('\r\n')
        return data

    def _file_service(self, service):
        """Wrap a service that reports failure with DOSError: CF=1 and AX=error code."""
        def wrapped(cpu):
            try:
                service(cpu)
            except DOSError as error:
                cpu.AX = error.code
                cpu.set_flags(CF=True)
            else:
                cpu.set_flags(CF=False)
        return wrapped

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
        """Line input into the buffer at DS:DX (capacity in the first byte)."""
        capacity = cpu.read8(cpu.DS, cpu.DX)
        if capacity == 0:
            return
        data = self._edit_line(capacity - 1)
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

    # --------------------------------------------------------- drives, DTA

    def _select_drive(self, cpu):
        cpu.AL = 3  # number of drives; only C: exists

    def _current_drive(self, cpu):
        cpu.AL = 2  # C:

    def _set_dta(self, cpu):
        self._dta = (cpu.DS, cpu.DX)

    def _get_dta(self, cpu):
        cpu.ES, cpu.BX = self._dta

    # ------------------------------------------------- files and directories

    def _path(self, cpu):
        return self._string(cpu.DS, cpu.DX)

    def _make_directory(self, cpu):
        self.files.make_directory(self._path(cpu))

    def _remove_directory(self, cpu):
        self.files.remove_directory(self._path(cpu))

    def _change_directory(self, cpu):
        self.files.change_directory(self._path(cpu))

    def _get_directory(self, cpu):
        if cpu.DL not in (0, 3):  # default drive or C:
            raise DOSError(dosfs.INVALID_DRIVE)
        path = self.files.current_directory().encode('latin-1')[:63] + b'\0'
        for i, byte in enumerate(path):
            cpu.write8(cpu.DS, cpu.SI + i, byte)

    def _create(self, cpu):
        cpu.AX = self.files.create(self._path(cpu))

    def _open(self, cpu):
        cpu.AX = self.files.open(self._path(cpu), cpu.AL)

    def _close(self, cpu):
        self.files.close(cpu.BX)

    def _read(self, cpu):
        if cpu.BX == 0:  # stdin: a line with echo, CR LF included, like DOS
            if not self._stdin:
                self._stdin = self._edit_line(126) + b'\r\n'
            data, self._stdin = self._stdin[:cpu.CX], self._stdin[cpu.CX:]
        elif cpu.BX in (1, 2, 3, 4):
            data = b''
        else:
            data = self.files.read(cpu.BX, cpu.CX)
        for i, byte in enumerate(data):
            cpu.write8(cpu.DS, cpu.DX + i, byte)
        cpu.AX = len(data)

    def _write(self, cpu):
        data = bytes(cpu.read8(cpu.DS, cpu.DX + i) for i in range(cpu.CX))
        if cpu.BX in (1, 2):
            self.write(data.decode('latin-1'))
            cpu.AX = len(data)
        elif cpu.BX in (0, 3, 4):
            cpu.AX = len(data)  # no auxiliary or printer device: the data is discarded
        else:
            cpu.AX = self.files.write(cpu.BX, data)

    def _delete(self, cpu):
        self.files.delete(self._path(cpu))

    def _seek(self, cpu):
        if cpu.BX in dosfs.STANDARD_HANDLES:
            cpu.DX = cpu.AX = 0
            return
        position = self.files.seek(cpu.BX, cpu.AL, (cpu.CX << 16) | cpu.DX)
        cpu.DX, cpu.AX = position >> 16, position & 0xFFFF

    def _attributes(self, cpu):
        attributes = self.files.attributes(self._path(cpu))
        if cpu.AL == 0:
            cpu.CX = attributes
        elif cpu.AL != 1:  # setting attributes is accepted and ignored
            raise DOSError(dosfs.INVALID_FUNCTION)

    def _ioctl(self, cpu):
        if cpu.AL != 0:  # only 'get device information'
            raise DOSError(dosfs.INVALID_FUNCTION)
        if cpu.BX in dosfs.STANDARD_HANDLES:
            cpu.DX = 0x80D3 if cpu.BX <= 2 else 0x80C0  # character device (console)
        else:
            self.files.file(cpu.BX)
            cpu.DX = 0x0002  # disk file on drive C:
        cpu.AX = cpu.DX

    def _rename(self, cpu):
        self.files.rename(self._path(cpu), self._string(cpu.ES, cpu.DI))

    def _find_first(self, cpu):
        segment, offset = self._dta
        matches = self.files.find(self._path(cpu), cpu.CX)
        self._searches[(segment, offset)] = matches
        self._find_next(cpu)

    def _find_next(self, cpu):
        segment, offset = self._dta
        matches = self._searches.get((segment, offset))
        if not matches:
            raise DOSError(dosfs.NO_MORE_FILES)
        name, attributes, size, mtime = matches.pop(0)
        time, date = dosfs.dos_time(mtime)
        record = bytearray(43)
        record[0x15] = attributes
        struct.pack_into('<HHI', record, 0x16, time, date, size)
        encoded = name.encode('latin-1')[:12]
        record[0x1E:0x1E + len(encoded)] = encoded
        for i, byte in enumerate(record):
            cpu.write8(segment, offset + i, byte)

    # --------------------------------------------------------------- memory

    def _allocate(self, cpu):
        paragraphs = cpu.BX
        available = self.HEAP_END_SEGMENT - self._heap
        if paragraphs > available:
            cpu.BX = available
            raise DOSError(dosfs.INSUFFICIENT_MEMORY)
        cpu.AX = self._heap
        self._heap += paragraphs

    def _free(self, cpu):
        pass  # blocks are not reused

    def _resize(self, cpu):
        pass  # the program's own block can always shrink or grow (nothing else lives there)
