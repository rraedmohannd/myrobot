#!/usr/bin/env bash
# Render every part to out/<part>.stl and an out/<part>.png preview, using
# OpenSCAD in Docker (official image openscad/openscad:2021.01, Docker Hub).
# Usage: bash mechanical/build.sh      (from anywhere)
# The per-part work is in render_all.sh, which runs inside the container.
set -euo pipefail

IMAGE="${OPENSCAD_IMAGE:-openscad/openscad:2021.01}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Git Bash on Windows: give Docker a Windows path and stop MSYS path mangling.
if command -v cygpath >/dev/null 2>&1; then
  MOUNT="$(cygpath -w "$HERE")"
  export MSYS_NO_PATHCONV=1
else
  MOUNT="$HERE"
fi

docker run --rm -v "$MOUNT:/work" -w /work "$IMAGE" sh /work/render_all.sh
