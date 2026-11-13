"""Admin analytics and tools (demo sprint M3): overview, knowledge gaps, conversations,
"ask the robot", golden-set evaluation runs and knowledge coverage.

All endpoints are under /api/admin and need the X-Admin-Token header. They only read data,
except `ask`, which runs one real question through retrieval + LLM (not stored).
"""

import json
import re
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import SessionDep, require_admin
from app.core.config import get_settings
from app.db.base import REVIEW_STATUSES
from app.db.models import Chunk, Program, Session, Source, Turn
from app.services.chat import answer_question
from app.services.evaluate import GOLDEN_DIR
from app.services.latency import percentile
from app.services.providers import LLM, Embedder, get_embedder, get_llm

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

# Stage name -> turns column. "answer audio" is tts_ms: end of speech -> first audio of the answer.
STAGES = {
    "stt": Turn.stt_ms,
    "retrieval": Turn.retrieval_ms,
    "llm_first": Turn.llm_first_ms,
    "llm": Turn.llm_ms,
    "ack": Turn.ack_ms,
    "answer_audio": Turn.tts_ms,
}
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي", "ـ": None})


def normalize_question(text: str) -> str:
    """Key used to group the same question asked with small spelling differences."""
    text = re.sub(r"[ً-ْٰ]", "", text or "").translate(_ALEF).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def _local_day_start(days_ago: int = 0) -> datetime:
    tz = ZoneInfo(get_settings().local_timezone)
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return (today - timedelta(days=days_ago)).astimezone(UTC)


def _stats(values: list[int]) -> dict[str, float | int | None]:
    return {
        "median": statistics.median(values) if values else None,
        "p90": percentile(values, 0.9),
        "n": len(values),
    }


# ---------------- overview ----------------
async def _counts(session, since: datetime) -> dict[str, int]:
    sessions = await session.scalar(
        select(func.count()).select_from(Session).where(Session.started_at >= since)
    )
    turns = await session.scalar(
        select(func.count()).select_from(Turn).where(Turn.created_at >= since)
    )
    questions = await session.scalar(
        select(func.count())
        .select_from(Turn)
        .where(Turn.created_at >= since, Turn.intent == "question")
    )
    return {"sessions": sessions or 0, "turns": turns or 0, "questions": questions or 0}


@router.get("/analytics/overview")
async def overview(session: SessionDep, days: int = Query(default=7, ge=1, le=90)) -> dict[str, Any]:
    tz = ZoneInfo(get_settings().local_timezone)
    since = _local_day_start(days - 1)

    per_day: dict[str, dict[str, int]] = {}
    for i in range(days):
        day = (datetime.now(tz) - timedelta(days=days - 1 - i)).date().isoformat()
        per_day[day] = {"sessions": 0, "turns": 0}
    for (started,) in await session.execute(
        select(Session.started_at).where(Session.started_at >= since)
    ):
        day = started.astimezone(tz).date().isoformat()
        if day in per_day:
            per_day[day]["sessions"] += 1
    turns = (
        await session.execute(
            select(Turn).where(Turn.created_at >= since).order_by(Turn.created_at)
        )
    ).scalars().all()  # fmt: skip
    for t in turns:
        day = t.created_at.astimezone(tz).date().isoformat()
        if day in per_day:
            per_day[day]["turns"] += 1

    questions = [t for t in turns if t.intent == "question" and t.error is None]
    latency = {}
    for name, column in STAGES.items():
        latency[name] = _stats([v for t in questions if (v := getattr(t, column.key)) is not None])

    found = sum(1 for t in questions if t.found is True)
    not_found = sum(1 for t in questions if t.found is False)
    known = found + not_found

    speakers: dict[int, set[int]] = {}
    for t in turns:
        if t.speaker_label is not None:
            speakers.setdefault(t.session_id, set()).add(t.speaker_label)
    per_session = [len(v) for v in speakers.values()]
    distribution: dict[str, int] = {}
    for n in per_session:
        distribution[str(n)] = distribution.get(str(n), 0) + 1

    return {
        "days": days,
        "timezone": get_settings().local_timezone,
        "today": await _counts(session, _local_day_start(0)),
        "period": await _counts(session, since),
        "per_day": [{"date": d, **v} for d, v in per_day.items()],
        "latency_ms": latency,
        "answers": {
            "found": found,
            "not_found": not_found,
            "unknown": len(questions) - known,  # turns stored before the found flag existed
            "found_rate": round(found / known, 3) if known else None,
            "errors": sum(1 for t in turns if t.error is not None),
        },
        "speakers": {
            "sessions_with_speaker_labels": len(per_session),
            "average_per_session": round(statistics.mean(per_session), 2) if per_session else None,
            "max_per_session": max(per_session) if per_session else None,
            "distribution": dict(sorted(distribution.items())),
        },
    }


# ---------------- knowledge gaps ----------------
@router.get("/analytics/gaps")
async def knowledge_gaps(
    session: SessionDep,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Questions the robot could not answer (found = false), grouped, most frequent first."""
    rows = (
        await session.execute(
            select(Turn.user_text, Turn.user_lang, Turn.answer_text, Turn.created_at)
            .where(
                Turn.created_at >= _local_day_start(days - 1),
                Turn.intent == "question",
                Turn.found.is_(False),
                Turn.error.is_(None),
            )
            .order_by(Turn.created_at.desc())
        )
    ).all()
    groups: dict[str, dict[str, Any]] = {}
    for text, lang, answer, created in rows:
        key = normalize_question(text or "")
        if not key:
            continue
        g = groups.setdefault(
            key, {"question": text, "lang": lang, "count": 0, "last_asked": created,
                  "last_answer": answer, "variants": []},  # fmt: skip
        )
        g["count"] += 1
        if text not in g["variants"] and len(g["variants"]) < 5:
            g["variants"].append(text)
    items = sorted(groups.values(), key=lambda g: (-g["count"], -g["last_asked"].timestamp()))
    return {"days": days, "total_not_found": len(rows), "items": items[:limit]}


# ---------------- conversations ----------------
@router.get("/conversations")
async def conversations(
    session: SessionDep,
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    total = await session.scalar(select(func.count()).select_from(Session)) or 0
    sessions = (
        await session.scalars(
            select(Session).order_by(Session.started_at.desc(), Session.id.desc())
            .limit(limit).offset(offset)
        )
    ).all()  # fmt: skip
    ids = [s.id for s in sessions]
    info: dict[int, dict[str, Any]] = {
        i: {"turns": 0, "speakers": set(), "first": None} for i in ids
    }
    if ids:
        for t in (
            await session.scalars(select(Turn).where(Turn.session_id.in_(ids)).order_by(Turn.idx))
        ).all():
            entry = info[t.session_id]
            entry["turns"] += 1
            if t.speaker_label is not None:
                entry["speakers"].add(t.speaker_label)
            if entry["first"] is None and t.intent == "question":
                entry["first"] = t.user_text
    return {
        "total": total,
        "items": [
            {
                "id": s.id, "started_at": s.started_at, "ended_at": s.ended_at,
                "end_reason": s.end_reason, "lang": s.lang_detected,
                "turns": info[s.id]["turns"], "speakers": len(info[s.id]["speakers"]),
                "first_question": info[s.id]["first"],
            }  # fmt: skip
            for s in sessions
        ],
    }


@router.get("/conversations/{session_id}")
async def conversation(session_id: int, session: SessionDep) -> dict[str, Any]:
    s = await session.get(Session, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    turns = (
        await session.scalars(select(Turn).where(Turn.session_id == s.id).order_by(Turn.idx))
    ).all()
    chunk_ids = sorted({c for t in turns for c in (t.used_chunk_ids or [])})
    sources: dict[int, dict[str, Any]] = {}
    if chunk_ids:
        rows = await session.execute(
            select(Chunk.id, Chunk.category, Source.title, Source.url)
            .join(Source, Source.id == Chunk.source_id)
            .where(Chunk.id.in_(chunk_ids))
        )
        sources = {
            cid: {"chunk_id": cid, "category": cat, "title": title, "url": url}
            for cid, cat, title, url in rows
        }
    return {
        "id": s.id, "started_at": s.started_at, "ended_at": s.ended_at,
        "end_reason": s.end_reason, "lang": s.lang_detected,
        "turns": [
            {
                "idx": t.idx, "created_at": t.created_at, "intent": t.intent,
                "speaker_label": t.speaker_label, "speaker_source": t.speaker_source,
                "user_text": t.user_text, "user_lang": t.user_lang, "answer_text": t.answer_text,
                "found": t.found, "error": t.error,
                # a chunk that was deleted since (re-import) is listed by id only
                "sources": [sources.get(c, {"chunk_id": c, "title": None, "url": None,
                                            "category": None}) for c in (t.used_chunk_ids or [])],
                "timings_ms": {
                    "stt": t.stt_ms, "retrieval": t.retrieval_ms, "llm_first": t.llm_first_ms,
                    "llm": t.llm_ms, "ack": t.ack_ms, "answer_audio": t.tts_ms,
                },
            }  # fmt: skip
            for t in turns
        ],
    }  # fmt: skip


# ---------------- ask the robot ----------------
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@router.post("/ask")
async def ask(
    body: AskRequest,
    session: SessionDep,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[LLM, Depends(get_llm)],
) -> dict[str, Any]:
    """One text question: the answer, `found`, and every retrieved passage with its score
    (BACKEND_SPEC section 7). Nothing is stored, so tests here do not change the analytics."""
    r = await answer_question(session, embedder, llm, body.question.strip(), log=False)
    used = {s.chunk_id for s in r.sources}
    return {
        "answer": r.answer, "spoken": r.spoken, "found": r.found, "lang": r.lang, "model": r.model,
        "error": r.error, "invented_numbers": r.invented_numbers, "dropped": r.dropped,
        # where the answer came from: rag, faq, pitch, place, place_unknown or offline
        "kind": r.kind, "promotional": r.promotional, "faq_id": r.faq_id, "pitch_ids": r.pitch_ids,
        "sources": [{"title": s.title, "url": s.url} for s in r.sources],
        "timings_ms": r.timings,
        "passages": [
            {
                "n": i, "chunk_id": p.chunk_id, "used": p.chunk_id in used, "score": p.score,
                "in_fts": p.in_fts, "in_vector": p.in_vector, "category": p.category,
                "program_id": p.program_id, "source_title": p.source_title,
                "source_url": p.source_url, "text": p.text,
            }  # fmt: skip
            for i, p in enumerate(r.passages, start=1)
        ],
    }  # fmt: skip


# ---------------- evaluation runs ----------------
def read_evaluation_runs(results_dir: Path) -> list[dict[str, Any]]:
    runs = []
    for path in sorted(results_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        results = data.get("results") or []
        runs.append({
            "file": path.name, "started": data.get("started"), "model": data.get("model"),
            "total": data.get("total"), "passed": data.get("passed"),
            "accuracy": data.get("accuracy"),
            "invented_numbers_total": data.get("invented_numbers_total"),
            "latency_median_ms": data.get("latency_median_ms"),
            "latency_p90_ms": data.get("latency_p90_ms"),
            "failed": [
                {"id": r.get("id"), "lang": r.get("lang"), "kind": r.get("kind"),
                 "question": r.get("question"), "answer": r.get("answer"),
                 "reasons": r.get("reasons") or []}
                for r in results if not r.get("passed")
            ],
        })  # fmt: skip
    return runs


def get_results_dir() -> Path:
    return GOLDEN_DIR / "results"


@router.get("/evaluation")
async def evaluation(results_dir: Annotated[Path, Depends(get_results_dir)]) -> dict[str, Any]:
    return {"runs": read_evaluation_runs(results_dir)}


# ---------------- knowledge coverage ----------------
@router.get("/coverage")
async def coverage(session: SessionDep) -> dict[str, Any]:
    by_status: dict[str, dict[str, int]] = {}
    for name, model in (("sources", Source), ("chunks", Chunk)):
        rows = await session.execute(
            select(model.review_status, func.count()).group_by(model.review_status)
        )
        by_status[name] = {s: 0 for s in REVIEW_STATUSES} | {st: n for st, n in rows}

    def table(rows: Any) -> list[dict[str, Any]]:
        out: dict[Any, dict[str, Any]] = {}
        for key, review_status, n in rows:
            out.setdefault(key, {s: 0 for s in REVIEW_STATUSES})[review_status] = n
        return [{"key": k, **v} for k, v in out.items()]

    categories = table(
        await session.execute(
            select(Chunk.category, Chunk.review_status, func.count())
            .group_by(Chunk.category, Chunk.review_status)
        )
    )  # fmt: skip
    for row in categories:
        row["category"] = row.pop("key") or "(none)"
    categories.sort(key=lambda r: (-r["approved"], r["category"]))

    names = {
        p.id: p for p in (await session.scalars(select(Program).order_by(Program.id))).all()
    }
    programs = table(
        await session.execute(
            select(Chunk.program_id, Chunk.review_status, func.count())
            .group_by(Chunk.program_id, Chunk.review_status)
        )
    )  # fmt: skip
    seen = set()
    for row in programs:
        pid = row.pop("key")
        seen.add(pid)
        p = names.get(pid)
        row.update(program_id=pid, name_en=p.name_en if p else "(general / faculty-wide)",
                   name_ar=p.name_ar if p else "(عام / على مستوى الكلية)")  # fmt: skip
    for pid, p in names.items():  # programs that have no chunks at all are a gap too
        if pid not in seen:
            programs.append({"program_id": pid, "name_en": p.name_en, "name_ar": p.name_ar,
                             **{s: 0 for s in REVIEW_STATUSES}})  # fmt: skip
    programs.sort(key=lambda r: (r["program_id"] is None, r["name_en"]))
    return {"by_status": by_status, "chunks_by_category": categories, "chunks_by_program": programs}
