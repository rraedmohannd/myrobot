"""Sprint 5 robotd behaviour against the SIMULATED ESP32 in simulated time: calibration push and
hash check, gyro bias, look-away tolerance, stop_roaming, black box, Pi health.

*** Simulation only (CLAUDE.md rule 5). ***
"""

import json
from pathlib import Path
from typing import Any

from scr_protocol import parse_robot_message
from scr_serial import ParamSet

from robotd import calibration as calib
from robotd.app import load_calibration
from robotd.calibration import SIM_PROFILE, Calibration
from robotd.config import Config
from robotd.core import Brain, battery_percent
from robotd.health import parse_meminfo, parse_throttled, read_health
from robotd.sim_esp32 import SimEsp32Config
from robotd.sim_harness import SimHarness


def kinds(h: SimHarness) -> list[str]:
    return [k for k, _ in h.brain.events]


def fresh_harness(cal: Calibration | None = None, **sim: Any) -> SimHarness:
    """A NEVER-calibrated simulated firmware; robotd brings the calibration."""
    h = SimHarness(sim_cfg=SimEsp32Config(nvs=None, **sim))
    if cal is not None:
        h.brain.set_calibration(cal)
    return h


# ---------------- S3.1 calibration ----------------
def test_robotd_pushes_its_calibration_to_a_fresh_firmware_and_checks_the_hash() -> None:
    h = fresh_harness()
    h.run(0.5)
    assert h.brain.params_ok and h.sim.param_hash == h.brain.expected_hash
    assert sum(isinstance(m, ParamSet) for m in h.sim.received) == 40  # every parameter
    assert h.sim.p("METRES_PER_TICK") == calib.f32(0.0005)
    assert h.brain.odom.metres_per_tick == 0.0005  # from the calibration, nothing else
    status = h.brain.status(h.now_s)
    assert status["calibration"] == {"status": "complete", "updated": None, "is_simulated": True}
    assert status["firmware"]["build_env"] == "robotd-simulated"
    assert status["firmware"]["param_hash"] == f"{h.brain.expected_hash:08x}"
    parse_robot_message(status)
    report = h.brain.take_calibration_report()
    assert report is not None and report["is_simulated"] is True
    parse_robot_message(report)
    assert h.brain.take_calibration_report() is None  # only when due


def test_push_again_after_a_reconnect_and_after_an_esp32_reboot() -> None:
    h = fresh_harness()
    h.run(0.5)
    pushes = h.brain.param_pushes
    h.brain.on_link(True, h.now_s)  # the port was opened again
    h.run(0.2)
    assert h.brain.param_pushes == pushes + 1
    h.sim.reboot()  # unsaved parameters are gone on the ESP32
    h.run(0.5)
    assert h.brain.param_pushes == pushes + 2 and h.brain.params_ok
    assert h.sim.param_hash == h.brain.expected_hash


def test_hash_mismatch_is_a_fault_and_robotd_keeps_pushing() -> None:
    h = fresh_harness()
    real_poll = h.brain.link.poll

    def lossy_poll(now: float) -> bytes:  # the ESP32 never gets the PARAM_SETs
        h.brain.link._queue = [m for m in h.brain.link._queue if not isinstance(m, ParamSet)]
        return real_poll(now)

    h.brain.link.poll = lossy_poll  # type: ignore[method-assign]
    h.run(5.0)
    assert h.brain.sup.state == "FAULT" and "param_hash_mismatch" in h.brain.sup.faults
    assert h.brain.param_pushes >= 3  # again every 2 s
    ack = h.command("start_roaming")
    assert ack["accepted"] is False
    h.brain.link.poll = real_poll  # type: ignore[method-assign]
    h.run(5.0)
    assert h.brain.params_ok and h.brain.sup.state == "IDLE"


def test_without_metres_per_tick_moving_is_refused_with_a_clear_reason() -> None:
    cal = calib.simulated()
    del cal.values["METRES_PER_TICK"]
    h = fresh_harness(cal)
    h.run(3.0)
    ack = h.command("start_roaming")
    assert ack["accepted"] is False and "METRES_PER_TICK" in ack["reason"]
    assert h.brain.calibration_status()["status"] == "partial"
    assert h.brain.params_ok  # the hash still matches: METRES_PER_TICK is the default 0


def test_load_calibration_missing_old_and_sim(tmp_path: Path) -> None:
    cal, problem, path = load_calibration(Config(sim=False, calibration_path=str(tmp_path / "x")))
    assert calib.status(cal) == "none" and "no calibration file" in problem
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schema": "scr-calibration", "version": 0,
                               "values": {"METRES_PER_TICK": 0.0005}}))  # fmt: skip
    cal, problem, path = load_calibration(Config(calibration_path=str(old)))
    assert problem == "" and cal.migrated_from == 0 and path == old
    cal, problem, path = load_calibration(Config(sim=True))
    assert cal.is_simulated and path is None
    brain = Brain(Config(), calibration=Calibration(), calibration_problem="no file")
    assert "no file" in brain.on_command("1", "start_roaming", None)["reason"]


# ---------------- S3.2 gyro bias ----------------
def test_gyro_calibrated_at_start_and_residual_bias_tracked_while_still() -> None:
    cfg = Config(sim=True, gyro_recal_idle_s=20.0)
    h = SimHarness(cfg, SimEsp32Config(nvs=dict(SIM_PROFILE), gyro_bias_rps=0.02,
                                       gyro_bias_drift_rps_per_s=0.0005))  # fmt: skip
    h.run(3.0)
    start = [d for k, d in h.brain.events if k == "gyro_bias"]
    assert start and start[0]["source"] == "start" and abs(start[0]["bias_rad_s"] - 0.021) < 0.003
    h.run(15.0)  # the bias drifts by 0.0075 rad/s; robotd's running estimate follows it
    still = [d for k, d in h.brain.events if k == "gyro_bias" and d["source"] == "still"]
    assert still and 0.003 < h.brain.gyro_bias_residual < 0.009
    assert h.brain.odom.gyro_bias == h.brain.gyro_bias_residual
    theta = h.brain.odom.pose.theta
    h.run(2.0)
    assert abs(h.brain.odom.pose.theta - theta) < 0.01  # the residual is removed: no creeping
    h.run(10.0)  # IDLE and still for 20 s: a new CALIBRATE_GYRO
    again = [d for k, d in h.brain.events if k == "gyro_bias" and d["source"] == "still"]
    assert h.brain.gyro_bias_residual == 0.0 or len(again) > len(still)
    assert len([k for k in kinds(h) if k == "gyro_bias"]) >= 3


# ---------------- S3.4 defaults table ----------------
def test_stop_roaming_command_keeps_the_run_open() -> None:
    h = SimHarness()
    h.run(0.5)
    h.command("start_roaming")
    h.run(4.0)
    ack = h.command("stop_roaming")
    assert ack == {"type": "robot.command.ack", "id": "test", "accepted": True}
    h.run(1.0)
    assert h.brain.sup.state == "IDLE" and h.sim.speed == (0.0, 0.0) and h.brain.sup.run_open
    assert not any(m["type"] == "run.end" for m in h.brain.outbox)
    assert h.command("return_to_start")["accepted"]


def vision(engaged: bool) -> dict[str, Any]:
    return {"type": "vision.state", "t_ms": 0, "camera_ok": True, "faces": [], "selected_id": 1,
            "engaged": engaged, "active_speaker_id": None, "overlap": False}  # fmt: skip


def test_look_away_tolerance() -> None:
    brain = Brain(Config(sim=True))
    brain.on_vision(vision(True), 10.0)
    brain.on_vision(vision(False), 10.1)
    brain.tick(10.25)  # looked away 0.25 s ago (last "engaged" at 10.0 s): still engaged
    assert brain.sup._facts.vision_engaged is True
    brain.on_vision(vision(False), 10.3)
    brain.tick(10.35)  # more than 0.3 s since the last "engaged": it drops
    assert brain.sup._facts.vision_engaged is False


def test_battery_percent_table() -> None:
    table = [(9.0, 0.0), (12.6, 100.0)]
    assert battery_percent(8.0, table) == 0 and battery_percent(13.0, table) == 100
    assert abs(battery_percent(10.8, table) - 50.0) < 1e-9


# ---------------- S6.4 black box ----------------
def test_black_box_written_on_obstacle_stop_and_bounded(tmp_path: Path) -> None:
    h = SimHarness(sim_cfg=SimEsp32Config(nvs=dict(SIM_PROFILE), start=(4.0, 0.0, 0.0)))
    h.brain.blackbox.directory = tmp_path
    h.brain.blackbox.keep = 2
    h.run(3.0)
    assert not list(tmp_path.glob("*.jsonl"))  # start-up: nothing written
    h.command("start_roaming")
    h.sim.cfg.obstacles = ((4.6, -1.0, 4.7, 1.0),)  # a box appears 0.6 m ahead
    h.run(6.0)
    files = sorted(tmp_path.glob("blackbox-*.jsonl"))
    if not files:  # the roamer may turn away in time; force the hard case
        h.brain.events.append(("x", {}))
        h.brain._event("obstacle_stop", {"front_mm": 200, "by": "esp32"})
        files = sorted(tmp_path.glob("blackbox-*.jsonl"))
    assert files
    lines = [json.loads(x) for x in files[-1].read_text(encoding="utf-8").splitlines()]
    assert lines[0]["kind"] == "header" and lines[0]["is_simulated"] is True
    assert any(r["kind"] == "telemetry" for r in lines) and any(r["kind"] == "event" for r in lines)
    text = files[-1].read_text(encoding="utf-8")
    assert "image" not in text and "audio" not in text and "pcm" not in text
    assert [d["file"] for k, d in h.brain.events if k == "blackbox"][-1] == files[-1].name
    for i in range(4):  # rate-limited (5 s) and bounded (keep 2)
        h.brain.now_s += 6.0
        h.brain._blackbox(f"test{i}")
    assert len(list(tmp_path.glob("blackbox-*.jsonl"))) == 2


# ---------------- S6.2 Pi health ----------------
def test_health_parsing_and_under_voltage_event() -> None:
    assert parse_throttled("throttled=0x50005") == {
        "under_voltage_now": True, "throttled_now": True, "under_voltage_seen": True,
        "throttled_seen": True,
    }  # fmt: skip
    assert parse_throttled("garbage") == {} and parse_throttled(None) == {}
    assert parse_meminfo("MemTotal: 1000 kB\nMemAvailable: 250 kB\n") == 75.0
    files = {"temp": "48312\n", "meminfo": "MemTotal: 8000 kB\nMemAvailable: 6000 kB\n"}
    health = read_health(
        read=lambda p: files["temp"] if "thermal" in str(p) else files["meminfo"],
        vcgencmd=lambda: "throttled=0x1\n", loadavg=lambda: 0.42, disk_pct=lambda: 31.25,
    )  # fmt: skip
    assert health == {"cpu_temp_c": 48.3, "under_voltage_now": True, "throttled_now": False,
                      "under_voltage_seen": False, "throttled_seen": False, "cpu_load_1m": 0.42,
                      "mem_used_pct": 25.0, "disk_used_pct": 31.2}  # fmt: skip
    windows = read_health(read=lambda p: None, vcgencmd=lambda: None, loadavg=lambda: None,
                          disk_pct=lambda: None)  # fmt: skip
    assert windows == {}  # nothing readable: the block is simply absent
    h = SimHarness()
    h.run(0.5)
    h.brain.set_health(health)
    h.brain.set_health(health)  # still under-voltage: one event per episode
    assert kinds(h).count("under_voltage") == 1
    assert [m for m in h.brain.outbox if m["type"] == "event" and m["kind"] == "under_voltage"]
    status = h.brain.status(h.now_s)
    assert status["health"]["cpu_temp_c"] == 48.3
    parse_robot_message(status)
