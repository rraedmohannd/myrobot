#!/usr/bin/env bash
# Kiosk setup for the robot screen (7-inch HDMI touch screen, 1024x600). Called by setup.sh;
# can be run again on its own:  bash robot/deploy/kiosk-setup.sh [--user pi] [--home /home/pi]
#
# Raspberry Pi OS (Bookworm and later) runs Wayland with the labwc compositor on every model by
# default; X11 (Openbox) is still selectable in raspi-config (6 Advanced Options > A7 Wayland).
# Both sessions are prepared, so switching does not break the kiosk:
#   labwc  : ~/.config/labwc/autostart starts kiosk.sh; ~/.config/labwc/environment selects an
#            invisible cursor theme; ~/.config/labwc/rc.xml maps the touch screen to HDMI-A-1
#   X11    : ~/.config/autostart/robot-kiosk.desktop starts kiosk.sh; kiosk.sh runs unclutter,
#            switches DPMS off and maps the touch screen with xinput
#   both   : `raspi-config nonint do_blanking 1` switches screen blanking off
# kiosk.sh takes a lock, so it runs once even if both autostart methods fire.
#
# Not checked without the real screen: the touch device name, the output name (HDMI-A-1), the
# 1024x600 mode, the cursor theme being honoured by labwc and Chromium. See robot/README.md.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USER_NAME="${SUDO_USER:-${USER:-$(id -un)}}"
USER_HOME=""
OUTPUT="${KIOSK_OUTPUT:-HDMI-A-1}"   # labwc / wlr-randr name of the first HDMI port on a Pi 5
while [[ $# -gt 0 ]]; do
    case "$1" in
        --user) USER_NAME="$2"; shift 2 ;;
        --home) USER_HOME="$2"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
USER_HOME="${USER_HOME:-$(getent passwd "$USER_NAME" | cut -d: -f6)}"
MARK="# smart-campus-robot kiosk"

as_root() { if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi; }
as_user() { if [[ "$(id -un)" == "$USER_NAME" ]]; then "$@"; else sudo -u "$USER_NAME" "$@"; fi; }
# append a line to a user file once
add_line() { # add_line <file> <line>
    as_user mkdir -p "$(dirname "$1")"
    as_user touch "$1"
    grep -qxF "$2" "$1" || printf '%s\n' "$2" | as_user tee -a "$1" >/dev/null
}

chmod +x "$REPO/robot/deploy/kiosk.sh" "$REPO/robot/deploy/audio-select.sh"

echo "   X11 autostart: $USER_HOME/.config/autostart/robot-kiosk.desktop"
as_user mkdir -p "$USER_HOME/.config/autostart"
sed -e "s|@REPO@|$REPO|g" "$REPO/robot/deploy/robot-kiosk.desktop" |
    as_user tee "$USER_HOME/.config/autostart/robot-kiosk.desktop" >/dev/null

LABWC="$USER_HOME/.config/labwc"
echo "   labwc autostart: $LABWC/autostart"
add_line "$LABWC/autostart" "$REPO/robot/deploy/kiosk.sh &  $MARK"

echo "   invisible cursor theme (labwc): $USER_HOME/.local/share/icons/scr-invisible"
as_user python3 "$REPO/robot/deploy/make_invisible_cursor.py" "$USER_HOME/.local/share/icons/scr-invisible"
add_line "$LABWC/environment" "XCURSOR_THEME=scr-invisible"

echo "   touch screen -> $OUTPUT"
TOUCH_NAME="${TOUCH_DEVICE:-}"
if [[ -z "$TOUCH_NAME" ]]; then
    for dev in /sys/class/input/event*; do
        [[ -e "$dev" ]] || continue
        if udevadm info -q property -p "$dev" 2>/dev/null | grep -qx 'ID_INPUT_TOUCHSCREEN=1'; then
            TOUCH_NAME="$(cat "$dev/device/name")"
            break
        fi
    done
fi
if [[ -z "$TOUCH_NAME" ]]; then
    echo "   !! no touch screen found now (is it plugged in?). With one HDMI screen the touch"
    echo "      already covers it; run this script again with the screen connected to map it."
else
    echo "   touch device: \"$TOUCH_NAME\""
    RC="$LABWC/rc.xml"
    TOUCH_XML="  <touch deviceName=\"$TOUCH_NAME\" mapToOutput=\"$OUTPUT\" mouseEmulation=\"no\" />"
    if [[ ! -f "$RC" ]]; then
        printf '<?xml version="1.0"?>\n<labwc_config>\n%s\n</labwc_config>\n' "$TOUCH_XML" |
            as_user tee "$RC" >/dev/null
    elif ! grep -q '<touch ' "$RC"; then
        as_user sed -i "s|</labwc_config>|$TOUCH_XML\n</labwc_config>|" "$RC"
    else
        echo "   $RC already has a <touch> entry: left as it is"
    fi
    # the X11 fallback reads the same name from robot.env
    if [[ -f /etc/smart-campus-robot/robot.env ]] &&
        ! grep -q '^TOUCH_DEVICE=' /etc/smart-campus-robot/robot.env; then
        echo "TOUCH_DEVICE=$TOUCH_NAME" | as_root tee -a /etc/smart-campus-robot/robot.env >/dev/null
    fi
fi

echo "   screen blanking off"
if command -v raspi-config >/dev/null 2>&1; then
    # documented non-interactive form; 1 = disable (removes swayidle from labwc autostart,
    # installs 10-blanking.conf for X11)
    SUDO_USER="$USER_NAME" as_root raspi-config nonint do_blanking 1
else
    echo "   !! raspi-config not found: switch Screen Blanking off in the Control Centre"
fi
