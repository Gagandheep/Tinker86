import os
import subprocess
import time
from sys import platform

# tasm_dir = r'C:\Users\Gagandheep\Documents\tasm'
tasm_dir = os.path.join(os.getcwd(), 'external', 'tasm')
log_file = 'DOSBOX.LOG'
log_dir = os.getcwd()

if platform == 'win32':
    executable = '.run.bat'
    command = 'DOSBox'
elif platform == 'linux':
    executable = '.run.sh'
    command = 'dosbox'


def dosbox_run(*commands, config=None, stdout_file=None, noconsole=True, headless=True, append=False):
    global command
    log_dir, log_file = '', ''
    if stdout_file is not None:
        if not append and os.path.exists(stdout_file):
            os.remove(stdout_file)
        s = '\\' if platform == "win32" else '/'
        log_dir, log_file = stdout_file.rsplit(s, 1)
        command += f' -c "mount y {log_dir}"'
    for comm in commands:
        if stdout_file is None:
            command += f' -c "{comm}"'
        else:
            command += f' -c "{comm} >> y:{log_file}"'
    if noconsole:
        command += ' -noconsole'
    if config is not None:
        command += f' -conf "{config}"'
    with open(executable, 'w') as f:
        f.writelines((['#! /bin/bash\n'] if platform == 'linux' else []) + (
            [f'{"set" if platform == "win32" else "export"} SDL_VIDEODRIVER=dummy\n']
            if headless else []) + [command])
    cmd = os.path.join(os.getcwd(), executable)
    subprocess.call([rf'{cmd}'], stdout=subprocess.DEVNULL)
    if os.path.exists('stderr.txt'):
        os.remove('stderr.txt')
    if os.path.exists('stdout.txt'):
        os.remove('stdout.txt')
    if os.path.exists(executable):
        os.remove(executable)


def tasm(target):
    s = '\\' if platform == "win32" else '/'
    target_dir, target_file = target.rstrip('.asm').rsplit(s, 1)
    commands = [f"mount c {tasm_dir}",
                f"mount x {target_dir}",
                "c:",
                "cd BIN",
                f"tasm x:\\{target_file} x:\\{target_file}",
                "exit"]
    dosbox_run(*commands, config=os.path.join(tasm_dir, 'BIN', 'dosbox.conf'),
               stdout_file=os.path.join(log_dir, log_file))


def tlink(target):
    s = '\\' if platform == "win32" else '/'
    target_dir, target_file = target.rstrip('.asm').rsplit(s, 1)
    commands = [f"mount c {tasm_dir}",
                f"mount x {target_dir}",
                "c:",
                "cd BIN",
                f"tlink x:\\{target_file} x:\\{target_file} /x",
                "exit"]
    dosbox_run(*commands, config=os.path.join(tasm_dir, 'BIN', 'dosbox.conf'),
               stdout_file=os.path.join(log_dir, log_file))


def tasm_tlink(target):
    s = '\\' if platform == "win32" else '/'
    target_dir, target_file = target.rstrip('.asm').rsplit(s, 1)
    commands = [f"mount c {tasm_dir}",
                f"mount x {target_dir}",
                "c:",
                "cd BIN",
                f"tasm x:\\{target_file} x:\\{target_file} /x",
                f"tlink x:\\{target_file} x:\\{target_file} /x",
                # f"td x:\\{target_file}",
                "exit"
                ]
    # ]
    dosbox_run(*commands, config=os.path.join(tasm_dir, 'BIN', 'dosbox.conf'),
               stdout_file=os.path.join(log_dir, log_file), headless=True)


if __name__ == '__main__':
    a = os.path.abspath('T1')
    t = time.time()
    tasm_tlink(a)
    print(time.time() - t)
