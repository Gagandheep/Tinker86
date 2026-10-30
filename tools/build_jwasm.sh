#!/usr/bin/env bash
# Download and build JWasm (https://github.com/Baron-von-Riedesel/JWasm) into
# external/jwasm/jwasm, where the simulator finds it. Needs git, make and gcc.
# Windows users can download jwasm.exe from the project's releases instead and
# put it on PATH (or set JWASM to its path).
set -euo pipefail
JWASM_COMMIT=7f6f32e78b79565d40bcce496756aadd1ff66900
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/external/jwasm"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
git clone --quiet https://github.com/Baron-von-Riedesel/JWasm "$work/src"
git -C "$work/src" checkout --quiet "$JWASM_COMMIT" 2>/dev/null || \
    echo "note: pinned commit not found, building the latest version"
make -s -C "$work/src" -f GccUnix.mak >"$work/build.log" 2>&1 || { cat "$work/build.log"; exit 1; }
mkdir -p "$dest"
cp "$work/src/build/GccUnixR/jwasm" "$dest/jwasm"
cp "$work/src/README.md" "$dest/README.md" 2>/dev/null || true
echo "JWasm built: $dest/jwasm"
