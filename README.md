# 8086 Simulator

A desktop IDE for writing, assembling and debugging Intel 8086 programs.
Programs are assembled with Turbo Assembler (TASM/TLINK) running inside DOSBox,
then loaded into a Python emulation of an 8086 PC where you can run or step
through them, follow execution in the source, set breakpoints and data
watchpoints, and inspect the disassembly, registers, memory and text screen.

## Features

- **CPU core** (`Microprocessor/`): the complete 8086 instruction set, including
  segment-override/`REP`/`LOCK` prefixes, interrupts and the single-step trap.
  It is checked against hardware-captured test vectors (see [Testing](#testing)).
- **Disassembler** (`Microprocessor/disassembler.py`): TASM-style output for the
  debugger; a second output style reproduces the test suite's instruction names
  for all 3,064,000 vectors.
- **Program loading**: DOS `.EXE` (MZ, with relocations and a PSP) and `.COM` files.
- **BIOS** (`Microprocessor/bios.py`): an 80×25 colour text screen at
  `B800:0000` (programs may also write video memory directly), `INT 10h` video
  (00h–03h, 05h–0Ah, 0Eh, 0Fh, 13h), `INT 16h` keyboard (00h–02h, 10h–12h),
  `INT 11h`, `INT 12h` and `INT 1Ah` (ticks, RTC time and date).
- **DOS** (`Microprocessor/dos.py`, `dosfs.py`): `INT 20h` and `INT 21h`
  - console: 01h, 02h, 06h–0Ch
  - files: create/open/close/read/write/seek (3Ch–42h), delete (41h), rename
    (56h), attributes (43h), IOCTL device info (44h/00h)
  - directories: 39h–3Bh, 47h; find first/next (4Eh, 4Fh) with DTA (1Ah, 2Fh)
  - drives (0Eh, 19h), memory (48h–4Ah), vectors (25h, 35h), date/time (2Ah,
    2Ch), version (30h), exit (00h, 4Ch)

  Drive C: is the program's folder; paths cannot leave it. Handles 0–2 are the
  console.
- **Debugger UI**:
  - Step (F10), Run (F5), Pause (Shift+F5), Run to Cursor (F4)
  - source view: the line being executed is highlighted in the editor, using
    the TASM listing and TLINK map the build writes next to the `.EXE`
  - breakpoints: click the editor gutter, F9, or double-click a row in the
    Disassembly view (source and disassembly share them)
  - data watchpoints: stop when an instruction writes, reads or accesses a
    variable (Watch dock, Ctrl+Shift+W, or right-click a memory cell). The log
    shows the instruction and the old → new value. Addresses can be listing
    symbols (`msg`, `count+2`), `DS:0010`, `ES:DI+2` or physical addresses.
  - editable register table, four memory views (one per segment register)
  - Screen view: program output appears there, and keys typed into it go to
    the program. When a program waits for a key, Run pauses and resumes as
    soon as you type.

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
3. **Debug** (F8) loads the resulting `.EXE`. The current line is highlighted
   in the source and in the Disassembly view. Set breakpoints by clicking the
   gutter (or F9), add watchpoints in the Watch dock, then Step (F10), Run (F5)
   or Run to Cursor (F4). Program output appears in the Screen view; click it
   and type when the program reads the keyboard. The Log shows breakpoint and
   watchpoint hits, and when the program exits and with which code.

The emulator can also be used without the UI:

```python
from Microprocessor import Microprocessor
from Microprocessor.dos import DOS

cpu = Microprocessor()
dos = DOS(cpu, output=print, root='.')   # drive C: is the current directory
dos.bios.keyboard.push_text('typed input\r')  # keys for programs that read the keyboard
cpu.load_program('HELLO.EXE')
cpu.run()          # returns early when the program waits for more keys
print('exit code', cpu.exit_code)
print(dos.bios.screen_text())
```

`cpu.run()` stops when the program halts or needs a key that was not typed
(`cpu.waiting_for_input`). Push more keys and call `run()` again to continue.

## Testing

```sh
python -m unittest discover tests     # unit, loader, assembler and UI tests
tests/fetch_sst.sh                    # download the SingleStepTests 8088 suite once (~800 MB)
python tests/sst_runner.py            # 300 vectors per documented opcode
python tests/sst_runner.py --all-statuses --limit 0   # every vector, incl. undocumented opcodes
SST_TESTS=1 python -m unittest tests.test_disassembler  # disassembler vs. the vector names
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
| `Microprocessor/disassembler.py` | Disassembler |
| `Microprocessor/program.py` | `.EXE`/`.COM` loader |
| `Microprocessor/bios.py` | BIOS services, text screen, keyboard queue |
| `Microprocessor/dos.py` | DOS `INT 20h`/`21h` services |
| `Microprocessor/dosfs.py` | Host directory as drive C: |
| `Microprocessor/watchpoints.py` | Address expressions and data watchpoints |
| `Assembler/__init__.py` | TASM/TLINK in DOSBox |
| `Assembler/listing.py` | TASM listing / TLINK map parsing for the source view |
| `UI/` | PyQt5 main window |
| `tests/` | Unit tests and the SingleStepTests runner |
| `external/tasm/` | Turbo Assembler support files and DOSBox config |

## Known limitations

**Emulated PC**
- Text mode only: no graphics modes (CGA/EGA/VGA) and only display page 0.
  Programs that switch to a graphics mode keep running but show nothing.
- Only the BIOS and DOS functions listed under Features. Unsupported calls stop
  the program with a message in the log. Not supported in particular:
  - DOS file handle duplication (45h/46h), file sharing and locking modes
  - FCB-based file functions (0Fh–28h)
  - running another program (4Bh) and terminate-and-stay-resident (31h)
  - DOS Ctrl+C / Ctrl+Break handling
- One drive (C:), mapped to the program's folder; long host file names are only
  reachable through their exact name, not as 8.3 aliases.
- No hardware interrupts: no timer tick (`INT 08h`), no keyboard IRQ (`INT 09h`),
  no PIC. `IN` from any port returns FFh and `OUT` is ignored (no PIT, speaker or
  serial ports).
- No 8087 coprocessor: `ESC` instructions decode their operand and do nothing.
- Timing (clock cycles, prefetch queue, wait states) is not emulated.

**Debugger**
- The source view needs the `.LST` listing and `.MAP` file from the build (F7
  writes both). It follows the main `.asm` file only: lines inside `INCLUDE`d
  files and macro expansions map to the nearest line of the main file. The parser
  follows TASM 4.1/TLINK 7.1 output; other assemblers and linkers are not supported.
- Breakpoints and watchpoints use physical addresses. A watchpoint's address is
  worked out when it is added (or when a program is loaded), so `ES:DI` watches
  the address `ES:DI` had then; it does not follow later register changes.
- Watchpoints report the instruction that accessed the memory, after it ran;
  memory written by the emulated DOS/BIOS services counts as written by the `INT`.
- No conditional breakpoints, hit counts or call stack view.
- The build has not been tested against a live TASM/DOSBox installation in this
  repository's test suite; it is exercised with a fake DOSBox and with listing and
  map files in TASM's format.
