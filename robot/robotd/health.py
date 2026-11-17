"""Raspberry Pi health for `robot.status.health` (sprint 5 S6.2). Every field is optional: what
cannot be read (e.g. on Windows, or without `vcgencmd`) is simply left out.

- cpu_temp_c: /sys/class/thermal/thermal_zone0/temp (millidegrees);
- under_voltage_now/seen, throttled_now/seen: `vcgencmd get_throttled` (bits 0/16 under-voltage,
  2/18 throttled), the evidence for the power design (an under-voltage becomes an event);
- cpu_load_1m: os.getloadavg(); mem_used_pct: /proc/meminfo; disk_used_pct: shutil.disk_usage.
"""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

THERMAL = Path("/sys/class/thermal/thermal_zone0/temp")
MEMINFO = Path("/proc/meminfo")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii")
    except OSError:
        return None


def _vcgencmd() -> str | None:
    exe = shutil.which("vcgencmd")
    if exe is None:
        return None
    try:
        return subprocess.run(
            [exe, "get_throttled"], capture_output=True, text=True, timeout=2, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _loadavg() -> float | None:
    getloadavg = getattr(os, "getloadavg", None)
    if getloadavg is None:
        return None
    try:
        return float(getloadavg()[0])
    except OSError:
        return None


def _disk_pct() -> float | None:
    try:
        usage = shutil.disk_usage("/")
    except OSError:
        return None
    return 100.0 * usage.used / usage.total if usage.total else None


def parse_throttled(text: str | None) -> dict[str, bool]:
    """`throttled=0x50005` -> the four flags. Empty dict if it cannot be parsed."""
    if not text or "=" not in text:
        return {}
    try:
        value = int(text.strip().split("=", 1)[1], 16)
    except ValueError:
        return {}
    return {
        "under_voltage_now": bool(value & 0x1), "throttled_now": bool(value & 0x4),
        "under_voltage_seen": bool(value & 0x10000), "throttled_seen": bool(value & 0x40000),
    }  # fmt: skip


def parse_meminfo(text: str | None) -> float | None:
    if not text:
        return None
    values: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name] = int(parts[0])
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    if not total or available is None:
        return None
    return 100.0 * (total - available) / total


def read_health(
    read: Callable[[Path], str | None] = _read,
    vcgencmd: Callable[[], str | None] = _vcgencmd,
    loadavg: Callable[[], float | None] = _loadavg,
    disk_pct: Callable[[], float | None] = _disk_pct,
) -> dict[str, Any]:
    """One reading. Blocking (file reads, a subprocess): call it from a worker thread."""
    out: dict[str, Any] = {}
    temp = read(THERMAL)
    if temp and temp.strip().lstrip("-").isdigit():
        out["cpu_temp_c"] = round(int(temp.strip()) / 1000.0, 1)
    out.update(parse_throttled(vcgencmd()))
    load = loadavg()
    if load is not None:
        out["cpu_load_1m"] = round(load, 2)
    mem = parse_meminfo(read(MEMINFO))
    if mem is not None:
        out["mem_used_pct"] = round(max(0.0, min(100.0, mem)), 1)
    disk = disk_pct()
    if disk is not None:
        out["disk_used_pct"] = round(max(0.0, min(100.0, disk)), 1)
    return out
