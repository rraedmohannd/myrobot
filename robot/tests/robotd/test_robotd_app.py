"""robotd end to end over real sockets: SIMULATED ESP32 (TCP), a FAKE backend WebSocket server,
the local UI server with a page client and a visiond client.

The real backend is never used here. Everything is simulation (CLAUDE.md rule 5).
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from scr_protocol import parse_backend_message, parse_robot_message

from robotd.app import RobotApp
from robotd.config import Config
from robotd.ui_server import inject

PAGE = """<!doctype html><html><head><title>robot</title></head><body>
<script src="/static/robot-client.js"></script>
<script>const client = {on() {}};</script>
</body></html>"""


class FakeBackend:
    """Speaks just enough of /ws/robot (protocol 2) and serves the page assets."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.invalid: list[str] = []
        self.urls: list[str] = []
        self.sockets: list[web.WebSocketResponse] = []
        self.down = False
        self.runs = 0
        self.port = 0
        app = web.Application()
        app.add_routes([
            web.get("/ws/robot", self._ws), web.get("/robot", self._page),
            web.get("/static/robot-client.js", self._js),
            web.get("/api/robot/screen", self._screen),
        ])  # fmt: skip
        self._runner = web.AppRunner(app)

    async def start(self) -> None:
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = self._runner.addresses[0][1]

    async def stop(self) -> None:
        await self.go_down()
        await self._runner.cleanup()

    async def go_down(self) -> None:
        self.down = True
        for ws in self.sockets:
            await ws.close()
        self.sockets.clear()

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.messages if m["type"] == kind]

    async def command(self, command_id: str, cmd: str, args: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"type": "robot.command", "id": command_id, "cmd": cmd, "ts": 1}
        if args is not None:
            msg["args"] = args
        parse_backend_message(msg)
        await self.sockets[-1].send_json(msg)

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        if self.down:
            return web.Response(status=503)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        self.urls.append(str(request.rel_url))
        async for raw in ws:
            msg = json.loads(raw.data)
            try:
                parse_robot_message(msg)
            except ValueError as exc:
                self.invalid.append(f"{msg.get('type')}: {exc}")
            self.messages.append(msg)
            reply: dict[str, Any] | None = None
            if msg["type"] == "hello":
                reply = {"type": "hello.ack", "protocol_version": 2, "follow_up_timeout_s": 10,
                         "max_utterance_s": 20, "speech_rate": 1.0, "vad_silence_ms": 700,
                         "audio_stream": False}  # fmt: skip
            elif msg["type"] == "heartbeat":
                reply = {"type": "heartbeat"}
            elif msg["type"] == "run.start":
                self.runs += 1
                reply = {"type": "run.started", "run_id": 100 + self.runs,
                         "is_simulated": msg["is_simulated"]}  # fmt: skip
            elif msg["type"] == "run.end":
                reply = {"type": "run.ended", "run_id": 100 + self.runs, "samples": 0, "events": 0}
            if reply is not None:
                await ws.send_json({**reply, "id": "0123456789ab", "ts": 1})
        return ws

    async def _page(self, request: web.Request) -> web.Response:
        if self.down:
            return web.Response(status=503)
        return web.Response(text=PAGE, content_type="text/html")

    async def _js(self, request: web.Request) -> web.Response:
        if self.down:
            return web.Response(status=503)
        return web.Response(text="/* robot-client */", content_type="text/javascript")

    async def _screen(self, request: web.Request) -> web.Response:
        if self.down:
            return web.Response(status=503)
        return web.json_response({"programs": [], "attract": [], "interests": False})


async def wait_for(condition: Callable[[], bool], within_s: float = 8.0) -> bool:
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return condition()


@pytest.fixture
async def backend() -> AsyncIterator[FakeBackend]:
    fake = FakeBackend()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def app(backend: FakeBackend, tmp_path: Path) -> AsyncIterator[RobotApp]:
    cfg = Config(
        sim=True, sim_port=0, ui_port=0, cache_dir=str(tmp_path / "cache"),
        backend_url=f"ws://127.0.0.1:{backend.port}/ws/robot", robot_token="s3cret",
        backend_reconnect_s=0.2, status_period_s=0.2, fault_clear_s=0.2, reconnect_s=0.1,
    )  # fmt: skip
    robot = RobotApp(cfg)
    await robot.start()
    # telemetry from the simulated ESP32 has arrived and the start-up fault has cleared
    assert await wait_for(lambda: robot.brain.sup.state == "IDLE" and robot.backend.connected)
    yield robot
    await robot.stop()


async def test_hello_status_and_simulated_label(app: RobotApp, backend: FakeBackend) -> None:
    assert await wait_for(lambda: len(backend.of("robot.status")) >= 3)
    hello = backend.of("hello")[0]
    assert hello["protocol_version"] == 2 and hello["robot_id"] == "robotd"
    assert backend.urls[0] == "/ws/robot?token=s3cret"
    status = backend.of("robot.status")[-1]
    assert status["is_simulated"] is True  # from the SIMULATED flag of the telemetry frames
    assert status["state"] == "IDLE" and status["sensors"]["esp32_link"] == "ok"
    assert status["sensors"]["camera"] == "absent"
    assert backend.invalid == []
    # about 1 Hz in production; here the period is 0.2 s
    count = len(backend.of("robot.status"))
    await asyncio.sleep(1.0)
    assert 3 <= len(backend.of("robot.status")) - count <= 7


async def test_command_accepted_then_refused(app: RobotApp, backend: FakeBackend) -> None:
    await backend.command("cmd-1", "start_roaming")
    assert await wait_for(lambda: len(backend.of("robot.command.ack")) == 1)
    assert backend.of("robot.command.ack")[0] == {
        "type": "robot.command.ack", "id": "cmd-1", "accepted": True,
    }  # fmt: skip
    assert await wait_for(lambda: bool(backend.of("run.start")) and bool(backend.of("telemetry")))
    run_start = backend.of("run.start")[0]
    assert run_start["is_simulated"] is True and "SIMULATED" in run_start["notes"]
    assert await wait_for(lambda: backend.of("robot.status")[-1].get("run_id") == 101)
    assert backend.of("robot.status")[-1]["state"] == "ROAMING"
    assert app.sim_server is not None
    assert await wait_for(lambda: app.sim_server.sim.true.x > 0.1)  # the simulated robot moves

    await backend.command("cmd-2", "start_roaming")  # already roaming: refused, with a reason
    assert await wait_for(lambda: len(backend.of("robot.command.ack")) == 2)
    assert backend.of("robot.command.ack")[1] == {
        "type": "robot.command.ack", "id": "cmd-2", "accepted": False,
        "reason": "not idle (state ROAMING)",
    }  # fmt: skip
    await backend.command("cmd-3", "set_mode", {"mode": "demo-day"})
    assert await wait_for(lambda: backend.of("robot.status")[-1].get("mode") == "demo-day")

    await backend.command("cmd-4", "return_to_start")
    assert await wait_for(lambda: backend.of("robot.status")[-1]["state"] == "RETURNING")
    kinds = [m["kind"] for m in backend.of("event")]
    assert "run_start" in kinds and "return_start" in kinds
    assert all(m["data"].get("simulated") is True for m in backend.of("event"))
    samples = [s for m in backend.of("telemetry") for s in m["samples"]]
    assert samples and all(len(m["samples"]) <= 10 for m in backend.of("telemetry"))
    assert backend.invalid == []


async def test_backend_loss_safe_state_ui_told_then_reconnect(
    app: RobotApp, backend: FakeBackend
) -> None:
    async with aiohttp.ClientSession() as session:
        page = await session.ws_connect(f"http://127.0.0.1:{app.ui.port}/ws/ui")
        seen: list[dict[str, Any]] = []

        async def read_page() -> None:
            async for raw in page:
                seen.append(json.loads(raw.data))

        reader = asyncio.create_task(read_page())
        await backend.command("go", "start_roaming")
        sim = app.sim_server.sim  # type: ignore[union-attr]
        assert await wait_for(lambda: sim.speed[0] > 0.1)
        assert await wait_for(lambda: any(m.get("state") == "ROAMING" for m in seen))
        assert all(m["is_simulated"] is True for m in seen if m["type"] == "local.state")

        await backend.go_down()  # the backend disappears
        assert await wait_for(lambda: app.brain.sup.state == "PAUSED")
        assert app.brain.sup.pause_reason == "backend_lost"
        assert await wait_for(lambda: sim.speed == (0.0, 0.0))  # safe state: it stopped roaming
        assert await wait_for(  # the page is told: it shows "service unavailable"
            lambda: any(m["type"] == "local.state" and m["backend_ok"] is False for m in seen)
        )
        x_stopped = sim.true.x
        await asyncio.sleep(0.6)  # robotd keeps trying; the robot stays where it is
        assert sim.true.x == x_stopped and not app.backend.connected
        assert sim.watchdog_stopped is False  # the ESP32 link is alive: heartbeats go on
        buffered = len(app.brain.outbox)
        assert buffered > 0  # run messages wait in the bounded buffer

        hellos = len(backend.of("hello"))
        backend.down = False  # the backend is back
        assert await wait_for(lambda: len(backend.of("hello")) == hellos + 1)
        assert await wait_for(lambda: app.brain.sup.state == "ROAMING")
        assert await wait_for(lambda: len(backend.of("run.start")) == 2)
        assert "continues" in backend.of("run.start")[1]["notes"]
        assert backend.of("run.start")[1]["is_simulated"] is True
        assert await wait_for(lambda: seen[-1].get("backend_ok") is True)
        assert await wait_for(lambda: sim.speed != (0.0, 0.0))
        assert backend.invalid == []
        reader.cancel()
        await page.close()


async def test_local_ui_server_page_assets_and_proxy(app: RobotApp, backend: FakeBackend) -> None:
    base = f"http://127.0.0.1:{app.ui.port}"
    async with aiohttp.ClientSession() as session:
        async with session.get(base + "/") as resp:
            html = await resp.text()
        assert resp.status == 200
        assert f'window.SCR_BACKEND = "127.0.0.1:{backend.port}"' in html
        assert 'window.SCR_ROBOT_TOKEN = "s3cret"' in html
        assert html.index("window.SCR_BACKEND") < html.index("/static/robot-client.js")
        assert html.rstrip().endswith("</body></html>") and "/local/robot-local.js" in html
        async with session.get(base + "/local/robot-local.js") as resp:
            local_js = await resp.text()
        assert "SIMULATED" in local_js and "محاكاة" in local_js and "local.engage" in local_js
        async with session.get(base + "/static/robot-client.js") as resp:
            assert resp.status == 200 and await resp.text() == "/* robot-client */"
        async with session.get(base + "/api/robot/screen") as resp:
            assert (await resp.json())["interests"] is False
        async with session.get(base + "/static/../config.py") as resp:
            assert resp.status == 404
        async with session.get(base + "/api/admin/anything") as resp:
            assert resp.status == 404  # only the public robot API is passed on
        async with session.get(base + "/local/status") as resp:
            local = await resp.json()
        assert local["is_simulated"] is True and local["label"].startswith("SIMULATED")

        # backend unreachable: the page and its scripts come from the cache
        await backend.go_down()
        async with session.get(base + "/robot") as resp:
            assert resp.status == 200 and "robot-client.js" in await resp.text()
        async with session.get(base + "/static/robot-client.js") as resp:
            assert resp.status == 200 and await resp.text() == "/* robot-client */"
        async with session.get(base + "/api/robot/screen") as resp:
            assert resp.status == 503
    assert (Path(app.cfg.cache_dir) / "static" / "robot-client.js").is_file()


async def test_vision_and_page_sockets_engagement_flow(app: RobotApp, backend: FakeBackend) -> None:
    base = f"http://127.0.0.1:{app.ui.port}"
    state = {
        "type": "vision.state", "t_ms": 1, "camera_ok": True,
        "faces": [{"id": 7, "box": [0.4, 0.3, 0.2, 0.3], "facing": True, "yaw_deg": 1.0,
                   "pitch_deg": 0.0, "mouth": 0.0, "speaking": False}],
        "selected_id": 7, "engaged": False, "active_speaker_id": 7, "overlap": True,
        "servo_target_deg": -20.0,
    }  # fmt: skip
    async with aiohttp.ClientSession() as session:
        page = await session.ws_connect(base + "/ws/ui")
        vision = await session.ws_connect(base + "/ws/vision")
        first = json.loads((await page.receive()).data)
        assert first["type"] == "local.state" and first["is_simulated"] is True
        from_robotd: list[dict[str, Any]] = []
        to_page: list[dict[str, Any]] = []

        async def read(ws: aiohttp.ClientWebSocketResponse, into: list[dict[str, Any]]) -> None:
            async for raw in ws:
                into.append(json.loads(raw.data))

        readers = [
            asyncio.create_task(read(vision, from_robotd)),
            asyncio.create_task(read(page, to_page)),
        ]

        async def send_vision(engaged: bool, seconds: float) -> None:
            for _ in range(int(seconds * 10)):
                await vision.send_json({**state, "engaged": engaged})
                await asyncio.sleep(0.1)

        await send_vision(False, 0.5)
        assert await wait_for(lambda: {"type": "servo", "pan_deg": -20.0} in from_robotd)
        assert app.sim_server.sim.servo_deg == -20.0  # type: ignore[union-attr]
        assert await wait_for(lambda: backend.of("robot.status")[-1]["sensors"]["camera"] == "ok")
        seen = [m for m in to_page if m["type"] == "local.vision"][-1]
        assert seen == {"type": "local.vision", "face_track_id": 7, "overlap": True,
                        "engaged": False, "faces": 1}  # fmt: skip

        await send_vision(True, 0.5)  # the person has faced the robot long enough
        assert await wait_for(lambda: any(m["type"] == "local.engage" for m in to_page))
        assert app.brain.sup.state == "ENGAGED"
        await page.send_json({"type": "local.conversation", "active": True})  # session.started
        await page.send_json({"type": "local.vad", "speaking": True})
        assert await wait_for(lambda: app.brain.sup.state == "CONVERSATION")
        assert await wait_for(lambda: {"type": "vad", "speaking": True} in from_robotd)
        assert await wait_for(lambda: backend.of("robot.status")[-1]["state"] == "CONVERSATION")
        await backend.command("x", "start_roaming")
        assert await wait_for(lambda: bool(backend.of("robot.command.ack")))
        assert backend.of("robot.command.ack")[0]["reason"] == "a conversation is in progress"

        await page.close()  # the page goes away: no conversation any more
        assert await wait_for(lambda: app.brain.sup.state == "IDLE")
        await vision.close()
        assert await wait_for(
            lambda: backend.of("robot.status")[-1]["sensors"]["camera"] == "absent"
        )
        for task in readers:
            task.cancel()
    assert backend.invalid == []


def test_inject_puts_backend_before_scripts_and_local_script_last() -> None:
    html = inject(PAGE, "192.168.1.20:8000", "")
    assert 'window.SCR_BACKEND = "192.168.1.20:8000";window.SCR_ROBOT_TOKEN = null;' in html
    assert html.index("SCR_BACKEND") < html.index("robot-client.js") < html.index("robot-local.js")
    assert html.count("<script") == 4
