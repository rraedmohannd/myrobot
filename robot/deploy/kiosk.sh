#!/usr/bin/env bash
# Opens the robot page full screen on the 7-inch HDMI touch screen (1024x600).
# Started when the desktop session begins, by ~/.config/labwc/autostart (Wayland / labwc, the
# Raspberry Pi OS default) or by ~/.config/autostart/robot-kiosk.desktop (X11 fallback).
# Both are installed by kiosk-setup.sh; the lock below makes sure only one Chromium starts.
set -u
ENV_FILE=/etc/smart-campus-robot/robot.env
env_value() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2-; }
PORT="$(env_value ROBOTD_UI_PORT)"
URL="http://localhost:${PORT:-8765}/"

exec 9>"${XDG_RUNTIME_DIR:-/tmp}/robot-kiosk.lock"
if ! flock -n 9; then
    echo "kiosk.sh: already running"
    exit 0
fi

if [[ -z "${WAYLAND_DISPLAY:-}" && -n "${DISPLAY:-}" ]]; then
    # X11 fallback session: no blanking, no DPMS, hidden cursor, touch mapped to the screen
    xset s off -dpms s noblank 2>/dev/null || true
    command -v unclutter >/dev/null && unclutter -idle 0.5 -root &
    TOUCH="$(env_value TOUCH_DEVICE)"
    if [[ -n "$TOUCH" ]] && command -v xinput >/dev/null; then
        xinput map-to-output "$TOUCH" "${KIOSK_X11_OUTPUT:-HDMI-1}" 2>/dev/null || true
    fi
fi
# (labwc: blanking is off through raspi-config, the cursor theme is invisible, the touch
# mapping is in ~/.config/labwc/rc.xml; see kiosk-setup.sh)

# wait until robotd serves the page (robotd also starts without the backend)
for _ in $(seq 1 60); do
    curl -fs -o /dev/null "$URL" && break
    sleep 1
done

BROWSER="$(command -v chromium || command -v chromium-browser)"
# --use-fake-ui-for-media-stream grants the microphone to the page without a permission prompt
# (the robot has no keyboard). The page is local: http://localhost is a secure context.
# --ozone-platform-hint=auto: native Wayland under labwc, X11 under the fallback session.
exec "$BROWSER" --kiosk --app="$URL" \
    --ozone-platform-hint=auto \
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --autoplay-policy=no-user-gesture-required \
    --use-fake-ui-for-media-stream \
    --touch-events=enabled --overscroll-history-navigation=0 \
    --check-for-update-interval=31536000
