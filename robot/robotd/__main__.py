"""`python -m robotd [--sim] [--port /dev/esp32] [--config robotd.json]`

Environment: BACKEND_URL (ws://<backend>:8000/ws/robot), ROBOT_TOKEN, ROBOT_ID,
ROBOTD_SERIAL_PORT, ROBOTD_UI_PORT, ROBOTD_UI_HOST, ROBOTD_SIM_PORT, ROBOTD_ASSETS_DIR,
ROBOTD_CACHE_DIR, ROBOTD_PAGE_BACKEND, ROBOTD_CALIBRATION, LOG_DIR, ROBOTD_SETTINGS_PIN,
ROBOTD_SETTINGS_FILE. BACKEND_URL may list several addresses separated by commas (tried in
order). The repository's .env is never read.
"""

import argparse
import asyncio
import contextlib
import logging

from robotd.app import RobotApp
from robotd.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="robotd", description="Raspberry Pi runtime of the reception robot"
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        default=None,
        help="use the SIMULATED ESP32 (labelled as simulation everywhere)",
    )
    parser.add_argument(
        "--port", dest="serial_port", help="serial port of the ESP32 (default /dev/esp32)"
    )
    parser.add_argument("--config", help="JSON file with Config fields")
    parser.add_argument(
        "--calibration", dest="calibration_path",
        help="calibration file (default /etc/smart-campus-robot/calibration.json on the Pi, "
        "robot/calibration.dev.json in development; --sim without it: SIMULATED profile)",
    )  # fmt: skip
    parser.add_argument(
        "--backend", dest="backend_url", help="ws://host:8000/ws/robot[,ws://other:8000/ws/robot]"
    )
    parser.add_argument("--ui-port", dest="ui_port", type=int)
    parser.add_argument("--sim-port", dest="sim_port", type=int)
    parser.add_argument("--seed", dest="sim_seed", type=int)
    parser.add_argument("--assets-dir", dest="assets_dir", help="local copy of backend/app/static")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    overrides = {k: v for k, v in vars(args).items() if k not in ("config", "verbose")}
    cfg = load_config(args.config, **overrides)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(RobotApp(cfg).run_forever())


if __name__ == "__main__":
    main()
