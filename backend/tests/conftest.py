import os

# Must be set before the app (and its cached settings/engine) is imported.
os.environ["APP_ENV"] = "test"
os.environ["ADMIN_TOKEN"] = "test-admin-token"

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession  # noqa: E402

from app.db.session import get_engine, get_session  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def engine() -> AsyncEngine:
    return get_engine()


@pytest.fixture
async def db_session(engine) -> AsyncIterator[AsyncSession]:
    """A session whose commits are only savepoints; everything is rolled back after the test,
    so tests never change the real development data."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture
async def admin_client(db_session) -> AsyncIterator[AsyncClient]:
    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_session, None)
