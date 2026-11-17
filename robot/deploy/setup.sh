#!/usr/bin/env bash
# Smart Campus Robot: one-time setup of a Raspberry Pi 5 with Raspberry Pi OS 64-bit
# (Debian 13 "trixie", desktop image, default user). Run from the repository:
#
#     cd ~/smart-campus-robot && bash robot/deploy/setup.sh [--i2s-amp]
#
# Options:
#   --i2s-amp          add the MAX98357 I2S amplifier overlay to /boot/firmware/config.txt
#   --container-test   install only what makes sense in a container (apt packages, virtualenv,
#                      locked dependencies, models). Skips udev, groups, robot.env, systemd,
#                      the kiosk, screen blanking and audio. Used by robot/scripts/container_test.sh
#                      inside an emulated arm64 Debian container. A container is NOT a Raspberry Pi.
#
# Dependencies come from robot/requirements.lock (all versions pinned, sha256 hashes checked).
#
# NOT YET RUN ON A REAL PI. The --container-test path was run in an emulated arm64 container
# (sprint 5). Read it before running it, and note in the sprint log what had to be changed.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USER_NAME="${SUDO_USER:-${USER:-$(id -un)}}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
USER_HOME="${USER_HOME:-$HOME}"
VENV="$REPO/robot/.venv"
LOCK="$REPO/robot/requirements.lock"
ENV_DIR=/etc/smart-campus-robot
PY_WANT=3.13          # the Python the lock was compiled for (Raspberry Pi OS trixie ships 3.13)
UV_VERSION=0.13.9     # only used when the system Python is not $PY_WANT
I2S_AMP=0
CONTAINER=0

usage() { sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
for arg in "$@"; do
    case "$arg" in
        --i2s-amp) I2S_AMP=1 ;;
        --container-test) CONTAINER=1 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
    esac
done

# root inside a container, sudo on the Pi
as_root() {
    if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi
}
# run as the desktop user (setup.sh may be started with sudo)
as_user() {
    if [[ $EUID -eq 0 && "$USER_NAME" != root ]]; then sudo -u "$USER_NAME" "$@"; else "$@"; fi
}

if [[ $CONTAINER -eq 1 ]]; then
    TOTAL=3
    echo "#### CONTAINER TEST MODE: no udev, systemd, kiosk or hardware steps (not a Raspberry Pi)"
else
    TOTAL=8
fi

echo "== 1/$TOTAL system packages"
export DEBIAN_FRONTEND=noninteractive
# libegl1 + libgles2: MediaPipe's face landmarker loads libEGL.so.1 at start (without it
# visiond falls back to YuNet; found by the container test); libgl1/libglib2.0-0: OpenCV;
# libportaudio2: sounddevice (pulled in by mediapipe)
APT_PKGS=(python3 python3-venv python3-dev git curl ca-certificates
    libgl1 libegl1 libgles2 libglib2.0-0 libportaudio2)
if [[ $CONTAINER -eq 0 ]]; then
    # v4l-utils: camera checks; pipewire/wireplumber/alsa-utils: audio; unclutter: hides the
    # cursor and xinput maps the touch screen in the X11 fallback session; util-linux gives
    # flock (kiosk single instance)
    APT_PKGS+=(v4l-utils pipewire-audio wireplumber alsa-utils unclutter xinput util-linux)
fi
as_root apt-get update
as_root apt-get install -y --no-install-recommends "${APT_PKGS[@]}"
if [[ $CONTAINER -eq 0 ]]; then
    # Raspberry Pi OS trixie calls the package "chromium"; older images used "chromium-browser"
    as_root apt-get install -y chromium || as_root apt-get install -y chromium-browser
fi

echo "== 2/$TOTAL Python environment ($VENV) from $(basename "$LOCK")"
PY_SYS="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
if [[ "$PY_SYS" == "$PY_WANT" ]]; then
    [[ -x "$VENV/bin/python" ]] || as_user python3 -m venv "$VENV"
else
    # The lock is compiled for $PY_WANT. A different system Python gets a pinned interpreter
    # managed by uv (downloaded once, kept under ~/.local/share/uv).
    echo "   system Python is $PY_SYS, the lock needs $PY_WANT: installing Python $PY_WANT with uv $UV_VERSION"
    BOOT="$REPO/robot/.uv-bootstrap"
    as_user python3 -m venv "$BOOT"
    as_user "$BOOT/bin/pip" install --quiet "uv==$UV_VERSION"
    as_user "$BOOT/bin/uv" python install "$PY_WANT"
    [[ -x "$VENV/bin/python" ]] || as_user "$BOOT/bin/uv" venv --seed --python "$PY_WANT" "$VENV"
fi
VPY="$VENV/bin/python"
"$VPY" --version
# every package and every hash from the lock; nothing is resolved on the Pi
as_user "$VPY" -m pip install --disable-pip-version-check --require-hashes -r "$LOCK"
# the two local packages, editable (git pull updates them), their dependencies are in the lock
as_user "$VPY" -m pip install --disable-pip-version-check --no-deps -e "$REPO/shared" -e "$REPO/robot"
as_user "$VPY" -m pip check || echo "!! pip check reported a problem (see above)"
sha256sum "$LOCK" | cut -d' ' -f1 | as_user tee "$VENV/.lock.sha256" >/dev/null

echo "== 3/$TOTAL vision models"
as_user "$VPY" "$REPO/robot/models/download_models.py" ||
    echo "!! model download failed: run robot/models/download_models.py again with internet"

if [[ $CONTAINER -eq 1 ]]; then
    echo "Done (container test mode). Skipped: udev, groups, robot.env, systemd, kiosk, blanking, audio."
    exit 0
fi

echo "== 4/$TOTAL serial port: stable name /dev/esp32, user in the dialout, video and audio groups"
as_root install -m 0644 "$REPO/robot/deploy/99-esp32.rules" /etc/udev/rules.d/99-esp32.rules
as_root udevadm control --reload-rules && as_root udevadm trigger
as_root usermod -aG dialout,video,audio,input "$USER_NAME"

echo "== 5/$TOTAL settings file $ENV_DIR/robot.env (kept if it already exists)"
as_root mkdir -p "$ENV_DIR"
if [[ ! -f "$ENV_DIR/robot.env" ]]; then
    as_root install -m 0640 -g "$USER_NAME" "$REPO/robot/deploy/robot.env.example" "$ENV_DIR/robot.env"
    echo "   -> edit it: BACKEND_URL and ROBOT_TOKEN (see robot/README.md)"
fi

echo "== 6/$TOTAL systemd services robotd and visiond"
for unit in robotd visiond; do
    sed -e "s|@REPO@|$REPO|g" -e "s|@USER@|$USER_NAME|g" "$REPO/robot/deploy/$unit.service" |
        as_root tee "/etc/systemd/system/$unit.service" >/dev/null
done
as_root systemctl daemon-reload
as_root systemctl enable robotd.service visiond.service

echo "== 7/$TOTAL kiosk on the 7-inch screen (labwc / Wayland, with an X11 fallback)"
bash "$REPO/robot/deploy/kiosk-setup.sh" --user "$USER_NAME" --home "$USER_HOME"

echo "== 8/$TOTAL audio"
if [[ $I2S_AMP -eq 1 ]]; then
    CONFIG=/boot/firmware/config.txt
    if ! grep -q '^dtoverlay=hifiberry-dac' "$CONFIG"; then
        echo "dtoverlay=hifiberry-dac   # MAX98357 I2S amplifier (smart-campus-robot)" | as_root tee -a "$CONFIG"
        echo "   -> I2S amplifier overlay added; it is active after a reboot"
    fi
fi
echo "   choose the speaker and microphone after the reboot: bash robot/deploy/audio-select.sh"

cat <<MSG

Done. Next:
  1. sudo nano $ENV_DIR/robot.env       (BACKEND_URL, ROBOT_TOKEN, CAMERA_SOURCE)
  2. sudo reboot
  3. after the reboot: systemctl status robotd visiond ; journalctl -u robotd -f
The firmware is a separate step: robot/deploy/flash_esp32.sh and firmware/README.md.
MSG
