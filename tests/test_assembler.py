"""The TASM/DOSBox wrapper, exercised with a fake dosbox executable."""
import os
import stat
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Assembler  # noqa: E402

TASM_OK = """\
Turbo Assembler  Version 4.1  Copyright (c) 1988, 1996 Borland International

Assembling file:   x:\\hello.asm
Error messages:    None
Warning messages:  None
Passes:            1

Turbo Link  Version 7.1.30.1. Copyright (c) 1987, 1996 Borland International
"""
TASM_ERROR = TASM_OK.replace('Error messages:    None', '**Error** x:\\hello.asm(5) Undefined symbol: FOO\n'
                             'Error messages:    1')
TLINK_ERROR = TASM_OK + 'Error: Undefined symbol MAIN in module HELLO.ASM\n'

FAKE_DOSBOX = textwrap.dedent('''\
    #!{python}
    # Stands in for DOSBox: writes the build log into the mounted y: drive and
    # creates the .EXE in the mounted x: drive, like TASM/TLINK would.
    import os, re, sys
    args = sys.argv[1:]
    commands = [args[i + 1] for i, a in enumerate(args) if a == '-c']
    drives = dict(re.match(r'mount (\\w) "(.*)"', c).groups() for c in commands if c.startswith('mount'))
    log = open(os.path.join(drives['y'], 'BUILD.LOG'), 'w')
    for c in commands:
        if c.startswith('tlink'):
            exe = re.search(r'x:\\\\(\\w+)\\.exe', c).group(1)
            open(os.path.join(drives['x'], exe.upper() + '.EXE'), 'wb').write(b'MZ')
    log.write(os.environ['FAKE_LOG'])
''')


class AssemblerTests(unittest.TestCase):

    def setUp(self):
        self.workdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.workdir.cleanup)
        root = self.workdir.name
        # fake toolchain directory with TASM/TLINK present
        bin_dir = os.path.join(root, 'tasm', 'BIN')
        os.makedirs(bin_dir)
        for tool in ('TASM.EXE', 'TLINK.EXE'):
            open(os.path.join(bin_dir, tool), 'wb').close()
        self.patch('TASM_DIR', os.path.join(root, 'tasm'))
        self.patch('TASM_BIN', bin_dir)
        self.patch('DOSBOX_CONFIG', os.path.join(bin_dir, 'dosbox.conf'))
        dosbox = os.path.join(root, 'fake-dosbox')
        with open(dosbox, 'w') as f:
            f.write(FAKE_DOSBOX.format(python=sys.executable))
        os.chmod(dosbox, os.stat(dosbox).st_mode | stat.S_IEXEC)
        self.env('DOSBOX', dosbox)
        self.source = os.path.join(root, 'src dir', 'hello.asm')
        os.makedirs(os.path.dirname(self.source))
        open(self.source, 'w').close()

    def patch(self, name, value):
        old = getattr(Assembler, name)
        setattr(Assembler, name, value)
        self.addCleanup(setattr, Assembler, name, old)

    def env(self, name, value):
        old = os.environ.get(name)
        os.environ[name] = value
        self.addCleanup(lambda: os.environ.pop(name) if old is None else os.environ.__setitem__(name, old))

    def test_successful_build_returns_exe(self):
        self.env('FAKE_LOG', TASM_OK)
        success, log, exe = Assembler.tasm_tlink(self.source)
        self.assertTrue(success)
        self.assertIn('Error messages:    None', log)
        self.assertEqual(os.path.basename(exe), 'HELLO.EXE')  # found case-insensitively

    def test_assembler_error(self):
        self.env('FAKE_LOG', TASM_ERROR)
        success, log, exe = Assembler.tasm_tlink(self.source)
        self.assertFalse(success)
        self.assertIsNone(exe)

    def test_linker_error(self):
        self.env('FAKE_LOG', TLINK_ERROR)
        self.assertFalse(Assembler.tasm_tlink(self.source)[0])

    def test_missing_tasm_is_reported(self):
        os.remove(os.path.join(Assembler.TASM_BIN, 'TASM.EXE'))
        with self.assertRaisesRegex(Assembler.AssemblerError, 'TASM.EXE'):
            Assembler.tasm_tlink(self.source)

    def test_names_must_fit_dos(self):
        for name in ('toolongname.asm', 'has space.asm'):
            with self.assertRaises(Assembler.AssemblerError):
                Assembler.split_source(os.path.join(self.workdir.name, name))
        with self.assertRaises(Assembler.AssemblerError):
            Assembler.split_source('notes.txt')
        # 'data.asm' used to become 'dat' via rstrip('.asm')
        self.assertEqual(Assembler.split_source('/tmp/data.asm'), ('/tmp', 'data'))


    def test_choose_assembler(self):
        self.env('FAKE_LOG', TASM_OK)
        self.assertEqual(Assembler.choose_assembler('tasm'), 'tasm')
        self.env('JWASM', os.path.join(self.workdir.name, 'no-such-jwasm'))
        self.assertEqual(Assembler.choose_assembler('auto'), 'tasm')  # TASM preferred when installed
        os.remove(os.path.join(Assembler.TASM_BIN, 'TLINK.EXE'))
        self.assertEqual(Assembler.choose_assembler('auto'), 'jwasm')  # JWASM is set (exists or not)
        with self.assertRaises(Assembler.AssemblerError):
            Assembler.choose_assembler('masm')

    def test_jwasm_backend_with_fake_executable(self):
        fake = os.path.join(self.workdir.name, 'fake-jwasm')
        with open(fake, 'w') as f:
            f.write(FAKE_JWASM.format(python=sys.executable))
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        self.env('JWASM', fake)
        self.env('FAKE_FAIL', '')
        success, log, exe, used = Assembler.build(self.source, 'jwasm')
        self.assertEqual((success, used, os.path.basename(exe)), (True, 'jwasm', 'hello.EXE'))
        self.assertIn('-mz', log)
        self.env('FAKE_FAIL', '1')
        success, log, exe, _ = Assembler.build(self.source, 'jwasm')
        self.assertEqual((success, exe), (False, None))
        self.assertIn('Error A2209', log)


FAKE_JWASM = textwrap.dedent('''\
    #!{python}
    # Stands in for JWasm: echoes its arguments, writes -Fo/-Fl files unless FAKE_FAIL is set.
    import os, sys
    args = sys.argv[1:]
    print(' '.join(args))
    if os.environ.get('FAKE_FAIL'):
        print(args[-1] + '(4) : Error A2209: Syntax error: badop')
        sys.exit(1)
    for a in args:
        if a.startswith(('-Fo=', '-Fl=')):
            open(a[4:], 'wb').write(b'MZ')
''')


if __name__ == '__main__':
    unittest.main()
