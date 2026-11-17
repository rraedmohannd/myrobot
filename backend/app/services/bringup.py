"""Bring-up support (sprint 5 S6.3): calibration records from `calibration.report`, and the
checklist of docs/BRINGUP.md.

- A calibration record is stored only when the value of a key changes (per robot and per
  simulated/real), so the rows of a key are its history.
- A simulated calibration (`is_simulated: true`) is never a measurement: it is stored apart and
  always shown with a SIMULATED label (CLAUDE.md rule 5).
- The checklist is read from the Markdown file every time (the file is the source); only the
  ticks (who, when, note) are kept in the database.

Checklist format (docs/BRINGUP.md):
    ## 3. Tracks off the ground
    - [ ] Motor test, left side <!-- step:motor-left -->
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from scr_protocol import ProtocolError, messages
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import CalibrationRecord

SIMULATED_CALIBRATION_LABEL = "SIMULATED — not a measurement"
UNKNOWN_ROBOT = "unknown"


# ---------------- calibration records ----------------
def _measured_at(date: str | None) -> datetime | None:
    if not date:
        return None
    try:
        return datetime.fromisoformat(date.replace("Z", "+00:00"))
    except ValueError:
        return None


async def store_calibration_report(
    db: AsyncSession, msg: dict[str, Any], fallback_robot_id: str | None = None
) -> list[str]:
    """Store the keys of a `calibration.report` whose value changed. Returns the stored keys.
    Raises ProtocolError for a message that does not match the contract."""
    try:
        report = messages.CalibrationReport.model_validate(msg)
    except ValueError as e:
        raise ProtocolError(f"calibration.report: {e.errors()[0]['msg']}") from e  # type: ignore[attr-defined]
    robot_id = (report.robot_id or fallback_robot_id or UNKNOWN_ROBOT)[:64]
    stored: list[str] = []
    for key, item in sorted(report.values.items()):
        key = key[:64]
        latest = await db.scalar(
            select(CalibrationRecord)
            .where(
                CalibrationRecord.robot_id == robot_id,
                CalibrationRecord.key == key,
                CalibrationRecord.is_simulated.is_(report.is_simulated),
            )
            .order_by(CalibrationRecord.id.desc())
            .limit(1)
        )
        if isinstance(latest, CalibrationRecord) and latest.value == item.value:
            continue
        db.add(CalibrationRecord(
            robot_id=robot_id, key=key, value=item.value, unit=item.unit,
            measured_at=_measured_at(item.date), operator=item.operator, method=item.method,
            raw=item.raw, is_simulated=report.is_simulated,
        ))  # fmt: skip
        stored.append(key)
    if stored:
        await db.commit()
    return stored


async def handle_calibration_report(
    sessionmaker: async_sessionmaker[AsyncSession], msg: dict[str, Any], robot_id: str | None
) -> list[str]:
    async with sessionmaker() as db:
        return await store_calibration_report(db, msg, robot_id)


def record_dict(r: CalibrationRecord) -> dict[str, Any]:
    return {
        "id": r.id, "robot_id": r.robot_id, "key": r.key, "value": r.value, "unit": r.unit,
        "measured_at": r.measured_at, "operator": r.operator, "method": r.method, "raw": r.raw,
        "is_simulated": r.is_simulated, "received_at": r.received_at,
        "label": SIMULATED_CALIBRATION_LABEL if r.is_simulated else "measured",
    }  # fmt: skip


async def calibration_records(
    db: AsyncSession, robot_ids: list[str] | None = None
) -> tuple[list[CalibrationRecord], list[CalibrationRecord]]:
    """(real records, simulated records), oldest first."""
    stmt = select(CalibrationRecord).order_by(
        CalibrationRecord.robot_id, CalibrationRecord.key, CalibrationRecord.id
    )
    if robot_ids is not None:
        stmt = stmt.where(CalibrationRecord.robot_id.in_(robot_ids))
    rows = list((await db.scalars(stmt)).all())
    return [r for r in rows if not r.is_simulated], [r for r in rows if r.is_simulated]


def latest_per_key(rows: list[CalibrationRecord]) -> list[dict[str, Any]]:
    """The current value of every (robot, key), with the number of earlier values."""
    groups: dict[tuple[str, str], list[CalibrationRecord]] = {}
    for r in rows:
        groups.setdefault((r.robot_id, r.key), []).append(r)
    return [
        {**record_dict(items[-1]), "history": [record_dict(r) for r in items[:-1]][::-1]}
        for _, items in sorted(groups.items())
    ]


# ---------------- docs/BRINGUP.md checklist ----------------
SECTION_RE = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
ITEM_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+(.*?)\s*<!--\s*step:([a-z0-9-]+)\s*-->\s*$")
LOOSE_ITEM_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+")


@dataclass
class ChecklistStep:
    id: str
    text: str
    line: int


@dataclass
class ChecklistSection:
    number: int | None
    title: str
    steps: list[ChecklistStep] = field(default_factory=list)


@dataclass
class Checklist:
    sections: list[ChecklistSection]
    problems: list[str]

    @property
    def step_ids(self) -> list[str]:
        return [s.id for sec in self.sections for s in sec.steps]


def parse_checklist(text: str) -> Checklist:
    """Read the checklist items of docs/BRINGUP.md. A `[x]` in the file is ignored (the ticks
    live in the database). Problems (duplicate ids, items without an id) are reported, never
    guessed around."""
    sections: list[ChecklistSection] = []
    problems: list[str] = []
    seen: dict[str, int] = {}
    current: ChecklistSection | None = None
    in_code = False
    for n, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = SECTION_RE.match(line)
        if heading:
            current = ChecklistSection(int(heading.group(1)), heading.group(2))
            sections.append(current)
            continue
        if line.startswith("## "):  # a heading without a number: not a checklist section
            current = None
            continue
        item = ITEM_RE.match(line)
        if item is None:
            if LOOSE_ITEM_RE.match(line):
                problems.append(f"line {n}: checkbox without <!-- step:<id> --> (not shown)")
            continue
        step_id = item.group(2)
        if step_id in seen:
            problems.append(
                f"line {n}: step id '{step_id}' is already used on line {seen[step_id]}"
            )
            continue
        seen[step_id] = n
        if current is None:
            current = ChecklistSection(None, "(before the first numbered section)")
            sections.append(current)
        current.steps.append(ChecklistStep(step_id, item.group(1), n))
    return Checklist([s for s in sections if s.steps], problems)


def read_checklist(path: Path) -> Checklist | None:
    """None when the file does not exist."""
    try:
        return parse_checklist(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
