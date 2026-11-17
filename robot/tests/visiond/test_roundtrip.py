"""visiond <-> a fake robotd over a real local WebSocket (no camera: scripted observations)."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from dataclasses import replace
from typing import Any

from aiohttp import WSMsgType, web
from visiond_synth import face, talking

from visiond.app import VisionApp
from visiond.config import Thresholds, VisionConfig
from visiond.logic import FaceObservation
from visiond.messages import FACE_KEYS, STATE_KEYS


class FakeCamera:
    """A 'camera' that hands out a new frame number every call. There is no picture at all."""

    eof = False

    def __init__(self) -> None:
        self.working = True
        self._seq = 0

    @property
    def ok(self) -> bool:
        return self.working

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def latest(self) -> tuple[int, Any]:
        self._seq += 1
        return self._seq, object()


class ScriptedAnalyzer:
    """One person, right of the centre, facing the camera, mouth moving all the time."""

    mode = "scripted (test)"

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, frame: Any) -> list[FaceObservation]:
        self.calls += 1
        return [face(0.7, talking(self.calls))]

    def close(self) -> None: ...


class FakeRobotd:
    """Accepts /ws/vision, sends `vad` and `servo`, records everything it receives."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.raw: list[str] = []
        self.binary_frames = 0
        self.connections = 0
        self._runner: web.AppRunner | None = None
        self._sockets: list[web.WebSocketResponse] = []

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        self._sockets.append(ws)
        await ws.send_str(json.dumps({"type": "servo", "pan_deg": 0.0}))
        await ws.send_str(json.dumps({"type": "vad", "speaking": True}))
        await ws.send_str("garbage that must be ignored")
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                self.raw.append(msg.data)
                self.messages.append(json.loads(msg.data))
            elif msg.type == WSMsgType.BINARY:
                self.binary_frames += 1
        return ws

    async def start(self, port: int) -> None:
        app = web.Application()
        app.router.add_get("/ws/vision", self._handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", port).start()

    async def stop(self) -> None:
        for ws in self._sockets:
            await ws.close()
        self._sockets.clear()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_app(port: int, camera: FakeCamera, **changes: Any) -> VisionApp:
    config = replace(
        VisionConfig(),
        robotd_url=f"ws://127.0.0.1:{port}/ws/vision",
        reconnect_s=0.2,
        max_fps=30.0,
        publish_hz=20.0,
        **changes,
    )
    return VisionApp(config, Thresholds(engage_ms=600), camera=camera, analyzer=ScriptedAnalyzer())


async def wait_for(predicate, timeout_s: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_round_trip_with_late_robotd_and_camera_loss() -> None:
    port = free_port()
    robotd = FakeRobotd()
    camera = FakeCamera()
    app = make_app(port, camera)
    task = asyncio.create_task(app.run())
    try:
        # 1. robotd is absent: visiond keeps running and tracking on its own.
        assert await wait_for(lambda: app.snapshot().state.engaged)
        assert not app.link.connected and app.published == 0
        assert app.snapshot().state.active_speaker_id is None  # no VAD without robotd

        # 2. robotd appears: visiond connects by itself and the messages flow.
        await robotd.start(port)
        assert await wait_for(lambda: len(robotd.messages) >= 5)
        assert robotd.connections == 1
        assert await wait_for(
            lambda: bool(robotd.messages) and robotd.messages[-1]["active_speaker_id"] == 1
        )  # the `vad` message arrived and was used
        message = robotd.messages[-1]
        assert set(message) == STATE_KEYS and message["type"] == "vision.state"
        assert message["camera_ok"] is True
        assert message["selected_id"] == 1 and message["engaged"] is True
        assert [set(f) for f in message["faces"]] == [FACE_KEYS]
        assert message["faces"][0]["facing"] is True and message["faces"][0]["speaking"] is True
        # The face is right of the centre -> the target turns right (negative), but not more
        # than max_lead_deg away from the angle robotd says it commanded (0).
        assert await wait_for(lambda: robotd.messages[-1]["servo_target_deg"] < -5.0)
        assert robotd.messages[-1]["servo_target_deg"] >= -app.config.servo.max_lead_deg - 1e-6

        # 3. about the configured rate, text frames only, small messages.
        before, t0 = len(robotd.messages), time.monotonic()
        await asyncio.sleep(1.0)
        rate = (len(robotd.messages) - before) / (time.monotonic() - t0)
        assert 10.0 <= rate <= 25.0
        assert robotd.binary_frames == 0
        assert max(len(text) for text in robotd.raw) < 400
        stamps = [m["t_ms"] for m in robotd.messages]
        assert stamps == sorted(stamps)

        # 4. the camera drops: camera_ok goes false and the face disappears.
        camera.working = False
        assert await wait_for(
            lambda: (
                robotd.messages[-1]["camera_ok"] is False
                and robotd.messages[-1]["faces"] == []
                and robotd.messages[-1]["selected_id"] is None
            )
        )
        # 5. the camera comes back: a new person id, since the old track expired.
        camera.working = True
        assert await wait_for(
            lambda: robotd.messages[-1]["camera_ok"] is True and robotd.messages[-1]["faces"]
        )
        assert robotd.messages[-1]["faces"][0]["id"] == 2

        # 6. robotd goes away: visiond stays alive and forgets the VAD state.
        await robotd.stop()
        assert await wait_for(lambda: not app.link.connected)
        assert not task.done()
        assert await wait_for(lambda: not app.snapshot().state.vad_speaking)
    finally:
        app.stop()
        await asyncio.wait_for(task, timeout=10)
        await robotd.stop()


async def test_console_mode_runs_without_robotd(capsys) -> None:
    app = make_app(free_port(), FakeCamera(), console=True, assume_speaking=True, sim_servo=True)
    app.config = replace(app.config, max_seconds=1.5)
    await asyncio.wait_for(app.run(), timeout=10)
    lines = [line for line in capsys.readouterr().out.splitlines() if "faces=" in line]
    assert lines, "the console mode printed nothing"
    assert any("faces=1" in line and "engaged=YES" in line for line in lines)
    assert all("servo SIMULATED" in line and "vad=ASSUMED" in line for line in lines)
    assert any("speaker=1" in line for line in lines)  # ASSUMED VAD from the moving mouth
    # SIMULATED servo with a fixed camera: the angle moves toward the face and stays bounded.
    last = lines[-1]
    target = float(last.rsplit("target=", 1)[1])
    assert -30.0 < target < -3.0
