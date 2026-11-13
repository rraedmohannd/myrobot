from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.rest import admin, analytics, chat, health, voice
from app.api.rest.voice import STATIC_DIR
from app.api.ws import robot
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import get_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
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
    app.include_router(chat.router)
    app.include_router(voice.router)
    app.include_router(robot.router)
    # Scripts, fonts and models for /robot and /voice are local files: no runtime CDN.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
