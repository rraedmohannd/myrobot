"""robotctl bundle: one zip with the logs, configuration, calibration and versions, for the
supervisor or for a bug report. NEVER images or audio: files are taken only by an extension
allow-list, and secrets in configuration files are redacted."""

import importlib.metadata
import io
import platform
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ALLOWED_EXT = {
    ".json", ".jsonl", ".log", ".txt", ".csv", ".md", ".toml", ".ini", ".conf", ".cfg",
    ".yaml", ".yml", ".env", ".service", ".rules", ".lock", ".example",
}  # fmt: skip
MAX_FILE_BYTES = 20 * 1024 * 1024
SECRET_WORDS = r"(token|secret|password|passwd|api[_-]?key|private|pin)"
_ENV_LINE = re.compile(rf"^(\s*[A-Za-z0-9_]*{SECRET_WORDS}[A-Za-z0-9_]*\s*[=:]\s*).+$", re.I | re.M)
_JSON_PAIR = re.compile(rf'("[^"]*{SECRET_WORDS}[^"]*"\s*:\s*)"[^"]*"', re.I)

PI_LOG_DIRS = (Path("/var/log/smart-campus-robot"),)
PI_CONFIG_DIRS = (Path("/etc/smart-campus-robot"),)
REPO_ROBOT = Path(__file__).resolve().parents[1]


def allowed(path: Path) -> bool:
    """By extension only; rotated logs (x.log.1, x.jsonl.2) count as their base type."""
    suffixes = [s.lower() for s in path.suffixes]
    while suffixes and suffixes[-1][1:].isdigit():
        suffixes.pop()
    return bool(suffixes) and suffixes[-1] in ALLOWED_EXT


def redact(text: str) -> str:
    text = _ENV_LINE.sub(lambda m: m.group(1) + "<redacted>", text)
    return _JSON_PAIR.sub(lambda m: m.group(1) + '"<redacted>"', text)


def versions_text(extra: dict[str, str] | None = None) -> str:
    lines = [
        f"date: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"python: {sys.version.split()[0]} ({platform.platform()})",
    ]
    try:
        git = subprocess.run(
            ["git", "-C", str(REPO_ROBOT), "describe", "--always", "--dirty", "--tags"],
            capture_output=True, text=True, timeout=5, check=False,
        )  # fmt: skip
        lines.append(f"git: {git.stdout.strip() or 'unknown'}")
    except (OSError, subprocess.SubprocessError):
        lines.append("git: unknown")
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {value}")
    lines.append("packages:")
    for dist in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"] or ""):
        lines.append(f"  {dist.metadata['Name']}=={dist.version}")
    return "\n".join(lines) + "\n"


def collect(sources: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    """(file, name in the zip) for every allowed file under the sources."""
    files: list[tuple[Path, str]] = []
    for src, prefix in sources:
        if src.is_file():
            files.append((src, f"{prefix}/{src.name}"))
        elif src.is_dir():
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    files.append((f, f"{prefix}/{f.relative_to(src).as_posix()}"))
    return files


def make_bundle(
    out: Path, sources: list[tuple[Path, str]], extra_versions: dict[str, str] | None = None
) -> tuple[list[str], list[str]]:
    """Write the zip; returns (names included, files skipped with the reason)."""
    included: list[str] = []
    skipped: list[str] = []
    out.parent.mkdir(parents=True, exist_ok=True)
    seen: set[Path] = {out.resolve()}
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, name in collect(sources):
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            if not allowed(path):
                skipped.append(f"{path} (type not allowed)")
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                skipped.append(f"{path} (larger than 20 MB)")
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                skipped.append(f"{path} (not text)")
                continue
            zf.writestr(name, redact(text))
            included.append(name)
        zf.writestr("versions.txt", versions_text(extra_versions))
        included.append("versions.txt")
        buf = io.StringIO()
        buf.write("Skipped files:\n" + "".join(f"  {s}\n" for s in skipped))
        zf.writestr("skipped.txt", buf.getvalue())
    return included, skipped


def default_sources(calibration_path: Path) -> list[tuple[Path, str]]:
    cal_dir = calibration_path.parent
    sources: list[tuple[Path, str]] = [
        (calibration_path, "calibration"),
        (calibration_path.with_name("calibration-log.jsonl"), "calibration"),
    ]
    sources += [(f, "calibration") for f in sorted(cal_dir.glob("pid-step-*.csv"))]
    sources += [(d, f"logs/{i}") for i, d in enumerate(PI_LOG_DIRS)]
    sources.append((REPO_ROBOT / "logs", "logs/robot"))
    sources += [(d, "config") for d in PI_CONFIG_DIRS]
    return sources
