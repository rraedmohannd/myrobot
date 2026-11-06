from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def require_admin(x_admin_token: Annotated[str | None, Header()] = None) -> None:
    """Simple shared-token check for the local admin tools (full login comes in B5)."""
    expected = get_settings().admin_token
    if expected is None or not expected.get_secret_value():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Admin API disabled: set ADMIN_TOKEN in .env"
        )
    if not x_admin_token or not compare_digest(x_admin_token, expected.get_secret_value()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or wrong X-Admin-Token")
