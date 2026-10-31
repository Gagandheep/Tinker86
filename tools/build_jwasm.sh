#!/usr/bin/env bash
# Download and build JWasm (https://github.com/Baron-von-Riedesel/JWasm) into
# external/jwasm/, where the simulator finds it. Needs git, make and a C compiler:
# gcc on Linux, clang (Xcode command line tools) on macOS; on Windows (Git Bash)
# Visual C++ from a "x64 Native Tools" prompt, or MinGW gcc. Windows users can also download jwasm.exe from the
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

src="$work/src"
log="$work/build.log"
case "$(uname -s)" in
    Darwin)
        # macOS has no <malloc.h> (its functions are in <stdlib.h>), and CLUnix.mak links with
        # GNU ld's -Map, which Apple's linker lacks: compile with the makefile, link here
        mkdir -p "$work/shim"
        printf '#include <stdlib.h>\n#include <alloca.h>\n' >"$work/shim/malloc.h"
        make -s -k -C "$src" -f CLUnix.mak "inc_dirs=-Isrc/H -I$work/shim" \
            "CC=clang -Wno-error=implicit-function-declaration -Wno-error=int-conversion" >"$log" 2>&1 || true
        output=build/CLUnixR/jwasm; name=jwasm
        clang "$src"/build/CLUnixR/*.o -o "$src/$output" >>"$log" 2>&1 || { cat "$log"; exit 1; }
        ;;
    MINGW*|MSYS*|CYGWIN*)
        name=jwasm.exe
        if command -v nmake >/dev/null && command -v cl >/dev/null; then
            # Visual C++ (from a "x64 Native Tools" prompt): the compiler JWasm's Windows releases use
            output=build/MSVC64R/jwasm.exe
            # cl's folder goes first so that link.exe is Microsoft's linker, not Git Bash's link
            (cd "$src" && PATH="$(dirname "$(command -v cl)"):$PATH" nmake -nologo -f Msvc64.mak) >"$log" 2>&1 \
                || { cat "$log"; exit 1; }
        else
            # MinGW: the output folder is made first because GccWin.mak's mkdir has no -p;
            # gnu99 because GCC 15 defaults to C23, where JWasm's bool typedef is an error
            output=build/MinGWR/jwasm.exe
            mkdir -p "$src/build/MinGWR"
            "$(command -v mingw32-make || command -v make)" -s -C "$src" -f GccWin.mak \
                "c_flags=-D__NT__ -DNDEBUG -O2 -std=gnu99" "LINK=gcc.exe -static" >"$log" 2>&1 || { cat "$log"; exit 1; }
        fi
        ;;
    *)
        output=build/GccUnixR/jwasm; name=jwasm
        make -s -C "$src" -f GccUnix.mak >"$log" 2>&1 || { cat "$log"; exit 1; }
        ;;
esac
mkdir -p "$dest"
cp "$work/src/$output" "$dest/$name"
cp "$work/src/README.md" "$dest/README.md" 2>/dev/null || true
cp "$work/src/Html/License.html" "$dest/License.html" 2>/dev/null || true  # Sybase Open Watcom Public License
echo "JWasm built: $dest/$name"
