"""The file system behind the DOS file services: drive C: is a host directory.

DOS paths (C:\\DIR\\FILE.TXT, relative paths, / or \\ separators) map onto
files under root, matched case-insensitively. Paths can never leave root.
"""
import datetime
import fnmatch
import os

# DOS error codes
INVALID_FUNCTION = 0x01
FILE_NOT_FOUND = 0x02
PATH_NOT_FOUND = 0x03
TOO_MANY_OPEN_FILES = 0x04
ACCESS_DENIED = 0x05
INVALID_HANDLE = 0x06
INSUFFICIENT_MEMORY = 0x08
INVALID_DRIVE = 0x0F
REMOVE_CURRENT_DIRECTORY = 0x10
NO_MORE_FILES = 0x12

# attributes
READ_ONLY, HIDDEN, SYSTEM, VOLUME, DIRECTORY, ARCHIVE = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20

STANDARD_HANDLES = {0: 'stdin', 1: 'stdout', 2: 'stderr', 3: 'aux', 4: 'prn'}
MAX_HANDLES = 20
INVALID_NAME_CHARACTERS = set('"*+,:;<=>?[]|') | {chr(c) for c in range(32)}


class DOSError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f'DOS error {code:02X}h')


def dos_time(timestamp):
    t = datetime.datetime.fromtimestamp(timestamp)
    time = (t.hour << 11) | (t.minute << 5) | (t.second // 2)
    date = (max(t.year - 1980, 0) << 9) | (t.month << 5) | t.day
    return time, date


def dos_name(name):
    """Upper-case 8.3 form of a host file name, or None if it has no 8.3 form."""
    upper = name.upper()
    base, dot, extension = upper.partition('.')
    if not base or len(base) > 8 or len(extension) > 3 or '.' in extension or ' ' in upper:
        return None
    if any(c in INVALID_NAME_CHARACTERS for c in upper):
        return None
    return upper


class DOSFileSystem:
    def __init__(self, root=None):
        self.handles = {}
        self.set_root(root or os.getcwd())

    def set_root(self, root):
        self.root = os.path.realpath(root)
        self.cwd = []  # components below root
        self.close_all()

    def close_all(self):
        for file in self.handles.values():
            file.close()
        self.handles = {}

    # ------------------------------------------------------------------ paths

    def _components(self, path, wildcards=False):
        """DOS path -> list of components relative to root ('..' resolved).
        With wildcards, * and ? are allowed in the last component."""
        path = path.replace('/', '\\')
        if len(path) >= 2 and path[1] == ':':
            if path[0].upper() != 'C':
                raise DOSError(INVALID_DRIVE)
            path = path[2:]
        parts = [] if path.startswith('\\') else list(self.cwd)
        pieces = path.split('\\')
        for i, part in enumerate(pieces):
            if part in ('', '.'):
                continue
            if part == '..':
                if parts:
                    parts.pop()
                continue
            allowed = set('*?') if wildcards and i == len(pieces) - 1 else set()
            if any(c in INVALID_NAME_CHARACTERS - allowed for c in part):
                raise DOSError(PATH_NOT_FOUND)
            parts.append(part)
        return parts

    def _host(self, parts, must_exist=True):
        """Host path for components, matching existing names case-insensitively.

        The last component may be missing when must_exist is False.
        """
        host = self.root
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            try:
                entries = os.listdir(host)
            except (FileNotFoundError, NotADirectoryError):
                raise DOSError(PATH_NOT_FOUND)
            match = next((e for e in entries if e.upper() == part.upper()), None)
            if match is None:
                if last and not must_exist:
                    match = part.upper()
                else:
                    raise DOSError(FILE_NOT_FOUND if last else PATH_NOT_FOUND)
            host = os.path.join(host, match)
            if not last and not os.path.isdir(host):
                raise DOSError(PATH_NOT_FOUND)
        real = os.path.realpath(host)
        if os.path.commonpath([real, self.root]) != self.root:
            raise DOSError(ACCESS_DENIED)  # e.g. a symlink pointing outside root
        return host

    def resolve(self, path, must_exist=True):
        return self._host(self._components(path), must_exist)

    def current_directory(self):
        """Current directory without drive or leading backslash, as DOS 47h returns it."""
        return '\\'.join(part.upper() for part in self.cwd)

    def change_directory(self, path):
        parts = self._components(path)
        try:
            host = self._host(parts)
        except DOSError as error:
            raise DOSError(PATH_NOT_FOUND) if error.code == FILE_NOT_FOUND else error
        if not os.path.isdir(host):
            raise DOSError(PATH_NOT_FOUND)
        self.cwd = [os.path.basename(self._host(parts[:i + 1])) for i in range(len(parts))]

    def make_directory(self, path):
        host = self.resolve(path, must_exist=False)
        try:
            os.mkdir(host)
        except FileExistsError:
            raise DOSError(ACCESS_DENIED)
        except FileNotFoundError:
            raise DOSError(PATH_NOT_FOUND)

    def remove_directory(self, path):
        parts = self._components(path)
        host = self._host(parts)
        if [p.upper() for p in parts] == [p.upper() for p in self.cwd[:len(parts)]]:
            raise DOSError(REMOVE_CURRENT_DIRECTORY)
        if not os.path.isdir(host):
            raise DOSError(PATH_NOT_FOUND)
        try:
            os.rmdir(host)
        except OSError:
            raise DOSError(ACCESS_DENIED)

    # ------------------------------------------------------------------ files

    def _new_handle(self, file):
        for handle in range(len(STANDARD_HANDLES), MAX_HANDLES):
            if handle not in self.handles:
                self.handles[handle] = file
                return handle
        file.close()
        raise DOSError(TOO_MANY_OPEN_FILES)

    def create(self, path):
        host = self.resolve(path, must_exist=False)
        if os.path.isdir(host):
            raise DOSError(ACCESS_DENIED)
        if len(self.handles) + len(STANDARD_HANDLES) >= MAX_HANDLES:
            raise DOSError(TOO_MANY_OPEN_FILES)
        try:
            return self._new_handle(open(host, 'w+b'))
        except PermissionError:
            raise DOSError(ACCESS_DENIED)

    def open(self, path, access):
        host = self.resolve(path)
        if os.path.isdir(host):
            raise DOSError(ACCESS_DENIED)
        mode = {0: 'rb', 1: 'r+b', 2: 'r+b'}.get(access & 7)
        if mode is None:
            raise DOSError(INVALID_FUNCTION)
        try:
            file = open(host, mode)
        except PermissionError:
            raise DOSError(ACCESS_DENIED)
        file.dos_access = access & 7
        return self._new_handle(file)

    def file(self, handle):
        file = self.handles.get(handle)
        if file is None:
            raise DOSError(INVALID_HANDLE)
        return file

    def close(self, handle):
        if handle in STANDARD_HANDLES:
            return
        self.file(handle).close()
        del self.handles[handle]

    def read(self, handle, count):
        file = self.file(handle)
        if getattr(file, 'dos_access', 2) == 1:
            raise DOSError(ACCESS_DENIED)
        return file.read(count)

    def write(self, handle, data):
        file = self.file(handle)
        if getattr(file, 'dos_access', 2) == 0:
            raise DOSError(ACCESS_DENIED)
        if not data:  # DOS: writing 0 bytes truncates at the current position
            file.truncate()
            return 0
        return file.write(data)

    def seek(self, handle, origin, offset):
        if origin not in (0, 1, 2):
            raise DOSError(INVALID_FUNCTION)
        file = self.file(handle)
        if origin != 0 and offset & 0x80000000:
            offset -= 1 << 32  # relative moves are signed
        position = file.seek(offset, origin)
        return position & 0xFFFFFFFF

    def delete(self, path):
        host = self.resolve(path)
        if os.path.isdir(host):
            raise DOSError(ACCESS_DENIED)
        try:
            os.remove(host)
        except PermissionError:
            raise DOSError(ACCESS_DENIED)

    def rename(self, old, new):
        source = self.resolve(old)
        target = self.resolve(new, must_exist=False)
        if os.path.exists(target):
            raise DOSError(ACCESS_DENIED)
        try:
            os.rename(source, target)
        except OSError:
            raise DOSError(ACCESS_DENIED)

    def attributes(self, path):
        host = self.resolve(path)
        if os.path.isdir(host):
            return DIRECTORY
        return ARCHIVE | (0 if os.access(host, os.W_OK) else READ_ONLY)

    def find(self, pattern, attributes):
        """All matches for a DOS wildcard pattern: list of (name, attributes, size, mtime)."""
        parts = self._components(pattern, wildcards=True)
        if not parts:
            raise DOSError(PATH_NOT_FOUND)
        directory = self._host(parts[:-1])
        if not os.path.isdir(directory):
            raise DOSError(PATH_NOT_FOUND)
        wildcard = parts[-1].upper()
        if '.' not in wildcard:
            wildcard += '.'  # DOS: 'NAME' matches 'NAME' with an empty extension
        matches = []
        for entry in sorted(os.listdir(directory)):
            name = dos_name(entry)
            if name is None:
                continue
            host = os.path.join(directory, entry)
            is_directory = os.path.isdir(host)
            if is_directory and not attributes & DIRECTORY:
                continue
            candidate = name if '.' in name else name + '.'
            if not _dos_match(candidate, wildcard):
                continue
            stat = os.stat(host)
            attribute = DIRECTORY if is_directory else ARCHIVE
            matches.append((name, attribute, 0 if is_directory else stat.st_size, stat.st_mtime))
        return matches


def _dos_match(name, pattern):
    """Match 'NAME.EXT' against a DOS pattern where * and ? stop at the dot."""
    name_base, _, name_ext = name.partition('.')
    pattern_base, _, pattern_ext = pattern.partition('.')
    return _match_part(name_base, pattern_base, 8) and _match_part(name_ext, pattern_ext, 3)


def _match_part(name, pattern, width):
    if '*' in pattern:
        pattern = pattern[:pattern.index('*')].ljust(width, '?')
    return fnmatch.fnmatchcase(name.ljust(width), pattern.ljust(width))
