"""Settings page endpoints (sprint 5 S6.1): PIN checked on the server, rate limit, local override
file for the backend addresses, volume. Real HTTP on 127.0.0.1; no backend, no hardware."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from robotd.config import Config
from robotd.core import Brain
from robotd.settings import SettingsApi, apply_override, valid_backend_url


class Server:
    def __init__(self, api: SettingsApi) -> None:
        self.api = api
        self.app = web.Application()
        self.app.add_routes(api.routes())
        self.runner = web.AppRunner(self.app)
        self.base = ""

    async def start(self) -> None:
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{self.runner.addresses[0][1]}"


async def make(tmp_path: Path, pin: str = "4321", **kw: Any) -> tuple[Server, Config, list[int]]:
    cfg = Config(sim=True, settings_pin=pin, settings_file=str(tmp_path / "override.json"),
                 backend_url="ws://10.0.0.5:8000/ws/robot")  # fmt: skip
    volumes: list[int] = []

    def volume_set(p: int) -> bool:
        volumes.append(p)
        return kw.get("wpctl", True)

    api = SettingsApi(
        cfg, Brain(cfg), volume_get=lambda: 40 if kw.get("wpctl", True) else None,
        volume_set=volume_set, wifi=lambda: {"ssid": "MEU-Guest", "signal": 70, "ip": "10.0.0.9"},
    )  # fmt: skip
    server = Server(api)
    await server.start()
    return server, cfg, volumes


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


async def login(s: aiohttp.ClientSession, base: str, pin: str) -> tuple[int, dict[str, Any]]:
    async with s.post(base + "/local/settings/login", json={"pin": pin}) as r:
        return r.status, (await r.json() if r.status == 200 else {})


async def test_disabled_without_pin(tmp_path: Path, session: aiohttp.ClientSession) -> None:
    server, _, _ = await make(tmp_path, pin="")
    try:
        status, _ = await login(session, server.base, "")
        assert status == 403
        async with session.get(server.base + "/local/settings/info") as r:
            assert r.status == 403
    finally:
        await server.runner.cleanup()


async def test_pin_token_info_backend_and_volume(
    tmp_path: Path, session: aiohttp.ClientSession
) -> None:
    server, cfg, volumes = await make(tmp_path)
    base = server.base
    try:
        async with session.get(base + "/local/settings") as r:  # the page itself: no PIN needed
            assert r.status == 200 and "الإعدادات" in await r.text()
        async with session.get(base + "/local/settings/info") as r:
            assert r.status == 401  # no token
        status, data = await login(session, base, "4321")
        assert status == 200
        head = {"X-Settings-Token": data["token"]}
        async with session.get(base + "/local/settings/info", headers=head) as r:
            info = await r.json()
        assert info["backend_urls"] == ["ws://10.0.0.5:8000/ws/robot"]
        assert info["volume"] == 40 and info["wifi"]["ip"] == "10.0.0.9"
        assert info["versions"]["robotd"] and info["calibration"]["is_simulated"] is True
        assert "settings_pin" not in json.dumps(info) and "4321" not in json.dumps(info)

        bad = {"urls": ["http://x:8000/ws/robot"]}
        async with session.post(base + "/local/settings/backend", json=bad, headers=head) as r:
            assert r.status == 400
        urls = ["ws://192.168.137.1:8000/ws/robot", "ws://10.0.0.5:8000/ws/robot"]
        async with session.post(base + "/local/settings/backend", json={"urls": urls},
                                headers=head) as r:  # fmt: skip
            assert r.status == 200
        saved = json.loads((tmp_path / "override.json").read_text(encoding="utf-8"))
        assert saved == {"backend_url": ",".join(urls)}
        assert cfg.backend_urls == urls
        fresh = Config(settings_file=str(tmp_path / "override.json"))
        assert apply_override(fresh) and fresh.backend_urls == urls  # used at the next start

        async with session.post(base + "/local/settings/volume", json={"percent": 150},
                                headers=head) as r:  # fmt: skip
            assert r.status == 400
        async with session.post(base + "/local/settings/volume", json={"percent": 65},
                                headers=head) as r:  # fmt: skip
            assert r.status == 200 and volumes == [65]
    finally:
        await server.runner.cleanup()


async def test_wrong_pins_are_rate_limited(tmp_path: Path, session: aiohttp.ClientSession) -> None:
    server, _, _ = await make(tmp_path)
    try:
        for _ in range(5):
            assert (await login(session, server.base, "0000"))[0] == 401
        assert (await login(session, server.base, "4321"))[0] == 429  # locked, even the right PIN
    finally:
        await server.runner.cleanup()


async def test_volume_absent_without_wpctl(tmp_path: Path, session: aiohttp.ClientSession) -> None:
    server, _, _ = await make(tmp_path, wpctl=False)
    try:
        _, data = await login(session, server.base, "4321")
        head = {"X-Settings-Token": data["token"]}
        async with session.get(server.base + "/local/settings/info", headers=head) as r:
            assert (await r.json())["volume"] is None
        async with session.post(server.base + "/local/settings/volume", json={"percent": 50},
                                headers=head) as r:  # fmt: skip
            assert r.status == 501
    finally:
        await server.runner.cleanup()


def test_backend_url_check() -> None:
    assert valid_backend_url("ws://192.168.1.20:8000/ws/robot")
    assert valid_backend_url("wss://robot.example/ws/robot")
    for bad in (
        "http://a:8000/ws/robot",
        "ws://a:8000/other",
        "ws://a/ws/robot?token=x",
        "ws:///ws/robot",
    ):
        assert not valid_backend_url(bad)
