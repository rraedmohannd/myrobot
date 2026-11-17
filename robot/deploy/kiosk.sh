#!/usr/bin/env bash
# Opens the robot page full screen on the 7-inch HDMI touch screen (1024x600).
# Started by ~/.config/autostart/robot-kiosk.desktop when the desktop session begins.
set -u
PORT="$(grep -E '^ROBOTD_UI_PORT=' /etc/smart-campus-robot/robot.env 2>/dev/null | cut -d= -f2)"
URL="http://localhost:${PORT:-8765}/"

# wait until robotd serves the page (robotd also starts without the backend)
for _ in $(seq 1 60); do
    curl -fs -o /dev/null "$URL" && break
    sleep 1
done

BROWSER="$(command -v chromium-browser || command -v chromium)"
# --use-fake-ui-for-media-stream grants the microphone to the page without a permission prompt
# (the robot has no keyboard). The page is local: http://localhost is a secure context.
exec "$BROWSER" --kiosk --app="$URL" \
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --autoplay-policy=no-user-gesture-required \
    --use-fake-ui-for-media-stream \
    --touch-events=enabled --overscroll-history-navigation=0 \
    --check-for-update-interval=31536000
