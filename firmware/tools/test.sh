#!/usr/bin/env bash
# Runs the firmware's off-target checks inside Docker (the host needs no compiler, no PlatformIO):
#   1. pio test -e native            unit tests + Python<->C++ protocol cross-test
#   2. pio run  -e sim-check         whole application with stub drivers, then runs it (SIMULATED)
# Usage: firmware/tools/test.sh [extra command instead of the default]
set -euo pipefail
TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$TOOLS_DIR/../.." && pwd)"
IMAGE=scr-firmware-test

# Git Bash on Windows: Docker needs a Windows-style path and no path conversion of /work.
if command -v cygpath >/dev/null 2>&1; then
  REPO_DIR="$(cygpath -m "$REPO_DIR")"
  TOOLS_DIR="$(cygpath -m "$TOOLS_DIR")"
fi
export MSYS_NO_PATHCONV=1

docker build -q -t "$IMAGE" "$TOOLS_DIR" >/dev/null

if [ "$#" -gt 0 ]; then
  CMD="$*"
else
  CMD="pio test -e native && pio run -e sim-check -t exec"
fi

docker run --rm \
  -v "$REPO_DIR:/work" \
  -v scr-pio-cache:/root/.platformio \
  -w /work/firmware \
  "$IMAGE" bash -c "$CMD"
