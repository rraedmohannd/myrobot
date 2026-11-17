#!/usr/bin/env bash
# Smart Campus Robot: one-time setup of a Raspberry Pi 5 with Raspberry Pi OS Bookworm 64-bit
# (desktop image, default user). Run from the repository:
#
#     cd ~/smart-campus-robot && bash robot/deploy/setup.sh [--i2s-amp]
#
# NOT YET RUN ON A REAL PI (written in sprint 4, before the hardware arrived). Read it before
# running it, and note in docs/SPRINT_4_LOG.md what had to be changed.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
VENV="$REPO/robot/.venv"
ENV_DIR=/etc/smart-campus-robot
I2S_AMP=0
[[ "${1:-}" == "--i2s-amp" ]] && I2S_AMP=1

echo "== 1/7 system packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev git chromium-browser \
    libgl1 libglib2.0-0 v4l-utils pipewire-audio wireplumber alsa-utils curl

echo "== 2/7 Python environment ($VENV)"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -e "$REPO/shared"   # editable: `git pull` updates it
"$VENV/bin/pip" install -e "$REPO/robot"
# MediaPipe brings its own OpenCV (opencv-contrib-python). Without MediaPipe, visiond falls back
# to YuNet landmarks and only needs plain OpenCV.
"$VENV/bin/pip" install "numpy>=1.26,<3"
if ! "$VENV/bin/pip" install "mediapipe>=0.10.14"; then
    echo "!! mediapipe did not install on this system: visiond will use the YuNet fallback"
    "$VENV/bin/pip" install "opencv-python-headless>=4.10,<6"
fi
"$VENV/bin/python" "$REPO/robot/models/download_models.py" ||
    echo "!! model download failed: run robot/models/download_models.py again with internet"

echo "== 3/7 serial port: stable name /dev/esp32, user in the dialout, video and audio groups"
sudo install -m 0644 "$REPO/robot/deploy/99-esp32.rules" /etc/udev/rules.d/99-esp32.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG dialout,video,audio "$USER_NAME"

echo "== 4/7 settings file $ENV_DIR/robot.env (kept if it already exists)"
sudo mkdir -p "$ENV_DIR"
if [[ ! -f "$ENV_DIR/robot.env" ]]; then
    sudo install -m 0640 -g "$USER_NAME" "$REPO/robot/deploy/robot.env.example" "$ENV_DIR/robot.env"
    echo "   -> edit it: BACKEND_URL and ROBOT_TOKEN (see robot/README.md)"
fi

echo "== 5/7 systemd services robotd and visiond"
for unit in robotd visiond; do
    sed -e "s|@REPO@|$REPO|g" -e "s|@USER@|$USER_NAME|g" "$REPO/robot/deploy/$unit.service" |
        sudo tee "/etc/systemd/system/$unit.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable robotd.service visiond.service

echo "== 6/7 Chromium kiosk on the 7-inch screen (starts with the desktop session)"
AUTOSTART="/home/$USER_NAME/.config/autostart"
mkdir -p "$AUTOSTART"
sed -e "s|@REPO@|$REPO|g" "$REPO/robot/deploy/robot-kiosk.desktop" >"$AUTOSTART/robot-kiosk.desktop"
chmod +x "$REPO/robot/deploy/kiosk.sh" "$REPO/robot/deploy/audio-select.sh"

echo "== 7/7 audio"
if [[ $I2S_AMP -eq 1 ]]; then
    CONFIG=/boot/firmware/config.txt
    if ! grep -q '^dtoverlay=hifiberry-dac' "$CONFIG"; then
        echo "dtoverlay=hifiberry-dac   # MAX98357 I2S amplifier (smart-campus-robot)" | sudo tee -a "$CONFIG"
        echo "   -> I2S amplifier overlay added; it is active after a reboot"
    fi
fi
echo "   choose the speaker and microphone after the reboot: bash robot/deploy/audio-select.sh"

cat <<MSG

Done. Next:
  1. sudo nano $ENV_DIR/robot.env       (BACKEND_URL, ROBOT_TOKEN, CAMERA_SOURCE)
  2. sudo reboot
  3. after the reboot: systemctl status robotd visiond ; journalctl -u robotd -f
The firmware and the pins are a separate step: firmware/README.md.
MSG
