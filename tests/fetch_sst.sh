#!/usr/bin/env bash
# Download the SingleStepTests 8088 suite (v1 JSON) into tests/data/8088.
# The 8088 shares the 8086 instruction set, so these hardware-captured vectors
# are the reference used by tests/sst_runner.py.
set -euo pipefail
dest="$(cd "$(dirname "$0")" && pwd)/data/8088"
if [ -d "$dest/v1" ]; then
    echo "already present: $dest/v1"
    exit 0
fi
mkdir -p "$(dirname "$dest")"
git clone --depth 1 --filter=blob:none --sparse https://github.com/SingleStepTests/8088 "$dest"
git -C "$dest" sparse-checkout set v1
echo "tests downloaded to $dest/v1"
