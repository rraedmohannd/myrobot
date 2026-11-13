"""Sprint 3 S6 and S7: robot token on the LAN, admin login, conversation retention.
No real password or token is used here; rows are rolled back after each test."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api.ws.robot import is_local_peer
from app.core.config import get_settings
from app.db.models import AdminSession, AdminUser, Session, Turn
from app.db.session import get_session
from app.main import app
from app.services import auth

PASSWORD = "correct-horse-battery"


@pytest.fixture
async def anon(db_session):
    """A client without the admin token, sharing the test transaction."""

    async def _session_override():
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    auth.reset_failures()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_session, None)
        auth.reset_failures()


# ---------------- S7: admin login ----------------
async def test_passwords_are_stored_as_argon2_hashes(db_session) -> None:
    user, created = await auth.create_admin(db_session, "Mohannd.Test", PASSWORD)
    assert created and user.username == "mohannd.test"
    assert user.password_hash.startswith("$argon2id$") and PASSWORD not in user.password_hash
    assert auth.verify_password(user.password_hash, PASSWORD) and not auth.verify_password(user.password_hash, "wrong")
    assert not auth.verify_password("not-a-hash", PASSWORD)
    with pytest.raises(auth.AuthError):
        await auth.create_admin(db_session, "short.pw", "123456789")
    with pytest.raises(auth.AuthError):
        await auth.create_admin(db_session, "bad name!", PASSWORD)
    again, created = await auth.create_admin(db_session, "mohannd.test", PASSWORD + "2")
    assert not created and again.id == user.id and auth.verify_password(again.password_hash, PASSWORD + "2")


async def test_login_session_cookie_and_logout(anon, db_session) -> None:
    await auth.create_admin(db_session, "tester", PASSWORD)
    assert (await anon.get("/api/admin/stats")).status_code == 401
    assert (await anon.get("/api/admin/me")).status_code == 401
    wrong = await anon.post("/api/admin/login", json={"username": "tester", "password": "nope-nope-nope"})
    assert wrong.status_code == 401 and "set-cookie" not in wrong.headers
    assert (await anon.post("/api/admin/login", json={"username": "ghost", "password": PASSWORD})).status_code == 401

    ok = await anon.post("/api/admin/login", json={"username": "Tester", "password": PASSWORD})
    assert ok.status_code == 200 and ok.json() == {"user": "tester"}
    cookie = ok.headers["set-cookie"]
    assert "admin_session=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    token = ok.cookies["admin_session"]
    stored = (await db_session.scalars(select(AdminSession))).all()
    assert len(stored) == 1 and stored[0].token_hash != token and len(stored[0].token_hash) == 64  # only a hash

    assert (await anon.get("/api/admin/stats")).status_code == 200  # the cookie is enough
    assert (await anon.get("/api/admin/me")).json() == {"user": "tester", "via": "session"}
    system = (await anon.get("/api/admin/system")).json()
    assert set(system["providers"]) == {"stt", "llm", "tts", "embeddings"} and system["admin_users"] >= 1
    assert system["privacy"]["retention_days"] == 30 and "robot_page" in system["lan"]

    assert (await anon.post("/api/admin/logout")).json() == {"ok": True}
    anon.cookies.set("admin_session", token)  # an old cookie is worthless after logout
    assert (await anon.get("/api/admin/stats")).status_code == 401


async def test_expired_sessions_and_the_script_token(anon, admin_client, db_session) -> None:
    await auth.create_admin(db_session, "tester", PASSWORD)
    token = await auth.login(db_session, "tester", PASSWORD)
    assert (await auth.session_user(db_session, token)).username == "tester"
    row = await db_session.scalar(select(AdminSession))
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    assert await auth.session_user(db_session, token) is None
    anon.cookies.set("admin_session", token)
    assert (await anon.get("/api/admin/stats")).status_code == 401
    # X-Admin-Token keeps working for scripts
    assert (await admin_client.get("/api/admin/stats")).status_code == 200
    assert (await admin_client.get("/api/admin/me")).json() == {"user": None, "via": "token"}
    assert (await anon.get("/api/admin/stats", headers={"X-Admin-Token": "wrong"})).status_code == 401


async def test_repeated_wrong_passwords_are_slowed_down(anon, db_session) -> None:
    await auth.create_admin(db_session, "tester", PASSWORD)
    for _ in range(auth.MAX_FAILURES):
        assert (await anon.post("/api/admin/login", json={"username": "tester", "password": "x" * 12})).status_code == 401
    locked = await anon.post("/api/admin/login", json={"username": "tester", "password": PASSWORD})
    assert locked.status_code == 429  # even the right password waits
    auth.reset_failures()
    assert (await anon.post("/api/admin/login", json={"username": "tester", "password": PASSWORD})).status_code == 200


async def test_admin_page_has_the_login_form(client) -> None:
    page = (await client.get("/admin")).text
    for needle in ('id="loginUser"', 'id="loginPass"', "/api/admin/login", "/api/admin/logout", 'data-view="system"',
                   'data-view="robot"', 'data-view="faqs"', 'data-view="places"'):  # fmt: skip
        assert needle in page, needle


# ---------------- S7: retention ----------------
async def test_purge_deletes_only_old_conversations(db_session) -> None:
    assert get_settings().conversation_retention_days == 30
    now = datetime.now(UTC)
    old = Session(started_at=now - timedelta(days=4000), lang_detected="ar")
    new = Session(started_at=now - timedelta(days=1), lang_detected="ar")
    db_session.add_all([old, new])
    await db_session.flush()
    db_session.add_all([
        Turn(session_id=old.id, idx=0, user_text="قديم", intent="question"),
        Turn(session_id=old.id, idx=1, user_text="قديم 2", intent="question"),
        Turn(session_id=new.id, idx=0, user_text="جديد", intent="question"),
    ])  # fmt: skip
    await db_session.commit()
    old_id, new_id = old.id, new.id

    preview = await auth.purge_conversations(db_session, days=3999, dry_run=True)
    assert preview == {"days": 3999, "sessions": 1, "turns": 2, "deleted": 0}
    assert await db_session.get(Session, old_id) is not None  # a dry run deletes nothing
    done = await auth.purge_conversations(db_session, days=3999)
    assert done["sessions"] == 1 and done["turns"] == 2 and done["deleted"] == 1
    db_session.expire_all()
    assert await db_session.get(Session, old_id) is None
    assert (await db_session.scalars(select(Turn).where(Turn.session_id == old_id))).all() == []
    assert await db_session.get(Session, new_id) is not None
    assert (await db_session.scalars(select(Turn).where(Turn.session_id == new_id))).all()


def test_no_audio_or_face_columns_exist() -> None:
    """Privacy by schema: nothing can store audio, images of faces or voiceprints."""
    from app.db.base import Base

    forbidden = ("audio_blob", "wav", "pcm", "voiceprint", "embedding_voice", "face_image", "photo", "frame")
    columns = [f"{t.name}.{c.name}" for t in Base.metadata.tables.values() for c in t.columns]
    conversation = [c for c in columns if c.startswith(("sessions.", "turns."))]
    assert not [c for c in conversation if any(word in c.lower() for word in forbidden)]
    assert "explainer_segments.audio_cache_key" in columns  # only the robot's own speech is cached, by key


# ---------------- S6: token for robots that are not local ----------------
def test_local_peer_detection(monkeypatch) -> None:
    assert is_local_peer("127.0.0.1") and is_local_peer("::1")
    assert not is_local_peer("192.168.1.50") and not is_local_peer("172.18.0.1")
    assert not is_local_peer(None) and not is_local_peer("testclient") and not is_local_peer("localhost")
    monkeypatch.setenv("ROBOT_LOCAL_NETS", "127.0.0.1/32, 192.168.1.0/24, not-a-network")
    get_settings.cache_clear()
    try:
        assert is_local_peer("192.168.1.50") and not is_local_peer("192.168.2.50")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def _connect(client_addr: tuple[str, int], token: str | None) -> bool:
    """True when /ws/robot accepts the connection."""
    from starlette.websockets import WebSocketDisconnect

    path = "/ws/robot" + (f"?token={token}" if token else "")
    with TestClient(app, client=client_addr) as tc:
        try:
            with tc.websocket_connect(path) as ws:
                ws.send_json({"type": "heartbeat"})
                return ws.receive_json()["type"] == "heartbeat"
        except WebSocketDisconnect as e:
            assert e.code == 4401
            return False


def test_robot_token_is_required_for_non_local_connections(monkeypatch) -> None:
    lan, local = ("192.168.1.50", 50000), ("127.0.0.1", 50000)
    assert _connect(lan, None)  # no ROBOT_TOKEN set: open, as before
    monkeypatch.setenv("ROBOT_TOKEN", "test-robot-token")
    get_settings.cache_clear()
    try:
        assert not _connect(lan, None)
        assert not _connect(lan, "wrong-token")
        assert _connect(lan, "test-robot-token")
        assert _connect(local, None)  # the backend's own machine does not need it
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert _connect(lan, None)
