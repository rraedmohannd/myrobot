"""Where robotctl writes its results: the calibration file (ONLY through `robotd.calibration`)
and a local calibration log (JSON lines next to the calibration file).

A simulated result never lands in a real calibration file and the other way round: the file's
`is_simulated` must match the device (CLAUDE.md rule 5).
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from robotd import calibration

LOG_NAME = "calibration-log.jsonl"


class CalibrationMismatch(RuntimeError):
    """A simulated result would go into a real calibration file, or the reverse."""


def default_calibration_path() -> Path:
    env = os.environ.get("ROBOTD_CALIBRATION")
    return Path(env) if env else calibration.default_path()


class CalStore:
    def __init__(self, path: Path, operator: str, robot_id: str = "") -> None:
        self.path = Path(path)
        self.operator = operator
        self.robot_id = robot_id
        self.log_path = self.path.with_name(LOG_NAME)

    def load(self) -> calibration.Calibration | None:
        try:
            return calibration.load(self.path)
        except calibration.CalibrationMissing:
            return None

    def _open_for(self, simulated: bool) -> calibration.Calibration:
        cal = self.load()
        if cal is None:
            return calibration.Calibration(
                robot_id=self.robot_id or ("robotctl-sim" if simulated else "rover5-1"),
                is_simulated=simulated,
            )
        if cal.is_simulated != simulated:
            what = "SIMULATED" if simulated else "real"
            file_kind = "SIMULATED" if cal.is_simulated else "REAL"
            raise CalibrationMismatch(
                f"refusing to write a {what} result into the {file_kind} calibration file "
                f"{self.path}. Use another file (--calibration) or move this one away first."
            )
        return cal

    def record(
        self, simulated: bool, values: dict[str, Any], *, method: str,
        raw: Any = None, units: dict[str, str] | None = None,
    ) -> Path:  # fmt: skip
        """Write one or more keys (with the same method and raw data) in one atomic save."""
        cal = self._open_for(simulated)
        label = f"SIMULATED: {method}" if simulated else method
        for key, value in values.items():
            cal.set(
                key, value, unit=(units or {}).get(key), operator=self.operator,
                method=label, raw=raw,
            )  # fmt: skip
        return calibration.save(cal, self.path)

    def log(self, simulated: bool, step: str, result: dict[str, Any]) -> Path:
        entry = {
            "date": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
            "step": step,
            "operator": self.operator,
            "is_simulated": simulated,
            **({"label": "SIMULATED - not a physical measurement"} if simulated else {}),
            "result": result,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return self.log_path
