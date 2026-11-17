"""The brain (odometry + supervisor + navigation) against the SIMULATED ESP32, in simulated time.

*** Simulation only (CLAUDE.md rule 5). ***
"""

import math
from typing import Any

from scr_protocol import parse_robot_message
from scr_serial import Event, EventCode, Flags, Heartbeat, Mode, Servo

from robotd.calibration import SIM_PROFILE
from robotd.config import Config
from robotd.sim_esp32 import SimEsp32Config
from robotd.sim_harness import SimHarness


def started(cfg: Config | None = None, sim_cfg: SimEsp32Config | None = None) -> SimHarness:
    h = SimHarness(cfg or Config(sim=True), sim_cfg)
    h.run(0.5)
    assert h.command("start_roaming")["accepted"]
    h.run(3.0)
    assert h.brain.sup.state == "ROAMING" and h.sim.speed[0] > 0.1
    return h


def vision(engaged: bool, servo: float = 0.0, camera_ok: bool = True) -> dict[str, Any]:
    face = {"id": 4, "box": [0.4, 0.3, 0.2, 0.3], "facing": True, "yaw_deg": 2.0, "pitch_deg": 0.0,
            "mouth": 0.01, "speaking": False}  # fmt: skip
    return {
        "type": "vision.state", "t_ms": 0, "camera_ok": camera_ok, "faces": [face],
        "selected_id": 4, "engaged": engaged, "active_speaker_id": None, "overlap": False,
        "servo_target_deg": servo,
    }  # fmt: skip


def run_with_vision(h: SimHarness, seconds: float, msg: dict[str, Any]) -> None:
    for _ in range(int(seconds * 10)):  # visiond sends at about 10 Hz
        h.brain.on_vision(msg, h.now_s)
        h.run(0.1)


def kinds(h: SimHarness) -> list[str]:
    return [k for k, _ in h.brain.events]


# ---------------- SIMULATED label (rule 5) ----------------
def test_simulated_label_is_everywhere() -> None:
    h = SimHarness()
    h.run(0.5)
    status = h.brain.status(h.now_s)
    assert status["is_simulated"] is True and status["state"] == "IDLE"
    parse_robot_message(status)  # the status is valid protocol 2
    assert status["sensors"] == {
        "esp32_link": "ok", "encoders": "ok", "imu": "ok", "ultrasonic": "ok", "camera": "absent",
    }  # fmt: skip
    assert 0 < status["battery_pct"] <= 100 and status["battery_v"] > 9
    states = [m for m in h.ui if m["type"] == "local.state"]
    assert states and all(m["is_simulated"] is True for m in states)
    h.command("start_roaming")
    h.run(2.0)
    run_start = h.brain.outbox[0]
    assert run_start["type"] == "run.start" and run_start["is_simulated"] is True
    assert "SIMULATED" in run_start["notes"] and run_start["odom_config"]["simulated"] is True
    assert run_start["odom_config"]["calibrated"] is False  # placeholders, not measurements
    for msg in h.brain.outbox:
        parse_robot_message(msg)
    events = [m for m in h.brain.outbox if m["type"] == "event"]
    assert events and all(m["data"]["simulated"] is True for m in events)


def test_is_simulated_follows_the_telemetry_flag_not_the_config() -> None:
    h = SimHarness(Config(sim=False))  # config says "real", the frames say SIMULATED
    assert h.brain.is_simulated is False
    h.run(0.2)
    assert h.brain.is_simulated is True and h.brain.status(h.now_s)["is_simulated"] is True


def test_run_telemetry_is_10_hz_and_valid() -> None:
    h = started()
    h.run(5.0)
    samples = [s for m in h.brain.outbox if m["type"] == "telemetry" for s in m["samples"]]
    assert 75 <= len(samples) <= 86  # about 8.5 s at 10 Hz
    assert all(b["t_ms"] - a["t_ms"] == 100 for a, b in zip(samples, samples[1:], strict=False))
    assert samples[-1]["x_m"] > 0.5 and samples[-1]["state"] == "roaming"
    assert "enc_theta_rad" in samples[-1]["enc"]  # encoder-only heading, logged for comparison
    assert "sim_true_pose" not in samples[-1]["imu"]  # robotd never knows the simulator's truth


# ---------------- ESP32 link ----------------
def test_power_on_safe_mode_is_left_only_when_the_robot_wants_to_move() -> None:
    h = SimHarness()
    h.run(1.0)
    assert not any(isinstance(m, Mode) for m in h.sim.received)
    assert sum(isinstance(m, Heartbeat) for m in h.sim.received) in (9, 10, 11)  # 10 Hz
    h.command("start_roaming")
    h.run(1.0)
    assert sum(isinstance(m, Mode) for m in h.sim.received) == 1
    assert h.brain.frame is not None and h.brain.frame.flags & Flags.MOTORS_ENABLED


def test_heartbeat_loss_stops_the_simulated_esp32_and_robot_goes_to_fault() -> None:
    h = started()
    x_before = h.sim.true.x
    h.pi_alive = False  # the Pi hangs: no DRIVE, no HEARTBEAT
    h.run(0.6)
    assert h.sim.watchdog_stopped and h.sim.speed == (0.0, 0.0)
    assert h.sim.flags & Flags.WATCHDOG_STOP
    assert h.sim.true.x - x_before < 0.2 * 0.35 + 0.01  # it rolled on for about 300 ms only
    x_stopped = h.sim.true.x
    h.run(2.0)
    assert h.sim.true.x == x_stopped
    # the Pi comes back: it saw no telemetry for > 500 ms, so it is in FAULT and does not move
    h.pi_alive = True
    h.run(0.1)
    assert h.brain.sup.state == "FAULT" and h.brain.sup.faults == []  # telemetry is back
    assert any(isinstance(m, Event) and m.code == EventCode.LINK_RESTORED for m in h.from_esp)
    h.run(3.0)
    assert h.brain.sup.state == "PAUSED" and h.brain.sup.pause_reason == "fault"
    assert h.sim.true.x == x_stopped  # no motion by itself after a fault
    assert h.command("resume")["accepted"]
    h.run(2.0)
    assert h.brain.sup.state == "ROAMING" and h.sim.true.x != x_stopped


def test_watchdog_event_is_recorded_when_the_pi_only_stops_sending() -> None:
    h = started()
    real_poll = h.brain.link.poll
    h.brain.link.poll = lambda now: b""  # type: ignore[method-assign]  # writes are stuck
    h.run(0.5)
    assert "comm_loss" in kinds(h)
    assert [d for k, d in h.brain.events if k == "comm_loss"][-1]["link"] == "esp32"
    h.brain.link.poll = real_poll  # type: ignore[method-assign]
    h.run(1.0)
    assert h.sim.speed[0] > 0.1  # the next DRIVE moves it again


def test_telemetry_loss_is_a_fault_and_commands_are_refused() -> None:
    h = started()
    feed = h.brain.link.feed
    h.brain.link.feed = lambda data, now: []  # type: ignore[method-assign]  # RX wire unplugged
    h.run(1.0)
    assert h.brain.sup.state == "FAULT" and h.brain.sup.faults == ["telemetry_lost"]
    assert h.brain.link.target == (0.0, 0.0) and h.sim.speed == (0.0, 0.0)
    status = h.brain.status(h.now_s)
    assert status["faults"] == ["telemetry_lost"] and status["sensors"]["esp32_link"] == "fault"
    ack = h.command("resume")
    assert ack == {"type": "robot.command.ack", "id": "test", "accepted": False,
                   "reason": "fault active: telemetry_lost"}  # fmt: skip
    h.brain.link.feed = feed  # type: ignore[method-assign]


def test_low_battery_is_a_fault() -> None:
    h = SimHarness(sim_cfg=SimEsp32Config(nvs=dict(SIM_PROFILE), battery_mv=9500))
    h.run(1.0)
    assert h.brain.sup.state == "FAULT" and h.brain.sup.faults == ["low_battery"]
    ack = h.command("start_roaming")
    assert ack["accepted"] is False and ack["reason"] == "battery low"
    assert h.sim.speed == (0.0, 0.0)


def test_slip_event_when_one_encoder_disagrees() -> None:
    h = started()
    assert "slip" not in kinds(h)
    h.sim.wheel_slip[0] = 0.4  # the front-left encoder sees less than half of the motion
    h.run(3.0)
    slips = [d for k, d in h.brain.events if k == "slip"]
    assert slips and slips[0]["side"] == "left" and slips[0]["front_ticks"] < slips[0]["rear_ticks"]


def test_u32_clock_wrap_during_a_run() -> None:
    h = SimHarness(sim_cfg=SimEsp32Config(nvs=dict(SIM_PROFILE), boot_t_ms=0xFFFFFFFF - 3000))
    h.run(0.5)
    h.command("start_roaming")
    h.run(8.0)  # the ESP32 clock wraps about 2.5 s into the run
    assert h.brain.sup.state == "ROAMING" and h.brain.odom.pose.x > 1.0
    assert abs(h.brain.odom.pose.x - h.sim.true.x) < 0.1
    samples = [s for m in h.brain.outbox if m["type"] == "telemetry" for s in m["samples"]]
    assert all(b["t_ms"] > a["t_ms"] for a, b in zip(samples, samples[1:], strict=False))


# ---------------- commands ----------------
def test_commands_accepted_and_refused() -> None:
    h = SimHarness()
    h.run(0.5)
    assert h.command("pause") == {"type": "robot.command.ack", "id": "test", "accepted": False,
                                  "reason": "nothing to pause (state IDLE)"}  # fmt: skip
    assert h.command("start_roaming") == {
        "type": "robot.command.ack",
        "id": "test",
        "accepted": True,
    }
    assert h.command("start_roaming")["reason"] == "not idle (state ROAMING)"
    h.run(3.0)
    assert h.command("pause")["accepted"]
    h.run(1.0)
    assert h.brain.sup.state == "PAUSED" and h.sim.speed == (0.0, 0.0)
    assert h.command("resume")["accepted"]
    h.run(2.0)
    assert h.sim.speed != (0.0, 0.0)
    assert h.command("set_mode", {"mode": "open-day"})["accepted"]
    assert h.brain.status(h.now_s)["mode"] == "open-day"
    for ack in (h.command("pause"), h.command("nonsense")):
        parse_robot_message(ack)


def test_start_roaming_refused_when_boxed_in() -> None:
    tiny = SimEsp32Config(nvs=dict(SIM_PROFILE), room=(-0.3, -0.3, 0.3, 0.3), obstacles=())
    h = SimHarness(sim_cfg=tiny)
    h.run(0.5)
    ack = h.command("start_roaming")
    assert ack["accepted"] is False and ack["reason"].startswith("blocked")
    assert h.brain.sup.state == "IDLE"


# ---------------- engagement ----------------
def test_engagement_stop_engage_conversation_resume() -> None:
    h = started()
    run_with_vision(h, 0.5, vision(engaged=False, servo=5.0))
    assert h.brain.sup.state == "ROAMING"
    assert h.brain.status(h.now_s)["sensors"]["camera"] == "ok"

    # a person has faced the robot for N seconds (visiond decides that)
    run_with_vision(h, 1.0, vision(engaged=True, servo=12.5))
    assert h.brain.sup.state == "ENGAGED"
    assert h.sim.speed == (0.0, 0.0)  # it stopped
    assert h.ui_types().count("local.engage") == 1  # the page is told to start the session
    assert h.sim.servo_deg == 12.5  # the camera turns to the person (SERVO message)
    assert [m for m in h.sim.received if isinstance(m, Servo)][-1] == Servo(12.5)
    assert {"type": "servo", "pan_deg": 12.5} in h.brain.vision_out
    seen = [m for m in h.ui if m["type"] == "local.vision"][-1]
    assert seen == {"type": "local.vision", "face_track_id": 4, "overlap": False, "engaged": True,
                    "faces": 1}  # fmt: skip
    stopped_at = (h.sim.true.x, h.sim.true.y)

    # the page reports the conversation; movement commands are refused meanwhile
    h.brain.on_ui({"type": "local.conversation", "active": True})
    run_with_vision(h, 3.0, vision(engaged=True, servo=10.0))
    assert h.brain.sup.state == "CONVERSATION"
    ack = h.command("return_to_start")
    assert ack["accepted"] is False and ack["reason"] == "a conversation is in progress"
    assert (h.sim.true.x, h.sim.true.y) == stopped_at
    h.brain.on_ui({"type": "local.vad", "speaking": True})
    assert {"type": "vad", "speaking": True} in h.brain.vision_out

    # conversation over, the visitor walks away: path rechecked, roaming resumes
    h.brain.on_ui({"type": "local.conversation", "active": False})
    run_with_vision(h, 0.3, vision(engaged=False))
    assert h.brain.sup.state == "ROAMING"
    h.run(3.0)
    assert math.hypot(h.sim.true.x - stopped_at[0], h.sim.true.y - stopped_at[1]) > 0.2
    assert h.ui_types().count("local.engage") == 1
    states = [m["state"] for m in h.ui if m["type"] == "local.state"]
    assert [s for i, s in enumerate(states) if i == 0 or s != states[i - 1]][-4:] == [
        "ROAMING", "ENGAGED", "CONVERSATION", "ROAMING",
    ]  # fmt: skip


def test_after_conversation_blocked_path_is_rechecked_before_moving() -> None:
    h = started()
    h.brain.on_ui({"type": "local.conversation", "active": True})  # start button on the page
    h.run(1.0)
    assert h.brain.sup.state == "CONVERSATION" and h.sim.speed == (0.0, 0.0)
    # someone now stands right in front of the robot (a box appears in the simulated room)
    p = h.sim.true
    ahead = (p.x + 0.5 * math.cos(p.theta), p.y + 0.5 * math.sin(p.theta))
    h.sim.cfg.obstacles = (
        *h.sim.cfg.obstacles,
        (ahead[0] - 0.1, ahead[1] - 0.6, ahead[0] + 0.1, ahead[1] + 0.6),
    )
    h.run(1.0)
    h.brain.on_ui({"type": "local.conversation", "active": False})
    h.run(0.1)
    assert h.brain.sup.state == "ROAMING" and h.brain.roamer.phase == "turn"  # not straight on
    h.run(15.0)
    assert h.sim.collisions == 0 and h.brain.rejected == []


def test_vision_silence_means_camera_absent_and_no_engagement() -> None:
    h = SimHarness()
    h.run(0.5)
    h.brain.on_vision(vision(engaged=True), h.now_s)
    h.run(3.0)  # visiond died right after its last message
    assert h.brain.status(h.now_s)["sensors"]["camera"] == "absent"
    h.run(12.0)
    assert h.brain.sup.state == "IDLE"  # ENGAGED timed out, nobody is there
    h.brain.on_vision(vision(engaged=False, camera_ok=False), h.now_s)
    assert h.brain.status(h.now_s)["sensors"]["camera"] == "fault"


# ---------------- backend loss (rule 4: stopping never needs the backend) ----------------
def test_backend_loss_stops_roaming_buffers_and_recovers() -> None:
    h = started(Config(sim=True, outbox_max=30))
    h.brain.take_outbox(1000)  # everything so far was "sent"
    h.brain.set_backend(False)
    h.run(1.0)
    assert h.brain.sup.state == "PAUSED" and h.brain.sup.pause_reason == "backend_lost"
    assert h.sim.speed == (0.0, 0.0)
    told = [m for m in h.ui if m["type"] == "local.state"][-1]
    assert told["backend_ok"] is False and told["state"] == "PAUSED"  # page shows "unavailable"
    assert "comm_loss" in kinds(h)
    h.run(60.0)  # a long outage: the buffer stays bounded, the newest data is kept
    assert len(h.brain.outbox) <= 30 and h.brain.outbox_dropped > 0
    assert h.sim.speed == (0.0, 0.0)
    h.brain.set_backend(True)
    assert h.brain.outbox[0]["type"] == "run.start" and h.brain.outbox[0]["is_simulated"] is True
    assert "continues" in h.brain.outbox[0]["notes"]
    h.run(2.0)
    assert h.brain.sup.state == "ROAMING" and h.sim.speed != (0.0, 0.0)


def test_engagement_is_not_started_without_backend() -> None:
    h = SimHarness()
    h.run(0.5)
    h.brain.set_backend(False)
    run_with_vision(h, 2.0, vision(engaged=True))
    assert h.brain.sup.state == "IDLE" and "local.engage" not in h.ui_types()
