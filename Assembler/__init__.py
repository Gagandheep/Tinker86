"""Build .asm files into DOS executables with one of two assemblers.

- TASM: Turbo Assembler and Turbo Link running in DOSBox. TASM.EXE and
  TLINK.EXE are not distributed with this repository; copy them into
  external/tasm/BIN (or set TASM_DIR to a Turbo Assembler directory). DOSBox
  must be on PATH (or named by the DOSBOX environment variable).
- JWasm: the open source MASM-compatible assembler, run natively. It writes
  the .EXE itself (no linker, no DOSBox) and accepts the MASM-style syntax
  most TASM programs use (not TASM's Ideal mode). Install it on PATH, set
  JWASM to its executable, or run tools/build_jwasm.sh.

build() picks TASM when it is fully installed, else JWasm (or the one named
by the ASSEMBLER environment variable).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_directory():
    """The folder the portable (PyInstaller) build was unpacked to, or None from source."""
    if not getattr(sys, 'frozen', False):
        return None
    directory = os.path.dirname(os.path.abspath(sys.executable))
    if directory.endswith(os.path.join('.app', 'Contents', 'MacOS')):  # macOS app bundle
        directory = os.path.dirname(os.path.dirname(os.path.dirname(directory)))
    return directory


# TASM_DIR may point at another Turbo Assembler installation (it needs BIN\TASM.EXE).
# The portable build does not include TASM; it looks for it in a "tasm" folder next to the app.
TASM_DIR = os.environ.get('TASM_DIR') or (os.path.join(app_directory(), 'tasm') if app_directory()
                                          else os.path.join(ROOT, 'external', 'tasm'))
TASM_BIN = os.path.join(TASM_DIR, 'BIN')
DOSBOX_CONFIG = os.path.join(TASM_BIN, 'dosbox.conf')
LOG_NAME = 'BUILD.LOG'
JWASM_LOCAL = os.path.join(ROOT, 'external', 'jwasm', 'jwasm')  # where tools/build_jwasm.sh puts it
ASSEMBLERS = ('tasm', 'jwasm')
DOS_NAME = re.compile(r'^[A-Za-z0-9_\-$~!#%&@^]{1,8}$')


class AssemblerError(Exception):
    """The toolchain is missing or the source file cannot be built."""


def dosbox_executable():
    configured = os.environ.get('DOSBOX')
    if configured:
        return configured
    for name in ('dosbox', 'DOSBox', 'dosbox-x'):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == 'darwin':
        app = '/Applications/dosbox.app/Contents/MacOS/DOSBox'
        if os.path.exists(app):
            return app
    raise AssemblerError('DOSBox was not found; install it or set the DOSBOX environment variable')


def find_file(directory, name):
    """Case-insensitive lookup (DOS tools write upper-case names)."""
    try:
        entries = os.listdir(directory)
    except FileNotFoundError:
        return None
    for entry in entries:
        if entry.lower() == name.lower():
            return os.path.join(directory, entry)
    return None


def check_toolchain():
    missing = [tool for tool in ('TASM.EXE', 'TLINK.EXE') if find_file(TASM_BIN, tool) is None]
    if missing:
        raise AssemblerError(f'{" and ".join(missing)} not found in {TASM_BIN}; '
                             'copy the Turbo Assembler binaries there')


def split_source(path):
    """Return (directory, DOS base name) for a source file, validating 8.3 naming."""
    directory, filename = os.path.split(os.path.abspath(path))
    base, extension = os.path.splitext(filename)
    if extension.lower() != '.asm':
        raise AssemblerError(f'{filename}: expected a .asm file')
    if not DOS_NAME.match(base):
        raise AssemblerError(f'{filename}: DOS tools need a name of at most 8 letters, digits or _ '
                             '(no spaces) before .asm')
    return directory, base


def dosbox_command(setup, captured, log_directory):
    """Build the DOSBox command line: run setup, then captured commands with output logged."""
    arguments = [dosbox_executable(), '-c', f'mount y "{log_directory}"']
    for command in setup:
        arguments += ['-c', command]
    for command in captured:
        arguments += ['-c', f'{command} >> y:\\{LOG_NAME}']
    arguments += ['-c', 'exit']
    if os.path.exists(DOSBOX_CONFIG):
        arguments += ['-conf', DOSBOX_CONFIG]
    if sys.platform == 'win32':
        arguments.append('-noconsole')
    return arguments


def run_in_dosbox(setup, captured, headless=True):
    """Run DOS commands in DOSBox and return everything the captured ones printed."""
    with tempfile.TemporaryDirectory() as log_directory:
        env = dict(os.environ)
        if headless:
            env['SDL_VIDEODRIVER'] = 'dummy'
            env['SDL_AUDIODRIVER'] = 'dummy'
        subprocess.run(dosbox_command(setup, captured, log_directory), env=env, cwd=log_directory,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        log_path = os.path.join(log_directory, LOG_NAME)
        if not os.path.exists(log_path):
            raise AssemblerError('DOSBox produced no output; check that it runs on this system')
        with open(log_path, encoding='latin-1') as f:
            return f.read().replace('\r\n', '\n')


def build_succeeded(log):
    """TASM reports 'Error messages: None'; TLINK prints 'Error:'/'Fatal:' lines on failure."""
    tasm_errors = re.findall(r'Error messages:\s*(.*)', log)
    if not tasm_errors or any(e.strip() != 'None' for e in tasm_errors):
        return False
    return re.search(r'^\s*(Fatal|Error):', log, re.MULTILINE) is None


def tasm_tlink(source):
    """Assemble and link source. Returns (success, log, path of the .EXE or None)."""
    check_toolchain()
    directory, base = split_source(source)
    stale = find_file(directory, base + '.exe')
    if stale:
        os.remove(stale)
    setup = [f'mount c "{TASM_DIR}"', f'mount x "{directory}"', 'c:', 'cd BIN']
    # the listing and map let the debugger show source lines and symbols (Assembler.listing)
    captured = [f'tasm /l x:\\{base}.asm, x:\\{base}.obj, x:\\{base}.lst',
                f'tlink x:\\{base}.obj, x:\\{base}.exe, x:\\{base}.map']
    log = run_in_dosbox(setup, captured)
    exe = find_file(directory, base + '.exe')
    success = build_succeeded(log) and exe is not None
    return success, log, exe if success else None


def jwasm_executable():
    configured = os.environ.get('JWASM')
    if configured:
        return configured
    for name in ('jwasm', 'uasm', 'JWasm'):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (JWASM_LOCAL, JWASM_LOCAL + '.exe'):
        if os.path.exists(candidate):
            return candidate
    raise AssemblerError('JWasm was not found; install it on PATH, set the JWASM environment variable, '
                         'or run tools/build_jwasm.sh')


def jwasm(source):
    """Assemble source with JWasm into NAME.EXE with listing NAME.LST.
    Returns (success, log, path of the .EXE or None)."""
    executable = jwasm_executable()
    directory, filename = os.path.split(os.path.abspath(source))
    base = os.path.splitext(filename)[0]
    exe = os.path.join(directory, base + '.EXE')
    for stale in (exe, find_file(directory, base + '.exe')):
        if stale and os.path.exists(stale):
            os.remove(stale)
    # -mz: DOS executable without a linker; -Sx: list false conditional blocks so the
    # listing keeps one line per source line (Assembler.listing.parse_jwasm_listing)
    command = [executable, '-mz', '-Sx', f'-Fl={base}.LST', f'-Fo={base}.EXE', filename]
    try:
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, errors='replace',
                                check=False)
    except OSError as e:
        raise AssemblerError(f'cannot run JWasm ({executable}): {e}')
    log = (result.stdout + result.stderr).replace('\r\n', '\n')
    success = result.returncode == 0 and os.path.exists(exe)
    return success, log, exe if success else None


def tasm_available():
    try:
        check_toolchain()
        dosbox_executable()
    except AssemblerError:
        return False
    return True


def jwasm_available():
    try:
        jwasm_executable()
    except AssemblerError:
        return False
    return True


def available_assemblers():
    """The assemblers that can be used right now, preferred first."""
    return [name for name, available in (('tasm', tasm_available), ('jwasm', jwasm_available)) if available()]


def choose_assembler(assembler=None):
    """Resolve 'auto'/None (or $ASSEMBLER) to an installed assembler; raises AssemblerError."""
    assembler = (assembler or os.environ.get('ASSEMBLER') or 'auto').lower()
    if assembler == 'auto':
        available = available_assemblers()
        if not available:
            raise AssemblerError('no assembler found: install JWasm (tools/build_jwasm.sh) or DOSBox with '
                                 f'TASM.EXE and TLINK.EXE in {TASM_BIN}')
        return available[0]
    if assembler not in ASSEMBLERS:
        raise AssemblerError(f'unknown assembler {assembler!r} (use tasm, jwasm or auto)')
    return assembler


def build(source, assembler=None):
    """Build source into a DOS executable. assembler: 'tasm', 'jwasm' or 'auto' (default).
    Returns (success, log, path of the .EXE or None, assembler used)."""
    assembler = choose_assembler(assembler)
    if assembler == 'tasm':
        success, log, exe = tasm_tlink(source)
    else:
        success, log, exe = jwasm(source)
    return success, log, exe, assembler
