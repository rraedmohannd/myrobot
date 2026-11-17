"""Sprint 3 S2: live robot status and command requests (protocol version 2).

Commands are requests: the fake robot here accepts, refuses, or stays silent, and the backend
must report each case truthfully and never send anything when no robot is connected.
"""

import asyncio
import threading
import time

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.db.models import Event, ReturnTrial, RobotRun, Session
from app.db.session import get_sessionmaker
from app.main import app
from app.services.providers import get_embedder
from app.services.robot_hub import SIMULATED_LABEL, RobotHub, RobotLink, RobotOffline, get_robot_hub
from app.services.speech import get_tts
from app.telemetry.sim_robot import SimulatedRobot
from tests.conftest import ADMIN_HEADERS
from tests.fakes import FakeEmbedder, FakeTTS, OneSession

STATUS = {"type": "robot.status", "state": "IDLE", "is_simulated": True, "battery_v": 7.9,
          "battery_pct": 75, "faults": [], "sensors": {"imu": "ok", "encoders": "ok"},
          "pose": {"x_m": 0.5, "y_m": -0.25, "theta_rad": 0.1}}  # fmt: skip


class FakeRobot:
    """Stands in for the robot side of one connection: records what it receives and answers
    `robot.command` with the configured ack (or not at all)."""

    def __init__(self, hub: RobotHub, accept: bool | None = True, reason: str | None = None,
                 simulated: bool = True, version: int = 2) -> None:  # fmt: skip
        self.hub, self.accept, self.reason = hub, accept, reason
        self.received: list[dict] = []
        self.link: RobotLink = hub.register(self.send)
        self.link.hello({"robot_id": "fake-robot", "protocol_version": version})
        self.status = {**STATUS, "is_simulated": simulated}

    def report(self, **changes) -> None:  # noqa: ANN003
        self.hub.on_status(self.link, {**self.status, **changes})

    async def send(self, msg: dict) -> None:
        self.received.append(msg)
        if msg["type"] == "robot.command" and self.accept is not None:
            ack = {"type": "robot.command.ack", "id": msg["id"], "accepted": self.accept}
            if self.reason:
                ack["reason"] = self.reason
            asyncio.get_running_loop().call_soon(self.hub.on_ack, self.link, ack)


async def _events(db_session) -> list[Event]:
    rows = await db_session.scalars(
        select(Event)
        .where(Event.kind.in_(("command", "command_ack")))
        .order_by(Event.id.desc())
        .limit(2)
    )
    return list(reversed(rows.all()))


async def test_accepted_command_is_sent_acked_and_stored(db_session) -> None:
    hub = RobotHub()
    robot = FakeRobot(hub)
    robot.report()
    record = await hub.send_command(OneSession(db_session), "start_roaming")
    assert (
        record["accepted"] is True
        and record["timed_out"] is False
        and record["is_simulated"] is True
    )
    sent = robot.received[0]
    assert sent == {"type": "robot.command", "id": record["id"], "cmd": "start_roaming"}
    command, ack = await _events(db_session)
    assert (command.kind, command.data["cmd"], command.data["id"]) == (
        "command",
        "start_roaming",
        record["id"],
    )
    assert (ack.kind, ack.data["accepted"], ack.data["id"]) == ("command_ack", True, record["id"])
    assert command.run_id is None  # no run is open: the event stands alone
    snap = hub.snapshot()
    assert (
        snap["online"]
        and snap["robot"]["label"] == SIMULATED_LABEL
        and snap["robot"]["is_simulated"]
    )
    assert snap["robot"]["status"]["battery_v"] == 7.9 and snap["robot"]["trail"] == [(0.5, -0.25)]
    assert snap["commands"][0]["id"] == record["id"] and not hub.pending


async def test_robot_may_refuse_and_set_mode_carries_args(db_session) -> None:
    hub = RobotHub()
    robot = FakeRobot(hub, accept=False, reason="a conversation is in progress")
    robot.report(state="CONVERSATION")
    record = await hub.send_command(OneSession(db_session), "return_to_start")
    assert record["accepted"] is False and record["reason"] == "a conversation is in progress"
    assert record["return_trial_id"] is None
    ack = (await _events(db_session))[1]
    assert ack.data == {
        "id": record["id"],
        "accepted": False,
        "reason": "a conversation is in progress",
    }

    robot.accept = True
    record = await hub.send_command(OneSession(db_session), "set_mode", {"mode": "quiet"})
    assert robot.received[-1]["args"] == {"mode": "quiet"} and record["accepted"]
    with pytest.raises(ValueError):
        await hub.send_command(OneSession(db_session), "self_destruct")


async def test_missing_ack_is_reported_as_a_timeout(db_session) -> None:
    hub = RobotHub()
    robot = FakeRobot(hub, accept=None)  # the robot never answers
    robot.report()
    started = time.monotonic()
    record = await hub.send_command(OneSession(db_session), "pause", timeout_s=0.1)
    assert time.monotonic() - started < 1.0
    assert (
        record["timed_out"] is True
        and record["accepted"] is None
        and "no answer" in record["reason"]
    )
    ack = (await _events(db_session))[1]
    assert ack.data == {"id": record["id"], "timed_out": True}
    assert not hub.pending
    # a late ack for the expired command changes nothing and breaks nothing
    hub.on_ack(robot.link, {"type": "robot.command.ack", "id": record["id"], "accepted": True})
    assert hub.snapshot()["commands"][0]["accepted"] is None


async def test_nothing_is_sent_when_no_robot_is_connected(db_session, monkeypatch) -> None:
    hub = RobotHub()
    maker = OneSession(db_session)
    before = len(await _events(db_session))
    with pytest.raises(RobotOffline):
        await hub.send_command(maker, "start_roaming")

    page = FakeRobot(hub, version=2)  # a browser page: connected, but it never sent robot.status
    with pytest.raises(RobotOffline):
        await hub.send_command(maker, "start_roaming")
    old = FakeRobot(hub, version=1)  # a version 1 client does not understand robot.command
    old.report()
    with pytest.raises(RobotOffline):
        await hub.send_command(maker, "start_roaming")
    gone = FakeRobot(hub)
    gone.report()
    gone.link.status_at = time.monotonic() - 60  # silent for a minute: offline
    with pytest.raises(RobotOffline):
        await hub.send_command(maker, "start_roaming")

    assert page.received == old.received == gone.received == []
    assert len(await _events(db_session)) == before and not hub.pending
    assert hub.snapshot()["online"] is False and hub.snapshot()["robot"] is None


async def test_return_to_start_opens_a_pending_trial_only_on_real_runs(
    admin_client, db_session
) -> None:
    maker = OneSession(db_session)
    real, simulated = RobotRun(is_simulated=False), RobotRun(is_simulated=True)
    db_session.add_all([real, simulated])
    await db_session.commit()

    hub = RobotHub()
    sim = FakeRobot(hub, simulated=True)
    sim.link.run_id = simulated.id
    sim.report()
    record = await hub.send_command(maker, "return_to_start")
    assert (
        record["accepted"] and record["return_trial_id"] is None
    )  # rule 5: never for a simulation
    command = (await _events(db_session))[0]
    assert command.run_id == simulated.id and command.t_ms >= 0
    hub.unregister(sim.link)

    robot = FakeRobot(hub, simulated=False)
    robot.link.run_id = real.id
    robot.report()
    record = await hub.send_command(maker, "return_to_start")
    trial = await db_session.get(ReturnTrial, record["return_trial_id"])
    assert trial.status == "pending" and trial.run_id == real.id and trial.measured_err_m is None
    assert trial.command_id == record["id"]

    # pending trials are not results: the statistics ignore them until the tape measurement
    stats = (await admin_client.get("/api/admin/return-trials/stats")).json()
    before = stats["trials"]
    measured = await admin_client.put(f"/api/admin/return-trials/{trial.id}",
                                      json={"measured_err_m": 0.21, "success": True})  # fmt: skip
    assert measured.status_code == 200 and measured.json()["status"] == "measured"
    assert (await admin_client.get("/api/admin/return-trials/stats")).json()["trials"] == before + 1
    again = await admin_client.delete(f"/api/admin/return-trials/{trial.id}")
    assert (
        again.status_code == 409
    )  # a measured trial is a result; only pending ones can be discarded


async def test_robot_api_offline_state_and_auth(admin_client, client, db_session) -> None:
    hub = RobotHub()
    app.dependency_overrides[get_robot_hub] = lambda: hub
    app.dependency_overrides[get_sessionmaker] = lambda: OneSession(db_session)
    try:
        assert (await client.get("/api/admin/robot")).status_code == 401
        assert (
            await client.post("/api/admin/robot/command", json={"cmd": "pause"})
        ).status_code == 401
        state = (await admin_client.get("/api/admin/robot")).json()
        assert state["online"] is False and state["robot"] is None and "requests" in state["note"]
        offline = await admin_client.post("/api/admin/robot/command", json={"cmd": "start_roaming"})
        assert offline.status_code == 503 and "nothing was sent" in offline.json()["detail"]
        assert (
            await admin_client.post("/api/admin/robot/command", json={"cmd": "dance"})
        ).status_code == 422
        assert (
            await admin_client.post("/api/admin/robot/command", json={"cmd": "set_mode"})
        ).status_code == 422

        robot = FakeRobot(hub, accept=False, reason="fault active: estop")
        robot.report(state="FAULT", faults=["estop"])
        refused = await admin_client.post("/api/admin/robot/command", json={"cmd": "start_roaming"})
        assert refused.status_code == 200 and refused.json()["accepted"] is False
        state = (await admin_client.get("/api/admin/robot")).json()
        assert state["online"] and state["robot"]["status"]["faults"] == ["estop"]
        assert state["commands"][0]["reason"] == "fault active: estop"
        history = (await admin_client.get("/api/admin/robot/commands")).json()["items"]
        assert [e["kind"] for e in history[:2]] == ["command_ack", "command"]
    finally:
        app.dependency_overrides.pop(get_robot_hub, None)
        app.dependency_overrides.pop(get_sessionmaker, None)


def test_status_rules() -> None:
    hub = RobotHub()
    robot = FakeRobot(hub)
    from scr_protocol import ProtocolError

    with pytest.raises(ProtocolError):  # a robot must say whether it is a simulation
        hub.on_status(robot.link, {"type": "robot.status", "state": "IDLE"})
    with pytest.raises(ProtocolError):
        hub.on_status(robot.link, {**STATUS, "state": "FLYING"})
    assert hub.snapshot()["online"] is False
    robot.report(is_simulated=False)
    assert hub.snapshot()["robot"]["label"] == "Physical robot"


def test_simulated_robot_decides_locally() -> None:
    robot = SimulatedRobot(seed=7)
    assert robot.status()["is_simulated"] is True and robot.status()["state"] == "IDLE"
    assert robot.command("pause")[0] is False and robot.command("return_to_start")[0] is False
    robot.begin_conversation()
    accepted, reason, _ = robot.command("start_roaming")
    assert not accepted and "conversation" in reason
    robot.end_conversation()

    accepted, _, effects = robot.command("start_roaming")
    assert accepted and effects[0]["type"] == "run.start" and effects[0]["is_simulated"] is True
    out = robot.tick()
    assert (
        out[0]["type"] == "telemetry" and len(out[0]["samples"]) == 10 and robot.state == "ROAMING"
    )
    assert robot.command("start_roaming")[0] is False  # already roaming
    assert robot.command("pause")[0] and robot.state == "PAUSED" and robot.tick() == []
    assert robot.command("resume")[0] and robot.state == "ROAMING"
    accepted, _, effects = robot.command("return_to_start")
    assert accepted and effects[0]["kind"] == "return_start" and robot.state == "RETURNING"
    kinds = []
    for _ in range(400):
        kinds += [m.get("kind") or m["type"] for m in robot.tick()]
        if robot.state == "IDLE":
            break
    assert robot.state == "IDLE" and kinds[-2:] == ["return_done", "run.end"]
    robot.faults.append("estop")
    assert robot.command("start_roaming") == (False, "fault active: estop", [])
    assert robot.command("set_mode", {"mode": "quiet"})[0] and robot.mode == "quiet"
    assert robot.command("set_mode")[0] is False


class _NullSession:
    """No database for the WebSocket-level test (it runs on the test client's own event loop)."""

    added: list = []

    def __call__(self):  # noqa: ANN204
        return self

    async def __aenter__(self):  # noqa: ANN204
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, *args: object):  # noqa: ANN201
        return None

    def add(self, row: object) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        return None


def test_command_flow_over_the_websocket_with_a_fake_robot_client() -> None:
    hub, null = RobotHub(), _NullSession()
    null.added.clear()
    app.dependency_overrides[get_robot_hub] = lambda: hub
    app.dependency_overrides[get_sessionmaker] = lambda: null
    try:
        with TestClient(app) as tc, tc.websocket_connect("/ws/robot") as ws:
            ws.send_json({"type": "hello", "robot_id": "pi-test", "protocol_version": 2})
            assert ws.receive_json()["protocol_version"] == 2
            ws.send_json({"type": "robot.status", "state": "IDLE"})  # is_simulated is missing
            error = ws.receive_json()
            assert error["type"] == "error" and error["code"] == "bad_message"
            ws.send_json(STATUS)
            ws.send_json({"type": "heartbeat"})
            assert ws.receive_json()["type"] == "heartbeat"
            state = tc.get("/api/admin/robot", headers=ADMIN_HEADERS).json()
            assert state["online"] and state["robot"]["robot_id"] == "pi-test"
            assert state["robot"]["label"] == SIMULATED_LABEL

            result: dict = {}

            def press_button() -> None:
                r = tc.post(
                    "/api/admin/robot/command", json={"cmd": "pause"}, headers=ADMIN_HEADERS
                )
                result.update(status=r.status_code, body=r.json())

            thread = threading.Thread(target=press_button)
            thread.start()
            command = ws.receive_json()
            assert (
                command["type"] == "robot.command" and command["cmd"] == "pause" and command["ts"]
            )
            ws.send_json({"type": "robot.command.ack", "id": command["id"], "accepted": False,
                          "reason": "nothing to pause (state IDLE)"})  # fmt: skip
            thread.join(timeout=10)
            assert result["status"] == 200 and result["body"]["id"] == command["id"]
            assert (
                result["body"]["accepted"] is False
                and "nothing to pause" in result["body"]["reason"]
            )
            assert [e.kind for e in null.added] == ["command", "command_ack"]
        assert hub.links == {}  # the connection is forgotten when the socket closes
        assert hub.snapshot()["online"] is False
    finally:
        app.dependency_overrides.pop(get_robot_hub, None)
        app.dependency_overrides.pop(get_sessionmaker, None)


# ---------------- two connections: the screen page talks, robotd records the run ----------------
class _MemoryDb(_NullSession):
    """Keeps rows in memory and numbers them, so two WebSocket connections can share runs and
    sessions without touching the database."""

    def __init__(self) -> None:
        self.rows: dict[tuple[type, int], object] = {}

    def add(self, row: object) -> None:
        row.id = len(self.rows) + 1  # type: ignore[attr-defined]
        self.rows[(type(row), row.id)] = row  # type: ignore[attr-defined]

    async def get(self, model: type, key: int):  # noqa: ANN201
        return self.rows.get((model, key))

    async def refresh(self, row: object) -> None:
        return None

    async def scalar(self, *args: object) -> int:
        return 0


def _settle(ws) -> None:  # noqa: ANN001
    """Wait until the connection's worker has handled everything sent so far."""
    ws.send_json({"type": "nonsense"})
    assert ws.receive_json()["code"] == "unknown_type"


def _robot_online(ws) -> None:  # noqa: ANN001
    ws.send_json({"type": "hello", "robot_id": "robotd-test", "protocol_version": 2})
    assert ws.receive_json()["type"] == "hello.ack"
    ws.send_json(STATUS)
    ws.send_json({"type": "heartbeat"})
    assert ws.receive_json()["type"] == "heartbeat"


def _open_run(ws) -> int:  # noqa: ANN001
    ws.send_json({"type": "run.start", "is_simulated": True})  # SIMULATED run (rule 5)
    started = ws.receive_json()
    assert started["type"] == "run.started" and started["is_simulated"] is True
    _settle(ws)
    return started["run_id"]


def _session_run_id(tc: TestClient, db: _MemoryDb, own_run: bool = False) -> tuple:
    """A screen page connects and starts a conversation: (the session's robot_run_id, the id
    of the run opened on the page's own connection when `own_run`)."""
    with tc.websocket_connect("/ws/robot") as page:
        own = _open_run(page) if own_run else None
        page.send_json({"type": "session.start", "lang": "ar"})
        while (msg := page.receive_json())["type"] != "session.started":
            pass
        while not (msg["type"] == "dialogue.state" and msg["state"] == "LISTENING"):
            msg = page.receive_json()
        session = next(r for r in db.rows.values() if isinstance(r, Session) and r.ended_at is None)
        return session.robot_run_id, own


def test_session_on_the_page_connection_is_linked_to_the_robots_open_run() -> None:
    hub, db = RobotHub(), _MemoryDb()
    app.dependency_overrides[get_robot_hub] = lambda: hub
    app.dependency_overrides[get_sessionmaker] = lambda: db
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    app.dependency_overrides[get_tts] = lambda: FakeTTS()
    try:
        with TestClient(app) as tc:
            assert _session_run_id(tc, db) == (None, None)  # no robot connected: no link
            with tc.websocket_connect("/ws/robot") as robot:
                _robot_online(robot)
                assert _session_run_id(tc, db) == (None, None)  # robot online, no run open
                run_id = _open_run(robot)
                assert hub.open_run_id() == run_id
                assert _session_run_id(tc, db) == (run_id, None)  # the other connection's run

                linked, own = _session_run_id(tc, db, own_run=True)
                assert linked == own != run_id  # a connection's own run still comes first

                robot.send_json({"type": "run.end"})
                assert robot.receive_json()["type"] == "run.ended"
                _settle(robot)
                assert _session_run_id(tc, db) == (None, None)  # the run has ended

                run_id = _open_run(robot)
                assert _session_run_id(tc, db) == (run_id, None)
            deadline = time.monotonic() + 5
            while hub.links and time.monotonic() < deadline:
                time.sleep(0.01)
            assert hub.links == {}
            assert _session_run_id(tc, db) == (None, None)  # the robot link is gone
            assert db.rows[(RobotRun, run_id)].ended_at is not None  # closed by the disconnect
    finally:
        for dep in (get_robot_hub, get_sessionmaker, get_embedder, get_tts):
            app.dependency_overrides.pop(dep, None)


def test_open_run_is_only_taken_from_a_live_robot_link() -> None:
    hub = RobotHub()
    assert hub.open_run_id() is None
    page = FakeRobot(hub)  # a browser page: it never sends robot.status
    page.link.run_id = 11
    old = FakeRobot(hub, version=1)
    old.link.run_id = 12
    old.report()
    assert hub.open_run_id() is None
    robot = FakeRobot(hub)
    robot.link.run_id = 13
    robot.report()
    assert hub.open_run_id() == 13
    robot.link.status_at = time.monotonic() - 60  # silent for a minute: offline
    assert hub.open_run_id() is None
    robot.report()
    robot.link.run_id = None  # run.end
    assert hub.open_run_id() is None
    robot.link.run_id = 14
    hub.unregister(robot.link)
    assert hub.open_run_id() is None
