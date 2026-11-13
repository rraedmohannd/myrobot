from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def require_admin(
    request: Request,
    session: SessionDep,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> None:
    """Admin access: a login session (cookie, see services/auth.py) or, for scripts, the shared
    `X-Admin-Token` (ADMIN_TOKEN in .env)."""
    from app.db.models import AdminUser
    from app.services import auth

    expected = get_settings().admin_token
    token = expected.get_secret_value() if expected is not None else ""
    if x_admin_token and token and compare_digest(x_admin_token, token):
        return
    cookie = request.cookies.get(auth.COOKIE)
    if cookie and await auth.session_user(session, cookie) is not None:
        return
    if not token and await session.scalar(select(AdminUser.id).limit(1)) is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Admin API disabled: set ADMIN_TOKEN in .env or create a user with create-admin",
        )
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or wrong X-Admin-Token, or not logged in")
