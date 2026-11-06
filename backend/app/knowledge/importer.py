"""Import curated knowledge files (backend/data/knowledge/*.json) into the database.

Each file describes one source (an official document or web page) and what it says:
    {
      "source":  {"url": unique id or URL, "title", "lang", "notes", "academic_year", "file"},
      "faculty": {"slug", "name_en", "name_ar"},                      (optional)
      "program": {"slug", ...program columns...},                     (optional)
      "chunks":  [{"category", "lang", "text"}],                      (optional)
      "courses": [{"group", "group_title", "group_credit_hours", "group_rule", "code",
                   "title_en", "title_ar", "credit_hours", "theory_hours", "practical_hours",
                   "prerequisites": [...], "learning", "jnqf_level"}]   (optional)
    }

Everything new or changed is stored as 'pending'; only a human approval makes it usable.
Re-importing an unchanged file does nothing.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Faculty, Program, Source
from app.knowledge.store import ChunkSpec, add_chunks, retire_old_chunks

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge"
PROGRAM_FIELDS = (
    "name_ar", "name_en", "degree", "duration_years", "credit_hours", "study_plan_url",
    "summary_ar", "summary_en", "academic_year",
)  # fmt: skip
MAX_WORDS = 280


class KnowledgeFileError(ValueError):
    pass


@dataclass
class ImportReport:
    files: list[tuple[str, str]] = field(default_factory=list)  # (file name, outcome)


# ---------------- course chunks ----------------
def _prereq_text(prereqs: list[str], titles: dict[str, str]) -> str:
    if not prereqs:
        return "none"
    out = []
    for p in prereqs:
        code, _, rest = p.partition(" ")
        out.append(f"{code} {titles[code]}{(' ' + rest) if rest else ''}" if code in titles else p)
    return "; ".join(out)


def course_chunks(courses: list[dict[str, Any]], program: dict[str, Any]) -> list[ChunkSpec]:
    """One or more readable chunks per course group, e.g. 'Area of Study Requirements'."""
    titles = {c["code"]: c["title_en"] for c in courses}
    name = program.get("name_en") or program.get("slug", "")
    name_ar = program.get("name_ar")
    year = program.get("academic_year") or ""
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in courses:
        groups.setdefault(c["group"], []).append(c)

    specs: list[ChunkSpec] = []
    for rows in groups.values():
        g = rows[0]
        hours = g.get("group_credit_hours")
        header = (
            f"{name}{f' ({name_ar})' if name_ar else ''} — study plan {year} — "
            f"{g['group_title']}" + (f" ({hours} credit hours, {g['group_rule']})" if hours else
                                     f" ({g['group_rule']})")
        )
        lines = [
            f"{c['code']} {c['title_en']} ({c['title_ar']}): "
            + (f"{c['credit_hours']} credit hours " if c["credit_hours"] is not None
               else "credit hours not stated in the plan ")
            + f"({c['theory_hours']} theory, {c['practical_hours']} practical); prerequisites: "
            f"{_prereq_text(c.get('prerequisites', []), titles)}; {c['learning']}; "
            f"JNQF level {c['jnqf_level']}." + (f" Note: {c['note']}." if c.get("note") else "")
            for c in rows
        ]
        parts: list[list[str]] = [[]]
        for line in lines:
            if parts[-1] and sum(len(x.split()) for x in parts[-1]) + len(line.split()) > MAX_WORDS:
                parts.append([])
            parts[-1].append(line)
        for i, part in enumerate(parts, start=1):
            suffix = f" — part {i} of {len(parts)}" if len(parts) > 1 else ""
            specs.append(
                ChunkSpec(
                    text=f"{header}{suffix}:\n" + "\n".join(part),
                    lang="en",
                    category="courses",
                    academic_year=year or None,
                )
            )
    return specs


# ---------------- loading / validation ----------------
def load_file(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise KnowledgeFileError(f"{path.name}: invalid JSON ({e})") from e
    src = doc.get("source") or {}
    if not src.get("url") or not src.get("title"):
        raise KnowledgeFileError(f"{path.name}: 'source' needs 'url' and 'title'")
    for i, ch in enumerate(doc.get("chunks", [])):
        if not ch.get("text") or ch.get("lang") not in ("ar", "en"):
            raise KnowledgeFileError(f"{path.name}: chunk {i} needs 'text' and lang 'ar'/'en'")
    if doc.get("courses") and not doc.get("program"):
        raise KnowledgeFileError(f"{path.name}: 'courses' need a 'program'")
    return doc


# ---------------- database ----------------
async def _upsert_faculty(session: AsyncSession, data: dict[str, Any]) -> Faculty:
    fac = await session.scalar(select(Faculty).where(Faculty.slug == data["slug"]))
    if fac is None:
        fac = Faculty(slug=data["slug"], name_ar=data["name_ar"], name_en=data["name_en"])
        session.add(fac)
    for key in ("name_ar", "name_en", "description_ar", "description_en"):
        if key in data:
            setattr(fac, key, data[key])
    await session.flush()
    return fac


async def _upsert_program(
    session: AsyncSession, data: dict[str, Any], faculty: Faculty | None, file: str
) -> Program:
    prog = await session.scalar(select(Program).where(Program.slug == data["slug"]))
    if prog is None:
        if faculty is None:
            raise KnowledgeFileError(
                f"{file}: program '{data['slug']}' does not exist yet; add a 'faculty' to the "
                "file that defines it"
            )
        missing = [k for k in ("name_ar", "name_en") if not data.get(k)]
        if missing:
            raise KnowledgeFileError(f"{file}: new program needs {missing}")
        prog = Program(
            slug=data["slug"], faculty_id=faculty.id, name_ar=data["name_ar"],
            name_en=data["name_en"], review_status="pending",
        )  # fmt: skip
        session.add(prog)
        await session.flush()
    changed = False
    for key in PROGRAM_FIELDS:
        if key in data and getattr(prog, key) != data[key]:
            setattr(prog, key, data[key])
            changed = True
    if changed:
        prog.updated_at = datetime.now(UTC)
        if prog.review_status == "approved":
            prog.review_status = "stale"
    await session.flush()
    return prog


def _digest(doc: dict[str, Any]) -> str:
    relevant = {k: doc.get(k) for k in ("program", "chunks", "courses")}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def import_doc(session: AsyncSession, doc: dict[str, Any], file: str) -> str:
    faculty = await _upsert_faculty(session, doc["faculty"]) if doc.get("faculty") else None
    program = await _upsert_program(session, doc["program"], faculty, file) if doc.get("program") else None

    meta = doc["source"]
    src = await session.scalar(select(Source).where(Source.url == meta["url"]))
    digest = _digest(doc)
    if src is not None and src.content_hash == digest:
        return "unchanged"

    if src is None:
        outcome = "new"
        src = Source(url=meta["url"], review_status="pending")
        session.add(src)
        await session.flush()
    else:
        outcome = "changed"
        await retire_old_chunks(session, src)

    notes = meta.get("notes", "")
    if meta.get("file"):
        notes = f"{notes} File: data/knowledge/{meta['file']}".strip()
    src.title = meta["title"]
    src.lang = meta.get("lang")
    src.fetched_at = datetime.now(UTC)
    src.content_hash = digest
    src.notes = notes or None

    year = meta.get("academic_year") or (doc.get("program") or {}).get("academic_year")
    pid = program.id if program else None
    specs = [
        ChunkSpec(text=c["text"], lang=c["lang"], category=c.get("category"), academic_year=year)
        for c in doc.get("chunks", [])
    ]
    if doc.get("courses"):
        names = {"name_en": program.name_en, "name_ar": program.name_ar} if program else {}
        program_info = {**names, **doc["program"]}
        program_info.setdefault("academic_year", year)
        specs += course_chunks(doc["courses"], program_info)
    for s in specs:
        s.program_id = pid
    src.clean_text = "\n\n".join(s.text for s in specs)
    await add_chunks(session, src, specs)
    if program is not None and program.source_id is None:
        program.source_id = src.id
    return outcome


async def import_dir(session: AsyncSession, directory: Path = DEFAULT_DIR) -> ImportReport:
    """Files that define a faculty go first, so programs exist before files that only add to them."""
    paths = sorted(directory.glob("*.json"))
    docs = [(p.name, load_file(p)) for p in paths]  # validate everything before writing anything
    docs.sort(key=lambda item: (0 if item[1].get("faculty") else 1, item[0]))
    report = ImportReport()
    for name, doc in docs:
        outcome = await import_doc(session, doc, name)
        await session.commit()
        report.files.append((name, outcome))
    return report
