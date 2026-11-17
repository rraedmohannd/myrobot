"""Admin login, logout and the "System" information of the admin page (sprint 3 S5, S6, S7)."""

import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import __version__
from app.api.deps import SessionDep, require_admin
from app.core.config import get_settings
from app.db.models import AdminUser, Session
from app.services import auth
from app.services.resilience import provider_status

router = APIRouter(prefix="/api/admin", tags=["admin"])
STARTED = time.monotonic()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
async def login(
    body: LoginRequest, request: Request, response: Response, session: SessionDep
) -> dict[str, Any]:
    """Username and password of a user made with `create-admin`. Sets the session cookie."""
    name = body.username.strip().lower()
    if auth.too_many_failures(name):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many wrong passwords. Wait five minutes."
        )
    token = await auth.login(session, name, body.password)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
    response.set_cookie(
        auth.COOKIE, token, max_age=get_settings().admin_session_hours * 3600, httponly=True,
        samesite="strict", secure=request.url.scheme == "https", path="/",
    )  # fmt: skip
    return {"user": name}


@router.post("/logout")
async def logout(request: Request, response: Response, session: SessionDep) -> dict[str, bool]:
    await auth.logout(session, request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(
    request: Request, session: SessionDep, _: None = Depends(require_admin)
) -> dict[str, Any]:
    user = await auth.session_user(session, request.cookies.get(auth.COOKIE))
    return {"user": user.username if user else None, "via": "session" if user else "token"}


def process_memory_mb() -> float | None:
    """Resident memory of this process (Linux /proc; None elsewhere)."""
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        return None
    return None


@router.get("/system", dependencies=[Depends(require_admin)])
async def system(request: Request, session: SessionDep) -> dict[str, Any]:
    """Provider status and recent errors, how a robot or another device reaches this backend
    (address for the QR code), privacy settings and process facts."""
    settings = get_settings()
    base = (
        settings.public_base_url.rstrip("/")
        or f"{request.url.scheme}://{request.headers.get('host', 'localhost:8000')}"
    )
    token = settings.robot_token.get_secret_value() if settings.robot_token else ""
    query = f"?token={token}" if token else ""
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    host = base.split("//", 1)[-1].split(":")[0].split("/")[0]
    retention = await auth.purge_conversations(session, dry_run=True)
    return {
        "version": __version__,
        "uptime_s": round(time.monotonic() - STARTED),
        "pid": os.getpid(),
        "memory_mb": process_memory_mb(),
        "providers": provider_status(),
        "lan": {
            "base_url": base,
            "robot_page": f"{base}/robot{query}",
            "robot_ws": f"{ws_base}/ws/robot{query}",
            "token_required": bool(token),
            "base_is_localhost": host in ("localhost", "127.0.0.1"),
            "public_base_url_set": bool(settings.public_base_url),
            "note": "Other devices need the laptop's network address, not localhost. Set "
            "PUBLIC_BASE_URL in .env "
            "(see docs/DEPLOY.md). Browsers give the microphone only to localhost or HTTPS pages.",
        },
        "privacy": {
            "retention_days": settings.conversation_retention_days,
            "conversations_older_than_retention": retention["sessions"],
            "conversations_total": await session.scalar(select(func.count()).select_from(Session))
            or 0,
        },
        "admin_users": await session.scalar(select(func.count()).select_from(AdminUser)) or 0,
        "admin_token_set": bool(settings.admin_token and settings.admin_token.get_secret_value()),
    }
