# 8086 Simulator

A desktop IDE for writing, assembling and single-stepping Intel 8086 programs.
Programs are assembled with Turbo Assembler (TASM/TLINK) running inside DOSBox,
then loaded into a Python emulation of the 8086 where you can step through them
and inspect registers and memory.

## Features

- **CPU core** (`Microprocessor/`): the complete 8086 instruction set, including
  segment-override/`REP`/`LOCK` prefixes, interrupts and the single-step trap.
  It is checked against hardware-captured test vectors (see [Testing](#testing)).
- **Program loading**: DOS `.EXE` (MZ, with relocations and a PSP) and `.COM` files.
- **DOS services**: `INT 20h` and the common `INT 21h` functions: character/string
  output (02h, 09h), keyboard input (01h, 06h, 07h, 08h, 0Ah), get/set interrupt
  vector (25h, 35h), date/time (2Ah, 2Ch), version (30h) and exit (00h, 4Ch).
  Program output appears in the Log dock; input is asked for in a dialog.
- **Debugger UI**: Step (F10), Run (F5), Pause (Shift+F5), editable register
  table, four memory views (one per segment register) with the next instruction
  highlighted, and the current `CS:IP` in the status bar.

## Requirements

- Python 3.8+
- PyQt5: `pip install -r requirements.txt`
- [DOSBox](https://www.dosbox.com/) on `PATH` (or set the `DOSBOX` environment
  variable to its executable). Only needed for building.
- Turbo Assembler binaries. `TASM.EXE` and `TLINK.EXE` are **not** included in
  this repository; copy them into `external/tasm/BIN/`.

## Usage

```sh
python main.py
```

1. Open or write an `.asm` file (see `examples/hello.asm`). DOS tools need file
   names of at most 8 characters without spaces.
2. **Build** (F7) runs TASM and TLINK; their output appears in the Log dock.
3. **Debug** (F8) loads the resulting `.EXE`. Step with F10 or run with F5;
   the log shows when the program exits and with which code.

The emulator can also be used without the UI:

```python
from Microprocessor import Microprocessor
from Microprocessor.dos import DOS

cpu = Microprocessor()
DOS(cpu, output=print)
cpu.load_program('HELLO.EXE')
cpu.run()
print('exit code', cpu.exit_code)
```

## Testing

```sh
python -m unittest discover tests     # unit, loader, assembler and UI tests
tests/fetch_sst.sh                    # download the SingleStepTests 8088 suite once (~800 MB)
python tests/sst_runner.py            # 300 vectors per documented opcode
python tests/sst_runner.py --all-statuses --limit 0   # every vector, incl. undocumented opcodes
```

[SingleStepTests/8088](https://github.com/SingleStepTests/8088) contains test
vectors captured from a real 8088, which has the same instruction set as the
8086. The emulator passes all 3,064,000 of them. Flags the ISA leaves undefined
are not compared; that includes the undefined bits of the FLAGS word a
divide-error trap pushes, whose values come from the CPU's internal microcode loop.

The UI tests need PyQt5 and run offscreen (`QT_QPA_PLATFORM=offscreen`).

## Project layout

| Path | Contents |
|---|---|
| `main.py` | Application entry point |
| `Microprocessor/__init__.py` | 8086 CPU core |
| `Microprocessor/program.py` | `.EXE`/`.COM` loader |
| `Microprocessor/dos.py` | DOS `INT 20h`/`21h` services |
| `Assembler/__init__.py` | TASM/TLINK in DOSBox |
| `UI/` | PyQt5 main window |
| `tests/` | Unit tests and the SingleStepTests runner |
| `external/tasm/` | Turbo Assembler support files and DOSBox config |

## Limitations

- No disassembly view or breakpoints yet; follow execution in the memory views.
- No BIOS services (`INT 10h`, `INT 16h`, ...), no video memory display, and
  only the DOS functions listed above. Unsupported calls stop the program with
  a message in the log.
- No 8087 coprocessor: `ESC` instructions decode their operand and do nothing.
- Timing (clock cycles, prefetch queue) is not emulated.
