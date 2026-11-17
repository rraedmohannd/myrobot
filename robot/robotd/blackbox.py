"""Black box (sprint 5 S6.4): the last 60 s of telemetry and events, written to a file when
something goes wrong (FAULT, obstacle stop, communication loss).

`<LOG_DIR>/blackbox-<UTC time>.jsonl`: one JSON object per line; the first line is a header
(reason, time, is_simulated). Telemetry is kept at 10 Hz. Only numbers and event texts: never
images or audio. The number of files is bounded (the oldest are deleted).
"""

import json
import logging
import os
import sys
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scr_serial import Telemetry

log = logging.getLogger("robotd.blackbox")

PI_LOG_DIR = Path("/var/log/smart-campus-robot")
DEV_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"  # robot/logs/ is git-ignored


def default_log_dir() -> Path:
    if sys.platform.startswith("linux") and os.access(PI_LOG_DIR, os.W_OK):
        return PI_LOG_DIR
    return DEV_LOG_DIR


class BlackBox:
    def __init__(
        self, directory: Path | None = None, window_s: float = 60.0, keep: int = 50,
        min_interval_s: float = 5.0, telemetry_period_s: float = 0.1,
    ) -> None:  # fmt: skip
        self.directory = directory  # None: keep in memory only, never write
        self.window_s, self.keep = window_s, keep
        self.min_interval_s, self.telemetry_period_s = min_interval_s, telemetry_period_s
        self.records: deque[dict[str, Any]] = deque()
        self.files: list[Path] = []
        self._last_tel_s = -1e9
        self._last_dump_s = -1e9

    def _add(self, now_s: float, record: dict[str, Any]) -> None:
        self.records.append({"t_s": round(now_s, 3), **record})
        while self.records and now_s - self.records[0]["t_s"] > self.window_s:
            self.records.popleft()

    def telemetry(self, now_s: float, frame: Telemetry) -> None:
        if now_s - self._last_tel_s < self.telemetry_period_s - 1e-6:
            return
        self._last_tel_s = now_s
        self._add(now_s, {
            "kind": "telemetry", "t_ms": frame.t_ms, "enc": list(frame.encoders),
            "gyro_z": round(frame.gyro_z, 5), "us_mm": list(frame.ultrasonic_mm),
            "battery_mv": frame.battery_mv, "battery_ma": frame.battery_ma,
            "flags": int(frame.flags), "param_hash": f"{frame.param_hash:08x}",
        })  # fmt: skip

    def event(self, now_s: float, kind: str, data: dict[str, Any]) -> None:
        self._add(now_s, {"kind": "event", "event": kind, "data": data})

    def note(self, now_s: float, data: dict[str, Any]) -> None:
        self._add(now_s, {"kind": "note", **data})

    def dump(self, now_s: float, reason: str, is_simulated: bool) -> Path | None:
        """Write the window to a file. None when disabled, rate-limited or the write failed."""
        if self.directory is None or now_s - self._last_dump_s < self.min_interval_s:
            return None
        self._last_dump_s = now_s
        stamp = datetime.now(UTC)
        path = self.directory / f"blackbox-{stamp.strftime('%Y%m%dT%H%M%S.%fZ')}.jsonl"
        header = {
            "kind": "header", "reason": reason, "written_utc": stamp.isoformat(),
            "is_simulated": is_simulated, "window_s": self.window_s,
            "note": "SIMULATED - not a physical result" if is_simulated else "",
        }  # fmt: skip
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                for record in (header, *self.records):
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("black box not written (%s): %s", path, exc)
            return None
        self._prune()
        log.warning("black box written: %s (%s)", path, reason)
        return path

    def _prune(self) -> None:
        assert self.directory is not None
        files = sorted(self.directory.glob("blackbox-*.jsonl"))
        for old in files[: max(0, len(files) - self.keep)]:
            try:
                old.unlink()
            except OSError:
                pass
        self.files = sorted(self.directory.glob("blackbox-*.jsonl"))
