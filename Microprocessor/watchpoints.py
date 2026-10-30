"""Data watchpoints: watch memory and stop when an instruction reads or writes it."""
import re

MODES = ('write', 'read', 'access', 'never')  # when to stop; 'never' only displays the value
SEGMENT_REGISTERS = ('CS', 'DS', 'ES', 'SS')
OFFSET_REGISTERS = ('AX', 'BX', 'CX', 'DX', 'SP', 'BP', 'SI', 'DI')
_TERM = re.compile(r'\s*([+-]?)\s*([^\s+-]+)')


class AddressError(ValueError):
    pass


def parse_number(text):
    """Hex number: 1234, 1234h or 0x1234."""
    text = text.strip().lower()
    if text.startswith('0x'):
        text = text[2:]
    elif text.endswith('h'):
        text = text[:-1]
    if not text or any(c not in '0123456789abcdef' for c in text):
        raise AddressError(f'not a hex number: {text!r}')
    return int(text, 16)


def parse_address(expression, cpu, symbols=None):
    """Physical address and size hint for an address expression.

    Accepted: a symbol from the listing (optionally +/- hex offset), SEG:OFFSET
    where SEG is CS/DS/ES/SS or a hex segment and OFFSET a sum of hex numbers
    and registers (DS:SI+2), a bare offset in DS, or a physical address of more
    than four hex digits. symbols(name) returns (address, size) or None.
    Returns (address, size or None).
    """
    expression = expression.strip()
    if not expression:
        raise AddressError('empty address')
    if ':' in expression:
        segment_text, offset_text = expression.split(':', 1)
        segment_text = segment_text.strip().upper()
        if segment_text in SEGMENT_REGISTERS:
            segment = getattr(cpu, segment_text)
        else:
            segment = parse_number(segment_text)
            if segment > 0xFFFF:
                raise AddressError('segment out of range')
        return cpu.physical_address(segment, _offset(offset_text, cpu)), None
    terms = _TERM.findall(expression)
    first = terms[0][1] if terms else ''
    if symbols is not None and not terms[0][0] and _is_name(first):
        found = symbols(first)
        if found is not None:
            address, size = found
            rest = expression[expression.upper().index(first.upper()) + len(first):]
            return (address + (_offset(rest, cpu, allow_empty=True) if rest.strip() else 0)) & 0xFFFFF, size
    if len(terms) == 1 and not terms[0][0] and first.upper() not in OFFSET_REGISTERS:
        digits = first.lower()
        digits = digits[2:] if digits.startswith('0x') else digits[:-1] if digits.endswith('h') else digits
        if len(digits) > 4:
            address = parse_number(first)
            if address > 0xFFFFF:
                raise AddressError('physical address out of range')
            return address, None
    return cpu.physical_address(cpu.DS, _offset(expression, cpu)), None


def _is_name(text):
    return re.match(r'^[A-Za-z_@?$][\w@?$]*$', text) is not None and text.upper() not in OFFSET_REGISTERS


def _offset(text, cpu, allow_empty=False):
    terms = _TERM.findall(text)
    if not terms:
        if allow_empty:
            return 0
        raise AddressError('missing offset')
    if ''.join(sign + value for sign, value in terms).replace(' ', '') != text.replace(' ', ''):
        raise AddressError(f'cannot parse {text!r}')
    total = 0
    for sign, value in terms:
        upper = value.upper()
        number = getattr(cpu, upper) if upper in OFFSET_REGISTERS else parse_number(value)
        if number > 0xFFFF:
            raise AddressError(f'offset {value} does not fit in 16 bits')
        total += -number if sign == '-' else number
    return total & 0xFFFF


class Watchpoint:
    def __init__(self, expression, address, size, mode='write'):
        if mode not in MODES:
            raise ValueError(mode)
        self.expression = expression
        self.address = address & 0xFFFFF
        self.size = max(1, size)
        self.mode = mode
        self.last = None  # bytes seen at the last check

    @property
    def addresses(self):
        return {(self.address + i) & 0xFFFFF for i in range(self.size)}

    def value(self, memory):
        return bytes(memory[(self.address + i) & 0xFFFFF] for i in range(self.size))

    def __repr__(self):
        return f'Watchpoint({self.expression!r}, {self.address:05X}, {self.size}, {self.mode})'


class Hit:
    def __init__(self, watchpoint, kind, old, new):
        self.watchpoint = watchpoint
        self.kind = kind  # 'written' or 'read'
        self.old = old
        self.new = new


def format_value(data):
    """Bytes as hex, plus the little-endian word for 2-byte watches."""
    text = ' '.join(f'{b:02X}' for b in data)
    if len(data) == 2:
        text += f'  ({data[0] | (data[1] << 8):04X}h)'
    return text


class WatchList:
    """The watchpoints of a CPU; check() after every step reports hits."""

    def __init__(self, cpu):
        self.cpu = cpu
        self.items = []
        self.watched = set()  # every watched address, whatever the mode
        self._writes = set()
        self._reads = set()

    def add(self, watchpoint):
        watchpoint.last = watchpoint.value(self.cpu.memory)
        self.items.append(watchpoint)
        self.changed()
        return watchpoint

    def remove(self, index):
        del self.items[index]
        self.changed()

    def clear(self):
        self.items = []
        self.changed()

    def changed(self):
        """Recompute the watched address sets after adding, removing or editing."""
        self._writes = set()
        self._reads = set()
        self.watched = set()
        for watchpoint in self.items:
            self.watched |= watchpoint.addresses
            if watchpoint.mode in ('write', 'access'):
                self._writes |= watchpoint.addresses
            if watchpoint.mode in ('read', 'access'):
                self._reads |= watchpoint.addresses
        self.cpu.track_reads = bool(self._reads)

    def check(self):
        """Hits caused by the last step (and refreshes the remembered values)."""
        cpu = self.cpu
        written = self._writes.intersection(cpu.last_writes) if self._writes and cpu.last_writes else set()
        read = self._reads.intersection(cpu.last_reads) if self._reads and cpu.last_reads else set()
        hits = []
        for watchpoint in self.items:
            new = watchpoint.value(cpu.memory)
            addresses = watchpoint.addresses
            if watchpoint.mode in ('write', 'access') and written & addresses:
                hits.append(Hit(watchpoint, 'written', watchpoint.last, new))
            elif watchpoint.mode in ('read', 'access') and read & addresses:
                hits.append(Hit(watchpoint, 'read', watchpoint.last, new))
            watchpoint.last = new
        return hits
