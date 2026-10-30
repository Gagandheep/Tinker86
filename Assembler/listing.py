"""Map source lines and symbols to addresses using assembler listings.

Two formats are understood: TASM listings with TLINK map files, and JWasm
(or UASM) listings, which describe the segment layout of the .EXE themselves
(see parse_jwasm_listing at the end of this module).

A TASM listing (tasm /l) gives every source line's offset inside its segment
plus the symbol table; the TLINK .MAP file gives where each segment starts
in the executable image. Together with the segment the program was loaded at,
that yields physical addresses for source lines and symbols.

Listing lines look like this once tabs are expanded. The offset field is four
characters wide for 16-bit code and eight (right-aligned) when 32-bit segments
may occur; the source text follows 25 columns after it:

         1         2         3         4
    01234567890123456789012345678901234567890
         14 0000  B8 0000s               start:  mov     ax, @data
         17 0005  BA 0000r                       mov     dx, offset message
         49     0010  68 0063r                   push OFFSET address   (8-wide)
    1    43                                      (a line of an INCLUDEd file)
"""
import bisect
import os
import re

OFFSET_COLUMN = 8
BYTES_WIDTH = 25  # columns between the offset field and the source text
WRAP_COLUMN = 120  # TASM wraps listing lines at 132 columns, ending them with '+'
SYMBOL_SIZES = {'byte': 1, 'word': 2, 'dword': 4, 'pword': 6, 'fword': 6, 'qword': 8, 'tbyte': 10}
SIMPLIFIED_SEGMENTS = {'.data': '_DATA', '.stack': 'STACK', '.const': 'CONST', '.data?': '_BSS',
                       '.fardata': 'FAR_DATA', '.fardata?': 'FAR_BSS'}
_HEX4 = re.compile(r'^[0-9A-Fa-f]{4}$')
_OFFSET_FIELD = re.compile(r'^( {4}[0-9A-Fa-f]{4}|[0-9A-Fa-f]{8}|[0-9A-Fa-f]{4})(?=\s|$)')
_SEGMENT = re.compile(r'^\s*(?:(\w+)\s+)?(segment|ends)\b', re.IGNORECASE)
_SIMPLIFIED = re.compile(r'^\s*(\.code|\.data\??|\.stack|\.const|\.fardata\??)(?=\s|$|;)', re.IGNORECASE)
_SYMBOL = re.compile(r'^(\S+)\s+(Byte|Word|Dword|Pword|Fword|Qword|Tbyte|Near|Far|Number|Text|Alias|'
                     r'Struct|Proc|Label)\b\s*(.*)$', re.IGNORECASE)
_MAP_SEGMENT = re.compile(r'^\s*([0-9A-Fa-f]{5})H\s+([0-9A-Fa-f]{5})H\s+([0-9A-Fa-f]{5})H\s+(\S+)\s*(\S*)')
_GROUP_MEMBER = re.compile(r'^(\s*)(\S+)\s+(Group|16|32)\b')


class ListingError(Exception):
    pass


class ListingLine:
    def __init__(self, number, segment, offset, has_bytes, source):
        self.number = number
        self.segment = segment
        self.offset = offset
        self.has_bytes = has_bytes
        self.source = source

    def __repr__(self):
        return f'ListingLine({self.number}, {self.segment}, {self.offset}, {self.has_bytes})'


class Listing:
    """Parsed TASM listing: lines of the main source file, symbols and groups."""

    def __init__(self, lines, symbols, groups, code_segment):
        self.lines = lines  # ListingLine for every main-file line that has an offset
        self.symbols = symbols  # NAME -> (segment or group, offset, size)
        self.groups = groups  # group name -> [segment names]
        self.code_segment = code_segment


def parse_listing(path):
    with open(path, encoding='latin-1') as f:
        text = f.read()
    raw_lines = text.replace('\f', '\n').splitlines()
    symbols, groups, code_segment = _parse_tables(raw_lines)
    width = _offset_width(raw_lines)
    source_column = OFFSET_COLUMN + width + BYTES_WIDTH
    lines = []
    segment = None
    # TASM numbers listing lines, not source lines: bytes that do not fit (a field
    # ending in '+') and long source lines wrapped at 132 columns get numbered
    # lines of their own. Count the source lines of the main file instead.
    number = 0
    continued = False
    for raw in raw_lines:
        expanded = raw.expandtabs(8).rstrip()
        head = expanded[:7].ljust(7)
        if not head[1:].strip().isdigit():
            continue  # page headers, tables
        if head[0] != ' ':
            continue  # nesting level: a line from an INCLUDEd file or macro expansion
        offset_field = expanded[OFFSET_COLUMN:OFFSET_COLUMN + width].strip()
        bytes_field = expanded[OFFSET_COLUMN + width:source_column].strip()
        continuation = continued and not offset_field
        continued = bytes_field.endswith('+') or (len(expanded) >= WRAP_COLUMN and expanded.endswith('+'))
        if continuation:
            continue
        number += 1
        source = expanded[source_column:]
        code = _strip_comment(source)
        # A .CODE/.DATA line shows the offset reached in the segment it leaves, a
        # SEGMENT line the offset in the segment it opens, an ENDS line its own segment's.
        line_segment = segment
        simplified = _SIMPLIFIED.match(code)
        explicit = _SEGMENT.match(code)
        if simplified:
            directive = simplified.group(1).lower()
            segment = code_segment if directive == '.code' else SIMPLIFIED_SEGMENTS[directive]
        elif explicit and explicit.group(1):
            if explicit.group(2).lower() == 'segment':
                segment = line_segment = explicit.group(1).upper()
            else:
                segment = None
        offset_text = offset_field
        if not _HEX4.match(offset_text[-4:]) or not all(c in '0123456789ABCDEFabcdef' for c in offset_text):
            continue
        has_bytes = bool(bytes_field)
        lines.append(ListingLine(number, line_segment, int(offset_text, 16), has_bytes, source.strip()))
    if not lines:
        raise ListingError(f'{path}: no source lines with offsets found')
    return Listing(lines, symbols, groups, code_segment)


def _offset_width(raw_lines):
    """4 or 8: the width of the offset field, from the first numbered line that has one."""
    for raw in raw_lines:
        expanded = raw.expandtabs(8)
        if len(expanded) > OFFSET_COLUMN and expanded[1:7].strip().isdigit():
            match = _OFFSET_FIELD.match(expanded[OFFSET_COLUMN:])
            if match:
                return len(match.group(1))
    return 4


def _strip_comment(source):
    """Source text without a ; comment (ignoring ; inside quotes)."""
    quote = None
    for i, char in enumerate(source):
        if quote:
            if char == quote:
                quote = None
        elif char in '\'"':
            quote = char
        elif char == ';':
            return source[:i]
    return source


def _parse_tables(raw_lines):
    """Symbol table and Groups & Segments table at the end of the listing."""
    symbols, groups = {}, {}
    code_segment = '_TEXT'
    section = None
    group = None
    for raw in raw_lines:
        line = raw.expandtabs(8)
        stripped = line.strip()
        if stripped.startswith('Symbol Name'):
            section = 'symbols'
            continue
        if stripped.startswith('Groups & Segments'):
            section = 'groups'
            continue
        if stripped.startswith(('Macro Name', 'Structure Name', 'Record Name', 'Turbo Assembler', 'Error messages')):
            section = None if not stripped.startswith('Turbo Assembler') else section
            continue
        if section == 'symbols':
            match = _SYMBOL.match(stripped)
            if not match:
                continue
            name, kind, value = match.group(1).upper(), match.group(2).lower(), match.group(3).strip()
            if name == '@CODE' and kind == 'text':
                code_segment = value.upper()
            location = re.match(r'^(\w+):([0-9A-Fa-f]{4})$', value)
            if location and kind not in ('text', 'number'):
                symbols[name] = (location.group(1).upper(), int(location.group(2), 16), SYMBOL_SIZES.get(kind, 1))
        elif section == 'groups':
            match = _GROUP_MEMBER.match(line)
            if not match:
                continue
            indent, name, kind = match.groups()
            if kind == 'Group':
                group = name.upper()
                groups[group] = []
            elif indent and group is not None:
                groups[group].append(name.upper())
            else:
                group = None
    return symbols, groups, code_segment


def parse_map(path):
    """TLINK map: segment name -> (start offset in the image, length, class)."""
    segments = {}
    with open(path, encoding='latin-1') as f:
        for line in f:
            match = _MAP_SEGMENT.match(line)
            if match:
                start, _, length, name, kind = match.groups()
                segments[name.upper()] = (int(start, 16), int(length, 16), kind.upper())
    if not segments:
        raise ListingError(f'{path}: no segment table found')
    return segments


class SourceMap:
    """Addresses of source lines and symbols for a program loaded at load_segment."""

    def __init__(self, listing, segments, load_segment):
        self.listing = listing
        self.segments = segments
        self.image_base = load_segment << 4
        self.line_address = {}  # source line number -> physical address (lines that emit bytes)
        by_address = {}
        code_segments = {name for name, (_, _, kind) in segments.items() if kind == 'CODE'}
        code_segments.add(listing.code_segment)
        for line in listing.lines:
            base = self.segment_base(line.segment)
            if base is None:
                continue
            address = base + line.offset
            if line.has_bytes:
                self.line_address[line.number] = address
            if line.segment in code_segments:
                previous = by_address.get(address)
                if previous is None or line.has_bytes or not previous.has_bytes:
                    by_address[address] = line
        self._code_addresses = sorted(by_address)
        self._code_lines = [by_address[a].number for a in self._code_addresses]
        self.code_line_address = {number: address for address, number in
                                  zip(self._code_addresses, self._code_lines)}

    def segment_base(self, name):
        """Physical address where a segment's offset 0 is."""
        if name is None or name not in self.segments:
            return None
        return self.image_base + self.segments[name][0]

    def group_base(self, name):
        """Physical address of a group's frame: the paragraph of its lowest segment."""
        members = [self.segments[m][0] for m in self.listing.groups.get(name, []) if m in self.segments]
        if not members:
            return None
        return self.image_base + (min(members) & ~0xF)

    def line_for_address(self, address):
        """Source line of the code containing physical address, or None."""
        index = bisect.bisect_right(self._code_addresses, address) - 1
        if index < 0:
            return None
        start = self._code_addresses[index]
        if address - start > 0x100:  # too far from any source line (e.g. outside the program)
            return None
        return self._code_lines[index]

    def address_for_line(self, number):
        """Physical address of the first instruction on or after source line number."""
        if number in self.code_line_address:
            return self.code_line_address[number]
        later = [n for n in self.code_line_address if n >= number]
        return self.code_line_address[min(later)] if later else None

    def symbol(self, name):
        """(physical address, size) of a symbol, or None."""
        entry = self.listing.symbols.get(name.upper())
        if entry is None:
            return None
        base_name, offset, size = entry
        base = self.group_base(base_name) if base_name in self.listing.groups else self.segment_base(base_name)
        if base is None:
            return None
        return base + offset, size


def find_companion(exe_path, extension):
    """The .LST/.MAP next to an executable (case-insensitive), or None."""
    directory, name = os.path.split(exe_path)
    wanted = (os.path.splitext(name)[0] + extension).lower()
    for entry in os.listdir(directory or '.'):
        if entry.lower() == wanted:
            return os.path.join(directory, entry)
    return None


def load_source_map(exe_path, load_segment):
    """SourceMap for an executable built by TASM/TLINK or JWasm, or None if the listing
    (or, for TASM, the map) is missing."""
    listing_path = find_companion(exe_path, '.lst')
    if listing_path is None:
        return None
    if is_jwasm_listing(listing_path):
        listing, segments = parse_jwasm_listing(listing_path)
        return SourceMap(listing, segments, load_segment)
    map_path = find_companion(exe_path, '.map')
    if map_path is None:
        return None
    return SourceMap(parse_listing(listing_path), parse_map(map_path), load_segment)


# ------------------------------------------------------------------- JWasm

# A JWasm listing (jwasm -mz -Sx -Fl) has one line per line of the main source
# file, without line numbers: offset in columns 0-3 (0-7 for 32-bit segments),
# bytes up to column 27, a macro nesting level (digit) or 'C' (INCLUDEd file)
# in columns 28-31 and the source from column 32. Macro definition lines start
# with ' >'. -Sx makes false conditional blocks keep their (blank) lines.
JWASM_SOURCE_COLUMN = 32
JWASM_MARKER = slice(28, 32)
_JWASM_SECTIONS = ('Binary Map:', 'Macros:', 'Segments and Groups:', 'Structures and Unions:', 'Records:',
                   'Types:', 'Procedures, parameters and locals:', 'Symbols:')
_JWASM_MESSAGE = re.compile(r'^\s*(Warning|Error)\s+A\d+:')
_JWASM_OFFSET = re.compile(r'^([0-9A-Fa-f]{8}|[0-9A-Fa-f]{4})(?= |$)')
_JWASM_BINARY_MAP = re.compile(r'^(\S+)\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s*$')
_JWASM_SYMBOL = re.compile(r'^(?:[LP] )?(\w+)(?:\[\d+\])?\s+([0-9A-Fa-f]+)h?\s+(\w+)')


def is_jwasm_listing(path):
    with open(path, encoding='latin-1') as f:
        first = f.readline()
    return first.startswith(('JWasm', 'UASM'))


def parse_jwasm_listing(path):
    """(Listing, segments) from a JWasm listing; segments maps a segment name to
    (start offset in the .EXE image, size, class) like parse_map does for TASM."""
    with open(path, encoding='latin-1') as f:
        raw_lines = f.read().splitlines()
    end = next((i for i, line in enumerate(raw_lines) if line.strip() in _JWASM_SECTIONS), len(raw_lines))
    segments, symbols, code_segment = _parse_jwasm_tables(raw_lines[end:])
    lines = []
    number = 0
    segment = None
    invoker = None  # the last main-file line without code of its own (a macro call, INCLUDE)
    for raw in raw_lines[2:end]:  # the first two lines are the banner and the file name
        expanded = raw.expandtabs(8).rstrip()
        if _JWASM_MESSAGE.match(expanded):
            continue
        if not expanded.startswith(' >') and expanded[JWASM_MARKER].strip():
            # macro expansion or a line of an INCLUDEd file: its first code belongs
            # to the line that expanded or included it
            offset = _JWASM_OFFSET.match(expanded)
            if invoker is not None and offset and expanded[offset.end():JWASM_MARKER.start].strip():
                lines.append(ListingLine(invoker, segment, int(offset.group(1), 16), True, ''))
                invoker = None
            continue
        number += 1
        invoker = None
        source = expanded[JWASM_SOURCE_COLUMN:]
        code = _strip_comment(source)
        line_segment = segment
        simplified = _SIMPLIFIED.match(code)
        explicit = _SEGMENT.match(code)
        if simplified:
            directive = simplified.group(1).lower()
            segment = line_segment = code_segment if directive == '.code' else SIMPLIFIED_SEGMENTS[directive]
        elif explicit and explicit.group(1):
            if explicit.group(2).lower() == 'segment':
                segment = line_segment = explicit.group(1).upper()
            else:
                segment = None
        offset = _JWASM_OFFSET.match(expanded)
        if offset is None:
            if code.strip() and not expanded.startswith(' >'):
                invoker = number
            continue
        has_bytes = bool(expanded[offset.end():JWASM_MARKER.start].strip())
        lines.append(ListingLine(number, line_segment, int(offset.group(1), 16), has_bytes, source.strip()))
    if not lines:
        raise ListingError(f'{path}: no source lines with offsets found')
    return Listing(lines, symbols, {}, code_segment), segments


def _parse_jwasm_tables(raw_lines):
    binary_map, classes, group_of = {}, {}, {}
    symbols = {}
    code_segment = '_TEXT'
    header = 0
    section = None
    group = None
    for raw in raw_lines:
        line = raw.expandtabs(8).rstrip()
        if line.strip() in _JWASM_SECTIONS:
            section = line.strip()
            group = None
            continue
        if not line.strip() or line.startswith(('-', 'Segment ', '                N a m e')):
            continue
        if section == 'Binary Map:':
            match = _JWASM_BINARY_MAP.match(line)
            if match:
                name, position, rva, file_size, memory_size = match.groups()
                if name == '<header>':
                    header = int(file_size, 16)
                else:
                    binary_map[name.upper()] = (int(position, 16), int(rva, 16), int(file_size, 16),
                                                int(memory_size, 16))
            continue
        name = line.split()[0]
        rest = line[len(name):].lstrip(' .')
        if section == 'Segments and Groups:':
            if rest.startswith('GROUP'):
                group = name.upper()
            else:
                classes[name.upper()] = rest.split()[-1].strip("'").upper() if rest.split() else ''
                if group is not None:
                    group_of[name.upper()] = group  # members are listed after their group
        elif section in ('Symbols:', 'Procedures, parameters and locals:'):
            if name.upper() == '@CODE' and rest.startswith('Text'):
                code_segment = rest.split()[-1].upper()
            match = _JWASM_SYMBOL.match(rest)
            if match:
                kind, value, segment = match.groups()
                if kind.lower() not in ('number', 'text', 'func'):
                    symbols[name.upper()] = (segment.upper(), int(value, 16), SYMBOL_SIZES.get(kind.lower(), 1))
    # image offsets: from the file position, or for segments without file data
    # (e.g. the stack) from their group's frame
    starts, frames = {}, {}
    for name, (position, rva, file_size, _) in binary_map.items():
        if file_size:
            starts[name] = position - header
            if name in group_of:
                frames[group_of[name]] = starts[name] - rva
    for name, (_, rva, file_size, _) in binary_map.items():
        if name not in starts and group_of.get(name) in frames:
            starts[name] = frames[group_of[name]] + rva
    segments = {name: (starts[name], binary_map[name][3], classes.get(name, ''))
                for name in binary_map if name in starts}
    return segments, symbols, code_segment
