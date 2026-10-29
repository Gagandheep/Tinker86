"""PC BIOS services (INT 10h video, INT 16h keyboard, INT 11h, 12h, 1Ah) in Python.

The screen is a real 80x25 colour text buffer at B800:0000 (character, attribute
pairs) that programs may also write directly. The cursor position and video mode
live in the BIOS data area at 0040:0000 like on a PC.
"""
import collections
import datetime

from .Exceptions import WaitingForInput

VIDEO_SEGMENT = 0xB800
COLUMNS, ROWS = 80, 25
DEFAULT_ATTRIBUTE = 0x07  # light grey on black
BIOS_DATA_SEGMENT = 0x0040
VIDEO_MODE = 0x49  # offsets in the BIOS data area
SCREEN_COLUMNS = 0x4A
CURSOR_POSITION = 0x50  # column, row of page 0
CURSOR_SHAPE = 0x60
SCREEN_ROWS = 0x84  # rows - 1

# US keyboard scan codes for the characters a key produces
_SCAN_ROWS = [
    (0x02, '1234567890-='), (0x10, 'qwertyuiop[]'), (0x1E, "asdfghjkl;'`"), (0x2B, '\\zxcvbnm,./'),
]
_SHIFTED = {'1': '!', '2': '@', '3': '#', '4': '$', '5': '%', '6': '^', '7': '&', '8': '*', '9': '(',
            '0': ')', '-': '_', '=': '+', '[': '{', ']': '}', ';': ':', "'": '"', '`': '~', '\\': '|',
            ',': '<', '.': '>', '/': '?'}
SCAN_CODES = {'\r': 0x1C, '\n': 0x1C, '\b': 0x0E, '\t': 0x0F, '\x1b': 0x01, ' ': 0x39}
for _first, _keys in _SCAN_ROWS:
    for _i, _key in enumerate(_keys):
        SCAN_CODES[_key] = _first + _i
        SCAN_CODES[_SHIFTED.get(_key, _key.upper())] = _first + _i


class BIOSServiceNotImplemented(Exception):
    def __init__(self, interrupt, function):
        self.interrupt = interrupt
        self.function = function
        super().__init__(f'INT {interrupt:02X}h function {function:02X}h is not supported by the emulator')


class Keyboard:
    """Queue of typed keys as (ASCII, scan code) pairs."""

    def __init__(self):
        self.keys = collections.deque()

    def __len__(self):
        return len(self.keys)

    def push(self, ascii, scan=None):
        if scan is None:
            scan = SCAN_CODES.get(chr(ascii), 0)
        self.keys.append((ascii & 0xFF, scan & 0xFF))

    def push_text(self, text):
        for char in text:
            self.push(0x0D if char == '\n' else ord(char) & 0xFF)

    def peek(self):
        return self.keys[0] if self.keys else None

    def pop(self):
        return self.keys.popleft() if self.keys else None

    def clear(self):
        self.keys.clear()


class BIOS:
    """output(text), if given, receives everything written as teletype text
    (INT 10h AH=0Eh and DOS console output)."""

    def __init__(self, cpu, output=None, keyboard=None):
        self.cpu = cpu
        self.output = output
        self.keyboard = keyboard or Keyboard()
        self._video = {
            0x00: self._set_mode, 0x01: self._set_cursor_shape, 0x02: self._set_cursor,
            0x03: self._get_cursor, 0x05: self._select_page, 0x06: self._scroll_up,
            0x07: self._scroll_down, 0x08: self._read_char, 0x09: self._write_char_attribute,
            0x0A: self._write_char_only, 0x0E: self._teletype_service, 0x0F: self._get_mode,
            0x13: self._write_string,
        }
        self._keys = {
            0x00: self._read_key, 0x01: self._check_key, 0x02: self._shift_flags,
            0x10: self._read_key, 0x11: self._check_key, 0x12: self._shift_flags,
        }
        cpu.interrupt_hooks.update({0x10: self._int10, 0x11: self._int11, 0x12: self._int12,
                                    0x16: self._int16, 0x1A: self._int1a})
        cpu.load_listeners.append(lambda c: self.initialize())
        self.initialize()

    # ---------------------------------------------------------------- state

    def initialize(self):
        """Set up the BIOS data area and clear the screen (memory was reset)."""
        cpu = self.cpu
        cpu.write8(BIOS_DATA_SEGMENT, VIDEO_MODE, 3)
        cpu.write16(BIOS_DATA_SEGMENT, SCREEN_COLUMNS, COLUMNS)
        cpu.write16(BIOS_DATA_SEGMENT, CURSOR_SHAPE, 0x0607)
        cpu.write8(BIOS_DATA_SEGMENT, SCREEN_ROWS, ROWS - 1)
        self.clear_screen()

    @property
    def cursor(self):
        """(row, column) of the cursor."""
        column = self.cpu.read8(BIOS_DATA_SEGMENT, CURSOR_POSITION)
        row = self.cpu.read8(BIOS_DATA_SEGMENT, CURSOR_POSITION + 1)
        return row, column

    @cursor.setter
    def cursor(self, position):
        row, column = position
        self.cpu.write8(BIOS_DATA_SEGMENT, CURSOR_POSITION, column)
        self.cpu.write8(BIOS_DATA_SEGMENT, CURSOR_POSITION + 1, row)

    def cell(self, row, column):
        """(character, attribute) on the screen."""
        offset = (row * COLUMNS + column) * 2
        return self.cpu.read8(VIDEO_SEGMENT, offset), self.cpu.read8(VIDEO_SEGMENT, offset + 1)

    def put(self, row, column, char, attribute=None):
        offset = (row * COLUMNS + column) * 2
        self.cpu.write8(VIDEO_SEGMENT, offset, char)
        if attribute is not None:
            self.cpu.write8(VIDEO_SEGMENT, offset + 1, attribute)

    def screen_text(self):
        """The screen as text lines with trailing blanks removed (for tests and copying)."""
        lines = []
        for row in range(ROWS):
            chars = ''.join(chr(self.cell(row, column)[0]) for column in range(COLUMNS))
            lines.append(chars.replace('\0', ' ').rstrip())
        return '\n'.join(lines).rstrip('\n')

    def clear_screen(self, attribute=DEFAULT_ATTRIBUTE):
        self.scroll(0, 0, 0, ROWS - 1, COLUMNS - 1, attribute)
        self.cursor = (0, 0)

    def scroll(self, lines, top, left, bottom, right, attribute, up=True):
        """Scroll a window up (or down) by lines; 0 lines (or too many) clears it."""
        bottom, right = min(bottom, ROWS - 1), min(right, COLUMNS - 1)
        if top > bottom or left > right:
            return
        height = bottom - top + 1
        if lines == 0 or lines >= height:
            lines = height
        rows = range(top, bottom + 1) if up else range(bottom, top - 1, -1)
        for row in rows:
            source = row + lines if up else row - lines
            for column in range(left, right + 1):
                if top <= source <= bottom:
                    char, attr = self.cell(source, column)
                else:
                    char, attr = 0x20, attribute
                self.put(row, column, char, attr)

    def teletype(self, text):
        """Write text at the cursor like INT 10h AH=0Eh, scrolling at the bottom."""
        for char in text:
            self._teletype(ord(char) & 0xFF)
        if self.output is not None and text:
            self.output(text)

    def _teletype(self, char):
        row, column = self.cursor
        if char == 0x0D:
            column = 0
        elif char == 0x0A:
            row += 1
        elif char == 0x08:
            column = max(0, column - 1)
        elif char == 0x07:
            pass  # bell
        else:
            self.put(row, column, char)
            column += 1
            if column >= COLUMNS:
                column = 0
                row += 1
        if row >= ROWS:
            attribute = self.cell(ROWS - 1, COLUMNS - 1)[1] or DEFAULT_ATTRIBUTE
            self.scroll(1, 0, 0, ROWS - 1, COLUMNS - 1, attribute)
            row = ROWS - 1
        self.cursor = (row, column)

    def next_key(self, remove=True):
        """The next (ASCII, scan) pair; raises WaitingForInput when none was typed."""
        key = self.keyboard.pop() if remove else self.keyboard.peek()
        if key is None:
            raise WaitingForInput()
        return key

    # -------------------------------------------------------------- INT 10h

    def _int10(self, cpu):
        service = self._video.get(cpu.AH)
        if service is None:
            raise BIOSServiceNotImplemented(0x10, cpu.AH)
        service(cpu)

    def _set_mode(self, cpu):
        cpu.write8(BIOS_DATA_SEGMENT, VIDEO_MODE, cpu.AL & 0x7F)
        if not cpu.AL & 0x80:  # bit 7 keeps the screen contents
            self.clear_screen()

    def _set_cursor_shape(self, cpu):
        cpu.write16(BIOS_DATA_SEGMENT, CURSOR_SHAPE, cpu.CX)

    def _set_cursor(self, cpu):
        self.cursor = (min(cpu.DH, ROWS - 1), min(cpu.DL, COLUMNS - 1))

    def _get_cursor(self, cpu):
        cpu.DH, cpu.DL = self.cursor
        cpu.CX = cpu.read16(BIOS_DATA_SEGMENT, CURSOR_SHAPE)

    def _select_page(self, cpu):
        pass  # only page 0 is emulated

    def _scroll_up(self, cpu):
        self.scroll(cpu.AL, cpu.CH, cpu.CL, cpu.DH, cpu.DL, cpu.BH, up=True)

    def _scroll_down(self, cpu):
        self.scroll(cpu.AL, cpu.CH, cpu.CL, cpu.DH, cpu.DL, cpu.BH, up=False)

    def _read_char(self, cpu):
        char, attribute = self.cell(*self.cursor)
        cpu.AX = (attribute << 8) | char

    def _write_repeated(self, cpu, attribute):
        row, column = self.cursor
        for i in range(cpu.CX):
            position = row * COLUMNS + column + i
            if position >= ROWS * COLUMNS:
                break
            self.put(position // COLUMNS, position % COLUMNS, cpu.AL, attribute)

    def _write_char_attribute(self, cpu):
        self._write_repeated(cpu, cpu.BL)

    def _write_char_only(self, cpu):
        self._write_repeated(cpu, None)

    def _teletype_service(self, cpu):
        self.teletype(chr(cpu.AL))

    def _get_mode(self, cpu):
        cpu.AL = cpu.read8(BIOS_DATA_SEGMENT, VIDEO_MODE)
        cpu.AH = COLUMNS
        cpu.BH = 0

    def _write_string(self, cpu):
        mode, attribute = cpu.AL, cpu.BL
        saved = self.cursor
        self.cursor = (cpu.DH, cpu.DL)
        offset = cpu.BP
        for _ in range(cpu.CX):
            char = cpu.read8(cpu.ES, offset)
            offset += 1
            if mode & 2:
                attribute = cpu.read8(cpu.ES, offset)
                offset += 1
            if char in (0x07, 0x08, 0x0A, 0x0D):
                self._teletype(char)
            else:
                row, column = self.cursor
                self.put(row, column, char, attribute)
                self._teletype(char)
        if not mode & 1:
            self.cursor = saved

    # -------------------------------------------------------------- INT 16h

    def _int16(self, cpu):
        service = self._keys.get(cpu.AH)
        if service is None:
            raise BIOSServiceNotImplemented(0x16, cpu.AH)
        service(cpu)

    def _read_key(self, cpu):
        ascii, scan = self.next_key()
        cpu.AX = (scan << 8) | ascii

    def _check_key(self, cpu):
        key = self.keyboard.peek()
        cpu.set_flags(ZF=key is None)
        if key is not None:
            cpu.AX = (key[1] << 8) | key[0]

    def _shift_flags(self, cpu):
        cpu.AL = 0

    # ---------------------------------------------------- INT 11h, 12h, 1Ah

    def _int11(self, cpu):
        cpu.AX = 0x0021  # one floppy drive, 80x25 colour display

    def _int12(self, cpu):
        cpu.AX = 640  # KB of conventional memory

    def _int1a(self, cpu):
        now = datetime.datetime.now()
        if cpu.AH == 0x00:
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            ticks = int((now - midnight).total_seconds() * 1193180 / 65536)
            cpu.CX, cpu.DX = ticks >> 16, ticks & 0xFFFF
            cpu.AL = 0
        elif cpu.AH == 0x02:
            cpu.CH, cpu.CL, cpu.DH = _bcd(now.hour), _bcd(now.minute), _bcd(now.second)
            cpu.DL = 0
            cpu.set_flags(CF=False)
        elif cpu.AH == 0x04:
            cpu.CH, cpu.CL = _bcd(now.year // 100), _bcd(now.year % 100)
            cpu.DH, cpu.DL = _bcd(now.month), _bcd(now.day)
            cpu.set_flags(CF=False)
        else:
            raise BIOSServiceNotImplemented(0x1A, cpu.AH)


def _bcd(value):
    return ((value // 10) << 4) | (value % 10)
