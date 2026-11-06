"""Review queue API: list crawled sources, read their chunks, approve/reject/edit.

Only approved chunks will ever be used by the robot (B3 retrieval filters on it).
"""

from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from app.api.deps import SessionDep, require_admin
from app.db.base import REVIEW_STATUSES
from app.db.models import Chunk, Program, Source

ADMIN_PAGE = Path(__file__).resolve().parents[2] / "static" / "admin.html"

page_router = APIRouter(include_in_schema=False)
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

ReviewStatus = Literal["pending", "approved", "rejected", "stale"]


@page_router.get("/admin")
async def admin_page() -> FileResponse:
    return FileResponse(ADMIN_PAGE, media_type="text/html")


# ---------- schemas ----------
class SourceSummary(BaseModel):
    id: int
    url: str
    title: str | None
    lang: str | None
    review_status: str
    http_status: int | None
    fetched_at: datetime | None
    notes: str | None
    chunk_counts: dict[str, int]


class SourceList(BaseModel):
    total: int
    items: list[SourceSummary]


class ChunkOut(BaseModel):
    id: int
    position: int
    lang: str
    category: str | None
    program_id: int | None
    review_status: str
    text: str


class SourceDetail(SourceSummary):
    chunks: list[ChunkOut]


class ReviewDecision(BaseModel):
    decision: Literal["approve", "reject"]


class ChunkPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    review_status: ReviewStatus | None = None
    category: str | None = Field(default=None, max_length=32)
    program_id: int | None = None


class Stats(BaseModel):
    sources: dict[str, int]
    chunks: dict[str, int]


# ---------- helpers ----------
async def _chunk_counts(session, source_ids: list[int]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {sid: {} for sid in source_ids}
    if not source_ids:
        return out
    rows = await session.execute(
        select(Chunk.source_id, Chunk.review_status, func.count())
        .where(Chunk.source_id.in_(source_ids))
        .group_by(Chunk.source_id, Chunk.review_status)
    )
    for sid, st, n in rows:
        out[sid][st] = n
    return out


def _summary(src: Source, counts: dict[str, int]) -> dict:
    return {
        "id": src.id,
        "url": src.url,
        "title": src.title,
        "lang": src.lang,
        "review_status": src.review_status,
        "http_status": src.http_status,
        "fetched_at": src.fetched_at,
        "notes": src.notes,
        "chunk_counts": counts,
    }


async def _get_source(session, source_id: int) -> Source:
    src = await session.get(Source, source_id)
    if src is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    return src


async def _detail(session, src: Source) -> SourceDetail:
    chunks = (
        await session.scalars(
            select(Chunk).where(Chunk.source_id == src.id).order_by(Chunk.position, Chunk.id)
        )
    ).all()
    counts = (await _chunk_counts(session, [src.id]))[src.id]
    return SourceDetail(
        **_summary(src, counts),
        chunks=[ChunkOut.model_validate(c, from_attributes=True) for c in chunks],
    )


# ---------- endpoints ----------
@router.get("/stats", response_model=Stats)
async def stats(session: SessionDep) -> Stats:
    result = {}
    for name, model in (("sources", Source), ("chunks", Chunk)):
        rows = await session.execute(
            select(model.review_status, func.count()).group_by(model.review_status)
        )
        result[name] = {s: 0 for s in REVIEW_STATUSES} | {st: n for st, n in rows}
    return Stats(**result)


@router.get("/sources", response_model=SourceList)
async def list_sources(
    session: SessionDep,
    review_status: ReviewStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SourceList:
    query = select(Source)
    count_query = select(func.count()).select_from(Source)
    if review_status:
        query = query.where(Source.review_status == review_status)
        count_query = count_query.where(Source.review_status == review_status)
    total = await session.scalar(count_query) or 0
    sources = (await session.scalars(query.order_by(Source.id).limit(limit).offset(offset))).all()
    counts = await _chunk_counts(session, [s.id for s in sources])
    return SourceList(
        total=total, items=[SourceSummary(**_summary(s, counts[s.id])) for s in sources]
    )


@router.get("/sources/{source_id}", response_model=SourceDetail)
async def get_source(source_id: int, session: SessionDep) -> SourceDetail:
    return await _detail(session, await _get_source(session, source_id))


@router.post("/sources/{source_id}/review", response_model=SourceDetail)
async def review_source(source_id: int, body: ReviewDecision, session: SessionDep) -> SourceDetail:
    """approve: pending chunks -> approved, superseded stale chunks deleted.
    reject: pending chunks -> rejected (never used)."""
    src = await _get_source(session, source_id)
    if body.decision == "approve":
        src.review_status = "approved"
        await session.execute(
            update(Chunk)
            .where(Chunk.source_id == src.id, Chunk.review_status == "pending")
            .values(review_status="approved")
        )
        await session.execute(
            delete(Chunk).where(Chunk.source_id == src.id, Chunk.review_status == "stale")
        )
    else:
        src.review_status = "rejected"
        await session.execute(
            update(Chunk)
            .where(Chunk.source_id == src.id, Chunk.review_status == "pending")
            .values(review_status="rejected")
        )
    await session.commit()
    return await _detail(session, src)


@router.patch("/chunks/{chunk_id}", response_model=ChunkOut)
async def patch_chunk(chunk_id: int, body: ChunkPatch, session: SessionDep) -> ChunkOut:
    chunk = await session.get(Chunk, chunk_id)
    if chunk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chunk not found")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("program_id") is not None and await session.get(
        Program, changes["program_id"]
    ) is None:
        raise HTTPException(422, "Unknown program_id")
    for key, value in changes.items():
        setattr(chunk, key, value)
    await session.commit()
    await session.refresh(chunk)
    return ChunkOut.model_validate(chunk, from_attributes=True)
