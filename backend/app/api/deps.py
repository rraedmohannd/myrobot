from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def admin_token_accepted() -> str:
    """The ADMIN_TOKEN that X-Admin-Token may match, or "" when the token is not accepted. In
    production the admin logs in; the token works only with ADMIN_TOKEN_SCRIPTS=true."""
    settings = get_settings()
    if settings.is_production and not settings.admin_token_scripts:
        return ""
    expected = settings.admin_token
    return expected.get_secret_value() if expected is not None else ""


async def is_admin(request: Request, session: AsyncSession) -> bool:
    """True for a valid admin login cookie or an accepted X-Admin-Token."""
    from app.services import auth

    token = admin_token_accepted()
    sent = request.headers.get("x-admin-token")
    if sent and token and compare_digest(sent.encode(), token.encode()):
        return True
    cookie = request.cookies.get(auth.COOKIE)
    return bool(cookie and await auth.session_user(session, cookie) is not None)


async def require_admin(
    request: Request,
    session: SessionDep,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> None:
    """Admin access: a login session (cookie, see services/auth.py) or, for scripts, the shared
    `X-Admin-Token` (ADMIN_TOKEN in .env; in production only with ADMIN_TOKEN_SCRIPTS=true)."""
    from app.db.models import AdminUser

    token = admin_token_accepted()
    if await is_admin(request, session):
        return
    if not token and await session.scalar(select(AdminUser.id).limit(1)) is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Admin API disabled: set ADMIN_TOKEN in .env or create a user with create-admin",
        )
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Missing or wrong X-Admin-Token, or not logged in"
    )


def require_paid_caller(request: Request) -> str:
    """Production: the robot token or a visitor session (no rate limit: no paid call)."""
    from app.services.access import paid_caller

    caller = paid_caller(request)
    if caller is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Enter the access code at /access (or send the robot token)",
        )
    return caller


def require_paid_access(request: Request) -> str:
    """For endpoints that may call a paid provider (/api/chat, /api/tts): in production the
    robot token or a visitor session (services/access.py), then the per-IP rate limit
    (services/cost_guard.py). Returns "robot" or "visitor"."""
    from app.services.access import client_ip
    from app.services.cost_guard import allow_paid_request

    caller = require_paid_caller(request)
    if not allow_paid_request(caller, client_ip(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Wait a minute and try again."
        )
    return caller


PaidAccess = Annotated[str, Depends(require_paid_access)]
PaidCaller = Annotated[str, Depends(require_paid_caller)]
