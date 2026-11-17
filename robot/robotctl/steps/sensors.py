"""ultrasonic-check, battery-cal, gyro-cal, rotate-test (BRINGUP.md sections 5 and 6)."""

import argparse
import math
import statistics
from typing import Any

from scr_serial import (
    US_ABSENT,
    US_NO_ECHO,
    AckStatus,
    BringupKind,
    CalibrateGyro,
    DriveMode,
    Event,
    EventCode,
    Flags,
    ImuRaw,
    Message,
    UsRaw,
)

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
from robotctl.steps import CHECK_FAILED
from robotctl.steps.basic import US_NAMES

US_TOLERANCE_MM = 20.0
US_TOLERANCE_REL = 0.05
FLOOR_CLEAR = "robot on the floor and 1 m clear around it"


# ------------------------------------------------------------------ ultrasonic-check
async def run_ultrasonic_check(s: Session, args: argparse.Namespace) -> int:
    distance = float(args.distance)
    if not 0.05 <= distance <= 4.0:
        raise Refused(f"--distance {distance}: must be 0.05..4.0 m")
    sensors = [int(x) for x in args.sensors.split(",")] if args.sensors else [0, 1, 2, 3]
    if any(i not in range(4) for i in sensors):
        raise Refused("--sensors: indices 0..3 (front-left, front-centre, front-right, rear)")
    s.say(
        f"ultrasonic-check: put a flat box {distance:.2f} m in front of "
        + ", ".join(US_NAMES[i] for i in sensors)
        + " (measure from the sensor's front face with the tape). Nothing moves."
    )
    secs = float(args.secs)
    rounds: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    source = "US_RAW"
    async with s.mode(DriveMode.TEST, None):
        try:
            await s.bringup(BringupKind.US_RAW)
            msgs = await s.dev.pump(secs)
            rounds = [(m.dist_mm, m.misses) for m in msgs if isinstance(m, UsRaw)]
        except StepError as exc:
            s.say(f"  US_RAW stream not available ({exc}); using telemetry instead")
        if not rounds:
            source = "TELEMETRY"
            msgs = await s.dev.pump(secs)
            rounds = [(t.ultrasonic_mm, (0, 0, 0, 0)) for t in telemetry(msgs)]
    if not rounds:
        raise StepError("no ultrasonic data received")
    target = distance * 1000
    table: list[dict[str, Any]] = []
    failed = False
    s.say(f"{len(rounds)} rounds from {source}")
    s.say(f"  {'sensor':13s} {'median':>8s} {'error':>8s} {'valid':>6s} {'no-echo':>8s} "
          f"{'absent':>7s} {'misses':>6s}")  # fmt: skip
    for i in range(4):
        vals = [r[0][i] for r in rounds]
        valid = [v for v in vals if v not in (US_ABSENT, US_NO_ECHO)]
        med = statistics.median(valid) if valid else None
        err = (med - target) if med is not None else None
        row = {
            "sensor": US_NAMES[i], "median_mm": med, "error_mm": err, "valid": len(valid),
            "no_echo": vals.count(US_NO_ECHO), "absent": vals.count(US_ABSENT),
            "max_misses": max(r[1][i] for r in rounds), "checked": i in sensors,
            "readings_mm": vals[:200],
        }  # fmt: skip
        ok = True
        if i in sensors:
            tol = max(US_TOLERANCE_MM, US_TOLERANCE_REL * target)
            ok = err is not None and abs(err) <= tol and len(valid) >= 0.5 * len(vals)
            failed |= not ok
        row["ok"] = ok
        table.append(row)
        s.say(
            f"  {US_NAMES[i]:13s} {_mm(med):>8s} {_mm(err, signed=True):>8s} {len(valid):>6d} "
            f"{row['no_echo']:>8d} {row['absent']:>7d} {row['max_misses']:>6d}"
            + ("" if i not in sensors else "  OK" if ok else "  FAIL")
        )
    offsets = {US_NAMES[r_i]: table[r_i]["error_mm"] for r_i in sensors}
    s.record(
        "ultrasonic-check", {"ultrasonic_check_mm": offsets},
        method=f"ultrasonic-check: box at {distance:.3f} m, {len(rounds)} rounds ({source})",
        raw={"distance_m": distance, "table": table},
    )  # fmt: skip
    if failed:
        s.say("ultrasonic-check: FAIL. Check the wiring, the echo divider, and the box; "
              "stop and ask if a sensor stays wrong.")  # fmt: skip
        return CHECK_FAILED
    s.say("ultrasonic-check: every checked sensor within tolerance")
    return 0


def _mm(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "-"
    return f"{v:+.0f}" if signed else f"{v:.0f}"


# ------------------------------------------------------------------ battery-cal
async def run_battery_cal(s: Session, args: argparse.Namespace) -> int:
    meter = await s.op.number(
        "Battery voltage on the multimeter (at the INA219's V+ terminal)", args.multimeter,
        6.0, 14.0, "V",
    )  # fmt: skip
    s.say("battery-cal: the robot stays still; reading the INA219 for 2 s")
    msgs = await s.dev.pump(float(args.secs))
    tel = [t for t in telemetry(msgs) if t.flags & Flags.POWER_SENSOR_OK]
    if not tel:
        raise StepError("no INA219 reading (POWER_SENSOR_OK off): run diag, check I2C wiring")
    readings = [t.battery_mv / 1000 for t in tel]
    reading = mean(readings)
    current = await s.param("BATTERY_VOLTAGE_SCALE")
    new = current * meter / reading
    s.say(f"  INA219 {reading:.3f} V (scale {current:g}), multimeter {meter:.3f} V "
          f"-> BATTERY_VOLTAGE_SCALE {new:.4f}")  # fmt: skip
    raw = {"multimeter_v": meter, "ina219_mean_v": reading, "ina219_sd_v": stdev(readings),
           "samples": len(readings), "scale_before": current}  # fmt: skip
    await s.propose(
        "battery-cal", {"BATTERY_VOLTAGE_SCALE": new},
        method=f"battery-cal: multimeter {meter:.3f} V vs INA219 {reading:.3f} V", raw=raw,
    )  # fmt: skip
    return 0


# ------------------------------------------------------------------ gyro-cal
async def run_gyro_cal(s: Session, args: argparse.Namespace) -> int:
    s.say("gyro-cal: do not touch the robot or the table for the next few seconds.")
    secs = float(args.secs)
    before = [t.gyro_z for t in telemetry(await s.dev.pump(secs))]
    if not before:
        raise StepError("no telemetry")
    got = await s.dev.request(
        CalibrateGyro(),
        lambda m: (
            isinstance(m, Event) and m.code in (EventCode.GYRO_CAL_DONE, EventCode.GYRO_CAL_FAILED)
        ),
        timeout=8.0,
    )
    ack_bad = [m for m in got if getattr(m, "status", AckStatus.OK) != AckStatus.OK]
    if ack_bad:
        raise StepError(f"CALIBRATE_GYRO refused: {ack_bad[0].status.name}")  # type: ignore[attr-defined]
    ev = got[-1]
    assert isinstance(ev, Event)
    if ev.code == EventCode.GYRO_CAL_FAILED:
        s.log("gyro-cal", {"result": "failed: the robot moved"})
        raise StepError("GYRO_CAL_FAILED: the robot moved. Keep it still and run again.")
    bias = ev.arg / 10000  # 0.1 mrad/s
    after = [t.gyro_z for t in telemetry(await s.dev.pump(secs))]
    s.say(f"  bias from the ESP32 {bias:+.5f} rad/s; telemetry mean before {mean(before):+.5f}, "
          f"after {mean(after):+.5f} rad/s (noise sd {stdev(after):.5f})")  # fmt: skip
    drift_deg_min = math.degrees(abs(mean(after))) * 60
    s.say(f"  remaining drift about {drift_deg_min:.1f} deg/min")
    s.record(
        "gyro-cal", {"gyro_bias_rad_s": rnd(bias)},
        method=f"gyro-cal: CALIBRATE_GYRO, still for {secs:g} s before and after",
        raw={"event_arg": ev.arg, "mean_before": mean(before), "mean_after": mean(after),
             "sd_after": stdev(after), "samples": [len(before), len(after)],
             "drift_deg_per_min": drift_deg_min},
    )  # fmt: skip
    return 0


# ------------------------------------------------------------------ rotate-test
def integrate_z(msgs: list[Message], use_raw: bool, bias: float) -> float:
    """Integrated yaw (rad) from IMU_RAW (µrad/s, unscaled) or telemetry gyro_z (rad/s)."""
    samples: list[tuple[int, float]] = []
    for m in msgs:
        if use_raw and isinstance(m, ImuRaw):
            samples.append((m.t_ms, m.gyro_urad_s[2] / 1e6))
        elif not use_raw and m.__class__.__name__ == "Telemetry":
            samples.append((m.t_ms, m.gyro_z))  # type: ignore[attr-defined]
    total = 0.0
    for (t0, g0), (t1, _g1) in zip(samples, samples[1:], strict=False):
        dt = ((t1 - t0) & 0xFFFFFFFF) / 1000
        if 0 < dt < 0.5:
            total += (g0 - bias) * dt
    return total


def z_rates(msgs: list[Message], use_raw: bool) -> list[float]:
    if use_raw:
        return [m.gyro_urad_s[2] / 1e6 for m in msgs if isinstance(m, ImuRaw)]
    return [t.gyro_z for t in telemetry(msgs)]


async def run_rotate_test(s: Session, args: argparse.Namespace) -> int:
    turns, runs = int(args.turns), int(args.runs)
    if not 1 <= turns <= 5 or not 1 <= runs <= 20:
        raise Refused("--turns 1..5, --runs 1..20")
    driven = args.driven
    duty = float(args.duty)
    if driven and not 0 < duty <= MAX_TEST_DUTY:
        raise Refused(f"--duty must be in (0, {MAX_TEST_DUTY}]")
    measured = [float(x) for x in args.measured_deg.split(",")] if args.measured_deg else []
    params = await s.read_params()
    scale_now = params["GYRO_SCALE"].value
    mpt = params["METRES_PER_TICK"].value
    s.say(
        f"rotate-test: {runs} runs of {turns} turn(s) COUNTER-CLOCKWISE seen from above, "
        + ("driven in MODE TEST" if driven else "turned BY HAND on the spot")
        + ".\n  Put a tape mark on the floor along the robot's side; every run starts and ends "
        "on the mark."
    )
    results: list[dict[str, Any]] = []
    use_raw = True
    ctx = (
        s.driven_test(FLOOR_CLEAR, "The robot will turn on the spot by itself.", duty)
        if driven else s.mode(DriveMode.TEST, None)
    )  # fmt: skip
    async with ctx:
        try:
            await s.bringup(BringupKind.IMU_RAW, 100)
        except StepError as exc:
            use_raw = False
            s.say(f"  IMU_RAW not available ({exc}); integrating telemetry gyro_z instead")
        for run in range(runs):
            still = await s.dev.pump(1.0)
            rates = z_rates(still, use_raw)
            if not rates:
                raise StepError("no gyro data")
            bias = mean(rates)
            if driven:
                target = 2 * math.pi * turns

                def reached(
                    msgs: list[Message], target: float = target, bias: float = bias
                ) -> bool:
                    return abs(integrate_z(msgs, use_raw, bias)) >= target * 0.98

                msgs = await s.drive_for(-duty, duty, float(args.max_secs), stop_when=reached)
            else:
                msgs = await s.wait_operator(
                    f"Run {run + 1}/{runs}: turn the robot {turns} full turn(s) counter-clockwise "
                    "back to the mark, then press Enter.", label="rotate-ccw",
                )  # fmt: skip
            integ = integrate_z(msgs, use_raw, bias)
            true_deg = (
                measured[run]
                if run < len(measured)
                else (
                    360.0 * turns
                    if not driven
                    else await s.op.number(
                        f"Run {run + 1}: angle really turned (read the mark)", None, 30, 1900, "deg"
                    )
                )
            )
            if abs(integ) < 1e-3:
                raise StepError("the gyro saw no rotation: IMU axis or wiring")
            ratio = math.radians(true_deg) / integ
            row: dict[str, Any] = {
                "run": run + 1,
                "gyro_deg": math.degrees(integ),
                "true_deg": true_deg,
                "ratio": ratio,
                "bias_rad_s": bias,
            }
            d = enc_delta(msgs)
            if mpt > 0 and any(d):
                left = (d[0] + d[1]) / 2 * mpt
                right = (d[2] + d[3]) / 2 * mpt
                row["enc_left_m"], row["enc_right_m"] = left, right
                if driven:
                    row["track_width_m"] = (right - left) / math.radians(true_deg)
            results.append(row)
            s.say(f"  run {run + 1}: gyro {math.degrees(integ):.1f} deg, true {true_deg:.1f} deg, "
                  f"ratio {ratio:.4f}"
                  + (f", effective track width {row['track_width_m']:.4f} m"
                     if "track_width_m" in row else ""))  # fmt: skip
    ratios = [r["ratio"] for r in results]
    m, sd = mean(ratios), stdev(ratios)
    new_scale = m if use_raw else scale_now * m
    s.say(f"ratio mean {m:.4f}, sd {sd:.4f} -> GYRO_SCALE {new_scale:.4f}"
          + ("" if use_raw else f" (telemetry already scaled by {scale_now:g})"))  # fmt: skip
    if sd > 0.02:
        s.say("  spread above 0.02: the runs disagree. Repeat more carefully or stop and ask.")
    pi: dict[str, Any] = {}
    widths = [r["track_width_m"] for r in results if "track_width_m" in r]
    if widths:
        pi["track_width_m"] = rnd(mean(widths), 5)
    raw = {"turns": turns, "driven": driven, "source": "IMU_RAW" if use_raw else "TELEMETRY",
           "runs": results, "mean_ratio": m, "sd_ratio": sd, "scale_before": scale_now}  # fmt: skip
    await s.propose(
        "rotate-test", {"GYRO_SCALE": new_scale}, pi_values=pi, raw=raw,
        method=f"rotate-test: {runs} x {turns} turn(s) "
        + ("driven" if driven else "by hand") + f", ratio sd {sd:.4f}",
    )  # fmt: skip
    return 0
