from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app import __version__
from app.api.rest import (
    admin,
    analytics,
    auth,
    bringup,
    chat,
    curated,
    explainers,
    health,
    media,
    runs,
    server,
    voice,
)
from app.api.rest import robot as robot_api
from app.api.rest.voice import STATIC_DIR
from app.api.ws import robot
from app.core.config import check_production, get_settings
from app.core.logging import setup_logging
from app.db.session import get_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await get_engine().dispose()


class RevalidatedStaticFiles(StaticFiles):
    """Static files that the browser re-checks on every load (cheap 304 answers), so the robot
    screen never runs an old script after the code was updated."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    # APP_ENV=production: refuse to start with weak or missing secrets (names, never values).
    check_production(settings)
    if settings.fake_providers:
        from app.services.providers import warn_fake_providers

        warn_fake_providers()

    # Production: no interactive API docs or schema (/docs, /redoc, /openapi.json).
    docs = {} if not settings.is_production else {
        "docs_url": None, "redoc_url": None, "openapi_url": None,
    }  # fmt: skip
    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan, **docs)
    # Production: only PUBLIC_BASE_URL (Settings.cors_origin_list); development: CORS_ORIGINS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(admin.page_router)
    app.include_router(analytics.router)
    app.include_router(media.router)
    app.include_router(media.public_router)
    app.include_router(runs.router)
    app.include_router(robot_api.router)
    app.include_router(bringup.router)
    app.include_router(explainers.router)
    app.include_router(curated.router)
    app.include_router(auth.router)
    app.include_router(server.router)
    app.include_router(explainers.public_router)
    app.include_router(chat.router)
    app.include_router(voice.router)
    app.include_router(robot.router)
    # Scripts, fonts and models for /robot and /voice are local files: no runtime CDN.
    app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
