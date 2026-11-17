"""status, diag, param: look without moving anything."""

import argparse
from typing import Any

from scr_serial import (
    US_ABSENT,
    US_NO_ECHO,
    DiagFlags,
    DiagReport,
    DiagRequest,
    Flags,
    Sensors,
    Telemetry,
)

from robotctl.operator import Refused
from robotctl.session import Session, StepError
from robotctl.steps import CHECK_FAILED
from robotd import calibration

IMU_ADDRS = (0x68, 0x69)  # MPU-6050 / ICM: AD0 low / high
INA219_ADDR = 0x40
EXPANDER_DEFAULT = 0x20  # MCP23017, docs/PIN_PLAN.md option B (recommended), A2..A0 low
EXPANDER_RANGE = range(0x20, 0x28)
FAULT_FLAGS = (
    Flags.WATCHDOG_STOP, Flags.OBSTACLE_STOP, Flags.REAR_OBSTACLE_STOP, Flags.LOW_BATTERY,
    Flags.ENCODER_FAULT, Flags.ULTRASONIC_FAULT, Flags.VOLTAGE_COMP_FALLBACK,
)  # fmt: skip
ENC_NAMES = ("FL", "RL", "FR", "RR")
US_NAMES = ("front-left", "front-centre", "front-right", "rear")


def us_text(mm: int) -> str:
    if mm == US_NO_ECHO:
        return "no echo"
    if mm == US_ABSENT:
        return "ABSENT"
    return f"{mm} mm"


def flag_names(flags: Flags) -> list[str]:
    return [f.name or "" for f in Flags if f in flags and f.name]


def telemetry_lines(t: Telemetry) -> list[str]:
    faults = [f.name for f in FAULT_FLAGS if t.flags & f]
    return [
        f"  t_ms {t.t_ms}  flags: {', '.join(flag_names(t.flags)) or '-'}",
        "  encoders: " + "  ".join(f"{n} {v}" for n, v in zip(ENC_NAMES, t.encoders, strict=True)),
        "  ultrasonic: "
        + "  ".join(f"{n} {us_text(v)}" for n, v in zip(US_NAMES, t.ultrasonic_mm, strict=True)),
        f"  gyro_z {t.gyro_z:+.4f} rad/s   accel {t.accel_mg} mg",
        f"  battery {t.battery_mv / 1000:.2f} V  {t.battery_ma} mA"
        + ("" if t.flags & Flags.POWER_SENSOR_OK else "  (no INA219 reading)"),
        f"  param_hash {t.param_hash:08x}",
        f"  faults: {', '.join(n or '' for n in faults) or 'none'}",
    ]


def diag_lines(d: DiagReport) -> list[str]:
    return [
        f"  firmware {d.version_text}  git {d.git_text}  build {d.build_env.name}  "
        f"driver {'VNH' if d.driver_type == 1 else 'L298N'}",
        f"  flags: {', '.join(f.name or '' for f in DiagFlags if f in d.diag_flags) or '-'}",
        f"  param_hash {d.param_hash:08x}  reset {d.reset_reason.name}  uptime {d.uptime_ms} ms",
        f"  loop max {d.loop_period_max_us} us  jitter {d.loop_jitter_us} us  "
        f"free heap {d.free_heap} B",
        f"  I2C found: {', '.join(f'0x{a:02X}' for a in d.i2c_addresses) or 'nothing'}",
        f"  sensors: {', '.join(f.name or '' for f in Sensors if f in d.sensors) or 'none'}",
    ]


async def request_diag(s: Session) -> DiagReport:
    got = await s.dev.request(DiagRequest(), lambda m: isinstance(m, DiagReport), timeout=2.0)
    d = got[-1]
    assert isinstance(d, DiagReport)
    return d


def expected_hash(s: Session) -> tuple[int | None, str]:
    cal = s.store.load()
    if cal is None:
        return None, "no calibration file"
    return calibration.expected_hash(cal, s.table), f"calibration file {s.store.path}"


async def run_status(s: Session, args: argparse.Namespace) -> int:
    t = await s.dev.wait_telemetry()
    try:
        d: DiagReport | None = await request_diag(s)
    except Exception:  # noqa: BLE001 - an old firmware without DIAG still shows telemetry
        d = None
    rounds = max(1, int(args.secs * 2)) if args.secs else 1
    for i in range(rounds):
        if i:
            await s.dev.pump(0.5)
            t = s.dev.last_telemetry or t
        if s.dev.ctl_status:
            st = s.dev.ctl_status
            s.say(f"robotd: state {st.get('state', '?')}  mode {st.get('mode', '?')}")
        s.say("\n".join(telemetry_lines(t)))
    if d is not None:
        s.say(f"firmware {d.version_text} git {d.git_text} ({d.build_env.name})")
    exp, source = expected_hash(s)
    if exp is not None:
        same = exp == t.param_hash
        s.say(
            f"parameter hash: ESP32 {t.param_hash:08x}, expected {exp:08x} from {source}: "
            + ("same" if same else "DIFFERENT (robotd pushes the file at its next connection)")
        )
    s.log("status", {"telemetry": _tel_dict(t), "firmware": d.version_text if d else None})
    return 0


def _tel_dict(t: Telemetry) -> dict[str, Any]:
    return {
        "t_ms": t.t_ms, "encoders": list(t.encoders), "gyro_z": t.gyro_z,
        "ultrasonic_mm": list(t.ultrasonic_mm), "battery_mv": t.battery_mv,
        "battery_ma": t.battery_ma, "flags": flag_names(t.flags),
        "param_hash": f"{t.param_hash:08x}",
    }  # fmt: skip


def expander_choice(s: Session, args: argparse.Namespace) -> int | None:
    """Option B (default): MCP23017 at 0x20. --no-expander or the calibration key
    `expander_addr` = 0 means option A."""
    if args.no_expander:
        return None
    if args.expander is not None:
        value = int(args.expander, 0)
        if value not in EXPANDER_RANGE:
            raise Refused(f"--expander 0x{value:02X}: an MCP23017 answers at 0x20..0x27")
        return value
    cal = s.store.load()
    stored = cal.value("expander_addr") if cal is not None else None
    if stored is not None:
        return int(stored) or None
    return EXPANDER_DEFAULT


async def run_diag(s: Session, args: argparse.Namespace) -> int:
    d = await request_diag(s)
    s.say("DIAG_REPORT")
    s.say("\n".join(diag_lines(d)))
    expander = expander_choice(s, args)
    found = set(d.i2c_addresses)
    rows: list[tuple[str, str, bool]] = []
    imu = [a for a in IMU_ADDRS if a in found]
    rows.append(
        ("IMU (MPU-6050) 0x68/0x69", ", ".join(f"0x{a:02X}" for a in imu) or "-", bool(imu))
    )
    rows.append(("INA219 0x40", "0x40" if INA219_ADDR in found else "-", INA219_ADDR in found))
    expected = {*IMU_ADDRS, INA219_ADDR}
    if expander is not None:
        expected.add(expander)
        rows.append((
            f"I2C expander (MCP23017, option B) 0x{expander:02X}",
            f"0x{expander:02X}" if expander in found else "-", expander in found,
        ))  # fmt: skip
    s.say("I2C scan against the expected devices"
          + (" (option A: no expander)" if expander is None else ""))  # fmt: skip
    for name, seen, ok in rows:
        s.say(f"  {'OK     ' if ok else 'MISSING'} {name:44s} found: {seen}")
    unexpected = sorted(found - expected)
    if unexpected:
        s.say("  UNEXPECTED: " + ", ".join(f"0x{a:02X}" for a in unexpected)
              + " (another device, or an address pin set differently: stop and ask)")  # fmt: skip
    problems = [name for name, _, ok in rows if not ok]
    if d.diag_flags & DiagFlags.CI_PINS_DO_NOT_FLASH:
        problems.append("CI pin build (not flashable): flash the real firmware")
        s.say("  STOP: this is the CI pin build, it never drives a pin. Flash the real build.")
    if not d.diag_flags & DiagFlags.PINS_CONFIRMED:
        problems.append("pins not confirmed")
        s.say("  PINS_CONFIRMED is off: the firmware was built without pins_generated.h")
    exp, source = expected_hash(s)
    if exp is not None:
        s.say(f"  parameter hash {d.param_hash:08x}, expected {exp:08x} ({source})"
              + ("" if exp == d.param_hash else "  DIFFERENT"))  # fmt: skip
    result = {
        "firmware": d.version_text, "git": d.git_text, "build_env": d.build_env.name,
        "reset_reason": d.reset_reason.name, "i2c": [f"0x{a:02X}" for a in d.i2c_addresses],
        "expander_expected": None if expander is None else f"0x{expander:02X}",
        "unexpected": [f"0x{a:02X}" for a in unexpected], "problems": problems,
        "sensors": [f.name for f in Sensors if f in d.sensors],
    }  # fmt: skip
    s.log("diag", result)
    if problems or unexpected:
        s.say("diag: PROBLEMS: " + "; ".join(problems + [f"unexpected {u}" for u in
                                                         result["unexpected"]]))  # fmt: skip
        return CHECK_FAILED
    s.say("diag: everything expected was found")
    return 0


async def run_param(s: Session, args: argparse.Namespace) -> int:
    """param get [NAME] | param set NAME VALUE | param save"""
    if args.action == "get":
        reports = await s.read_params()
        for name, rep in reports.items():
            if args.name and name != args.name:
                continue
            spec = s.table.by_name(name)
            s.say(f"  {name:28s} {rep.value:<12g} [{rep.min:g}..{rep.max:g}] {spec.unit:10s} "
                  f"{rep.source.name}")  # fmt: skip
        return 0
    if args.action == "save":
        await s.offer_param_save()
        return 0
    if not args.name or args.value is None:
        raise Refused("param set needs NAME and VALUE")
    try:
        spec = s.table.by_name(args.name)
    except KeyError:
        raise Refused(f"{args.name}: not a parameter of params.json") from None
    value = float(args.value)
    old = await s.param(spec.name)
    s.say(f"{spec.name}: {old:g} -> {value:g} {spec.unit}  ({spec.note or 'no note'})")
    await s.op.confirm(
        f"set {spec.name}", "This changes a firmware parameter by hand (method: manual)."
    )
    ok = await s.propose(
        "param-set", {spec.name: value}, method=f"param set by hand (was {old:g})",
        raw={"old": old, "new": value, "reason": args.reason or ""},
    )  # fmt: skip
    if not ok:
        raise StepError("not accepted")
    return 0
