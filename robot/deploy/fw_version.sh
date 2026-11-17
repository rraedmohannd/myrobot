#!/usr/bin/env bash
# Prints the firmware version of this checkout (X.Y.Z) from firmware/lib/app/scr_version.h,
# the one place of the version (reported by the ESP32 in DIAG). Exit 1 if it cannot be read.
set -euo pipefail
HEADER="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/firmware/lib/app/scr_version.h"
[[ -f "$HEADER" ]] || exit 1
part() { awk -v k="SCR_FW_VERSION_$1" '$1 == "#define" && $2 == k { print $3 }' "$HEADER" | tr -d '\r'; }
major="$(part MAJOR)" minor="$(part MINOR)" patch="$(part PATCH)"
[[ "$major$minor$patch" =~ ^[0-9]+$ ]] || exit 1
echo "$major.$minor.$patch"
