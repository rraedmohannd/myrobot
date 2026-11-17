"""distance-cal, pid-step, square-test (BRINGUP.md sections 6 and 8)."""

import argparse
import asyncio
import contextlib
import csv
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from scr_serial import DriveMode, Message, Telemetry

from robotctl.operator import Refused
from robotctl.session import (
    MAX_TEST_DUTY,
    Session,
    StepError,
    enc_delta,
    mean,
    rnd,
    stdev,
    telemetry,
)
from robotctl.steps.motors import TRACKS_OFF

FLOOR_3M = "3 m clear in front of the robot"
FLOOR_SQUARE = "floor clear for the square"


def _ticks(deltas: tuple[int, int, int, int]) -> tuple[float, list[str]]:
    """Mean forward ticks of the four encoders, with warnings about ones that disagree."""
    warn: list[str] = []
    m = mean([abs(d) for d in deltas])
    for name, d in zip(("FL", "RL", "FR", "RR"), deltas, strict=True):
        if d <= 0:
            warn.append(f"{name} counted {d:+d} going forward (run encoder-check)")
        elif m and abs(d - m) / m > 0.1:
            warn.append(f"{name} differs from the mean by more than 10 % (slip?)")
    return m, warn


async def _enter_or_secs(s: Session, secs: float | None) -> "asyncio.Future[str] | None":
    if secs is not None or not s.op.interactive:
        return None
    return asyncio.ensure_future(s.op.line("  ...press Enter to STOP "))


# ------------------------------------------------------------------ distance-cal
async def run_distance_cal(s: Session, args: argparse.Namespace) -> int:
    runs = int(args.runs)
    if not 1 <= runs <= 20:
        raise Refused("--runs 1..20")
    measured = [float(x) for x in args.measured.split(",")] if args.measured else []
    duty = float(args.duty)
    if args.drive and not 0 < duty <= MAX_TEST_DUTY:
        raise Refused(f"--duty must be in (0, {MAX_TEST_DUTY}]")
    s.say(
        "distance-cal: lay a 3 m tape on a flat floor, a start mark at 0. The robot's front edge\n"
        "  (or a pointer taped to it) starts on 0. "
        + ("It drives straight in MODE TEST at a low duty; press Enter to stop near 3 m."
           if args.drive else "PUSH it straight along the tape by hand to about 3 m.")
        + "\n  Then read where the same edge stopped, in metres (e.g. 2.987)."
    )  # fmt: skip
    rows: list[dict[str, Any]] = []
    ctx = (
        s.driven_test(FLOOR_3M, "The robot will drive forward by itself.", duty)
        if args.drive else contextlib.nullcontext()
    )  # fmt: skip
    async with ctx:
        for run in range(runs):
            if run:
                await s.wait_operator(
                    f"Put the robot back on the start mark for run {run + 1}, then press Enter.",
                    label="reposition",
                )  # fmt: skip
            if args.drive:
                s.say(f"Run {run + 1}/{runs}: driving at duty {duty:g}")
                enter = await _enter_or_secs(s, args.drive_secs)
                limit = float(args.drive_secs) if args.drive_secs else float(args.max_secs)
                try:
                    msgs = await s.drive_for(
                        duty, duty, limit,
                        stop_when=(lambda _m, e=enter: e.done()) if enter else None,
                    )  # fmt: skip
                finally:
                    if enter is not None and not enter.done():
                        enter.cancel()
            else:
                msgs = await s.wait_operator(
                    f"Run {run + 1}/{runs}: push the robot along the tape to about 3 m, then "
                    "press Enter.", label="push-forward",
                )  # fmt: skip
            deltas = enc_delta(msgs)
            ticks, warn = _ticks(deltas)
            for w in warn:
                s.say(f"  warning: {w}")
            if ticks < 100:
                raise StepError(f"only {ticks:.0f} ticks: did the encoders count? (encoder-check)")
            dist = await s.op.number(
                f"Run {run + 1}: distance on the tape", measured[run] if run < len(measured)
                else None, 0.5, 5.0, "m",
            )  # fmt: skip
            mpt = dist / ticks
            rows.append({"run": run + 1, "deltas": list(deltas), "ticks_mean": ticks,
                         "tape_m": dist, "m_per_tick": mpt, "warnings": warn})  # fmt: skip
            s.say(f"  run {run + 1}: {ticks:.0f} ticks over {dist:.3f} m -> {mpt:.4e} m/tick")
    values = [r["m_per_tick"] for r in rows]
    m, sd = mean(values), stdev(values)
    spread = max(values) - min(values)
    s.say(f"METRES_PER_TICK mean {m:.5e}, spread {spread:.2e} ({spread / m * 100:.2f} %), "
          f"sd {sd:.2e}")  # fmt: skip
    if spread / m > 0.02:
        s.say("  spread above 2 %: check the tape, the floor and slip; repeat or stop and ask.")
    await s.propose(
        "distance-cal", {"METRES_PER_TICK": m},
        method=f"distance-cal: tape, {runs} runs, " + ("driven" if args.drive else "pushed"),
        raw={"runs": rows, "mean": m, "spread": spread, "sd": sd},
    )  # fmt: skip
    return 0


# ------------------------------------------------------------------ pid-step
GAINS = ("PID_KP", "PID_KI", "PID_KD", "PID_KFF")


def speeds(tel: list[Telemetry], mpt: float, window: int = 5) -> list[tuple[float, float, float]]:
    """(t s, left m/s, right m/s) from encoder differences over `window` samples."""
    out: list[tuple[float, float, float]] = []
    if not tel:
        return out
    t0 = tel[0].t_ms
    for i in range(window, len(tel)):
        a, b = tel[i - window], tel[i]
        dt = ((b.t_ms - a.t_ms) & 0xFFFFFFFF) / 1000
        if dt <= 0:
            continue
        e = [b.encoders[k] - a.encoders[k] for k in range(4)]
        left = (e[0] + e[1]) / 2 * mpt / dt
        right = (e[2] + e[3]) / 2 * mpt / dt
        out.append((((b.t_ms - t0) & 0xFFFFFFFF) / 1000, left, right))
    return out


def step_metrics(series: list[tuple[float, float]], target: float, t_step: float) -> dict[str, Any]:
    """Rise time 10-90 %, overshoot, steady-state error (last second), oscillation count."""
    after = [(t - t_step, v) for t, v in series if t >= t_step]
    if not after or target == 0:
        return {"ok": False}
    t_end = after[-1][0]
    steady = [v for t, v in after if t >= t_end - 1.0]
    t10 = next((t for t, v in after if v >= 0.1 * target), None)
    t90 = next((t for t, v in after if v >= 0.9 * target), None)
    peak = max(v for _, v in after)
    ss = mean(steady)
    crossings = sum(
        1 for (_, a), (_, b) in zip(after, after[1:], strict=False)
        if (a - target) * (b - target) < 0
    )  # fmt: skip
    return {
        "ok": True,
        "rise_s": None if t10 is None or t90 is None else t90 - t10,
        "overshoot": (peak - target) / target,
        "steady_mps": ss,
        "steady_error": (target - ss) / target,
        "crossings": crossings,
    }


def suggest(gains: dict[str, float], m: dict[str, Any]) -> tuple[dict[str, float], list[str]]:
    """Rules of thumb (not a tuning method with guarantees): the operator decides."""
    g = dict(gains)
    why: list[str] = []
    if not m.get("ok"):
        return g, ["no step response: nothing to suggest"]
    if g["PID_KP"] == 0 and g["PID_KFF"] == 0:
        return g, ["KP and KFF are 0: the loop cannot move. Try --kff 1.0 --kp 0.5 first."]
    if m["overshoot"] > 0.15 or m["crossings"] > 6:
        g["PID_KP"] *= 0.7
        g["PID_KI"] *= 0.7
        why.append("overshoot/oscillation: KP and KI x0.7")
    elif m["rise_s"] is None or m["rise_s"] > 0.8:
        g["PID_KP"] = g["PID_KP"] * 1.3 if g["PID_KP"] else 0.5
        why.append("slow rise: KP x1.3")
    if abs(m["steady_error"]) > 0.05:
        g["PID_KI"] = g["PID_KI"] * 1.5 if g["PID_KI"] else max(0.1, g["PID_KP"] * 0.5)
        why.append("steady-state error above 5 %: more KI")
    if not why:
        why.append("the response looks fine: keep the gains")
    return g, why


def write_csv(path: Path, rows: list[tuple[float, float, float]], target: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "target_mps", "left_mps", "right_mps"])
        for t, left, right in rows:
            w.writerow([f"{t:.3f}", f"{target:.3f}", f"{left:.4f}", f"{right:.4f}"])


def write_png(path: Path, rows: list[tuple[float, float, float]], target: float,
              title: str) -> bool:  # fmt: skip
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot([r[0] for r in rows], [r[1] for r in rows], label="left")
    ax.plot([r[0] for r in rows], [r[2] for r in rows], label="right")
    ax.axhline(target, linestyle="--", color="grey", label="target")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("track speed (m/s)")
    ax.set_title(title)
    ax.legend()
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return True


async def run_pid_step(s: Session, args: argparse.Namespace) -> int:
    speed, secs = float(args.speed), float(args.secs)
    if not 0.05 <= speed <= 0.6 or not 1.0 <= secs <= 10.0:
        raise Refused("--speed 0.05..0.6 m/s, --secs 1..10")
    params = await s.read_params()
    mpt = params["METRES_PER_TICK"].value
    if mpt <= 0:
        raise Refused("METRES_PER_TICK is 0: run distance-cal first (MODE DRIVE needs it)")
    for key in ("MAX_SPEED_MM_S", "RAMP_MM_S2"):
        if params[key].value <= 0:
            raise Refused(
                f"{key} is 0, so DRIVE targets stay 0. Choose a value with the supervisor and set "
                f"it with `robotctl param set {key} <value>` first."
            )
    before = {k: params[k].value for k in GAINS}
    trial = {k: getattr(args, k.lower().removeprefix("pid_")) for k in GAINS}
    trial = {k: float(v) for k, v in trial.items() if v is not None}
    s.say(f"pid-step: closed-loop step to {speed:g} m/s for {secs:g} s on the stand "
          "(MODE DRIVE, tracks in the air). Gains now: "
          + ", ".join(f"{k} {v:g}" for k, v in before.items()))  # fmt: skip
    await s.op.confirm(TRACKS_OFF, "Both tracks must be off the ground.")
    restore: dict[str, float] = {}
    accepted = False
    try:
        for key, value in trial.items():
            await s.set_param(key, value)
            restore[key] = before[key]
        async with s.mode(DriveMode.DRIVE, None):
            base = await s.dev.pump(0.5)
            msgs = await s.drive_for(speed, speed, secs)
        tel = telemetry(base + msgs)
        t_step = (
            ((telemetry(msgs)[0].t_ms - tel[0].t_ms) & 0xFFFFFFFF) / 1000
            if telemetry(msgs)
            else 0.0
        )
        rows = speeds(tel, mpt)
        avg = [(t, (left + right) / 2) for t, left, right in rows if t <= t_step + secs]
        metrics = step_metrics(avg, speed, t_step)
        gains_now = {**before, **trial}
        proposal, why = suggest(gains_now, metrics)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        csv_path = Path(args.out_dir or s.store.log_path.parent) / f"pid-step-{stamp}.csv"
        write_csv(csv_path, rows, speed)
        png = write_png(csv_path.with_suffix(".png"), rows, speed,
                        ("SIMULATED - " if s.sim else "") + f"pid-step {speed:g} m/s")  # fmt: skip
        s.say(f"  CSV {csv_path}" + (f", plot {csv_path.with_suffix('.png')}" if png
                                     else " (no matplotlib: CSV only)"))  # fmt: skip
        if metrics.get("ok"):
            s.say(
                f"  rise {metrics['rise_s'] if metrics['rise_s'] is None else round(metrics['rise_s'], 2)} s, "  # noqa: E501
                f"overshoot {metrics['overshoot'] * 100:.0f} %, steady {metrics['steady_mps']:.3f}"
                f" m/s (error {metrics['steady_error'] * 100:.1f} %), crossings "
                f"{metrics['crossings']}"
            )
        for w in why:
            s.say(f"  suggestion (rule of thumb): {w}")
        raw = {"speed": speed, "secs": secs, "gains_tested": gains_now, "metrics": metrics,
               "csv": str(csv_path), "why": why}  # fmt: skip
        proposal = {k: rnd(min(max(v, s.table.by_name(k).min), s.table.by_name(k).max))
                    for k, v in proposal.items()}  # fmt: skip
        accepted = await s.propose("pid-step", proposal, raw=raw,
                                   method=f"pid-step {speed:g} m/s on the stand")  # fmt: skip
    finally:
        if not accepted:
            for key, value in restore.items():
                with contextlib.suppress(Exception):
                    await s.set_param(key, value)
    return 0


# ------------------------------------------------------------------ square-test
def dead_reckon(tel: list[Telemetry], mpt: float, bias: float) -> tuple[float, float, float]:
    x = y = th = 0.0
    for a, b in zip(tel, tel[1:], strict=False):
        dt = ((b.t_ms - a.t_ms) & 0xFFFFFFFF) / 1000
        if not 0 < dt < 0.5:
            continue
        e = [b.encoders[k] - a.encoders[k] for k in range(4)]
        d = ((e[0] + e[1]) / 2 + (e[2] + e[3]) / 2) / 2 * mpt
        th_mid = th + (a.gyro_z - bias) * dt / 2
        x += d * math.cos(th_mid)
        y += d * math.sin(th_mid)
        th += (a.gyro_z - bias) * dt
    return x, y, th


async def _straight(s: Session, side: float, speed: float, mpt: float) -> list[Message]:
    def done(msgs: list[Message]) -> bool:
        ticks = mean([abs(d) for d in enc_delta(msgs)])
        return ticks * mpt >= side - speed * 0.15

    return await s.drive_for(speed, speed, side / speed * 2 + 2, stop_when=done)


async def _turn(s: Session, sign: int, bias: float, turn_speed: float) -> list[Message]:
    def done(msgs: list[Message]) -> bool:
        tel = telemetry(msgs)
        ang = 0.0
        for a, b in zip(tel, tel[1:], strict=False):
            dt = ((b.t_ms - a.t_ms) & 0xFFFFFFFF) / 1000
            if 0 < dt < 0.5:
                ang += (a.gyro_z - bias) * dt
        return abs(ang) >= math.radians(90 - 4)

    return await s.drive_for(-sign * turn_speed, sign * turn_speed, 8.0, stop_when=done)


async def run_square_test(s: Session, args: argparse.Namespace) -> int:
    side, speed = float(args.side), float(args.speed)
    if not 0.5 <= side <= 3.0 or not 0.05 <= speed <= 0.4:
        raise Refused("--side 0.5..3 m, --speed 0.05..0.4 m/s")
    params = await s.read_params()
    mpt = params["METRES_PER_TICK"].value
    if mpt <= 0:
        raise Refused("METRES_PER_TICK is 0: run distance-cal first")
    measured = [float(x) for x in args.measured.split(",")] if args.measured else []
    s.say(
        f"square-test: a {side:g} m x {side:g} m square, clockwise then counter-clockwise, in\n"
        "  MODE DRIVE using the encoders and the gyro. Mark the start (front edge and side) with\n"
        "  tape. After each square, measure with the tape how far the same point stopped from\n"
        "  the start mark."
    )
    results: dict[str, Any] = {}
    async with s.mode(DriveMode.DRIVE, FLOOR_SQUARE,
                      f"The robot drives a {side:g} m square by itself."):  # fmt: skip
        for i, direction in enumerate(("cw", "ccw")):
            if i:
                await s.wait_operator("Put the robot back on the start mark, then press Enter.",
                                      label="reposition")  # fmt: skip
            still = telemetry(await s.dev.pump(1.0))
            bias = mean([t.gyro_z for t in still]) if still else 0.0
            sign = -1 if direction == "cw" else 1
            msgs: list[Message] = []
            for _ in range(4):
                msgs += await _straight(s, side, speed, mpt)
                msgs += await _turn(s, sign, bias, 0.1)
            x, y, th = dead_reckon(telemetry(msgs), mpt, bias)
            odo = math.hypot(x, y)
            tape = await s.op.number(
                f"{direction.upper()}: distance from the start mark (tape)",
                measured[i] if i < len(measured) else None, 0.0, 5.0, "m",
            )  # fmt: skip
            results[direction] = {"tape_closure_m": tape, "odometry_closure_m": odo,
                                  "odometry_end": [x, y, math.degrees(th)]}  # fmt: skip
            s.say(f"  {direction}: tape closure {tape:.3f} m, odometry says {odo:.3f} m")
    s.record(
        "square-test",
        {"square_test_closure_m": {d: r["tape_closure_m"] for d, r in results.items()}},
        method=f"square-test {side:g} m, {speed:g} m/s, tape closure CW and CCW",
        raw={"side_m": side, "speed": speed, "results": results},
    )  # fmt: skip
    return 0
