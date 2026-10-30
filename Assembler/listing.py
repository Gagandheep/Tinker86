"""Map source lines and symbols to addresses using TASM listing and TLINK map files.

A TASM listing (tasm /l) gives every source line's offset inside its segment
plus the symbol table; the TLINK .MAP file gives where each segment starts
in the executable image. Together with the segment the program was loaded at,
that yields physical addresses for source lines and symbols.

Listing lines look like this once tabs are expanded (source from column 40):

         1         2         3         4
    0123456789012345678901234567890123456789
         17    0000  50 6C 65 61 73 65 20+  msg DB 'Please type a character',0
         49    0010  68 0063r                push OFFSET address
    1    43                                  (a line of an INCLUDEd file)
"""
import bisect
import os
import re

SOURCE_COLUMN = 40
SYMBOL_SIZES = {'byte': 1, 'word': 2, 'dword': 4, 'pword': 6, 'fword': 6, 'qword': 8, 'tbyte': 10}
SIMPLIFIED_SEGMENTS = {'.data': '_DATA', '.stack': 'STACK', '.const': 'CONST', '.data?': '_BSS',
                       '.fardata': 'FAR_DATA', '.fardata?': 'FAR_BSS'}
_HEX4 = re.compile(r'^[0-9A-Fa-f]{4}$')
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
    lines = []
    segment = None
    for raw in raw_lines:
        expanded = raw.expandtabs(8)
        head = expanded[:7]
        if len(head) < 7 or not head[1:].strip().isdigit():
            continue  # page headers, continuation lines, tables
        if head[0] != ' ':
            continue  # nesting level: a line from an INCLUDEd file or macro expansion
        number = int(head[1:])
        source = expanded[SOURCE_COLUMN:]
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
        offset_text = expanded[8:16].strip()
        if not _HEX4.match(offset_text):
            continue
        has_bytes = bool(expanded[16:SOURCE_COLUMN].strip())
        lines.append(ListingLine(number, line_segment, int(offset_text, 16), has_bytes, source.strip()))
    if not lines:
        raise ListingError(f'{path}: no source lines with offsets found')
    return Listing(lines, symbols, groups, code_segment)


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
    """SourceMap for an executable built by TASM/TLINK, or None if the listing or map is missing."""
    listing_path = find_companion(exe_path, '.lst')
    map_path = find_companion(exe_path, '.map')
    if listing_path is None or map_path is None:
        return None
    return SourceMap(parse_listing(listing_path), parse_map(map_path), load_segment)
