8086 Simulator {version} (portable)
===================================

A desktop IDE for writing, assembling and debugging Intel 8086 programs.
Nothing needs to be installed: unpack this folder anywhere and start the app.

  Windows   8086-Simulator.exe
  Linux     ./8086-Simulator
  macOS     8086-Simulator.app

The JWasm assembler is included, so Build (F7) and Debug (F8) work right away.
Try examples/hello.asm first, then examples/sum.asm with the debugger.

The user guide is in the docs folder: docs/USER_GUIDE.pdf (or USER_GUIDE.md).
It walks through a first program and explains the editor, the debugger,
watchpoints, the emulated PC and its DOS/BIOS services, and troubleshooting.

First start on macOS: the app is not signed by Apple, so macOS blocks it.
Right-click (or Control-click) 8086-Simulator.app, choose Open, then Open
again. If macOS says the app is damaged, run this once in Terminal from this
folder:  xattr -dr com.apple.quarantine 8086-Simulator.app

First start on Windows: SmartScreen may warn about an unknown publisher;
choose "More info", then "Run anyway".

Linux: needs a desktop with X11 or XWayland and glibc 2.35 or newer
(Ubuntu 22.04, Debian 12, Fedora 36 or later).

Turbo Assembler (optional)
--------------------------
TASM is not included. To build with TASM and TLINK instead of JWasm, install
DOSBox (on PATH, or set the DOSBOX environment variable), and copy a Turbo
Assembler folder whose BIN folder holds TASM.EXE and TLINK.EXE into a folder
named "tasm" next to the app (or set TASM_DIR to it). Then pick TASM in the
Assembler menu.

Source code, documentation and issues:
https://github.com/Gagandheep/8086-Simulator

JWasm is distributed under the Sybase Open Watcom Public License; its license
is included in the app bundle (external/jwasm/License.html).
