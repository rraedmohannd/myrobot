"""Health endpoint: is the API up, is the database reachable, is pgvector installed?"""

import logging

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.db.session import get_engine

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str  # "ok" or "degraded"
    version: str
    database: str  # "ok" or "unreachable"
    pgvector: bool
    migration: str | None  # current Alembic revision, None if migrations not applied


@router.get("/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    database, pgvector, migration = "unreachable", False, None
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
            database = "ok"
            pgvector = bool(
                await conn.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
                )
            )
            has_alembic = await conn.scalar(
                text("SELECT to_regclass('alembic_version') IS NOT NULL")
            )
            if has_alembic:
                migration = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception:  # noqa: BLE001 - health must report, not crash
        log.exception("Health check: database error")

    ok = database == "ok" and pgvector and migration is not None
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if ok else "degraded",
        version=__version__,
        database=database,
        pgvector=pgvector,
        migration=migration,
    )
