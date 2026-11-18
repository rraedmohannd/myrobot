"""Admin login (sprint 3 S7): argon2id password hashes, server-side sessions, retention purge.

- Passwords are stored only as argon2id hashes (`admin_users.password_hash`).
- A login creates a random 256-bit token. The browser gets it in an HttpOnly, SameSite=Strict
  cookie; the database stores only its SHA-256, so a copy of the database cannot be used to log in.
- `X-Admin-Token` (ADMIN_TOKEN in .env) still works, for scripts (in production only with
  ADMIN_TOKEN_SCRIPTS=true, see api/deps.py).
- Wrong passwords: LOGIN_MAX_FAILURES within LOGIN_LOCKOUT_MIN lock the username (and, in
  production, the client IP as well) for that time.
"""

import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AdminSession, AdminUser, Session, Turn

COOKIE = "admin_session"
MIN_PASSWORD = 10
MAX_FAILURES, WINDOW_S = 5, 300  # defaults of LOGIN_MAX_FAILURES / LOGIN_LOCKOUT_MIN (dev)

_hasher = PasswordHasher()
_DUMMY = _hasher.hash("not-a-real-password")  # verified when the user does not exist (same timing)
_failures: dict[str, list[float]] = {}


class AuthError(ValueError):
    pass


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"the password needs at least {MIN_PASSWORD} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _keys(username: str, ip: str | None) -> list[str]:
    """Failure counters to check: the username, and in production also the client IP."""
    keys = [username]
    if ip and get_settings().is_production:
        keys.append(f"ip:{ip}")
    return keys


def too_many_failures(username: str, ip: str | None = None) -> bool:
    settings = get_settings()
    window, limit = settings.login_lockout_s, settings.login_max_failures
    locked = False
    for key in _keys(username, ip):
        recent = [t for t in _failures.get(key, []) if time.monotonic() - t < window]
        _failures[key] = recent
        locked = locked or len(recent) >= limit
    return locked


def record_failure(username: str, ip: str | None = None) -> None:
    for key in _keys(username, ip):
        _failures.setdefault(key, []).append(time.monotonic())


def reset_failures() -> None:
    _failures.clear()


async def create_admin(
    session: AsyncSession, username: str, password: str
) -> tuple[AdminUser, bool]:
    """Create the user, or set a new password if the name exists. Returns (user, created)."""
    username = username.strip().lower()
    if (
        not (3 <= len(username) <= 64)
        or not username.replace("_", "").replace(".", "").replace("-", "").isalnum()
    ):
        raise AuthError("the username needs 3-64 letters, digits, '.', '-' or '_'")
    password_hash = hash_password(password)
    user = await session.scalar(select(AdminUser).where(AdminUser.username == username))
    created = user is None
    if user is None:
        user = AdminUser(username=username, password_hash=password_hash)
        session.add(user)
    else:
        user.password_hash = password_hash
        await session.execute(
            delete(AdminSession).where(AdminSession.user_id == user.id)
        )  # old logins end
    await session.commit()
    return user, created


async def login(
    session: AsyncSession, username: str, password: str, ip: str | None = None
) -> str | None:
    """The session token for the cookie, or None when the name or the password is wrong."""
    username = username.strip().lower()
    user = await session.scalar(select(AdminUser).where(AdminUser.username == username))
    ok = verify_password(user.password_hash if user else _DUMMY, password) and user is not None
    if not ok:
        record_failure(username, ip)
        return None
    _failures.pop(username, None)
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    await session.execute(delete(AdminSession).where(AdminSession.expires_at < now))  # tidy up
    session.add(AdminSession(
        user_id=user.id, token_hash=_digest(token),
        expires_at=now + timedelta(hours=get_settings().admin_session_hours),
    ))  # fmt: skip
    await session.commit()
    return token


async def session_user(session: AsyncSession, token: str | None) -> AdminUser | None:
    if not token:
        return None
    row = await session.scalar(
        select(AdminSession).where(
            AdminSession.token_hash == _digest(token), AdminSession.expires_at > datetime.now(UTC)
        )
    )
    return await session.get(AdminUser, row.user_id) if row else None


async def logout(session: AsyncSession, token: str | None) -> None:
    if token:
        await session.execute(delete(AdminSession).where(AdminSession.token_hash == _digest(token)))
        await session.commit()


# ---------------- retention ----------------
async def purge_conversations(
    session: AsyncSession, days: int | None = None, dry_run: bool = False
) -> dict[str, int]:
    """Delete conversations (sessions and their turns) that started more than `days` ago
    (default CONVERSATION_RETENTION_DAYS). Only text and timings exist to delete: audio, face
    images and voiceprints are never stored (docs/PRIVACY.md)."""
    days = get_settings().conversation_retention_days if days is None else days
    cutoff = datetime.now(UTC) - timedelta(days=days)
    old = select(Session.id).where(Session.started_at < cutoff)
    sessions = (
        await session.scalar(
            select(func.count()).select_from(Session).where(Session.started_at < cutoff)
        )
        or 0
    )
    turns = (
        await session.scalar(select(func.count()).select_from(Turn).where(Turn.session_id.in_(old)))
        or 0
    )
    if not dry_run and sessions:
        await session.execute(delete(Session).where(Session.started_at < cutoff))  # turns cascade
        await session.commit()
    return {"days": days, "sessions": sessions, "turns": turns, "deleted": int(not dry_run)}
