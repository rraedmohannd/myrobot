#!/usr/bin/env bash
# Builds the real firmware (env esp32dev) in Docker and copies the files that esptool needs into
# firmware/release/ (git-ignored), with SHA256SUMS and a VERSION file (major.minor.patch from
# lib/app/scr_version.h). Needs include/pins_generated.h (see firmware/README.md).
#   bootloader.bin 0x1000, partitions.bin 0x8000, boot_app0.bin 0xe000, firmware.bin 0x10000
set -euo pipefail
TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$TOOLS_DIR/test.sh" 'pio run -e esp32dev \
  && rm -rf release && mkdir -p release \
  && cp "$PLATFORMIO_WORKSPACE_DIR/build/esp32dev/firmware.bin" \
        "$PLATFORMIO_WORKSPACE_DIR/build/esp32dev/bootloader.bin" \
        "$PLATFORMIO_WORKSPACE_DIR/build/esp32dev/partitions.bin" release/ \
  && cp /root/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin release/ \
  && awk '"'"'$1 == "#define" && $2 ~ /^SCR_FW_VERSION_(MAJOR|MINOR|PATCH)$/ { v = v (v ? "." : "") $3 } END { print v }'"'"' lib/app/scr_version.h > release/VERSION \
  && (cd release && sha256sum *.bin > SHA256SUMS) && cat release/VERSION && ls -l release'
