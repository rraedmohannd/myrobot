"""Shared rules for replacing the chunks of a source (used by the crawler and the importer).

When a source's content changes:
- old approved chunks become 'stale' (kept for comparison, never used for answers)
- old pending/rejected chunks are deleted
- new chunks are inserted as 'pending'
- an approved source becomes 'stale' until a human reviews it again
"""

from dataclasses import dataclass

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Source


@dataclass
class ChunkSpec:
    text: str
    lang: str
    category: str | None = None
    program_id: int | None = None
    academic_year: str | None = None


async def retire_old_chunks(session: AsyncSession, src: Source) -> None:
    await session.execute(
        delete(Chunk).where(
            Chunk.source_id == src.id, Chunk.review_status.in_(("pending", "rejected"))
        )
    )
    await session.execute(
        update(Chunk)
        .where(Chunk.source_id == src.id, Chunk.review_status == "approved")
        .values(review_status="stale")
    )
    src.review_status = "stale" if src.review_status == "approved" else "pending"


async def add_chunks(session: AsyncSession, src: Source, specs: list[ChunkSpec]) -> None:
    for position, spec in enumerate(specs):
        session.add(
            Chunk(
                source_id=src.id,
                program_id=spec.program_id,
                lang=spec.lang,
                category=spec.category,
                academic_year=spec.academic_year,
                text=spec.text,
                position=position,
                review_status="pending",
            )
        )
    await session.flush()
