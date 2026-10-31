#!/usr/bin/env bash
# Download and build JWasm (https://github.com/Baron-von-Riedesel/JWasm) into
# external/jwasm/, where the simulator finds it. Needs git, make and a C compiler:
# gcc on Linux, clang (Xcode command line tools) on macOS, MinGW gcc on Windows
# (run from Git Bash). Windows users can also download jwasm.exe from the
# project's releases and put it on PATH (or set JWASM to its path).
set -euo pipefail
JWASM_COMMIT=7f6f32e78b79565d40bcce496756aadd1ff66900
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/external/jwasm"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
git clone --quiet https://github.com/Baron-von-Riedesel/JWasm "$work/src"
git -C "$work/src" checkout --quiet "$JWASM_COMMIT" 2>/dev/null || \
    echo "note: pinned commit not found, building the latest version"

case "$(uname -s)" in
    Darwin)               makefile=CLUnix.mak; output=build/CLUnixR/jwasm; name=jwasm; make=make ;;
    MINGW*|MSYS*|CYGWIN*) makefile=GccWin.mak; output=build/MinGWR/jwasm.exe; name=jwasm.exe
                          make=$(command -v mingw32-make || command -v make) ;;
    *)                    makefile=GccUnix.mak; output=build/GccUnixR/jwasm; name=jwasm; make=make ;;
esac
make_args=()
mkdir -p "$work/src/$(dirname "$output")"  # GccWin.mak's mkdir has no -p
case "$makefile" in
    GccWin.mak) make_args=("LINK=gcc.exe -static") ;;  # no MinGW runtime DLLs needed next to jwasm.exe
esac
if [ "$(uname -s)" = Darwin ]; then
    # macOS has no <malloc.h>; its functions are in <stdlib.h> (alloca in <alloca.h>)
    mkdir -p "$work/shim"
    printf '#include <stdlib.h>\n#include <alloca.h>\n' >"$work/shim/malloc.h"
    make_args=("inc_dirs=-Isrc/H -I$work/shim" "CC=clang -Wno-error=implicit-function-declaration -Wno-error=int-conversion")
fi
if [ "$(uname -s)" = Darwin ]; then
    # CLUnix.mak links with GNU ld's -Map, which Apple's linker lacks: compile with it, link here
    "$make" -s -k -C "$work/src" -f "$makefile" ${make_args[@]+"${make_args[@]}"} >"$work/build.log" 2>&1 || true
    clang "$work/src/$(dirname "$output")"/*.o -o "$work/src/$output" >>"$work/build.log" 2>&1 || { cat "$work/build.log"; exit 1; }
else
    "$make" -s -C "$work/src" -f "$makefile" ${make_args[@]+"${make_args[@]}"} >"$work/build.log" 2>&1 || { cat "$work/build.log"; exit 1; }
fi
mkdir -p "$dest"
cp "$work/src/$output" "$dest/$name"
cp "$work/src/README.md" "$dest/README.md" 2>/dev/null || true
cp "$work/src/Html/License.html" "$dest/License.html" 2>/dev/null || true  # Sybase Open Watcom Public License
echo "JWasm built: $dest/$name"
