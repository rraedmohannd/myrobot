#!/usr/bin/env bash
# Flashes the ESP32 from the Raspberry Pi with a firmware release built on the laptop.
#
#     bash robot/deploy/flash_esp32.sh [--yes] [--port /dev/esp32] [--baud 460800]
#                                      [--expect-version X.Y.Z] <release-dir>
#
# <release-dir> is firmware/release/ made on the laptop by `bash firmware/tools/build_release.sh`
# and copied to the Pi: bootloader.bin partitions.bin boot_app0.bin firmware.bin SHA256SUMS.
# SHA256SUMS is required and checked before anything is flashed. Expected version:
# --expect-version, else VERSION in the release dir, else firmware/lib/app/scr_version.h of
# this checkout (so build the release from the same commit). See robot/README.md.
#
# Steps: check the files -> ask (default NO; --yes skips the question) -> stop robotd (it owns
# the serial port; the ESP32 stops the motors by itself when robotd goes silent) -> esptool
# write-flash at the standard ESP32 offsets (keeps the saved NVS parameters; this script never
# erases the flash) -> hard reset -> `python -m robotctl diag
# --expect-version` -> start robotd again (also when something failed).
#
# Offsets (classic ESP32, Arduino-ESP32 through PlatformIO platform espressif32 6.x,
# builder/frameworks/arduino.py FLASH_EXTRA_IMAGES and ESP32_APP_OFFSET):
#     0x1000 bootloader, 0x8000 partition table, 0xe000 boot_app0 (OTA data), 0x10000 app.
# They hold for the default partition table only; a custom partitions CSV may move the app.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VPY="$REPO/robot/.venv/bin/python"
PORT="${ROBOTD_SERIAL_PORT:-/dev/esp32}"
BAUD=460800
YES=0
EXPECT=""
DIR=""
usage() { sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes | -y) YES=1; shift ;;
        --port) PORT="$2"; shift 2 ;;
        --baud) BAUD="$2"; shift 2 ;;
        --expect-version) EXPECT="$2"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) DIR="$1"; shift ;;
    esac
done
[[ -n "$DIR" ]] || { usage >&2; exit 2; }

IMAGES=(bootloader.bin partitions.bin boot_app0.bin firmware.bin)
OFFSETS=(0x1000 0x8000 0xe000 0x10000)
for f in "${IMAGES[@]}"; do
    [[ -s "$DIR/$f" ]] || { echo "!! $DIR/$f is missing or empty" >&2; exit 1; }
done
SUMS="$DIR/SHA256SUMS"
[[ -f "$SUMS" ]] || { echo "!! $SUMS is missing: copy the whole release folder" >&2; exit 1; }
for f in "${IMAGES[@]}"; do
    awk '{print $2}' "$SUMS" | sed 's/^\*//' | grep -qxF "$f" ||
        { echo "!! $f is not listed in SHA256SUMS" >&2; exit 1; }
done
(cd "$DIR" && sha256sum --check --quiet SHA256SUMS) || { echo "!! checksum mismatch: not flashed" >&2; exit 1; }
echo "checksums: OK"
if [[ -z "$EXPECT" && -f "$DIR/VERSION" ]]; then EXPECT="$(tr -d ' \r\n' <"$DIR/VERSION")"; fi
if [[ -z "$EXPECT" ]]; then EXPECT="$(bash "$REPO/robot/deploy/fw_version.sh" 2>/dev/null || true)"; fi
[[ -x "$VPY" ]] || { echo "!! $VPY not found: run robot/deploy/setup.sh first" >&2; exit 1; }
"$VPY" -m esptool version >/dev/null 2>&1 ||
    { echo "!! esptool is not installed in the virtualenv (it is in robot/requirements.lock)" >&2; exit 1; }

echo "Firmware release: $DIR (version ${EXPECT:-unknown})"
for i in "${!IMAGES[@]}"; do printf '   %-8s %s\n' "${OFFSETS[$i]}" "${IMAGES[$i]}"; done
echo "ESP32 port: $PORT"
if [[ $YES -ne 1 ]]; then
    read -rp "Flash the ESP32 now? The robot must stand still (tracks off the ground). [y/N] " answer
    [[ "$answer" == [yY] || "$answer" == [yY][eE][sS] ]] || { echo "Not flashed."; exit 0; }
fi

ROBOTD_WAS_ACTIVE=0
if systemctl is-active --quiet robotd 2>/dev/null; then
    ROBOTD_WAS_ACTIVE=1
    echo "stopping robotd (it owns the serial port)"
    sudo systemctl stop robotd
fi
restart_robotd() {
    if [[ $ROBOTD_WAS_ACTIVE -eq 1 ]]; then
        echo "starting robotd again"
        sudo systemctl start robotd || echo "!! could not start robotd: sudo systemctl start robotd"
    fi
}
trap restart_robotd EXIT

ARGS=()
for i in "${!IMAGES[@]}"; do ARGS+=("${OFFSETS[$i]}" "$DIR/${IMAGES[$i]}"); done
echo "flashing ..."
"$VPY" -m esptool --chip esp32 --port "$PORT" --baud "$BAUD" \
    --before default-reset --after hard-reset write-flash -z "${ARGS[@]}"
echo "flashed; the ESP32 was reset"
sleep 3

if [[ -z "$EXPECT" ]]; then
    echo "!! no version given (--expect-version or VERSION in the release): version not checked"
elif ! "$VPY" -c "import robotctl" >/dev/null 2>&1; then
    echo "!! robotctl is not installed: version NOT checked. Check by hand later:"
    echo "   $VPY -m robotctl diag --expect-version $EXPECT"
else
    # robotd is still stopped here, so robotctl talks to the ESP32 directly on this port
    if (cd "$REPO/robot" && ROBOTD_SERIAL_PORT="$PORT" "$VPY" -m robotctl diag --expect-version "$EXPECT"); then
        echo "version check: OK ($EXPECT)"
    else
        echo "!! version check FAILED: the ESP32 does not report $EXPECT (see the DIAG output above)" >&2
        exit 3
    fi
fi
