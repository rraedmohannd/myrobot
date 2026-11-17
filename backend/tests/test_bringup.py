"""Sprint 5 S6.3: the admin "Bring-up" tab — the docs/BRINGUP.md checklist parser, ticks stored
in the database (who and when), calibration records from `calibration.report` (real apart from
simulated), live diagnostics and the power chart data."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.db.models import PoseSample, RobotRun
from app.db.session import get_sessionmaker
from app.main import app
from app.services import auth
from app.services.bringup import (
    SIMULATED_CALIBRATION_LABEL,
    calibration_records,
    parse_checklist,
    store_calibration_report,
)
from app.services.robot_hub import RobotHub, get_robot_hub
from tests.fakes import OneSession
from tests.test_protocol_contract import FULL_STATUS
from tests.test_robot_control import FakeRobot

CHECKLIST = """# Bring-up

Intro text with a [ ] that is not an item.

## 1. Before power
- [ ] Check the battery polarity with a multimeter <!-- step:polarity -->
- [x] Fuse in place <!-- step:fuse-in -->
- [ ] An item without an id

## 2. Flash
```
- [ ] not an item: inside a code block <!-- step:in-code -->
```
- [ ] Flash the firmware: `pio run -t upload` <!-- step:flash-fw -->
- [ ] Duplicate <!-- step:polarity -->

## Notes
- [ ] Ignored: not a numbered section <!-- step:notes-item -->

## 8. Floor
* [ ] Return trial on the floor <!-- step:floor-return -->
"""
PASSWORD = "a-long-test-password-123"


def test_checklist_parser() -> None:
    c = parse_checklist(CHECKLIST)
    assert [(s.number, s.title) for s in c.sections] == [
        (1, "Before power"), (2, "Flash"), (None, "(before the first numbered section)"),
        (8, "Floor"),
    ]  # fmt: skip
    assert c.step_ids == ["polarity", "fuse-in", "flash-fw", "notes-item", "floor-return"]
    assert c.sections[1].steps[0].text == "Flash the firmware: `pio run -t upload`"
    assert any("without <!-- step" in p for p in c.problems)
    assert any("'polarity' is already used" in p for p in c.problems)
    assert parse_checklist("no checklist here").sections == []


@pytest.fixture
def checklist_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "BRINGUP.md"
    path.write_text(CHECKLIST, encoding="utf-8")
    monkeypatch.setattr(get_settings(), "bringup_doc_path", str(path))
    return path


async def test_bringup_endpoints_need_admin(client) -> None:
    assert (await client.get("/api/admin/bringup")).status_code == 401
    put = await client.put("/api/admin/bringup/steps/polarity", json={"done": True})
    assert put.status_code == 401
    assert (await client.get("/api/admin/runs/1/power")).status_code == 401


async def test_missing_checklist_is_reported(admin_client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "bringup_doc_path", str(tmp_path / "missing.md"))
    d = (await admin_client.get("/api/admin/bringup")).json()
    assert d["checklist"]["exists"] is False and d["checklist"]["sections"] == []
    r = await admin_client.put("/api/admin/bringup/steps/polarity", json={"done": True})
    assert r.status_code == 404 and "does not exist" in r.json()["detail"]


async def test_ticks_are_stored_with_who_and_when(admin_client, db_session, checklist_file) -> None:
    d = (await admin_client.get("/api/admin/bringup")).json()["checklist"]
    assert d["exists"] and d["total"] == 5 and d["done"] == 0
    assert d["sections"][0]["steps"][1] == {
        "id": "fuse-in",
        "text": "Fuse in place",
        "done": False,
        "done_by": None,
        "done_at": None,
        "note": None,
    }  # fmt: skip  (a [x] in the file is ignored)
    r = await admin_client.put("/api/admin/bringup/steps/polarity",
                               json={"done": True, "note": "12.4 V, polarity ok"})  # fmt: skip
    assert r.status_code == 200 and r.json()["done_by"] == "token" and r.json()["done_at"]
    assert (
        await admin_client.put("/api/admin/bringup/steps/no-such-step", json={"done": True})
    ).status_code == 404

    # a logged-in admin user is recorded by name
    await auth.create_admin(db_session, "bringup.tester", PASSWORD)
    cookie = await auth.login(db_session, "bringup.tester", PASSWORD)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           cookies={auth.COOKIE: cookie}) as user_client:  # fmt: skip
        r = await user_client.put("/api/admin/bringup/steps/flash-fw", json={"done": True})
        assert r.status_code == 200 and r.json()["done_by"] == "bringup.tester"

    d = (await admin_client.get("/api/admin/bringup")).json()["checklist"]
    steps = {s["id"]: s for sec in d["sections"] for s in sec["steps"]}
    assert d["done"] == 2 and steps["polarity"]["note"] == "12.4 V, polarity ok"
    assert steps["flash-fw"]["done_by"] == "bringup.tester"
    r = await admin_client.put("/api/admin/bringup/steps/polarity", json={"done": False})
    assert r.json() == {"id": "polarity", "done": False, "done_by": None, "done_at": None,
                        "note": None}  # fmt: skip


async def test_calibration_records_store_changes_only_and_keep_simulated_apart(
    admin_client, db_session, checklist_file
) -> None:
    robot = "bringup-test-robot"
    report = {
        "type": "calibration.report", "is_simulated": False, "robot_id": robot, "version": 1,
        "values": {
            "METRES_PER_TICK": {"value": 0.000512, "unit": "m/tick", "operator": "Mohannd",
                                "date": "2026-12-03T14:05:00+03:00",
                                "method": "distance-cal: 3 m tape, 5 runs",
                                "raw": {"runs": [3.01, 2.99], "mean": 3.0}},
            "track_width_m": {"value": 0.19, "unit": "m"},
        },
    }  # fmt: skip
    assert await store_calibration_report(db_session, report) == [
        "METRES_PER_TICK",
        "track_width_m",
    ]
    assert await store_calibration_report(db_session, report) == []  # nothing changed
    report["values"]["track_width_m"]["value"] = 0.192
    assert await store_calibration_report(db_session, report) == ["track_width_m"]
    simulated = {"type": "calibration.report", "is_simulated": True,
                 "values": {"GYRO_SCALE": {"value": 1.0}}}  # fmt: skip
    assert await store_calibration_report(db_session, simulated, "sim-robot") == ["GYRO_SCALE"]
    with pytest.raises(ValueError):  # is_simulated is required
        await store_calibration_report(db_session, {"type": "calibration.report", "values": {}})

    real, sim = await calibration_records(db_session, [robot, "sim-robot"])
    assert [(r.key, r.value) for r in real] == [
        ("METRES_PER_TICK", 0.000512), ("track_width_m", 0.19), ("track_width_m", 0.192)
    ]  # fmt: skip
    assert real[0].measured_at.isoformat() == "2026-12-03T11:05:00+00:00"
    assert real[0].raw == {"runs": [3.01, 2.99], "mean": 3.0}
    assert [(r.robot_id, r.key, r.is_simulated) for r in sim] == [("sim-robot", "GYRO_SCALE", True)]

    d = (await admin_client.get("/api/admin/bringup")).json()["calibration"]
    mine = [r for r in d["real"] if r["robot_id"] == robot]
    width = next(r for r in mine if r["key"] == "track_width_m")
    assert width["value"] == 0.192 and [h["value"] for h in width["history"]] == [0.19]
    assert all(not r["is_simulated"] for r in d["real"])
    gyro = next(r for r in d["simulated"] if r["robot_id"] == "sim-robot")
    assert gyro["label"] == SIMULATED_CALIBRATION_LABEL == d["simulated_label"]


async def test_live_diagnostics_and_power_chart_data(admin_client, db_session) -> None:
    hub = RobotHub()
    app.dependency_overrides[get_robot_hub] = lambda: hub
    app.dependency_overrides[get_sessionmaker] = lambda: OneSession(db_session)
    try:
        assert (await admin_client.get("/api/admin/bringup")).json()["live"] is None
        robot = FakeRobot(hub, simulated=True)
        robot.report(**{k: v for k, v in FULL_STATUS.items() if k != "type"})
        live = (await admin_client.get("/api/admin/bringup")).json()["live"]
        assert live["is_simulated"] is True and "SIMULATED" in live["label"]
        assert live["firmware"]["param_hash"] == "89abcdef" and live["health"]["cpu_temp_c"] == 61.5
        assert live["calibration"]["status"] == "partial" and live["battery"]["energy_wh"] == 3.4
    finally:
        app.dependency_overrides.pop(get_robot_hub, None)
        app.dependency_overrides.pop(get_sessionmaker, None)

    real, sim = RobotRun(is_simulated=False), RobotRun(is_simulated=True)
    db_session.add_all([real, sim])
    await db_session.flush()
    for i in range(5):
        power = {"v": 12.0 - i * 0.1, "a": 1.5, "wh": i * 0.01}
        db_session.add(
            PoseSample(run_id=real.id, t_ms=i * 1000, x_m=0, y_m=0, theta_rad=0, power=power)
        )
    db_session.add(PoseSample(run_id=real.id, t_ms=9000, x_m=0, y_m=0, theta_rad=0))  # no power
    db_session.add(PoseSample(run_id=sim.id, t_ms=0, x_m=0, y_m=0, theta_rad=0, power={"v": 8.2}))
    await db_session.commit()

    p = (await admin_client.get(f"/api/admin/runs/{real.id}/power")).json()
    assert p["is_simulated"] is False and p["samples"] == 5 and len(p["points"]) == 5
    assert p["points"][-1] == {"t_s": 4.0, "v": 11.6, "a": 1.5, "wh": 0.04}
    s = (await admin_client.get(f"/api/admin/runs/{sim.id}/power")).json()
    assert s["is_simulated"] is True and "SIMULATED" in s["label"]
    runs = {r["id"]: r for r in (await admin_client.get("/api/admin/bringup")).json()["power_runs"]}
    assert runs[real.id]["power_samples"] == 5 and runs[sim.id]["is_simulated"] is True
    assert (await admin_client.get("/api/admin/runs/999999999/power")).status_code == 404
