#!/usr/bin/env bash
# Regenerates robot/requirements.lock (all robot dependencies pinned, with sha256 hashes) for the
# Raspberry Pi: linux aarch64, Python 3.13 (Raspberry Pi OS trixie). Runs on any machine with uv,
# including the Windows laptop (Git Bash), because the target platform is given explicitly:
#
#     pip install "uv==0.13.9"        # once
#     bash robot/deploy/lock.sh
#
# Change version ranges in robot/pyproject.toml, never in the lock. Afterwards run
# robot/scripts/container_test.sh, commit the lock, and run robot/deploy/update.sh on the Pi.
set -euo pipefail
ROBOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$ROBOT/requirements.lock"
UV="${UV:-uv}"
command -v "$UV" >/dev/null || { echo "uv not found: pip install \"uv==0.13.9\"" >&2; exit 1; }

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
"$UV" pip compile "$ROBOT/pyproject.toml" --extra vision --extra dev --extra flash \
    --python-platform aarch64-unknown-linux-gnu --python-version 3.13 \
    --generate-hashes --no-header --quiet -o "$body"
{
    sed -n '1,/^# ---- end of header/p' "$LOCK" |
        sed "s/^# Generated .*/# Generated $(date +%F) with $("$UV" --version | cut -d" " -f1,2)./"
    cat "$body"
} >"$LOCK.tmp"
mv "$LOCK.tmp" "$LOCK"
echo "$LOCK: $(grep -c '^[a-z0-9]' "$LOCK") packages pinned"
