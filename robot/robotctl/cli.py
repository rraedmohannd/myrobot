"""`python -m robotctl <step> ...` (run from robot/, or with robot/ and shared/ on the path).

Connection (pick one; default: robotd's control API on this Pi):
  --ctl ws://127.0.0.1:8765/ws/ctl   robotd is running (it must be IDLE)
  --port /dev/esp32                  robotd is stopped (sudo systemctl stop robotd)
  --sim-tcp 127.0.0.1:8766           the SIMULATED ESP32 (python -m robotd.sim_esp32)

Exit codes: 0 done, 2 refused (nothing moved), 3 link problem, 4 a check found a problem,
5 the step could not finish (motors stopped), 6 calibration file problem, 130 Ctrl-C.
"""

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from robotctl import __version__
from robotctl.bundle import default_sources, make_bundle
from robotctl.calstore import CalibrationMismatch, CalStore, default_calibration_path
from robotctl.device import CtlDevice, Device, DirectDevice, LinkError
from robotctl.operator import Operator, Refused
from robotctl.session import Session, StepError
from robotctl.steps import basic, distance, motors, sensors, servo, trial
from robotd.calibration import CalibrationError

StepFn = Callable[[Session, argparse.Namespace], Awaitable[int]]

DEVICE_STEPS: dict[str, StepFn] = {
    "status": basic.run_status,
    "diag": basic.run_diag,
    "param": basic.run_param,
    "motor-test": motors.run_motor_test,
    "encoder-check": motors.run_encoder_check,
    "ultrasonic-check": sensors.run_ultrasonic_check,
    "gyro-cal": sensors.run_gyro_cal,
    "rotate-test": sensors.run_rotate_test,
    "servo-cal": servo.run_servo_cal,
    "battery-cal": sensors.run_battery_cal,
    "distance-cal": distance.run_distance_cal,
    "pid-step": distance.run_pid_step,
    "square-test": distance.run_square_test,
    "hfov-cal": servo.run_hfov_cal,
}


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("connection and answers")
    g.add_argument("--ctl", help="robotd control API URL (default ws://127.0.0.1:<ui_port>/ws/ctl)")
    g.add_argument("--port", help="serial port of the ESP32 when robotd is stopped (/dev/esp32)")
    g.add_argument("--sim-tcp", help="host:port of the SIMULATED ESP32")
    g.add_argument("--calibration", help="calibration file (default: ROBOTD_CALIBRATION, "
                   "/etc/smart-campus-robot/calibration.json on the Pi, else robot/"
                   "calibration.dev.json)")  # fmt: skip
    g.add_argument("--operator", help="your name, stored with every value")
    g.add_argument(
        "--yes-i-confirm",
        action="append",
        default=[],
        metavar="PHRASE",
        help='the typed confirmation, e.g. "tracks off the ground" (repeatable)',
    )
    g.add_argument(
        "--non-interactive",
        action="store_true",
        help="never prompt: missing answers refuse the step",
    )
    g.add_argument(
        "--hand-secs", type=float, help="non-interactive: how long each by-hand action lasts"
    )
    g.add_argument(
        "--accept",
        dest="accept",
        action="store_true",
        default=None,
        help="accept the proposed values without asking",
    )
    g.add_argument(
        "--reject",
        dest="accept",
        action="store_false",
        help="show the proposed values, change nothing",
    )
    g.add_argument(
        "--save-params",
        dest="save_params",
        action="store_true",
        default=None,
        help="PARAM_SAVE after setting values, without asking",
    )
    g.add_argument("--no-save-params", dest="save_params", action="store_false")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    ap = argparse.ArgumentParser(
        prog="robotctl", description="Bring-up and calibration of the Smart Campus Robot "
        "(docs/BRINGUP.md). Anything run against the simulated ESP32 is SIMULATED.",
    )  # fmt: skip
    ap.add_argument("--version", action="version", version=f"robotctl {__version__}")
    sub = ap.add_subparsers(dest="step", required=True, metavar="STEP")

    def add(name: str, text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], help=text, description=text)

    p = add("status", "live view: telemetry, encoders, ultrasonics, gyro, battery, faults, hash")
    p.add_argument("--secs", type=float, default=0, help="keep showing for this long")
    p = add("diag", "DIAG_REPORT and the I2C scan against the expected addresses")
    p.add_argument("--expander", help="I2C address of the MCP23017 (default 0x20, option B)")
    p.add_argument("--no-expander", action="store_true", help="pin plan option A: no expander")
    p = add("param", "read, set or save firmware parameters by hand")
    p.add_argument("action", choices=("get", "set", "save"))
    p.add_argument("name", nargs="?")
    p.add_argument("value", nargs="?", type=float)
    p.add_argument("--reason", help="why (stored with the value)")
    p = add("motor-test", "open loop on the stand; checks the encoders; proposes inversions")
    p.add_argument("--side", choices=("left", "right"), required=True)
    p.add_argument("--duty", type=float, default=0.15)
    p.add_argument("--secs", type=float, default=1.0)
    p.add_argument(
        "--track-moved",
        choices=("forward", "backward", "none"),
        help="non-interactive: which way the track ran",
    )
    add("encoder-check", "push each track by hand; shows each count and its sign")
    p = add("ultrasonic-check", "box at a known distance; per-sensor reading, error, misses")
    p.add_argument("--distance", type=float, required=True, help="metres from the sensor face")
    p.add_argument("--sensors", help="indices to check, e.g. 0,1,2 (default all)")
    p.add_argument("--secs", type=float, default=3.0)
    p = add("gyro-cal", "gyro bias with the robot still")
    p.add_argument("--secs", type=float, default=2.0)
    p = add("rotate-test", "full turns by hand or driven -> GYRO_SCALE (and track width)")
    p.add_argument("--turns", type=int, default=1)
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--driven", action="store_true", help="turn in MODE TEST instead of by hand")
    p.add_argument("--duty", type=float, default=0.2)
    p.add_argument("--max-secs", type=float, default=20.0)
    p.add_argument("--measured-deg", help="true angle per run, comma separated")
    p = add("servo-cal", "jog the pan servo: limits, centre, us per degree, which way is left")
    p.add_argument("--ref-deg", type=float, default=45.0)
    p.add_argument(
        "--slew-deg-s",
        type=float,
        default=90.0,
        help="servo speed limit to propose if none is set (a choice)",
    )
    p.add_argument("--keys", help='non-interactive jog keys, e.g. "+50 c +100 a ..."')
    p = add("battery-cal", "voltage scale from a multimeter reading")
    p.add_argument("--multimeter", type=float, help="volts read on the multimeter")
    p.add_argument("--secs", type=float, default=2.0)
    p = add("distance-cal", "3 m on a tape, pushed or driven -> METRES_PER_TICK")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--drive", action="store_true", help="drive in MODE TEST instead of pushing")
    p.add_argument("--duty", type=float, default=0.2)
    p.add_argument("--max-secs", type=float, default=25.0)
    p.add_argument("--drive-secs", type=float, help="non-interactive: drive this long")
    p.add_argument("--measured", help="tape distance per run in metres, comma separated")
    p = add("return-trial", "roam, return, tape distance -> a real return trial in the backend")
    p.add_argument("--backend", default=os.environ.get("SCR_BACKEND", "http://localhost:8000"))
    p.add_argument("--admin-token", help="admin token (or SCR_ADMIN_TOKEN); never stored")
    p.add_argument("--roam-secs", type=float, default=60.0)
    p.add_argument("--return-timeout", type=float, default=240.0)
    p.add_argument("--poll-s", type=float, default=2.0)
    p.add_argument("--measured", type=float, help="tape distance from the start mark (m)")
    p.add_argument("--heading-err-deg", type=float)
    p.add_argument("--tolerance", type=float, default=0.15)
    p.add_argument("--success", choices=("yes", "no"))
    p = add("pid-step", "closed-loop speed step on the stand; CSV/plot; suggested gains")
    p.add_argument("--speed", type=float, default=0.2)
    p.add_argument("--secs", type=float, default=3.0)
    for gain in ("kp", "ki", "kd", "kff"):
        p.add_argument(f"--{gain}", type=float, help=f"try this PID_{gain.upper()} (not saved)")
    p.add_argument("--out-dir", help="where the CSV/PNG go (default: next to the calibration)")
    p = add("square-test", "2 m x 2 m square CW and CCW; tape closure typed in")
    p.add_argument("--side", type=float, default=2.0)
    p.add_argument("--speed", type=float, default=0.2)
    p.add_argument("--measured", help="tape closure CW,CCW in metres")
    p = add("hfov-cal", "camera horizontal field of view with the pan servo and a face")
    p.add_argument("--sweep-deg", type=float, default=30.0)
    p.add_argument("--edge-angles", help="LEFT,RIGHT pan angles where the face leaves the picture")
    p = add("bundle", "zip of logs, configuration, calibration, versions (no images or audio)")
    p.add_argument("--out", help="zip file (default robotctl-bundle-<time>.zip)")
    p.add_argument("--include", action="append", default=[], help="another file or folder")
    return ap


def make_device(args: argparse.Namespace) -> Device:
    if args.sim_tcp:
        from robotd.transport import TcpTransport

        host, _, port = args.sim_tcp.rpartition(":")
        return DirectDevice(TcpTransport(host or "127.0.0.1", int(port)), simulated=True)
    if args.port:
        from robotd.transport import SerialTransport

        return DirectDevice(SerialTransport(args.port), simulated=False)
    url = args.ctl or f"ws://127.0.0.1:{os.environ.get('ROBOTD_UI_PORT', '8765')}/ws/ctl"
    return CtlDevice(url)


async def run(
    argv: list[str] | None = None, *, device: Device | None = None,
    out: Callable[[str], None] = print, input_fn: Callable[[str], str] = input,
) -> int:  # fmt: skip
    args = build_parser().parse_args(argv)
    interactive = not args.non_interactive and sys.stdin is not None and sys.stdin.isatty()
    if input_fn is not input:
        interactive = not args.non_interactive
    op = Operator(interactive, args.yes_i_confirm, out=out, input_fn=input_fn)
    cal_path = Path(args.calibration) if args.calibration else default_calibration_path()
    operator = args.operator or os.environ.get("SCR_OPERATOR") or _user()
    store = CalStore(cal_path, operator)
    try:
        if args.step == "bundle":
            return _bundle(args, cal_path, out)
        if args.step == "return-trial":
            return await trial.run_return_trial(args, op, store, out)
        dev = device or make_device(args)
        await dev.open()
        session = Session(dev, op, store, out=out, hand_secs=args.hand_secs,
                          save_params=args.save_params, accept=args.accept)  # fmt: skip
        try:
            session.banner(args.step)
            await dev.wait_telemetry()
            code = await DEVICE_STEPS[args.step](session, args)
        finally:
            if session.in_motion_mode:
                await session.safe_exit()
            await dev.close()
        if session.wrote:
            session.say(f"Calibration keys written: {', '.join(session.wrote)}"
                        + ("  (SIMULATED file)" if session.sim else ""))  # fmt: skip
        return code
    except Refused as exc:
        out(f"REFUSED: {exc}")
        return 2
    except LinkError as exc:
        out(f"LINK: {exc}")
        return 3
    except StepError as exc:
        out(f"STOPPED: {exc}")
        return 5
    except (CalibrationMismatch, CalibrationError) as exc:
        out(f"CALIBRATION FILE: {exc}")
        return 6


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "unknown"


def _bundle(args: argparse.Namespace, cal_path: Path, out: Callable[[str], None]) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(args.out or f"robotctl-bundle-{stamp}.zip")
    sources = default_sources(cal_path) + [
        (Path(p), f"extra/{i}") for i, p in enumerate(args.include)
    ]
    included, skipped = make_bundle(target, sources, {"robotctl": __version__})
    out(f"bundle {target}: {len(included)} files, {len(skipped)} skipped (see skipped.txt)")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(run(argv))
    except KeyboardInterrupt:
        print("Ctrl-C: STOP + MODE SAFE were sent by the step's safety frame")
        return 130
