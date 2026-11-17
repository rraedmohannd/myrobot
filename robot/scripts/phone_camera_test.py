"""Laptop test of visiond with a phone as the camera (development setup, not the robot's camera).

    cd robot
    .venv\\Scripts\\python scripts\\phone_camera_test.py 192.168.1.23            # IP Webcam app
    .venv\\Scripts\\python scripts\\phone_camera_test.py 192.168.1.23 --app droidcam
    .venv\\Scripts\\python scripts\\phone_camera_test.py --source 0             # laptop webcam

What it runs: `python -m visiond --console --assume-speaking --sim-servo` on the phone stream.
  * no robotd and no microphone are needed;
  * "vad=ASSUMED" : speech is ASSUMED while any visible mouth moves (there is no real VAD here);
  * "servo SIMULATED" : no servo exists; the printed angle is where a servo WOULD point.
Nothing is recorded: frames stay in memory and only the status lines are printed.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visiond.app import VisionApp  # noqa: E402
from visiond.config import config_from_env, load_thresholds  # noqa: E402

APPS = {"ipwebcam": ("http", 8080, "/video"), "droidcam": ("http", 4747, "/video")}

LEGEND = """
Each line:  time  cam=ok fps  faces=N  #id[FM x= yaw= pitch= mouth=] ...  | selected= engaged=
            | vad=ASSUMED(mouth motion)=on/off speaker= overlap= | servo SIMULATED at= target=
  F = facing the camera   M = mouth moving   x = face centre (0 left edge .. 1 right edge)
  selected = the one participant the robot attends to   speaker = active speaker right now
  target = pan angle in degrees, 0 = straight ahead, positive = LEFT (as the ESP32 SERVO message)
Stop with Ctrl+C.
"""


def phone_url(host: str, app: str) -> str:
    scheme, port, path = APPS[app]
    return f"{scheme}://{host}:{port}{path}"


def reachable(url: str, timeout_s: float = 3.0) -> bool:
    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout_s):
            return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phone_ip", nargs="?", help="the phone's Wi-Fi address, e.g. 192.168.1.23")
    parser.add_argument("--app", choices=sorted(APPS), default="ipwebcam")
    parser.add_argument("--source", help="use this camera source instead (index, URL or file)")
    parser.add_argument("--landmarks", choices=["auto", "mediapipe", "yunet"], default="auto")
    parser.add_argument("--hfov-deg", type=float, help="the camera's horizontal field of view")
    parser.add_argument("--mirror", action="store_true", help="flip the picture left-right")
    parser.add_argument("--debug-window", action="store_true", help="local preview, not saved")
    parser.add_argument("--seconds", type=float, default=0.0, help="stop after this long")
    args = parser.parse_args(argv)
    if not args.source and not args.phone_ip:
        parser.error("give the phone's IP address, or --source")
    source = args.source or phone_url(args.phone_ip, args.app)
    if "://" in source and not reachable(source):
        print(
            f"Cannot reach {source}.\n"
            "  - Is the app's server started on the phone (IP Webcam: 'Start server')?\n"
            "  - Are the phone and the laptop on the same Wi-Fi (not mobile data)?\n"
            "  - Is the address the one shown on the phone's screen?",
            file=sys.stderr,
        )
        return 2
    config = config_from_env()
    servo = config.servo if args.hfov_deg is None else replace(config.servo, hfov_deg=args.hfov_deg)
    config = replace(
        config,
        camera_source=source,
        landmarks=args.landmarks,
        console=True,
        assume_speaking=True,
        sim_servo=True,
        mirror=args.mirror,
        debug_window=args.debug_window,
        max_seconds=args.seconds,
        servo=servo,
    )
    print(f"visiond phone test: source={source}  landmarks={args.landmarks}")
    print("VAD is ASSUMED from mouth motion; the servo is SIMULATED; nothing is recorded.")
    print(LEGEND, flush=True)
    try:
        asyncio.run(VisionApp(config, load_thresholds()).run())
    except KeyboardInterrupt:
        pass
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
