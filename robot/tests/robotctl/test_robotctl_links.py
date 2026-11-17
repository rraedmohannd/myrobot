"""robotctl over TCP to the SIMULATED ESP32, over a robotd-like /ws/ctl, the message JSON of the
control API, the bundle allow-list, and return-trial against a fake backend."""

import asyncio
import contextlib
import json
import zipfile
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from scr_serial import (
    CANONICAL_VECTORS,
    DriveMode,
    FrameDecoder,
    Heartbeat,
    Mode,
    encode_frame,
)

from robotctl import cli, wire
from robotctl.bundle import allowed, make_bundle, redact
from robotd.sim_esp32 import SimEsp32, SimEsp32Server


# ------------------------------------------------------------------ control API JSON
def test_wire_round_trip_of_every_canonical_message() -> None:
    for _seq, msg in CANONICAL_VECTORS.values():
        data = json.loads(json.dumps(wire.to_json(msg)))
        assert wire.from_json(data["frame_type"], data["fields"]) == msg


def test_wire_accepts_enum_names() -> None:
    assert wire.from_json("MODE", {"mode": "TEST"}) == Mode(DriveMode.TEST)


# ------------------------------------------------------------------ TCP (real time)
async def test_diag_over_tcp_to_the_simulated_esp32(tmp_path: Path) -> None:
    server = SimEsp32Server(SimEsp32(), port=0)
    port = await server.start()
    lines: list[str] = []
    try:
        code = await cli.run(
            ["diag", "--no-expander", "--sim-tcp", f"127.0.0.1:{port}", "--non-interactive",
             "--calibration", str(tmp_path / "c.json")], out=lines.append,
        )  # fmt: skip
    finally:
        await server.stop()
    text = "\n".join(lines)
    assert "SIMULATED ESP32" in text and "(SIMULATED ESP32)" in text  # transport name
    assert code == 4  # the simulated firmware has PINS_CONFIRMED off
    assert "PINS_CONFIRMED is off" in text


# ------------------------------------------------------------------ /ws/ctl
class FakeCtl:
    """STOP-GAP stand-in for robotd's /ws/ctl (interface section C), used until robotd's own
    endpoint can be tested here: it forwards ctl.serial frames to a SIMULATED ESP32 in real
    time, sends ctl.serial_rx for every frame back, keeps the HEARTBEAT going, and on
    disconnect does bring-up off, STOP, MODE SAFE."""

    def __init__(self, accept: bool = True) -> None:
        self.sim = SimEsp32()
        self.accept = accept
        self.seq = 0
        self.received: list[dict[str, Any]] = []

    def _send(self, msg: Any) -> None:
        self.sim.receive(encode_frame(msg, self.seq))
        self.seq = (self.seq + 1) & 0xFF

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        decoder = FrameDecoder()

        async def pump() -> None:
            n = 0
            while True:
                await asyncio.sleep(0.01)
                n += 1
                if n % 10 == 0:
                    self._send(Heartbeat())
                for frame in decoder.feed(self.sim.step()):
                    await ws.send_str(json.dumps({"type": "ctl.serial_rx",
                                                  **wire.to_json(frame.msg)}))  # fmt: skip

        task = asyncio.create_task(pump())
        try:
            async for raw in ws:
                if raw.type != WSMsgType.TEXT:
                    continue
                data = json.loads(raw.data)
                self.received.append(data)
                if data["type"] == "ctl.bringup":
                    ok = self.accept or not data["on"]
                    await ws.send_str(json.dumps({"type": "ctl.bringup", "on": data["on"],
                                                  "ok": ok, "reason": None if ok else
                                                  "robotd is ROAMING"}))  # fmt: skip
                elif data["type"] == "ctl.serial":
                    self._send(wire.from_json(data["frame_type"], data["fields"]))
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._send(Mode(DriveMode.SAFE))
        return ws


async def _serve(fake: FakeCtl) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_get("/ws/ctl", fake.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return runner, f"ws://127.0.0.1:{port}/ws/ctl"


async def test_motor_test_through_the_ctl_api(tmp_path: Path) -> None:
    fake = FakeCtl()
    runner, url = await _serve(fake)
    lines: list[str] = []
    try:
        code = await cli.run([
            "motor-test", "--side", "right", "--ctl", url, "--non-interactive",
            "--yes-i-confirm", "tracks off the ground", "--yes-i-confirm",
            "raise the duty caps for this test", "--track-moved", "forward", "--accept",
            "--no-save-params", "--calibration", str(tmp_path / "c.json"),
        ], out=lines.append)  # fmt: skip
    finally:
        await runner.cleanup()
    text = "\n".join(lines)
    assert code == 0, text
    assert "SIMULATED" in text  # robotctl saw the SIMULATED flag in telemetry
    kinds = [d["type"] for d in fake.received]
    assert kinds[:2] == ["ctl.hello", "ctl.bringup"] and kinds[-1] == "ctl.bringup"
    assert fake.sim.mode == DriveMode.SAFE
    cal = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert cal["is_simulated"] is True and "MOTOR_INVERT_RIGHT" in cal["values"]


async def test_ctl_refused_when_robotd_is_busy(tmp_path: Path) -> None:
    fake = FakeCtl(accept=False)
    runner, url = await _serve(fake)
    lines: list[str] = []
    try:
        code = await cli.run(
            [
                "status",
                "--ctl",
                url,
                "--non-interactive",
                "--calibration",
                str(tmp_path / "c.json"),
            ],
            out=lines.append,
        )
    finally:
        await runner.cleanup()
    assert code == 3 and "refused bring-up" in "\n".join(lines)
    assert not any(d["type"] == "ctl.serial" for d in fake.received)


# ------------------------------------------------------------------ bundle
def test_bundle_takes_only_allowed_types_and_redacts(tmp_path: Path) -> None:
    src = tmp_path / "logs"
    (src / "sub").mkdir(parents=True)
    for name in ("face.jpg", "snap.png", "voice.wav", "talk.mp3", "clip.mp4", "model.onnx",
                 "noext"):  # fmt: skip
        (src / name).write_bytes(b"\x00\x01binary")
    (src / "robotd.log").write_text("ok\n")
    (src / "robotd.log.1").write_text("old\n")
    (src / "sub" / "blackbox-1.jsonl").write_text('{"t": 1}\n')
    (src / "robot.env").write_text("BACKEND_URL=ws://x\nROBOT_TOKEN=abc123\nSETTINGS_PIN=4321\n")
    (src / "config.json").write_text('{"robot_token": "abc123", "ui_port": 8765}')
    out = tmp_path / "b.zip"
    included, skipped = make_bundle(out, [(src, "logs")])
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        env = zf.read("logs/robot.env").decode()
        conf = zf.read("logs/config.json").decode()
    assert {
        "logs/robotd.log",
        "logs/robotd.log.1",
        "logs/sub/blackbox-1.jsonl",
        "logs/robot.env",
        "logs/config.json",
        "versions.txt",
        "skipped.txt",
    } <= names
    assert not any(n.endswith((".jpg", ".png", ".wav", ".mp3", ".mp4", ".onnx")) for n in names)
    assert "logs/noext" not in names and len(skipped) == 7
    assert "abc123" not in env and "4321" not in env and "BACKEND_URL=ws://x" in env
    assert "abc123" not in conf and '"ui_port": 8765' in conf


def test_allowed_and_redact_helpers() -> None:
    assert allowed(Path("a.jsonl")) and allowed(Path("x.log.3")) and not allowed(Path("a.webm"))
    assert redact("API_KEY = s3cret\nNAME=a") == "API_KEY = <redacted>\nNAME=a"


async def test_bundle_command(tmp_path: Path) -> None:
    (tmp_path / "calibration.json").write_text("{}")
    lines: list[str] = []
    code = await cli.run(["bundle", "--calibration", str(tmp_path / "calibration.json"),
                          "--out", str(tmp_path / "out.zip")], out=lines.append)  # fmt: skip
    assert code == 0
    with zipfile.ZipFile(tmp_path / "out.zip") as zf:
        assert "calibration/calibration.json" in zf.namelist()


# ------------------------------------------------------------------ return-trial
class FakeBackend:
    def __init__(self, simulated: bool) -> None:
        self.simulated = simulated
        self.state = "IDLE"
        self.calls: list[tuple[str, str, Any]] = []
        self.token_seen: list[str] = []

    async def robot(self, request: web.Request) -> web.Response:
        self.token_seen.append(request.headers.get("X-Admin-Token", ""))
        self.calls.append(("GET", request.path, None))
        status = {"state": self.state, "run_id": 7}
        robot = {"is_simulated": self.simulated, "run_id": 7, "status": status}
        return web.json_response({"online": True, "robot": robot})

    async def command(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.calls.append(("POST", request.path, body))
        if body["cmd"] == "start_roaming":
            self.state = "ROAMING"
            return web.json_response({"accepted": True, "run_id": 7})
        self.state = "IDLE"
        return web.json_response({"accepted": True, "run_id": 7, "return_trial_id": 31})

    async def measure(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.calls.append(("PUT", request.path, body))
        return web.json_response({"id": 31, "run_id": 7, "status": "measured"})


async def _backend(fake: FakeBackend) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_get("/api/admin/robot", fake.robot)
    app.router.add_post("/api/admin/robot/command", fake.command)
    app.router.add_put("/api/admin/return-trials/{tid}", fake.measure)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{runner.addresses[0][1]}"


async def test_return_trial_refuses_a_simulated_robot(tmp_path: Path) -> None:
    fake = FakeBackend(simulated=True)
    runner, url = await _backend(fake)
    lines: list[str] = []
    try:
        code = await cli.run([
            "return-trial", "--backend", url, "--admin-token", "t0k", "--non-interactive",
            "--yes-i-confirm", "floor clear for roaming", "--measured", "0.1",
            "--calibration", str(tmp_path / "c.json"),
        ], out=lines.append)  # fmt: skip
    finally:
        await runner.cleanup()
    assert code == 2 and "SIMULATED" in "\n".join(lines)
    assert not any(c[0] == "POST" for c in fake.calls)


async def test_return_trial_needs_a_token(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("SCR_ADMIN_TOKEN", raising=False)
    lines: list[str] = []
    code = await cli.run(
        ["return-trial", "--non-interactive", "--calibration", str(tmp_path / "c.json")],
        out=lines.append,
    )
    assert code == 2 and "admin token" in "\n".join(lines)


async def test_return_trial_saves_the_tape_measurement(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """The backend here is FAKE (a stand-in for the admin API); the flow is what is tested."""
    monkeypatch.setenv("SCR_ADMIN_TOKEN", "t0k")
    fake = FakeBackend(simulated=False)
    runner, url = await _backend(fake)
    lines: list[str] = []
    try:
        code = await cli.run([
            "return-trial", "--backend", url, "--non-interactive", "--roam-secs", "5",
            "--poll-s", "0.5", "--yes-i-confirm", "floor clear for roaming",
            "--measured", "0.12", "--heading-err-deg", "4", "--calibration",
            str(tmp_path / "c.json"),
        ], out=lines.append)  # fmt: skip
    finally:
        await runner.cleanup()
    assert code == 0, "\n".join(lines)
    put = [c for c in fake.calls if c[0] == "PUT"]
    assert put and put[0][1] == "/api/admin/return-trials/31"
    assert put[0][2]["measured_err_m"] == 0.12 and put[0][2]["success"] is True
    assert set(fake.token_seen) == {"t0k"}
    log = (tmp_path / "calibration-log.jsonl").read_text(encoding="utf-8")
    assert '"step": "return-trial"' in log and "t0k" not in log


async def test_motor_test_through_robotd_itself(tmp_path: Path) -> None:
    """robotd --sim (SIMULATED ESP32, SIMULATED calibration profile) with its real /ws/ctl."""
    from robotd import calibration as calib
    from robotd.app import RobotApp
    from robotd.config import Config

    path = tmp_path / "calibration.json"
    calib.save(calib.simulated(), path)
    robot = RobotApp(Config(
        sim=True, sim_port=0, ui_port=0, cache_dir=str(tmp_path / "cache"),
        backend_url="ws://127.0.0.1:9/ws/robot", backend_reconnect_s=5.0, fault_clear_s=0.2,
        reconnect_s=0.1, log_dir=str(tmp_path / "logs"), calibration_path=str(path),
    ))  # fmt: skip
    await robot.start()
    lines: list[str] = []
    try:
        for _ in range(400):  # robotd: IDLE, parameters pushed, start-up gyro calibration done
            if (
                robot.brain.sup.state == "IDLE"
                and robot.brain.params_ok
                and "gyro_bias" in [k for k, _ in robot.brain.events]
            ):
                break
            await asyncio.sleep(0.02)
        code = await cli.run([
            "motor-test", "--side", "left", "--ctl", f"ws://127.0.0.1:{robot.ui.port}/ws/ctl",
            "--non-interactive", "--yes-i-confirm", "tracks off the ground", "--track-moved",
            "forward", "--accept", "--no-save-params", "--calibration", str(path),
        ], out=lines.append)  # fmt: skip
    finally:
        await robot.stop()
    text = "\n".join(lines)
    assert code == 0, text
    assert "[SIMULATED]" in text and "robotd ws://" in text
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["is_simulated"] is True
    assert data["values"]["MOTOR_INVERT_LEFT"]["method"].startswith("SIMULATED: motor-test")
