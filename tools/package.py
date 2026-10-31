#!/usr/bin/env python3
"""Build the portable app for the current platform with PyInstaller.

    python tools/package.py [--version VERSION] [--test]

Needs PyInstaller and PyQt5 (pip install pyinstaller -r requirements.txt) and
JWasm in external/jwasm/ (tools/build_jwasm.sh), which is bundled with the app.
TASM is not bundled. Writes dist/8086-Simulator-VERSION-PLATFORM.zip (.tar.gz on
Linux). --test unpacks the archive into a temporary folder and runs the app's
self-test from there (main.py --self-test).
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = '8086-Simulator'
BUILD = os.path.join(ROOT, 'build', 'pyinstaller')
DIST = os.path.join(ROOT, 'dist')


def platform_tag():
    machine = platform.machine().lower()
    machine = {'amd64': 'x64', 'x86_64': 'x64', 'arm64': 'arm64', 'aarch64': 'arm64'}.get(machine, machine)
    system = {'win32': 'windows', 'darwin': 'macos'}.get(sys.platform, 'linux')
    return f'{system}-{machine}'


def jwasm_binary():
    for name in ('jwasm.exe', 'jwasm'):
        path = os.path.join(ROOT, 'external', 'jwasm', name)
        if os.path.exists(path):
            return path
    sys.exit('JWasm is missing from external/jwasm/; run tools/build_jwasm.sh first')


def run_pyinstaller(version):
    sep = os.pathsep
    jwasm_dir = os.path.join(ROOT, 'external', 'jwasm')
    arguments = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
        '--name', NAME,
        '--workpath', BUILD, '--specpath', BUILD, '--distpath', os.path.join(BUILD, 'dist'),
        '--add-data', f'{os.path.join(ROOT, "examples")}{sep}examples',
        '--add-binary', f'{jwasm_binary()}{sep}external/jwasm',
        '--osx-bundle-identifier', 'io.github.gagandheep.simulator8086',
    ]
    for extra in ('License.html', 'README.md'):
        if os.path.exists(os.path.join(jwasm_dir, extra)):
            arguments += ['--add-data', f'{os.path.join(jwasm_dir, extra)}{sep}external/jwasm']
    arguments.append(os.path.join(ROOT, 'main.py'))
    subprocess.run(arguments, check=True, cwd=ROOT)
    if sys.platform == 'darwin':
        return os.path.join(BUILD, 'dist', NAME + '.app')
    return os.path.join(BUILD, 'dist', NAME)


def stage(app, version):
    """Lay out the folder users unpack: the app, the examples and a README."""
    folder = os.path.join(BUILD, 'stage', NAME)
    shutil.rmtree(os.path.dirname(folder), ignore_errors=True)
    os.makedirs(folder)
    if sys.platform == 'darwin':
        # keep the symlinks inside the .app bundle
        subprocess.run(['ditto', app, os.path.join(folder, os.path.basename(app))], check=True)
    else:
        shutil.copytree(app, folder, dirs_exist_ok=True, symlinks=True)
    shutil.copytree(os.path.join(ROOT, 'examples'), os.path.join(folder, 'examples'))
    with open(os.path.join(ROOT, 'tools', 'PORTABLE-README.txt'), encoding='utf-8') as f:
        readme = f.read().replace('{version}', version)
    with open(os.path.join(folder, 'README.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(readme)
    return folder


def archive(folder, version):
    os.makedirs(DIST, exist_ok=True)
    base = os.path.join(DIST, f'{NAME}-{version}-{platform_tag()}')
    parent = os.path.dirname(folder)
    if sys.platform.startswith('linux'):
        path = base + '.tar.gz'
        with tarfile.open(path, 'w:gz') as tar:
            tar.add(folder, arcname=NAME)
    elif sys.platform == 'darwin':
        path = base + '.zip'
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', folder, path], check=True)
    else:
        path = base + '.zip'
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for directory, _, files in os.walk(folder):
                for name in files:
                    full = os.path.join(directory, name)
                    zf.write(full, os.path.relpath(full, parent))
    return path


def extract(path, destination):
    if path.endswith('.tar.gz'):
        with tarfile.open(path) as tar:
            if hasattr(tarfile, 'data_filter'):
                tar.extractall(destination, filter='tar')
            else:
                tar.extractall(destination)
    elif sys.platform == 'darwin':
        subprocess.run(['ditto', '-x', '-k', path, destination], check=True)
    else:
        with zipfile.ZipFile(path) as zf:
            zf.extractall(destination)


def executable(folder):
    if sys.platform == 'darwin':
        return os.path.join(folder, NAME + '.app', 'Contents', 'MacOS', NAME)
    if sys.platform == 'win32':
        return os.path.join(folder, NAME + '.exe')
    return os.path.join(folder, NAME)


def self_test(path, wrapper=()):
    """Unpack the archive somewhere new and run the bundled app's self-test, with no
    JWasm on PATH so that only the bundled one can be used."""
    with tempfile.TemporaryDirectory() as directory:
        extract(path, directory)
        program = executable(os.path.join(directory, NAME))
        report = os.path.join(directory, 'self-test.txt')
        env = {k: v for k, v in os.environ.items() if k not in ('JWASM', 'ASSEMBLER', 'TASM_DIR', 'PYTHONPATH')}
        env['PATH'] = os.pathsep.join(p for p in env.get('PATH', '').split(os.pathsep)
                                      if not any(os.path.exists(os.path.join(p, n))
                                                 for n in ('jwasm', 'jwasm.exe', 'uasm')))
        result = subprocess.run([*wrapper, program, '--self-test', report], env=env, cwd=directory,
                                timeout=300, check=False)
        text = open(report, encoding='utf-8').read() if os.path.exists(report) else '(no report written)\n'
        print(text, end='')
        if result.returncode != 0 or 'self-test passed' not in text:
            sys.exit(f'self-test of {os.path.basename(path)} failed (exit code {result.returncode})')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', default='dev')
    parser.add_argument('--test', action='store_true', help='run the self-test from the unpacked archive')
    parser.add_argument('--test-only', metavar='ARCHIVE', help='only run the self-test of an existing archive')
    parser.add_argument('--wrapper', default='', help='command to run the test under, e.g. "xvfb-run -a"')
    options = parser.parse_args()
    wrapper = options.wrapper.split()
    if options.test_only:
        self_test(options.test_only, wrapper)
        return
    app = run_pyinstaller(options.version)
    path = archive(stage(app, options.version), options.version)
    print(f'packaged {path}')
    if options.test:
        self_test(path, wrapper)


if __name__ == '__main__':
    main()
