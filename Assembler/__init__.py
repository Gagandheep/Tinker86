"""Assemble and link .asm files with Turbo Assembler (TASM/TLINK) running in DOSBox.

TASM.EXE and TLINK.EXE are not distributed with this repository; copy them
into external/tasm/BIN. DOSBox must be installed and on PATH (or named by
the DOSBOX environment variable).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASM_DIR = os.path.join(ROOT, 'external', 'tasm')
TASM_BIN = os.path.join(TASM_DIR, 'BIN')
DOSBOX_CONFIG = os.path.join(TASM_BIN, 'dosbox.conf')
LOG_NAME = 'BUILD.LOG'
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
