"""servo-cal and hfov-cal (BRINGUP.md section 6)."""

import argparse
from typing import Any

from scr_serial import Ack, AckStatus, Bringup, BringupKind, DriveMode, Event, EventCode, Servo

from robotctl.operator import Refused
from robotctl.session import Session, StepError, mean
from robotctl.steps import CHECK_FAILED

CAMERA_CLEAR = "camera mount clear"
LIMIT_MARGIN_DEG = 3.0
MAX_JOG = 50
JOG_HELP = (
    "  keys: +N / -N jog the pulse by N us (e.g. +20, -50), c = this is straight ahead (centre),\n"
    "        a = this points at the reference mark, l = left limit, r = right limit, q = done"
)


async def _jog(s: Session, step: int) -> int:
    """Move the servo pulse by `step` µs (in chunks of at most 50); returns the pulse."""
    pulse = -1
    remaining = step
    while remaining:
        chunk = max(-MAX_JOG, min(MAX_JOG, remaining))
        remaining -= chunk
        got = await s.dev.request(
            Bringup(BringupKind.SERVO_JOG, chunk),
            lambda m: (isinstance(m, Event) and m.code == EventCode.SERVO_PULSE)
            or (isinstance(m, Ack) and m.acked_type == int(Bringup.TYPE)
                and m.status != AckStatus.OK),
            timeout=1.5,
        )  # fmt: skip
        last = got[-1]
        if isinstance(last, Ack):
            raise StepError(f"SERVO_JOG refused: {last.status.name}")
        assert isinstance(last, Event)
        pulse = last.arg
    return pulse


async def run_servo_cal(s: Session, args: argparse.Namespace) -> int:
    ref = float(args.ref_deg)
    if not 10 <= ref <= 80:
        raise Refused("--ref-deg must be 10..80 (a mark that far to the LEFT of straight ahead)")
    s.say(
        "servo-cal: the pan servo moves in small steps (BRINGUP SERVO_JOG, MODE TEST, the\n"
        "  tracks are not driven). Before: tape a mark straight ahead of the camera and one\n"
        f"  {ref:g} deg to the LEFT (protractor at the servo axis). Jog the pulse until the\n"
        "  camera points at each, and find the limits where the mount or cable starts to bind.\n"
        "  Never jog past a point where the servo buzzes or strains."
    )
    s.say(JOG_HELP)
    marks: dict[str, int] = {}
    keys = args.keys.split() if args.keys else None
    async with s.mode(DriveMode.TEST, CAMERA_CLEAR, "The camera will turn."):
        pulse: int | None = None  # no movement until the first key
        i = 0
        while True:
            if keys is not None:
                if i >= len(keys):
                    break
                key = keys[i]
                i += 1
            else:
                key = (await s.op.line(f"jog [{pulse if pulse is not None else '?'} us]> ")).strip()
            if not key:
                continue
            if key[0] in "+-":
                try:
                    step = int(key)
                except ValueError:
                    s.say("  not a number")
                    continue
                if abs(step) > 2000:
                    s.say("  at most 2000 us per key")
                    continue
                pulse = await _jog(s, step)
                s.say(f"  pulse {pulse} us")
            elif key in ("c", "a", "l", "r"):
                if pulse is None:
                    pulse = await _jog_probe(s)
                marks[key] = pulse
                s.say(f"  marked {key} = {pulse} us")
            elif key == "q":
                break
            else:
                s.say(JOG_HELP)
    missing = [k for k in "calr" if k not in marks]
    if missing:
        raise Refused(f"servo-cal needs the marks c, a, l, r; missing: {', '.join(missing)}")
    pc, pa, pl, pr = marks["c"], marks["a"], marks["l"], marks["r"]
    if pa == pc:
        raise Refused("the reference mark has the same pulse as the centre")
    us_deg = abs(pa - pc) / ref
    left_up = pa > pc  # a higher pulse turns the camera left
    invert = 0 if left_up else 1
    if (pl - pc > 0) != left_up or (pr - pc > 0) == left_up:
        raise Refused("the left limit must be on the reference mark's side and the right limit "
                      "on the other side of the centre: jog again")  # fmt: skip
    left_deg = abs(pl - pc) / us_deg - LIMIT_MARGIN_DEG
    right_deg = abs(pr - pc) / us_deg - LIMIT_MARGIN_DEG
    if left_deg <= 0 or right_deg <= 0:
        raise Refused("a limit is closer than 3 deg to the centre: jog again")
    params = await s.read_params()
    slew = params["SERVO_SLEW_CDEG_PER_S"].value or float(args.slew_deg_s) * 100
    proposal = {
        "SERVO_CENTER_PULSE_US": float(pc), "SERVO_US_PER_DEG": us_deg, "SERVO_INVERT": invert,
        "SERVO_MAX_CDEG": min(9000, int(left_deg * 100)),
        "SERVO_MIN_CDEG": -min(9000, int(right_deg * 100)),
        "SERVO_PULSE_MIN_US": min(pl, pr), "SERVO_PULSE_MAX_US": max(pl, pr),
        "SERVO_SLEW_CDEG_PER_S": slew,
    }  # fmt: skip
    s.say(
        f"  {us_deg:.2f} us/deg, left = {'higher' if left_up else 'lower'} pulse, usable "
        f"{left_deg:.1f} deg left / {right_deg:.1f} deg right (limits minus {LIMIT_MARGIN_DEG:g})"
    )
    if not params["SERVO_SLEW_CDEG_PER_S"].value:
        s.say(f"  SERVO_SLEW_CDEG_PER_S: {slew:g} is a CHOICE (--slew-deg-s), not a measurement")
    raw = {"marks_us": marks, "ref_deg": ref, "margin_deg": LIMIT_MARGIN_DEG}
    await s.propose(
        "servo-cal", proposal, raw=raw,
        method=f"servo-cal: jogged to centre, {ref:g} deg left mark and both limits",
    )  # fmt: skip
    return 0


async def _jog_probe(s: Session) -> int:
    """The current pulse without moving: +1 then -1 µs."""
    await _jog(s, 1)
    return await _jog(s, -1)


# ------------------------------------------------------------------ hfov-cal
def _face_cx(vision: dict[str, Any] | None) -> float | None:
    if not vision:
        return None
    faces = vision.get("faces") or []
    boxes = [f.get("box") for f in faces if isinstance(f, dict) and f.get("box")]
    if not boxes:
        return None
    x, _y, w, h = max(boxes, key=lambda b: b[2] * b[3])
    return float(x) + float(w) / 2


def fit_hfov(points: list[tuple[float, float]]) -> float:
    """Least-squares slope of image x (0..1) against pan angle; hfov = 1 / |slope|."""
    xs = [a for a, _ in points]
    ys = [c for _, c in points]
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        raise StepError("no spread of angles")
    slope = sum((x - mx) * (y - my) for x, y in points) / sxx
    if abs(slope) < 1e-4:
        raise StepError("the face did not move in the picture: is visiond running?")
    return 1.0 / abs(slope)


async def run_hfov_cal(s: Session, args: argparse.Namespace) -> int:
    params = await s.read_params()
    if not params["SERVO_US_PER_DEG"].value:
        raise Refused("SERVO_US_PER_DEG is 0: run servo-cal first")
    s.say(
        "hfov-cal: put a printed face (or a person) 1.5-2 m straight ahead of the camera, at\n"
        "  camera height. The camera pans; the robot does not drive."
    )
    method = ""
    raw: dict[str, Any] = {}
    if args.edge_angles:
        left, right = (float(v) for v in args.edge_angles.split(","))
        hfov = abs(left - right)
        method, raw = "hfov-cal: operator-typed edge angles", {"left": left, "right": right}
    else:
        await s.op.confirm(CAMERA_CLEAR, "The camera will pan.")
        span = float(args.sweep_deg)
        if s.dev.vision is None:
            await s.dev.pump(1.0)
        points: list[tuple[float, float]] = []
        try:
            if s.dev.vision is not None:
                angle = -span
                while angle <= span + 1e-6:
                    await s.dev.send(Servo(angle))
                    await s.dev.pump(0.8)
                    cx = _face_cx(s.dev.vision)
                    if cx is not None and 0.02 < cx < 0.98:
                        points.append((angle, cx))
                    angle += 2.0
        finally:
            await s.dev.send(Servo(0.0))
        if len(points) >= 4:
            hfov = fit_hfov(points)
            method = f"hfov-cal: servo sweep +-{span:g} deg, face position from visiond"
            raw = {"points": points}
        elif s.op.interactive:
            s.say(
                "  No face data from visiond through robotd. By eye instead: watch the camera "
                "picture,\n  pan with +N / -N (degrees), type l when the face just leaves the "
                "picture on one side,\n  r on the other, q to end."
            )
            left, right = await _pan_by_eye(s)
            hfov = abs(left - right)
            method = "hfov-cal: edge angles found by eye"
            raw = {"left": left, "right": right}
        else:
            s.say("  No face data from visiond: run interactively, or give --edge-angles L,R")
            s.log("hfov-cal", {"result": "no vision data", "points": points})
            return CHECK_FAILED
    if not 20 <= hfov <= 140:
        raise Refused(f"field of view {hfov:.1f} deg is not plausible for a webcam: stop and ask")
    s.say(f"  horizontal field of view {hfov:.1f} deg")
    await s.propose("hfov-cal", {}, pi_values={"camera_hfov_deg": round(hfov, 1)},
                    method=method, raw=raw, firmware=False)  # fmt: skip
    return 0


async def _pan_by_eye(s: Session) -> tuple[float, float]:
    angle = 0.0
    marks: dict[str, float] = {}
    try:
        while True:
            key = (await s.op.line(f"pan [{angle:+.1f} deg]> ")).strip()
            if key[:1] in "+-" and key[1:].replace(".", "", 1).isdigit():
                angle = max(-90.0, min(90.0, angle + float(key)))
                await s.dev.send(Servo(angle))
                await s.dev.pump(0.3)
            elif key in ("l", "r"):
                marks[key] = angle
            elif key == "q":
                break
    finally:
        await s.dev.send(Servo(0.0))
    if len(marks) < 2:
        raise Refused("hfov-cal needs both marks l and r")
    return marks["l"], marks["r"]
