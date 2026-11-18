"""Facts for the admin "Server" card (sprint 6 P1): production mode, paid calls today against
the daily cap, the last backup, disk usage and the TLS certificate expiry. Every part is
optional: on the laptop most of them are simply null."""

import json
import logging
import shutil
import ssl
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services import cost_guard

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"  # /app/data in the container


def last_backup(backups_dir: str) -> dict[str, Any] | None:
    """`last-backup.json` written by the backup service (deploy/backup/backup.sh)."""
    try:
        data = json.loads((Path(backups_dir) / "last-backup.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {key: data.get(key) for key in ("time", "db_bytes", "files_bytes")}


def disk_usage(path: Path = DATA_DIR) -> dict[str, int] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free}


def _not_after(path: Path) -> datetime | None:
    try:
        decoded = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]  # noqa: SLF001
        return datetime.fromtimestamp(ssl.cert_time_to_seconds(decoded["notAfter"]), UTC)
    except Exception:  # noqa: BLE001 - not a certificate, or unreadable: skip it
        return None


def certificate(caddy_data_dir: str) -> dict[str, Any] | None:
    """The earliest expiry of the site certificates Caddy keeps (`certificates/**/*.crt`; if
    there are none, every `*.crt`, e.g. the internal CA of a rehearsal)."""
    root = Path(caddy_data_dir)
    try:
        if not root.is_dir():
            return None
        files = sorted((root / "certificates").rglob("*.crt")) or sorted(root.rglob("*.crt"))
    except OSError:
        return None
    best: tuple[datetime, Path] | None = None
    for path in files:
        when = _not_after(path)
        if when is not None and (best is None or when < best[0]):
            best = (when, path)
    if best is None:
        return None
    when, path = best
    return {
        "not_after": when.isoformat(timespec="seconds"),
        "days_left": (when - datetime.now(UTC)).days,
        "file": str(path.relative_to(root)),
    }


async def server_status() -> dict[str, Any]:
    settings = get_settings()
    try:
        usage = await cost_guard.usage_today()
    except Exception:  # noqa: BLE001 - the card shows what it can
        log.exception("server card: paid-call counter unavailable")
        usage = {"paid_calls": None, "refused": None, "limit": settings.daily_paid_call_limit}
    limit, calls = usage["limit"], usage["paid_calls"]
    rate = cost_guard.rate_limit_stats()
    return {
        "production": settings.is_production,
        "fake_providers": settings.fake_providers,
        "paid_calls_today": calls,
        "refused_today": usage.get("refused"),
        "daily_limit": limit,
        "over_limit": bool(isinstance(limit, int) and isinstance(calls, int) and calls >= limit),
        "rate_limits": {
            "visitor_per_min": settings.rate_limit_paid_per_min,
            "robot_per_min": settings.rate_limit_robot_per_min,
            "refused_since_start": rate["rate_limited"],
            "last_refused_at": (
                datetime.fromtimestamp(float(rate["last_rate_limited_at"]), UTC).isoformat(
                    timespec="seconds"
                )
                if rate["last_rate_limited_at"]
                else None
            ),
        },
        "last_backup": last_backup(settings.backups_dir),
        "disk": disk_usage(),
        "certificate": certificate(settings.caddy_data_dir),
    }
