"""The robot's calibration file (sprint 5 interface B).

One JSON file holds every measured value of THIS robot, with how it was measured:

    {"schema": "scr-calibration", "version": 1, "robot_id": "rover5-1", "is_simulated": false,
     "values": {"METRES_PER_TICK": {"value": 0.000512, "unit": "m/tick", "date": "...",
                                    "operator": "...", "method": "...", "raw": {...}}, ...},
     "history": [{"key": ..., "old": ..., "new": ..., "date": ..., "operator": ..., "method": ...}]}

- Keys that are firmware parameters use the `name` of `shared/scr_serial/params.json`
  (e.g. `METRES_PER_TICK`); robotd pushes them to the ESP32 at every connection and checks the
  parameter hash. Pi-only keys are lower-case (`track_width_m`, `camera_hfov_deg`,
  `battery_table`, ...).
- `robotd` owns this module; `robotctl` writes through `save()` (atomic, keeps the history).
- A simulated calibration has `"is_simulated": true` and is never a measurement (CLAUDE.md
  rule 5). `simulated()` builds the one `robotd --sim` uses, from `SIM_PROFILE`.

Path: `/etc/smart-campus-robot/calibration.json` on the Pi, `robot/calibration.dev.json`
(git-ignored) in development, or `ROBOTD_CALIBRATION` / `--calibration`.
"""

import contextlib
import copy
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from scr_serial import ParamTable, f32, load_table, param_hash

SCHEMA = "scr-calibration"
VERSION = 1
PI_DEFAULT_PATH = Path("/etc/smart-campus-robot/calibration.json")
DEV_PATH = Path(__file__).resolve().parents[1] / "calibration.dev.json"
HISTORY_MAX = 500

# Pi-only keys robotd understands (lower-case). `required` ones count for status "complete".
PI_KEYS: dict[str, dict[str, Any]] = {
    "track_width_m": {"unit": "m", "required": True, "step": "rotate-test"},
    "camera_hfov_deg": {"unit": "deg", "required": False, "step": "hfov-cal"},
    "battery_table": {"unit": "V->%", "required": False, "step": "battery-cal"},
    "arrival_tolerance_m": {"unit": "m", "required": False, "step": "return-trial"},
}

Status = Literal["none", "partial", "complete"]

# *** SIMULATED values: used ONLY with the simulated ESP32 (`robotd --sim`, tests). They are
# guesses that make the simulation drive; NONE of them is a measurement of the Rover 5. ***
SIM_PROFILE: dict[str, float] = {
    "METRES_PER_TICK": 0.0005,
    "PID_KP": 1.0,
    "PID_KFF": 1.0,
    "RAMP_MM_S2": 1500.0,
    "MAX_SPEED_MM_S": 600.0,
    "TEST_MAX_DUTY": 0.3,
    "MOTOR_MAX_DUTY": 1.0,
    "MOTOR_INVERT_LEFT": 0,
    "MOTOR_INVERT_RIGHT": 0,
    "ENCODER_INVERT_FL": 0,
    "ENCODER_INVERT_RL": 0,
    "ENCODER_INVERT_FR": 0,
    "ENCODER_INVERT_RR": 0,
    "SERVO_MIN_CDEG": -9000,
    "SERVO_MAX_CDEG": 9000,
    "SERVO_CENTER_PULSE_US": 1500.0,
    "SERVO_US_PER_DEG": 10.0,
    "SERVO_INVERT": 0,
    "SERVO_SLEW_CDEG_PER_S": 36000.0,
    "SERVO_PULSE_MIN_US": 600,
    "SERVO_PULSE_MAX_US": 2400,
    "BATTERY_VOLTAGE_SCALE": 1.0,
    "GYRO_SCALE": 1.0,
}
SIM_PI_PROFILE: dict[str, Any] = {"track_width_m": 0.19}


class CalibrationError(ValueError):
    """The file cannot be used (unknown schema, newer version, bad values). Says why."""


class CalibrationMissing(CalibrationError):
    """There is no calibration file at that path."""


@dataclass
class Calibration:
    robot_id: str = ""
    is_simulated: bool = False
    version: int = VERSION
    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    path: Path | None = None  # where it was loaded from (None: built in memory)
    migrated_from: int | None = None  # version of the file before migration

    def value(self, key: str, default: Any = None) -> Any:
        entry = self.values.get(key)
        return default if entry is None else entry.get("value", default)

    @property
    def updated(self) -> str | None:
        """The newest `date` among the values (ISO text), or None."""
        dates = [str(v["date"]) for v in self.values.values() if v.get("date")]
        return max(dates) if dates else None

    def set(
        self, key: str, value: Any, *, unit: str | None = None, operator: str = "",
        method: str = "", raw: Any = None, date: str | None = None,
    ) -> None:  # fmt: skip
        """Record a measured value; the old one goes to the history."""
        check_value(key, value)
        when = date or now_iso()
        old = self.value(key)
        entry: dict[str, Any] = {"value": value, "date": when}
        if unit is None:
            unit = _unit(key)
        if unit:
            entry["unit"] = unit
        if operator:
            entry["operator"] = operator
        if method:
            entry["method"] = method
        if raw is not None:
            entry["raw"] = raw
        self.values[key] = entry
        self.history.append({
            "key": key, "old": old, "new": value, "date": when, "operator": operator,
            "method": method,
        })  # fmt: skip
        self.history = self.history[-HISTORY_MAX:]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "version": self.version, "robot_id": self.robot_id,
            "is_simulated": self.is_simulated, "values": self.values, "history": self.history,
        }  # fmt: skip

    def report(self) -> dict[str, Any]:
        """The `calibration.report` message for the backend (protocol interface D)."""
        out: dict[str, Any] = {
            "type": "calibration.report", "is_simulated": self.is_simulated,
            "version": self.version, "values": copy.deepcopy(self.values),
        }  # fmt: skip
        if self.robot_id:
            out["robot_id"] = self.robot_id[:64]
        return out


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def default_path() -> Path:
    """`/etc/smart-campus-robot/calibration.json` on the Pi (Linux, folder exists), else the
    development file `robot/calibration.dev.json`."""
    if sys.platform.startswith("linux") and PI_DEFAULT_PATH.parent.is_dir():
        return PI_DEFAULT_PATH
    return DEV_PATH


def _unit(key: str) -> str:
    if key in PI_KEYS:
        return str(PI_KEYS[key]["unit"])
    with contextlib.suppress(KeyError):
        return load_table().by_name(key).unit
    return ""


def check_value(key: str, value: Any, table: ParamTable | None = None) -> None:
    """Raises CalibrationError when a firmware key is unknown, not a number or out of bounds."""
    if key[:1].islower():  # Pi-only key
        if key == "battery_table":
            _check_battery_table(value)
        elif key in PI_KEYS and not _is_number(value):
            raise CalibrationError(f"{key}: must be a number")
        return
    table = table or load_table()
    try:
        spec = table.by_name(key)
    except KeyError:
        raise CalibrationError(f"{key}: not a firmware parameter (params.json)") from None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise CalibrationError(f"{key}: must be a number")
    if not math.isfinite(float(value)) or not spec.in_range(float(value)):
        raise CalibrationError(f"{key} = {value}: outside [{spec.min:g}, {spec.max:g}]")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _check_battery_table(value: Any) -> None:
    ok = isinstance(value, list) and len(value) >= 2 and all(
        isinstance(p, list | tuple) and len(p) == 2
        and all(isinstance(n, int | float) and not isinstance(n, bool) for n in p)
        for p in value
    )  # fmt: skip
    if not ok:
        raise CalibrationError("battery_table: must be a list of [volts, percent] pairs")
    volts = [float(p[0]) for p in value]
    if volts != sorted(volts) or len(set(volts)) != len(volts):
        raise CalibrationError("battery_table: volts must be strictly ascending")


# ---------------- migrations: old file version -> current ----------------
def _migrate_0(data: dict[str, Any]) -> dict[str, Any]:
    """Version 0 (never shipped; a flat {"key": value} draft): wrap every value."""
    values = {k: {"value": v} for k, v in (data.get("values") or {}).items()}
    return {**data, "version": 1, "values": values, "history": data.get("history") or []}


MIGRATIONS = {0: _migrate_0}


def load(path: str | Path) -> Calibration:
    """Read and check a calibration file. Raises CalibrationMissing / CalibrationError."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CalibrationMissing(f"no calibration file at {p}") from None
    except OSError as exc:
        raise CalibrationError(f"cannot read {p}: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CalibrationError(f"{p} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise CalibrationError(f"{p}: not a calibration file (schema must be {SCHEMA!r})")
    version = data.get("version")
    if not isinstance(version, int):
        raise CalibrationError(f"{p}: version missing")
    migrated_from = None
    if version > VERSION:
        raise CalibrationError(
            f"{p}: version {version} was written by a newer robotd (this one reads {VERSION}); "
            "update robotd"
        )
    while version < VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise CalibrationError(f"{p}: version {version} is too old and cannot be migrated")
        migrated_from = migrated_from if migrated_from is not None else version
        data = step(data)
        version = int(data["version"])
    values = data.get("values") or {}
    if not isinstance(values, dict) or not all(isinstance(v, dict) for v in values.values()):
        raise CalibrationError(f"{p}: 'values' must map a key to an object with 'value'")
    table = load_table()
    for key, entry in values.items():
        if "value" not in entry:
            raise CalibrationError(f"{p}: {key} has no 'value'")
        check_value(key, entry["value"], table)
    history = data.get("history") or []
    if not isinstance(history, list):
        raise CalibrationError(f"{p}: 'history' must be a list")
    return Calibration(
        robot_id=str(data.get("robot_id") or ""), is_simulated=bool(data.get("is_simulated")),
        version=VERSION, values=values, history=history, path=p, migrated_from=migrated_from,
    )  # fmt: skip


def save(cal: Calibration, path: str | Path | None = None) -> Path:
    """Atomic write (temporary file in the same folder, fsync, rename). The history of the file
    on disk is kept: entries it has that `cal` lacks are put in front."""
    p = Path(path) if path is not None else cal.path
    if p is None:
        raise CalibrationError("save(): no path")
    for key, entry in cal.values.items():
        check_value(key, entry.get("value"))
    with contextlib.suppress(CalibrationError):
        on_disk = load(p).history
        if on_disk and cal.history[: len(on_disk)] != on_disk:
            missing = [h for h in on_disk if h not in cal.history]
            cal.history = (missing + cal.history)[-HISTORY_MAX:]
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(cal.to_json(), indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    cal.path = p
    return p


def firmware_params(cal: Calibration, table: ParamTable | None = None) -> dict[int, float]:
    """{parameter id: value} for every calibration key that is a firmware parameter."""
    table = table or load_table()
    out: dict[int, float] = {}
    for key in cal.values:
        if key[:1].islower():
            continue
        with contextlib.suppress(KeyError):
            out[table.by_name(key).id] = float(cal.value(key))
    return out


def push_values(cal: Calibration, table: ParamTable | None = None) -> dict[int, float]:
    """Every parameter as robotd pushes it: the calibration value, else the compiled default
    (so a value left in NVS by an experiment cannot survive). f32-rounded."""
    table = table or load_table()
    return {pid: f32(v) for pid, v in {**table.defaults(), **firmware_params(cal, table)}.items()}


def expected_hash(cal: Calibration, table: ParamTable | None = None) -> int:
    """The parameter hash the ESP32 must report once the calibration is pushed."""
    return param_hash(push_values(cal, table))


def required_keys(table: ParamTable | None = None) -> list[str]:
    table = table or load_table()
    return table.required_names() + [k for k, v in PI_KEYS.items() if v["required"]]


def missing_keys(cal: Calibration, table: ParamTable | None = None) -> list[str]:
    return [k for k in required_keys(table) if k not in cal.values]


def status(cal: Calibration, table: ParamTable | None = None) -> Status:
    if not cal.values:
        return "none"
    return "partial" if missing_keys(cal, table) else "complete"


def status_dict(cal: Calibration, table: ParamTable | None = None) -> dict[str, Any]:
    """`robot.status.calibration` (interface D)."""
    return {"status": status(cal, table), "updated": cal.updated, "is_simulated": cal.is_simulated}


def simulated(robot_id: str = "robotd-sim") -> Calibration:
    """The SIMULATED calibration of `robotd --sim` (never a measurement)."""
    cal = Calibration(robot_id=robot_id, is_simulated=True)
    for key, value in {**SIM_PROFILE, **SIM_PI_PROFILE}.items():
        cal.values[key] = {
            "value": value, "unit": _unit(key), "method": "SIMULATED profile (robotd --sim)",
            "operator": "robotd",
        }  # fmt: skip
    return cal


def battery_table(cal: Calibration | None) -> list[tuple[float, float]] | None:
    raw = cal.value("battery_table") if cal is not None else None
    if raw is None:
        return None
    try:
        _check_battery_table(raw)
    except CalibrationError:
        return None
    return [(float(v), float(p)) for v, p in raw]
