"""Follow-ups of the independent firmware safety review (sprint 5), robotd side, against the
SIMULATED ESP32 in simulated time. *** Simulation only (CLAUDE.md rule 5). ***"""

from scr_serial import Drive, DriveMode, Event, EventCode, Mode, ParamSet, Stop, load_table

from robotd.calibration import SIM_PROFILE
from robotd.config import Config
from robotd.sim_esp32 import SimEsp32Config
from robotd.sim_harness import SimHarness

TABLE = load_table()


def roaming(sim_cfg: SimEsp32Config | None = None) -> SimHarness:
    h = SimHarness(Config(sim=True), sim_cfg)
    h.run(0.5)
    assert h.command("start_roaming")["accepted"]
    h.run(3.0)
    assert h.brain.sup.state == "ROAMING" and h.sim.speed != (0.0, 0.0)
    return h


# 5c: encoder inversion changes and reboots never put a phantom jump into the pose
def test_encoder_invert_change_rebaselines_the_odometry() -> None:
    h = SimHarness()
    h.run(3.0)
    h.sim.enc = [20000.0, 20000.0, 20000.0, 20000.0]  # large counts (the robot drove before)
    h.run(0.1)
    pose = h.brain.odom.pose
    fl = TABLE.by_name("ENCODER_INVERT_FL").id
    assert h.brain.set_bringup(True, h.now_s)[0]  # robotctl motor-test (robotd does not re-push)
    h.brain.link.send(ParamSet(fl, 1.0))  # the FL count flips sign at once
    h.run(0.2)
    assert any(isinstance(m, Event) and m.code == EventCode.PARAM_CHANGED for m in h.from_esp)
    assert abs(h.brain.odom.pose.x - pose.x) < 0.01  # a jump of 40000 ticks would be 10 m


def test_robotd_push_of_new_inversions_rebaselines_once_applied() -> None:
    h = SimHarness()
    h.run(3.0)
    h.sim.enc = [20000.0, 20000.0, 20000.0, 20000.0]
    h.run(0.1)
    x = h.brain.odom.pose.x
    cal = h.brain.cal
    cal.values["ENCODER_INVERT_RR"] = {"value": 1}
    h.brain.set_calibration(cal)
    h.brain.on_serial(Event(0, EventCode.PARAM_CHANGED, 0), h.now_s)  # unrelated id: no rebase
    h.run(1.0)
    assert h.brain.params_ok and abs(h.brain.odom.pose.x - x) < 0.01


def test_reboot_rebaselines_the_odometry() -> None:
    h = roaming()
    x = h.brain.odom.pose.x
    h.sim.reboot()  # counters and clock restart from 0
    h.run(0.3)
    assert abs(h.brain.odom.pose.x - x) < 0.05


# 1c: after a stall the first thing the ESP32 gets is STOP, then fresh DRIVE frames only
def test_link_restored_sends_stop_first() -> None:
    h = roaming()
    real_poll = h.brain.link.poll
    h.brain.link.poll = lambda now: b""  # type: ignore[method-assign]  # writes stall
    h.run(0.5)
    assert h.sim.watchdog_stopped
    h.brain.link.poll = real_poll  # type: ignore[method-assign]
    before = len(h.sim.received)
    h.run(0.3)
    after = h.sim.received[before:]
    first_stop = next(i for i, m in enumerate(after) if isinstance(m, Stop))
    stop_after_restore = [i for i, m in enumerate(after) if isinstance(m, Stop) and i > 0]
    assert stop_after_restore, "STOP must follow LINK_RESTORED"
    assert first_stop <= stop_after_restore[0]
    assert "esp32_link_restored" in [k for k, _ in h.brain.events]


# 1b: motion is kept alive by DRIVE at >= 20 Hz, never by HEARTBEAT; stopping sends STOP
def test_drive_rate_while_moving_and_stop_when_stopping() -> None:
    h = roaming()
    before = len(h.sim.received)
    h.run(2.0)
    window = h.sim.received[before:]
    drives = [m for m in window if isinstance(m, Drive)]
    assert len(drives) >= 39  # 20 Hz over 2 s
    assert not any(type(m).__name__ == "Heartbeat" for m in window)  # no heartbeat while moving
    before = len(h.sim.received)
    assert h.command("pause")["accepted"]
    h.run(0.2)
    assert any(isinstance(m, Stop) for m in h.sim.received[before:])
    assert h.sim.speed == (0.0, 0.0)


# 2b: a missing CENTRE front sensor means "front blocked"
def test_missing_centre_sensor_blocks_forward_and_is_reported() -> None:
    cfg = SimEsp32Config(nvs=dict(SIM_PROFILE), front_sensors=(True, False, True))
    h = SimHarness(Config(sim=True), cfg)
    h.run(3.0)
    ack = h.command("start_roaming")
    assert ack["accepted"] is False and ack["reason"].startswith("blocked")
    assert h.brain.status(h.now_s)["sensors"]["ultrasonic"] == "fault"
    one_side = SimEsp32Config(nvs=dict(SIM_PROFILE), front_sensors=(False, True, True))
    side = SimHarness(Config(sim=True), one_side)
    side.run(1.0)
    assert side.brain.status(side.now_s)["sensors"]["ultrasonic"] == "degraded"


def test_simulated_esp32_refuses_forward_without_the_centre_sensor() -> None:
    cfg = SimEsp32Config(nvs=dict(SIM_PROFILE), front_sensors=(True, False, True))
    h = SimHarness(Config(sim=True), cfg)
    h.run(3.0)
    h.brain.link.send(Mode(DriveMode.DRIVE))
    for _ in range(50):
        h.brain.link.send(Drive(0.2, 0.2))
        h.run(0.02)
    assert h.sim.true.x == 0.0


# 5: speed sign mismatch from the firmware is a FAULT with a clear reason
def test_speed_sign_mismatch_is_a_latched_fault() -> None:
    h = roaming()
    h.brain.on_serial(Event(0, EventCode.ENCODER_FAULT, 11), h.now_s)
    h.run(0.5)
    assert h.brain.sup.state == "FAULT"
    assert "speed_sign_mismatch_right" in h.brain.sup.faults
    data = [d for k, d in h.brain.events if k == "encoder_fault"][-1]
    assert data["reason"] == "speed_sign_mismatch_right" and "robotctl motor-test" in data["hint"]
    h.run(5.0)  # latched: it does not clear by itself
    assert h.brain.sup.state == "FAULT"
    assert "speed_sign_mismatch_right" in h.brain.status(h.now_s)["faults"]
    h.sim.reboot()  # the ESP32 restarted (after the operator fixed it): cleared
    h.run(4.0)
    assert h.brain.sup.state in ("PAUSED", "IDLE") and not h.brain.latched_faults
