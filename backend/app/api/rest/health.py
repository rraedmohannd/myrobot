"""Health endpoint: is the API up, is the database reachable, is pgvector installed?

In production (APP_ENV=production) the public answer is only {"status", "version"}, with the
same HTTP status codes (200 ok, 503 degraded); an admin (login cookie) gets the full body.
"""

import logging

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.services.resilience import provider_status

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str  # "ok" or "degraded"
    version: str
    database: str  # "ok" or "unreachable"
    pgvector: bool
    migration: str | None  # current Alembic revision, None if migrations not applied
    # AI providers as seen by this process: breaker state, last success, recent errors. They do
    # not change the HTTP status: the backend is "ok" without internet, it just answers less.
    providers: dict = {}
    production: bool = False
    simulated_providers: bool = False  # FAKE_PROVIDERS=true: SIMULATED answers and speech


class PublicHealthResponse(BaseModel):
    status: str
    version: str


async def _caller_is_admin(request: Request) -> bool:
    from app.api.deps import is_admin

    try:
        async with get_sessionmaker()() as session:
            return await is_admin(request, session)
    except Exception:  # noqa: BLE001 - no database: nobody is an admin, health still answers
        return False


@router.get("/health", response_model=None)
async def health(request: Request, response: Response) -> dict:
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
    settings = get_settings()
    if settings.is_production and not await _caller_is_admin(request):
        return PublicHealthResponse(
            status="ok" if ok else "degraded", version=__version__
        ).model_dump()
    return HealthResponse(
        status="ok" if ok else "degraded",
        version=__version__,
        database=database,
        pgvector=pgvector,
        migration=migration,
        providers=provider_status(),
        production=settings.is_production,
        simulated_providers=settings.fake_providers,
    ).model_dump()
