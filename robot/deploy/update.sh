#!/usr/bin/env bash
# Updates the robot software on the Raspberry Pi:
#
#     bash robot/deploy/update.sh [--firmware-dir ~/firmware-release] [--no-firmware-check]
#
# 1. git pull (fast-forward only; local changes stop it)
# 2. reinstall the locked dependencies if robot/requirements.lock changed since the last install
#    (the local packages shared/ and robot/ are always re-registered, without dependencies)
# 3. restart robotd and visiond
# 4. firmware: if the ESP32 does not report the version in firmware/lib/app/scr_version.h, OFFER to flash
#    the release in --firmware-dir with flash_esp32.sh. It asks first; the default is NO, and
#    without a terminal (e.g. over a script) it never flashes.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="$REPO/robot/.venv"
VPY="$VENV/bin/python"
LOCK="$REPO/robot/requirements.lock"
FW_DIR="$HOME/firmware-release"
FW_CHECK=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --firmware-dir) FW_DIR="$2"; shift 2 ;;
        --no-firmware-check) FW_CHECK=0; shift ;;
        -h | --help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
[[ -x "$VPY" ]] || { echo "!! $VPY not found: run robot/deploy/setup.sh first" >&2; exit 1; }

echo "== 1/4 git pull"
git -C "$REPO" pull --ff-only
git -C "$REPO" log -1 --format='   now at %h %s'

echo "== 2/4 dependencies"
new_hash="$(sha256sum "$LOCK" | cut -d' ' -f1)"
old_hash="$(cat "$VENV/.lock.sha256" 2>/dev/null || true)"
if [[ "$new_hash" != "$old_hash" ]]; then
    echo "   $(basename "$LOCK") changed: installing the locked versions"
    "$VPY" -m pip install --disable-pip-version-check --require-hashes -r "$LOCK"
    echo "$new_hash" >"$VENV/.lock.sha256"
else
    echo "   lock unchanged"
fi
"$VPY" -m pip install --disable-pip-version-check --quiet --no-deps -e "$REPO/shared" -e "$REPO/robot"

echo "== 3/4 restart robotd and visiond"
sudo systemctl restart robotd visiond
sleep 3
systemctl --no-pager --lines=0 status robotd visiond || true

echo "== 4/4 ESP32 firmware version"
[[ $FW_CHECK -eq 1 ]] || { echo "   skipped (--no-firmware-check)"; exit 0; }
# The repository's firmware version, from firmware/lib/app/scr_version.h
if ! REPO_FW="$(bash "$REPO/robot/deploy/fw_version.sh")"; then
    echo "   firmware version not found in firmware/lib/app/scr_version.h: not compared"
    exit 0
fi
if ! "$VPY" -c "import robotctl" >/dev/null 2>&1; then
    echo "   robotctl is not installed: cannot read the ESP32 version (repository: $REPO_FW)"
    exit 0
fi
if (cd "$REPO/robot" && "$VPY" -m robotctl diag --expect-version "$REPO_FW" >/tmp/robot-update-diag.log 2>&1); then
    echo "   ESP32 reports $REPO_FW: up to date"
    exit 0
fi
echo "   the ESP32 does not report $REPO_FW, or could not be read (/tmp/robot-update-diag.log)"
if [[ ! -f "$FW_DIR/firmware.bin" ]]; then
    echo "   no firmware release in $FW_DIR: build it on the laptop and copy it (robot/README.md)"
    exit 0
fi
if [[ -f "$FW_DIR/VERSION" ]]; then
    FW_REL="$(tr -d ' \r\n' <"$FW_DIR/VERSION")"
    if [[ "$FW_REL" != "$REPO_FW" ]]; then
        echo "   !! the release in $FW_DIR is version $FW_REL, not $REPO_FW: build a new one first"
        exit 0
    fi
else
    echo "   (the release in $FW_DIR does not name its version: it must be built from this commit;"
    echo "    flash_esp32.sh checks the version through DIAG after flashing)"
fi
if [[ ! -t 0 ]]; then
    echo "   no terminal: not flashing. Run: bash robot/deploy/flash_esp32.sh $FW_DIR"
    exit 0
fi
read -rp "   Flash firmware $REPO_FW from $FW_DIR now? [y/N] " answer
if [[ "$answer" == [yY] || "$answer" == [yY][eE][sS] ]]; then
    # flash_esp32.sh asks again (with the safety reminder) and checks the version afterwards
    bash "$REPO/robot/deploy/flash_esp32.sh" --expect-version "$REPO_FW" "$FW_DIR"
else
    echo "   not flashed"
fi
