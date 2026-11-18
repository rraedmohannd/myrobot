"""Sprint 6 P0.2 / P1: production mode of the backend (APP_ENV=production).

Every production test builds its own app with `create_app()` under a production environment
made only of obvious test strings (no real secret), and restores the test settings afterwards.
Fakes only; nothing calls OpenAI."""

import json
import logging
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import (
    ProductionConfigError,
    Settings,
    check_production,
    get_settings,
    production_problems,
)
from app.core.logging import SecretRedactionFilter, install_secret_redaction
from app.db.session import get_engine, get_session
from app.main import create_app
from app.services import access, auth, cost_guard, providers
from app.services.cost_guard import PaidLimitError
from app.services.dialogue import PHRASES, DialogueManager
from app.services.fake_providers import REHEARSAL_TRANSCRIPT
from app.services.resilience import ResilientEmbedder, ResilientSTT, ResilientTTS, guard
from app.services.speech import CachedTTS, get_tts
from tests.fakes import FakeEmbedder, FakeLLM, FakeSTT, FakeTTS, OneSession

ROBOT = "test-robot-token-prod-0001"
CODE = "visitor-code-4711"
ADMIN = "test-admin-token-production-mode-0001"
BASE = "https://robot.scr-test.invalid"
PROD_ENV = {
    "APP_ENV": "production",
    "ADMIN_TOKEN": ADMIN,
    "ROBOT_TOKEN": ROBOT,
    "POSTGRES_PASSWORD": "test-only-postgres-pw-0001",
    "PUBLIC_BASE_URL": BASE,
    "SECRET_KEY": "test-only-secret-key-for-cookies-0001",
    "SITE_ACCESS_CODE": CODE,
    "PUBLIC_PAGES": "true",
}
SPEECH = "AQAB" * 8000  # about 1.5 s of PCM16 at 16 kHz (base64)


def _reset() -> None:
    get_settings.cache_clear()
    cost_guard.reset_limits()
    auth.reset_failures()
    from app.api.rest.voice import reset_access_attempts

    reset_access_attempts()


@pytest.fixture
def env(monkeypatch) -> Iterator:
    """Set environment variables for one test; the test settings come back afterwards."""
    get_engine()  # created with the test settings (NullPool) before anything changes

    def apply(values: dict[str, str]) -> Settings:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        _reset()
        return get_settings()

    yield apply
    monkeypatch.undo()
    _reset()


@pytest.fixture
def prod(env) -> Iterator:
    """A function that builds an app in production mode (extra env on top of PROD_ENV)."""

    def build(**extra: str) -> FastAPI:
        env({**PROD_ENV, **extra})
        return create_app()

    yield build


@pytest.fixture
async def today_row(engine) -> AsyncIterator[None]:
    """The paid-call counter of today starts empty and is removed afterwards."""

    async def clear() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM paid_call_days WHERE day = :d"),
                               {"d": cost_guard.local_day()})  # fmt: skip

    await clear()
    yield
    await clear()


def _client(app: FastAPI, **kwargs) -> AsyncClient:  # noqa: ANN003
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver", **kwargs)


def _with_db(app: FastAPI, db_session) -> None:  # noqa: ANN001
    async def override() -> AsyncIterator:
        yield db_session

    app.dependency_overrides[get_session] = override


def _fake_tts(app: FastAPI, tmp_path: Path) -> FakeTTS:
    fake = FakeTTS()
    app.dependency_overrides[get_tts] = lambda: CachedTTS(ResilientTTS(fake), tmp_path)
    return fake


# ---------------- 1. refuse to start with weak or missing secrets ----------------
def test_weak_production_settings_are_all_reported_by_name_only() -> None:
    weak = Settings(
        _env_file=None, app_env="production", admin_token="change-me-admin",
        database_url="postgresql+asyncpg://robot:change-me@db:5432/x", public_base_url="http://x",
        secret_key="short", site_access_code=None, robot_token=None, public_pages=True,
    )  # fmt: skip
    problems = production_problems(weak)
    text_ = "\n".join(problems)
    for name in (
        "ADMIN_TOKEN", "ROBOT_TOKEN", "POSTGRES_PASSWORD", "PUBLIC_BASE_URL", "SECRET_KEY",
        "SITE_ACCESS_CODE",
    ):  # fmt: skip
        assert name in text_, name
    assert len(problems) == 6
    assert "change-me-admin" not in text_ and "short" not in text_.replace("shorter", "")
    with pytest.raises(ProductionConfigError) as e:
        check_production(weak)
    assert "change-me" not in str(e.value) and "ADMIN_TOKEN" in str(e.value)

    # a long admin token that contains the example marker is refused too
    long_example = weak.model_copy(
        update={"admin_token": weak.admin_token.__class__("change-me-" * 5)}
    )
    assert any("ADMIN_TOKEN" in p for p in production_problems(long_example))
    # PUBLIC_PAGES=false needs no access code
    assert not any(
        "SITE_ACCESS_CODE" in p
        for p in production_problems(weak.model_copy(update={"public_pages": False}))
    )
    # outside production nothing is checked: the laptop setup is unchanged
    assert production_problems(weak.model_copy(update={"app_env": "development"})) == []


def test_good_production_settings_pass_and_take_the_password_from_the_url(env) -> None:
    settings = env({**PROD_ENV, "POSTGRES_PASSWORD": ""})
    # POSTGRES_PASSWORD empty: the password of DATABASE_URL counts (the test one is long enough)
    assert production_problems(settings) == []
    assert settings.daily_paid_call_limit == 500 and settings.rate_limit_paid_per_min == 12
    assert settings.rate_limit_robot_per_min == 60 and settings.login_lockout_min == 15
    settings = env({"DATABASE_URL": "postgresql+asyncpg://robot:short@db:5432/x"})
    assert any("POSTGRES_PASSWORD" in p for p in production_problems(settings))


def test_create_app_and_the_cli_refuse_to_start(env, monkeypatch, capsys) -> None:
    env({**PROD_ENV, "SECRET_KEY": "too-short"})
    with pytest.raises(ProductionConfigError, match="SECRET_KEY"):
        create_app()
    from app.ingestion import cli

    monkeypatch.setattr(sys, "argv", ["cli", "usage-today"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "SECRET_KEY" in str(e.value) and "too-short" not in str(e.value)


def test_development_defaults_are_unchanged() -> None:
    s = get_settings()
    assert s.app_env == "test" and not s.is_production
    assert s.daily_paid_call_limit is None and s.rate_limit_paid_per_min is None
    assert s.rate_limit_robot_per_min is None and s.login_lockout_s == 300
    assert s.cors_origin_list == [o.strip() for o in s.cors_origins.split(",") if o.strip()]


# ---------------- 2. visitor access code and robot token ----------------
async def test_visitor_access_code_flow(prod, tmp_path) -> None:
    app = prod()
    _fake_tts(app, tmp_path)
    async with _client(app) as c:
        r = await c.get("/robot")
        assert r.status_code == 303 and r.headers["location"] == "/access?next=%2Frobot"
        r = await c.get("/voice?lang=en")
        assert r.headers["location"] == "/access?next=%2Fvoice%3Flang%3Den"
        assert (await c.post("/api/chat", json={"message": "hi"})).status_code == 401
        assert (await c.post("/api/tts", json={"text": "hi"})).status_code == 401
        assert (await c.get("/api/tts/voices")).status_code == 401

        page = await c.get("/access?next=/voice")
        assert page.status_code == 200 and 'name="code"' in page.text
        assert "رمز الدخول" in page.text and "Access code" in page.text
        assert 'value="/voice"' in page.text and "http" not in page.text.split("<body>")[1]

        wrong = await c.post("/access", data={"code": "nope", "next": "/voice"})
        assert wrong.status_code == 401 and "set-cookie" not in wrong.headers
        ok = await c.post("/access", data={"code": CODE, "next": "//evil.example/x"})
        assert ok.status_code == 303 and ok.headers["location"] == "/robot"  # local paths only
        cookie = ok.headers["set-cookie"]
        assert "visitor_session=" in cookie and "HttpOnly" in cookie and "Secure" in cookie
        assert "SameSite=strict" in cookie and "Max-Age=43200" in cookie
        assert CODE not in cookie

        assert (await c.get("/robot")).status_code == 200
        assert (await c.get("/voice")).status_code == 200
        assert (await c.get("/api/tts/voices")).status_code == 200
        assert (await c.post("/api/tts", json={"text": "مرحبا"})).status_code == 200
        assert (await c.get("/access?next=/voice")).headers["location"] == "/voice"

        tampered = c.cookies["visitor_session"][:-2] + "00"
        c.cookies.clear()
        c.cookies.set("visitor_session", tampered)
        assert (await c.get("/api/tts/voices")).status_code == 401


def test_visitor_cookie_signature_and_expiry(env) -> None:
    env(PROD_ENV)
    value = access.make_visitor_cookie()
    assert access.visitor_cookie_valid(value)
    assert not access.visitor_cookie_valid(access.make_visitor_cookie(now=1000.0))  # expired
    assert not access.visitor_cookie_valid("v1.99999999999.abc")
    assert not access.visitor_cookie_valid("garbage") and not access.visitor_cookie_valid(None)
    for target, expected in (
        ("/voice", "/voice"), ("//evil.example", "/robot"), ("https://evil.example", "/robot"),
        ("/\\evil.example", "/robot"), ("", "/robot"), (None, "/robot"),
        ("/robot?x=1", "/robot?x=1"),
    ):  # fmt: skip
        assert access.safe_next(target) == expected, target


async def test_access_page_is_rate_limited_per_ip(prod) -> None:
    app = prod()
    async with _client(app) as c:
        for _ in range(10):
            assert (await c.post("/access", data={"code": "x"})).status_code == 401
        assert (await c.post("/access", data={"code": CODE})).status_code == 429


async def test_the_robot_token_in_query_bearer_or_header(prod) -> None:
    app = prod()
    async with _client(app) as c:
        for kwargs in (
            {"params": {"token": ROBOT}},
            {"headers": {"Authorization": f"Bearer {ROBOT}"}},
            {"headers": {"X-Robot-Token": ROBOT}},
        ):
            assert (await c.get("/api/tts/voices", **kwargs)).status_code == 200, kwargs
            assert (await c.get("/robot", **kwargs)).status_code == 200, kwargs
        assert (await c.get("/api/tts/voices", params={"token": "wrong"})).status_code == 401
        bad = {"Authorization": "Bearer wrong"}
        assert (await c.get("/api/tts/voices", headers=bad)).status_code == 401
        # no paid content: static files, the screen data and approved media need no session
        assert (await c.get("/static/robot-client.js")).status_code == 200
        assert (await c.get("/static/access.html")).status_code == 200
        assert (await c.get("/api/robot/screen")).status_code not in (401, 303)


async def test_public_pages_off_leaves_only_the_robot_token(prod) -> None:
    app = prod(PUBLIC_PAGES="false", SITE_ACCESS_CODE="")
    async with _client(app) as c:
        assert (await c.get("/access")).status_code == 404
        assert (await c.post("/access", data={"code": CODE})).status_code == 404
        assert (await c.get("/robot")).status_code == 404
        assert (await c.get("/voice")).status_code == 404
        assert (await c.get("/robot", params={"token": ROBOT})).status_code == 200
        c.cookies.set("visitor_session", access.make_visitor_cookie())
        assert (await c.get("/api/tts/voices")).status_code == 401


def _ws(app: FastAPI, path: str = "/ws/robot", client=("127.0.0.1", 5000), **kw) -> bool:  # noqa: ANN001, ANN003
    with TestClient(app, client=client) as tc:
        try:
            with tc.websocket_connect(path, **kw) as ws:
                ws.send_json({"type": "heartbeat"})
                return ws.receive_json()["type"] == "heartbeat"
        except WebSocketDisconnect as e:
            assert e.code == 4401
            return False


def test_websocket_in_production(prod) -> None:
    app = prod(ROBOT_LOCAL_NETS="127.0.0.1/32,192.168.0.0/16")
    assert not _ws(app)  # ROBOT_LOCAL_NETS is ignored: localhost needs the token too
    assert not _ws(app, client=("192.168.1.5", 5000))
    assert not _ws(app, "/ws/robot?token=wrong")
    assert _ws(app, f"/ws/robot?token={ROBOT}")
    assert _ws(app, headers={"Authorization": f"Bearer {ROBOT}"})
    assert _ws(app, headers={"X-Robot-Token": ROBOT})
    # a visitor session: the browser sends the cookie with the same-origin WebSocket upgrade
    assert _ws(app, headers={"Cookie": f"visitor_session={access.make_visitor_cookie()}"})
    assert not _ws(app, headers={"Cookie": "visitor_session=v1.9999999999.forged"})


def test_websocket_development_rules_unchanged(env) -> None:
    env({"ROBOT_TOKEN": ROBOT})
    app = create_app()
    assert _ws(app)  # local peer, no token: allowed on the laptop as before
    assert not _ws(app, client=("192.168.1.5", 5000))
    assert _ws(app, client=("192.168.1.5", 5000), headers={"X-Robot-Token": ROBOT})


# ---------------- 3. cost guard ----------------
async def test_rate_limits_per_ip_and_caller_kind(prod, tmp_path) -> None:
    app = prod(RATE_LIMIT_PAID_PER_MIN="2", RATE_LIMIT_ROBOT_PER_MIN="3")
    _fake_tts(app, tmp_path)
    async with _client(app) as c:
        c.cookies.set("visitor_session", access.make_visitor_cookie())
        # X-Forwarded-For is never read by the app (uvicorn --proxy-headers handles it, only
        # for FORWARDED_ALLOW_IPS): changing it does not give a fresh limit
        codes = []
        for i in range(3):
            xff = {"X-Forwarded-For": f"10.0.0.{i}"}
            codes.append((await c.post("/api/tts", json={"text": "a"}, headers=xff)).status_code)
        assert codes == [200, 200, 429]
        r = await c.post("/api/chat", json={"message": "hi"})
        assert r.status_code == 429 and "minute" in r.json()["detail"]
        robot = {"X-Robot-Token": ROBOT}
        codes = [(await c.post("/api/tts", json={"text": "a"}, headers=robot)).status_code
                 for _ in range(4)]  # fmt: skip
        assert codes == [200, 200, 200, 429]  # the robot's own, higher limit
    assert cost_guard.rate_limit_stats()["rate_limited"] == 3


async def test_daily_cap_counts_real_calls_only_and_survives_in_the_database(
    env, today_row, tmp_path
) -> None:
    env({"DAILY_PAID_CALL_LIMIT": "3"})
    embedder = ResilientEmbedder(FakeEmbedder())
    await embedder.embed(["a"])
    tts = CachedTTS(ResilientTTS(FakeTTS()), tmp_path)
    await tts.synthesize("hello", cache=True)
    await tts.synthesize("hello", cache=True)  # a cache hit is not a call
    assert (await cost_guard.usage_today())["paid_calls"] == 2
    await embedder.embed(["b"])
    with pytest.raises(PaidLimitError):
        await embedder.embed(["c"])
    usage = await cost_guard.usage_today()
    assert usage["paid_calls"] == 3 and usage["refused"] == 1 and usage["limit"] == 3
    assert guard("embeddings").state == "closed"  # the provider is fine: breaker untouched


async def test_over_the_daily_cap_the_robot_gives_the_offline_reply(
    env, today_row, db_session
) -> None:
    env({"DAILY_PAID_CALL_LIMIT": "0"})
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    tts = FakeTTS()
    dm = DialogueManager(send, OneSession(db_session), ResilientEmbedder(FakeEmbedder()),
                         FakeLLM(), ResilientSTT(FakeSTT(["x"])), tts)  # fmt: skip
    await dm.handle({"type": "session.start"})
    await dm.handle({"type": "audio.chunk", "pcm16_b64": SPEECH, "sample_rate": 16000})
    await dm.handle({"type": "audio.end"})
    assert any(m["type"] == "error" and m["code"] == "stt_failed" for m in sent)
    assert PHRASES["offline"]["ar"] in " ".join(tts.spoken) and dm.state == "LISTENING"


async def test_rate_limited_turns_get_the_offline_reply(db_session) -> None:
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    stt, llm, tts = FakeSTT(["سؤال"]), FakeLLM(), FakeTTS()
    dm = DialogueManager(send, OneSession(db_session), FakeEmbedder(), llm, stt, tts,
                         turn_allowed=lambda: False)  # fmt: skip
    await dm.handle({"type": "text.input", "text": "شو هي هندسة الأنظمة الذكية؟"})
    await dm.handle({"type": "audio.chunk", "pcm16_b64": SPEECH, "sample_rate": 16000})
    await dm.handle({"type": "audio.end"})
    assert [m["code"] for m in sent if m["type"] == "error"] == ["rate_limited", "rate_limited"]
    assert not llm.calls and not stt.received  # no paid call at all
    assert " ".join(tts.spoken).count(PHRASES["offline"]["ar"]) == 2 and dm.state == "LISTENING"
    display = [m for m in sent if m["type"] == "display.update"][-1]
    assert display["mode"] == "unavailable"


async def test_chat_over_the_daily_cap_is_429(env, today_row, admin_client) -> None:
    from app.main import app as main_app
    from app.services.providers import get_embedder, get_llm

    env({"DAILY_PAID_CALL_LIMIT": "0"})
    main_app.dependency_overrides[get_embedder] = lambda: ResilientEmbedder(FakeEmbedder())
    main_app.dependency_overrides[get_llm] = lambda: FakeLLM()
    try:
        r = await admin_client.post("/api/chat", json={"message": "zqxv paid cap question"})
    finally:
        main_app.dependency_overrides.pop(get_embedder, None)
        main_app.dependency_overrides.pop(get_llm, None)
    assert r.status_code == 429 and "Daily limit" in r.json()["detail"]


# ---------------- 4. admin ----------------
async def test_admin_token_refused_in_production_unless_scripts(prod, db_session) -> None:
    await auth.create_admin(db_session, "tester", "correct-horse-battery")
    app = prod()
    _with_db(app, db_session)
    async with _client(app, headers={"X-Admin-Token": ADMIN}) as c:
        assert (await c.get("/api/admin/stats")).status_code == 401
    app = prod(ADMIN_TOKEN_SCRIPTS="true")
    _with_db(app, db_session)
    async with _client(app, headers={"X-Admin-Token": ADMIN}) as c:
        assert (await c.get("/api/admin/stats")).status_code == 200
        system = (await c.get("/api/admin/system")).json()
    assert ROBOT not in json.dumps(system)  # the robot token never reaches the browser
    assert system["lan"]["token_required"] and not system["lan"]["token_in_urls"]
    assert "token=" not in system["lan"]["robot_page"] and system["admin_token_accepted"]


async def test_login_cookie_flags_and_lockout_per_user_and_ip(prod, db_session) -> None:
    app = prod(LOGIN_MAX_FAILURES="2", ADMIN_SESSION_HOURS="3")
    _with_db(app, db_session)
    password = "correct-horse-battery"
    await auth.create_admin(db_session, "tester", password)
    async with _client(app) as c:
        ok = await c.post("/api/admin/login", json={"username": "tester", "password": password})
        cookie = ok.headers["set-cookie"]
        assert ok.status_code == 200 and "Secure" in cookie and "HttpOnly" in cookie
        assert "SameSite=strict" in cookie and "Max-Age=10800" in cookie
        for name in ("ghost-a", "ghost-b"):  # two different usernames, same IP
            r = await c.post("/api/admin/login", json={"username": name, "password": "x" * 12})
            assert r.status_code == 401
        locked = await c.post("/api/admin/login", json={"username": "tester", "password": password})
        assert locked.status_code == 429 and "15 minutes" in locked.json()["detail"]


async def test_lockout_is_per_username_only_in_development(env, db_session) -> None:
    env({"LOGIN_MAX_FAILURES": "2"})
    for name in ("ghost-a", "ghost-b"):
        auth.record_failure(name, "10.0.0.1")
    assert not auth.too_many_failures("tester", "10.0.0.1")
    assert auth.too_many_failures("ghost-a", "10.0.0.1") is False  # 1 < 2
    auth.record_failure("ghost-a", "10.0.0.1")
    assert auth.too_many_failures("ghost-a", "10.0.0.1")


# ---------------- 5. locked down ----------------
async def test_cors_docs_and_health_in_production(prod) -> None:
    app = prod(ADMIN_TOKEN_SCRIPTS="true")
    async with _client(app) as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await c.get(path)).status_code == 404, path
        pre = {"Access-Control-Request-Method": "POST"}
        good = await c.options("/api/chat", headers={"Origin": BASE, **pre})
        assert good.headers.get("access-control-allow-origin") == BASE
        bad = await c.options("/api/chat", headers={"Origin": "http://localhost:5173", **pre})
        assert "access-control-allow-origin" not in bad.headers

        public = await c.get("/health")
        assert public.status_code in (200, 503) and set(public.json()) == {"status", "version"}
        assert public.json()["version"] == "0.6.0"
        full = (await c.get("/health", headers={"X-Admin-Token": ADMIN})).json()
        assert full["production"] is True and "providers" in full and "database" in full


async def test_development_keeps_docs_and_the_full_health(client) -> None:
    assert (await client.get("/openapi.json")).status_code == 200
    body = (await client.get("/health")).json()
    assert {"database", "pgvector", "migration", "providers"} <= set(body)
    assert body["production"] is False and body["simulated_providers"] is False


def test_media_limit_is_configurable(env) -> None:
    from app.services import media

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024)
    media.validate("a.png", png)  # 2 MB: fine under the 50 MB default
    env({"MEDIA_MAX_MB": "1"})
    with pytest.raises(media.MediaError, match="larger than 1 MB"):
        media.validate("a.png", png)
    with pytest.raises(media.MediaError, match="Only jpg"):
        media.validate("a.svg", b"<svg/>")
    with pytest.raises(media.MediaError, match="not image/png"):
        media.validate("a.png", b"GIF89a" + b"\x00" * 20)


# ---------------- 6. fake providers ----------------
async def test_fake_providers_are_simulated_counted_and_logged(env, today_row, caplog) -> None:
    from app.services.providers import get_embedder, get_llm
    from app.services.speech import get_stt

    env({"FAKE_PROVIDERS": "true", "DAILY_PAID_CALL_LIMIT": "3"})
    providers._warned_fake = False
    with caplog.at_level(logging.WARNING):
        embedder = get_embedder()
    assert "SIMULATED providers (FAKE_PROVIDERS=true)" in caplog.text
    assert embedder.inner.model == "fake-embedder" and get_llm().inner.model == "fake-llm"
    assert (await get_stt().transcribe(b"RIFF")).text == REHEARSAL_TRANSCRIPT
    wav = await get_tts().synthesize("مرحبا")
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    # fake calls count against the daily cap (a rehearsal can trigger it without OpenAI)
    await embedder.embed(["x"])
    with pytest.raises(PaidLimitError):
        await embedder.embed(["y"])
    providers._warned_fake = False


async def test_health_admin_view_shows_simulated_providers(prod) -> None:
    app = prod(FAKE_PROVIDERS="true", ADMIN_TOKEN_SCRIPTS="true")
    async with _client(app, headers={"X-Admin-Token": ADMIN}) as c:
        assert (await c.get("/health")).json()["simulated_providers"] is True
    providers._warned_fake = False


# ---------------- 7. usage-today and the Server card ----------------
def test_usage_today_prints_one_json_line(env, monkeypatch, capsys) -> None:
    from app.ingestion import cli

    env({"DAILY_PAID_CALL_LIMIT": "7"})
    monkeypatch.setattr(sys, "argv", ["cli", "usage-today"])
    cli.main()
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert set(data) == {"date", "paid_calls", "limit"} and data["limit"] == 7
    assert (
        isinstance(data["paid_calls"], int) and data["date"] == cost_guard.local_day().isoformat()
    )


CERT = """-----BEGIN CERTIFICATE-----
MIIBizCCATGgAwIBAgIUXE+/3D++HMeP526nEaT2oMXkhiUwCgYIKoZIzj0EAwIw
GzEZMBcGA1UEAwwQc2NyLXRlc3QuaW52YWxpZDAeFw0yNjExMTgyMjI1MTZaFw0z
NjExMTUyMjI1MTZaMBsxGTAXBgNVBAMMEHNjci10ZXN0LmludmFsaWQwWTATBgcq
hkjOPQIBBggqhkjOPQMBBwNCAAQCCcdCUZCvqbz60grtsIBMVzW3pBH+WiBvSyTc
5XMfGJ8+Unj6EHB1IoB6cjjuJuqCQRXotQJbmEG4LM06CCHzo1MwUTAdBgNVHQ4E
FgQUa+oZ8Q/0ycdK010t+K0BQYbRCnEwHwYDVR0jBBgwFoAUa+oZ8Q/0ycdK010t
+K0BQYbRCnEwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAgNIADBFAiBb8Hhi
O2U3D9xv356c9NcZJ6fOkd5k6PKx5PwfERWoegIhAO0q58xoag0O9Ao83BQ6WJJX
oiJtMMeoDjW2QKrDqJDb
-----END CERTIFICATE-----
"""  # a throw-away self-signed test certificate (CN=scr-test.invalid, no key kept)


async def test_server_card(env, today_row, admin_client, tmp_path) -> None:
    empty = (await admin_client.get("/api/admin/server")).json()
    assert empty["production"] is False and empty["daily_limit"] is None
    assert empty["over_limit"] is False and empty["certificate"] is None

    backups, caddy = tmp_path / "backups", tmp_path / "caddy"
    (backups).mkdir()
    (backups / "last-backup.json").write_text(json.dumps(
        {"time": "2026-11-19T03:00:01Z", "stamp": "20261119-030001", "db_bytes": 1234,
         "files_bytes": 5678}))  # fmt: skip
    site = caddy / "certificates" / "acme" / "scr-test.invalid"
    site.mkdir(parents=True)
    (site / "scr-test.invalid.crt").write_text(CERT)
    (caddy / "garbage.crt").write_text("not a certificate")
    env({"BACKUPS_DIR": str(backups), "CADDY_DATA_DIR": str(caddy),
         "DAILY_PAID_CALL_LIMIT": "1"})  # fmt: skip
    await ResilientEmbedder(FakeEmbedder()).embed(["x"])
    r = await admin_client.get("/api/admin/server")
    assert r.status_code == 200
    card = r.json()
    assert card["paid_calls_today"] == 1 and card["daily_limit"] == 1 and card["over_limit"]
    assert card["last_backup"] == {"time": "2026-11-19T03:00:01Z", "db_bytes": 1234,
                                   "files_bytes": 5678}  # fmt: skip
    assert card["certificate"]["not_after"].startswith("2036-11-15T22:25:16")
    assert card["certificate"]["days_left"] > 3000
    assert set(card["disk"]) == {"total_bytes", "used_bytes", "free_bytes"}
    assert (
        await admin_client.get("/api/admin/server", headers={"X-Admin-Token": "x"})
    ).status_code == 401


async def test_admin_page_has_the_server_card(client) -> None:
    page = (await client.get("/admin")).text
    for needle in ("/api/admin/server", "serverCard", "overLimit", "Paid AI calls today"):
        assert needle in page, needle


# ---------------- 8. secrets never reach the logs ----------------
def test_tokens_are_redacted_from_logs(env, caplog) -> None:
    env({"ROBOT_TOKEN": ROBOT, "SITE_ACCESS_CODE": CODE})
    install_secret_redaction()
    assert any(
        isinstance(f, SecretRedactionFilter) for f in logging.getLogger("uvicorn.error").filters
    )
    with caplog.at_level(logging.INFO):
        logging.getLogger("uvicorn.error").info(
            '%s - "WebSocket %s" [accepted]', "1.2.3.4:5", f"/ws/robot?token={ROBOT}"
        )
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", "/robot?lang=ar&token=abc123", "1.1", 200
        )
    assert ROBOT not in caplog.text and "abc123" not in caplog.text
    assert "token=***" in caplog.text and "lang=ar" in caplog.text
    access_record = next(r for r in caplog.records if r.name == "uvicorn.access")
    assert isinstance(access_record.args, tuple) and len(access_record.args) == 5  # formatter-safe

    # records of any other logger are cleaned by the handlers (basicConfig's root handler)
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        install_secret_redaction()
        logging.getLogger("app.test").warning("code typed: %s, url /robot?token=%s", CODE, ROBOT)
    finally:
        root.removeHandler(handler)
    assert CODE not in stream.getvalue() and ROBOT not in stream.getvalue()
    assert "code typed: ***" in stream.getvalue()
