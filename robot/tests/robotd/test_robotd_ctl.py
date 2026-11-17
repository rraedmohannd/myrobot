"""robotd's local control API `/ws/ctl` (sprint 5 interface C), over real sockets, against the
SIMULATED ESP32. No backend is needed. Simulation only (CLAUDE.md rule 5)."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from scr_serial import DriveMode, load_table

from robotd import calibration as calib
from robotd.app import RobotApp
from robotd.config import Config
from robotd.ctl import build_message, message_fields

TABLE = load_table()


async def wait_for(condition: Callable[[], bool], within_s: float = 8.0) -> bool:
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return condition()


def _config(tmp_path: Path, **kw: Any) -> Config:
    return Config(
        sim=True, sim_port=0, ui_port=0, cache_dir=str(tmp_path / "cache"),
        backend_url="ws://127.0.0.1:9/ws/robot", backend_reconnect_s=5.0, fault_clear_s=0.2,
        reconnect_s=0.1, log_dir=str(tmp_path / "logs"), **kw,
    )  # fmt: skip


@pytest.fixture
async def app(tmp_path: Path) -> AsyncIterator[RobotApp]:
    robot = RobotApp(_config(tmp_path))
    await robot.start()
    assert await wait_for(lambda: robot.brain.sup.state == "IDLE" and robot.brain.params_ok)
    # the gyro calibration at start (2 s): the ESP32 answers DRIVE with BUSY meanwhile
    assert await wait_for(lambda: "gyro_bias" in [k for k, _ in robot.brain.events])
    yield robot
    await robot.stop()


class Ctl:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws, self.got = ws, []
        self.task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for raw in self.ws:
            self.got.append(json.loads(raw.data))

    async def ask(self, msg: dict[str, Any]) -> dict[str, Any]:
        before = len([m for m in self.got if m["type"] == "ctl.ack"])
        await self.ws.send_json(msg)
        assert await wait_for(lambda: len([m for m in self.got if m["type"] == "ctl.ack"]) > before)
        return [m for m in self.got if m["type"] == "ctl.ack"][-1]

    def rx(self, frame_type: str) -> list[dict[str, Any]]:
        return [
            m for m in self.got if m["type"] == "ctl.serial_rx" and m["frame_type"] == frame_type
        ]


async def test_ctl_bringup_forwarding_and_release(app: RobotApp) -> None:
    sim = app.sim_server.sim  # type: ignore[union-attr]
    async with aiohttp.ClientSession() as session:
        ws = await session.ws_connect(f"http://127.0.0.1:{app.ui.port}/ws/ctl")
        ctl = Ctl(ws)
        await ws.send_json({"type": "ctl.hello", "client": "robotctl"})
        assert await wait_for(lambda: any(m["type"] == "ctl.hello.ack" for m in ctl.got))
        hello = next(m for m in ctl.got if m["type"] == "ctl.hello.ack")
        assert hello["is_simulated"] is True
        assert await wait_for(lambda: any(m["type"] == "ctl.status" for m in ctl.got))
        assert await wait_for(lambda: len(ctl.rx("TELEMETRY")) >= 5)
        tel = ctl.rx("TELEMETRY")[-1]["fields"]
        assert tel["param_hash_hex"] == f"{app.brain.expected_hash:08x}"

        # MODE / DRIVE / BRINGUP need bring-up; read-only requests do not
        ack = await ctl.ask(
            {"type": "ctl.serial", "frame_type": "MODE", "fields": {"mode": "TEST"}}
        )
        assert ack == {"type": "ctl.ack", "request": "ctl.serial", "ok": False,
                       "reason": "MODE needs bring-up on"}  # fmt: skip
        assert (await ctl.ask({"type": "ctl.serial", "frame_type": "DIAG_REQUEST"}))["ok"]
        assert await wait_for(lambda: bool(ctl.rx("DIAG_REPORT")))
        assert ctl.rx("DIAG_REPORT")[-1]["fields"]["build_env"] == "ROBOTD_SIMULATED"
        bad = await ctl.ask({"type": "ctl.serial", "frame_type": "TELEMETRY", "fields": {}})
        assert bad["ok"] is False and "not allowed" in bad["reason"]

        assert (await ctl.ask({"type": "ctl.bringup", "on": True}))["ok"]
        assert app.brain.sup.state == "PAUSED" and app.brain.sup.mode == "bringup"
        duty = TABLE.by_name("TEST_MAX_DUTY").id
        ack = await ctl.ask({"type": "ctl.serial", "frame_type": "PARAM_SET",
                             "fields": {"id": duty, "value": 0.2}})  # fmt: skip
        assert ack["ok"]
        assert await wait_for(
            lambda: any(r["fields"]["id"] == duty for r in ctl.rx("PARAM_REPORT"))
        )
        await ctl.ask({"type": "ctl.serial", "frame_type": "MODE", "fields": {"mode": "TEST"}})
        await ctl.ask({"type": "ctl.serial", "frame_type": "DRIVE",
                       "fields": {"left": 0.1, "right": 0.0}})  # fmt: skip
        assert await wait_for(lambda: sim.speed[0] > 0.0 and sim.speed[1] == 0.0)
        assert app.brain.sup.state == "PAUSED"  # no fault for the changed hash during bring-up
        status = [m for m in ctl.got if m["type"] == "ctl.status"][-1]
        assert await wait_for(
            lambda: [m for m in ctl.got if m["type"] == "ctl.status"][-1]["bringup"] is True
        )
        assert status["calibration"]["is_simulated"] is True

        await ws.close()  # robotctl goes away: STOP, MODE SAFE, the calibration is pushed again
        ctl.task.cancel()
    assert await wait_for(lambda: sim.mode == DriveMode.SAFE and sim.speed == (0.0, 0.0))
    assert await wait_for(lambda: app.brain.params_ok and app.brain.sup.state == "IDLE")
    assert sim.param_hash == app.brain.expected_hash


async def test_ctl_reload_calibration_from_file(tmp_path: Path) -> None:
    path = tmp_path / "calibration.json"
    cal = calib.simulated()
    cal.values.pop("METRES_PER_TICK")  # a robot that is not distance-calibrated yet
    calib.save(cal, path)
    robot = RobotApp(_config(tmp_path, calibration_path=str(path)))
    await robot.start()
    try:
        assert await wait_for(lambda: robot.brain.params_ok and robot.brain.sup.state == "IDLE")
        ack = robot.brain.on_command("c1", "start_roaming", None)
        assert ack["accepted"] is False and "METRES_PER_TICK" in ack["reason"]
        cal.set("METRES_PER_TICK", 0.0005, operator="test", method="SIMULATED")
        calib.save(cal, path)
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(f"http://127.0.0.1:{robot.ui.port}/ws/ctl")
            ctl = Ctl(ws)
            assert (await ctl.ask({"type": "ctl.reload_calibration"}))["ok"]
            await ws.close()
            ctl.task.cancel()
        assert robot.brain.metres_per_tick == 0.0005
        assert await wait_for(lambda: robot.brain.params_ok)
        ack = robot.brain.on_command("c2", "start_roaming", None)
        assert ack["accepted"], ack
    finally:
        await robot.stop()


def test_message_conversion_both_ways() -> None:
    msg = build_message("BRINGUP", {"kind": "IMU_RAW", "arg": 100})
    assert message_fields(msg) == {"kind": "IMU_RAW", "arg": 100}
    assert build_message("MODE", {"mode": 2}).mode == DriveMode.TEST
    with pytest.raises(ValueError, match="bad fields"):
        build_message("PARAM_SET", {"id": 1})
    with pytest.raises(ValueError, match="not allowed"):
        build_message("HEARTBEAT", {})
