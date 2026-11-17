"""Robot runs (demo sprint M6): odometry maths, the SIMULATED run generator, storage through
the /ws/robot telemetry messages, and the rule that simulated runs never get return trials."""

import math

import pytest
from sqlalchemy import func, select

from app.db.models import Event, PoseSample, ReturnTrial, RobotRun, Session
from app.services.dialogue import DialogueManager
from app.telemetry.odometry import (
    Pose,
    encoder_heading_change,
    track_distances,
    update_pose,
    vector_to_origin,
    wrap_angle,
)
from app.telemetry.simulator import SimConfig, range_ahead, simulate_run
from app.telemetry.store import (
    ReturnTrialIn,
    TelemetryError,
    TelemetryRecorder,
    add_return_trial,
    start_run,
)
from tests.fakes import FakeEmbedder, FakeLLM, FakeSTT, FakeTTS, OneSession


# ---------------- kinematics (docs/ODOMETRY.md section 3) ----------------
def test_pose_update_straight_and_turn() -> None:
    p = update_pose(Pose(), 0.10, 0.10, gyro_z=0.0, dt=0.02)
    assert (p.x, p.y, p.theta) == pytest.approx((0.10, 0.0, 0.0))

    p = update_pose(Pose(theta=math.pi / 2), 0.10, 0.10, 0.0, 0.02)  # facing +y (left)
    assert (p.x, p.y) == pytest.approx((0.0, 0.10), abs=1e-12)

    p = update_pose(Pose(), 0.05, -0.05, gyro_z=1.0, dt=0.02)  # turn in place: gyro gives heading
    assert (p.x, p.y, p.theta) == pytest.approx((0.0, 0.0, 0.02))

    # the heading comes from the gyro, NOT from the encoders (tracks slip while turning)
    p = update_pose(Pose(), 0.0, 0.10, gyro_z=0.0, dt=0.02)
    assert p.theta == 0.0 and p.x == pytest.approx(0.05)
    assert encoder_heading_change(0.0, 0.10, track_width_m=0.2) == pytest.approx(0.5)


def test_pose_update_uses_the_mid_heading_and_wraps() -> None:
    p = update_pose(Pose(), 0.2, 0.2, gyro_z=math.pi / 2, dt=1.0)  # quarter turn while driving
    assert (p.x, p.y) == pytest.approx((0.2 * math.cos(math.pi / 4), 0.2 * math.sin(math.pi / 4)))
    assert p.theta == pytest.approx(math.pi / 2)
    p = update_pose(Pose(theta=3.0), 0.0, 0.0, gyro_z=1.0, dt=0.5)
    assert -math.pi <= p.theta <= math.pi and p.theta == pytest.approx(3.5 - 2 * math.pi)
    assert wrap_angle(-3.5) == pytest.approx(-3.5 + 2 * math.pi)


def test_square_path_closes_and_vector_to_origin() -> None:
    pose = Pose()
    for _ in range(4):  # 2 m x 2 m square, counter-clockwise, ideal sensors
        for _ in range(100):
            pose = update_pose(pose, 0.02, 0.02, 0.0, 0.02)
        for _ in range(50):
            pose = update_pose(pose, -0.001, 0.001, gyro_z=math.pi, dt=0.01)
    assert (pose.x, pose.y) == pytest.approx((0.0, 0.0), abs=1e-9)
    distance, bearing = vector_to_origin(Pose(x=3.0, y=4.0))
    assert distance == pytest.approx(5.0) and bearing == pytest.approx(math.atan2(-4.0, -3.0))
    assert track_distances(100, 200, metres_per_tick=0.001) == pytest.approx((0.1, 0.2))


# ---------------- simulator ----------------
def test_simulated_run_is_deterministic_and_stays_in_the_room() -> None:
    cfg = SimConfig(seed=7)
    a, b = simulate_run(cfg), simulate_run(SimConfig(seed=7))
    assert a.samples == b.samples and a.events == b.events  # same seed, same run
    assert simulate_run(SimConfig(seed=8)).samples != a.samples

    x0, y0, x1, y1 = cfg.room
    for s in a.samples:
        tx, ty, _ = s["imu"]["sim_true_pose"]
        assert x0 < tx < x1 and y0 < ty < y1
        assert not any(ox0 < tx < ox1 and oy0 < ty < oy1 for ox0, oy0, ox1, oy1 in cfg.obstacles)
    assert [s["t_ms"] for s in a.samples] == sorted(s["t_ms"] for s in a.samples)

    kinds = [e["kind"] for e in a.events]
    assert kinds[0] == "run_start" and "return_start" in kinds
    assert kinds[-1] in ("return_done", "return_failed") and a.outcome == kinds[-1]
    end = a.events[-1]["data"]
    assert end["simulated"] is True and "simulated_true_error_m" in end
    if a.outcome == "return_done":
        assert end["odom_distance_m"] <= cfg.arrival_tolerance_m + 0.01
    assert any(s["state"].startswith("return") for s in a.samples)
    assert a.odom_config()["simulated"] is True and a.odom_config()["sim"]["seed"] == 7


def test_simulated_segments_are_at_most_five_metres() -> None:
    cfg = SimConfig(seed=3)
    run = simulate_run(cfg)
    longest, start = 0.0, None
    for s in run.samples:
        if s["state"] == "roam":
            start = start or (s["x_m"], s["y_m"])
            longest = max(longest, math.hypot(s["x_m"] - start[0], s["y_m"] - start[1]))
        else:
            start = None
    assert 0.5 < longest <= cfg.max_segment_m + 0.05


def test_breadcrumb_fallback_and_noise_are_simulated() -> None:
    methods = {simulate_run(SimConfig(seed=s)).events[-1]["data"]["method"] for s in (7, 16)}
    assert methods == {"direct", "breadcrumbs"}  # seed 16: the direct path is blocked
    blocked = simulate_run(SimConfig(seed=16))
    assert "return_blocked" in [e["kind"] for e in blocked.events]
    last = blocked.samples[-1]
    tx, ty, _ = last["imu"]["sim_true_pose"]  # slip and gyro bias: odometry differs from truth
    assert math.hypot(tx - last["x_m"], ty - last["y_m"]) > 0.01
    assert last["enc"]["enc_theta_rad"] != last["theta_rad"]
    # empty room: the side ray (25 degrees) reaches the right wall before the centre ray
    assert range_ahead(SimConfig(obstacles=()), Pose()) == pytest.approx(1.5 / math.sin(0.44))
    assert range_ahead(SimConfig(), Pose(x=1.0, y=1.2)) == pytest.approx(0.6)  # an obstacle


# ---------------- storage over /ws/robot ----------------
def _recorder(db_session) -> tuple[TelemetryRecorder, list[dict]]:
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    return TelemetryRecorder(send, OneSession(db_session)), sent


async def test_telemetry_messages_are_stored(db_session) -> None:
    rec, sent = _recorder(db_session)
    await rec.handle({"type": "telemetry", "samples": []})
    assert sent[-1]["code"] == "no_run"
    await rec.handle({"type": "run.start"})  # is_simulated is required: no silent default
    assert sent[-1]["code"] == "bad_message" and rec.run_id is None

    sim = simulate_run(SimConfig(seed=7, roam_seconds=5))
    await rec.handle({"type": "run.start", "is_simulated": True, "odom_config": sim.odom_config()})
    run_id = sent[-1]["run_id"]
    assert sent[-1] == {"type": "run.started", "run_id": run_id, "is_simulated": True}
    await rec.handle({"type": "telemetry", "samples": sim.samples})
    for event in sim.events:
        await rec.handle({"type": "event", **event})
    await rec.handle({"type": "telemetry", "samples": [{"t_ms": 1, "x_m": "north"}]})
    assert sent[-1]["code"] == "bad_message"
    await rec.handle({"type": "event", "t_ms": 5})
    assert sent[-1]["code"] == "bad_message"
    await rec.handle({"type": "run.end"})
    assert sent[-1] == {"type": "run.ended", "run_id": run_id, "samples": len(sim.samples),
                        "events": len(sim.events)}  # fmt: skip
    assert rec.run_id is None

    run = await db_session.get(RobotRun, run_id)
    assert run.is_simulated is True and run.ended_at is not None
    assert run.odom_config["simulated"] is True
    first = await db_session.scalar(
        select(PoseSample).where(PoseSample.run_id == run_id).order_by(PoseSample.t_ms).limit(1)
    )
    assert (first.x_m, first.y_m, first.theta_rad, first.state) == (0.0, 0.0, 0.0, "start")
    kinds = (await db_session.scalars(select(Event.kind).where(Event.run_id == run_id))).all()
    assert "return_start" in kinds


async def test_conversations_during_a_run_are_linked_to_it(db_session) -> None:
    run = await start_run(db_session, False, {"track_width_m": 0.19})

    async def send(msg: dict) -> None:
        pass

    dm = DialogueManager(
        send, OneSession(db_session), FakeEmbedder(), FakeLLM(), FakeSTT([]), FakeTTS()
    )
    dm.robot_run_id = run.id
    await dm.handle({"type": "session.start"})
    assert (await db_session.get(Session, dm.session_id)).robot_run_id == run.id


def test_websocket_run_start_and_end() -> None:
    from starlette.testclient import TestClient

    from app.main import app

    with TestClient(app) as tc, tc.websocket_connect("/ws/robot") as ws:
        ws.send_json({"type": "event", "t_ms": 0, "kind": "obstacle"})
        assert ws.receive_json()["code"] == "no_run"
        ws.send_json({"type": "run.start", "is_simulated": "yes"})
        assert ws.receive_json()["code"] == "bad_message"


# ---------------- return trials: physical runs only ----------------
async def test_simulated_runs_cannot_get_return_trials(db_session) -> None:
    simulated = await start_run(db_session, True, {"simulated": True})
    real = await start_run(db_session, False)
    trial = ReturnTrialIn(measured_err_m=0.12, measured_heading_err_deg=4, success=True)

    with pytest.raises(TelemetryError) as err:
        await add_return_trial(db_session, simulated.id, trial)
    assert err.value.code == "simulated"
    with pytest.raises(TelemetryError):
        await add_return_trial(db_session, 999999999, trial)
    count = await db_session.scalar(
        select(func.count()).select_from(ReturnTrial).where(ReturnTrial.run_id == simulated.id)
    )
    assert count == 0

    row = await add_return_trial(db_session, real.id, trial)
    assert row.run_id == real.id and row.measured_err_m == 0.12 and row.success is True


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/admin/runs"), ("get", "/api/admin/runs/1"),
     ("post", "/api/admin/runs/1/return-trials"), ("get", "/api/admin/return-trials/stats")],
)  # fmt: skip
async def test_runs_api_needs_the_token(client, method: str, path: str) -> None:
    assert (await getattr(client, method)(path)).status_code == 401


async def test_runs_api(admin_client, db_session) -> None:
    before = (await admin_client.get("/api/admin/return-trials/stats")).json()
    sim = simulate_run(SimConfig(seed=16))
    rec, sent = _recorder(db_session)
    await rec.handle({"type": "run.start", "is_simulated": True, "odom_config": sim.odom_config()})
    sim_id = sent[-1]["run_id"]
    for i in range(0, len(sim.samples), 1000):
        await rec.handle({"type": "telemetry", "samples": sim.samples[i : i + 1000]})
    for event in sim.events:
        await rec.handle({"type": "event", **event})
    await rec.handle({"type": "run.end"})
    real = await start_run(db_session, False, notes="corridor test")

    listed = (await admin_client.get("/api/admin/runs")).json()["items"]
    by_id = {r["id"]: r for r in listed}
    assert (
        by_id[sim_id]["is_simulated"]
        and by_id[sim_id]["label"] == "SIMULATED — not a physical result"
    )
    assert by_id[sim_id]["samples"] == len(sim.samples) and by_id[sim_id]["events"] == len(
        sim.events
    )
    assert by_id[real.id]["label"] == "Physical run"

    r = await admin_client.get(f"/api/admin/runs/{sim_id}", params={"max_points": 200})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["is_simulated"] and d["can_add_return_trial"] is False
    assert 100 <= len(d["points"]) <= 201 and d["points"][-1]["t_ms"] == sim.samples[-1]["t_ms"]
    assert {p["phase"] for p in d["points"]} == {"outbound", "return"}
    assert d["points"][0]["sim_true"] == [0.0, 0.0]
    assert d["events"][-1]["kind"] == sim.outcome and d["odom_config"]["sim"]["seed"] == 16

    body = {"measured_err_m": 0.2, "success": True}
    refused = await admin_client.post(f"/api/admin/runs/{sim_id}/return-trials", json=body)
    assert refused.status_code == 409 and "SIMULATED" in refused.json()["detail"]
    assert (
        await admin_client.post("/api/admin/runs/999999999/return-trials", json=body)
    ).status_code == 404
    bad = await admin_client.post(
        f"/api/admin/runs/{real.id}/return-trials", json={"measured_err_m": -1, "success": True}
    )
    assert bad.status_code == 422

    for err, ok in ((0.10, True), (0.30, False)):
        r = await admin_client.post(
            f"/api/admin/runs/{real.id}/return-trials",
            json={"measured_err_m": err, "success": ok, "notes": "tape"},
        )
        assert r.status_code == 201, r.text
    detail = (await admin_client.get(f"/api/admin/runs/{real.id}")).json()
    assert detail["can_add_return_trial"] and len(detail["trials"]) == 2
    assert "sim_true" not in str(detail["points"])

    stats = (await admin_client.get("/api/admin/return-trials/stats")).json()
    assert stats["trials"] - before["trials"] == 2 and stats["successes"] - before["successes"] == 1
    assert stats["simulated_runs"] - before["simulated_runs"] == 1
    if before["trials"] == 0:
        assert (
            stats["mean_err_m"] == 0.2
            and stats["max_err_m"] == 0.3
            and stats["success_rate"] == 0.5
        )
        assert "No physical return trials" in before["note"]
    assert (await admin_client.get("/api/admin/runs/999999999")).status_code == 404
