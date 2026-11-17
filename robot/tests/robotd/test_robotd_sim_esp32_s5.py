"""The SIMULATED ESP32 mirrors the firmware rules of docs/SERIAL_PROTOCOL.md section 10.7
(sprint 5 safety review): stepwise PARAM_SAVE, the speed-sign mismatch fault, the ramp-aware
obstacle rule and the centre front sensor rule.

Everything here is simulation (CLAUDE.md rule 5); no physical robot is involved.
"""

from scr_serial import (
    US_ABSENT,
    Ack,
    AckStatus,
    CalibrateGyro,
    Drive,
    DriveMode,
    Event,
    EventCode,
    Flags,
    FrameDecoder,
    Heartbeat,
    Message,
    Mode,
    MsgType,
    ParamGet,
    ParamReport,
    ParamSave,
    ParamSet,
    ParamSource,
    Stop,
    Telemetry,
    encode_frame,
    f32,
    load_table,
)

from robotd.calibration import SIM_PROFILE
from robotd.sim_esp32 import SimEsp32, SimEsp32Config


def Cfg(**kw: object) -> SimEsp32Config:  # noqa: N802
    """A simulated ESP32 that boots with the SIMULATED calibration profile in its NVS."""
    return SimEsp32Config(**{"nvs": dict(SIM_PROFILE), **kw})  # type: ignore[arg-type]


class Pi:
    """A tiny hand-driven Pi side for the pure simulator."""

    def __init__(self, sim: SimEsp32) -> None:
        self.sim, self.seq, self.decoder = sim, 0, FrameDecoder()
        self.got: list[Message] = []

    def send(self, msg: Message) -> None:
        self.sim.receive(encode_frame(msg, self.seq))
        self.seq = (self.seq + 1) & 0xFF

    def steps(self, n: int, keep: Message | None = None) -> None:
        """n x 10 ms; `keep` is sent every 50 ms (keeps the watchdog fed)."""
        for i in range(n):
            if keep is not None and i % 5 == 0:
                self.send(keep)
            self.got += [f.msg for f in self.decoder.feed(self.sim.step())]

    def of(self, cls: type) -> list:
        return [m for m in self.got if isinstance(m, cls)]

    def events(self) -> list[EventCode]:
        return [m.code for m in self.of(Event)]


def _pid(name: str) -> int:
    return load_table().by_name(name).id


def _save_acks(pi: Pi) -> list[Ack]:
    return [a for a in pi.of(Ack) if a.acked_type == MsgType.PARAM_SAVE]


# ---------------- PARAM_SAVE: one key per 10 ms step, ACK after the commit ----------------
def test_param_save_is_stepwise_and_acks_after_the_commit() -> None:
    pi = Pi(SimEsp32())
    mpt = _pid("METRES_PER_TICK")
    pi.send(ParamSet(mpt, 0.0005))
    pi.steps(2)
    pi.got.clear()
    n = len(load_table().params)
    assert n == 40
    pi.send(ParamSave())
    pi.steps(n)  # one key per step: still running after 40 steps
    assert not _save_acks(pi) and pi.sim.saving
    assert all(t.flags & Flags.PARAMS_UNSAVED for t in pi.of(Telemetry))  # until the commit
    pi.steps(1)  # the commit: about 0.41 s after the request
    assert _save_acks(pi) == [Ack(MsgType.PARAM_SAVE, pi.seq - 1, AckStatus.OK)]
    assert not pi.sim.saving and pi.sim.nvs[mpt] == f32(0.0005)
    pi.steps(2)
    assert not pi.of(Telemetry)[-1].flags & Flags.PARAMS_UNSAVED
    pi.send(ParamGet(mpt))
    pi.steps(1)
    assert pi.of(ParamReport)[-1].source == ParamSource.NVS


def test_param_set_and_param_save_are_busy_during_a_save() -> None:
    pi = Pi(SimEsp32())
    kp = _pid("PID_KP")
    pi.send(ParamSet(kp, 2.0))
    pi.steps(2)
    pi.got.clear()
    pi.send(ParamSave())
    pi.steps(5)
    pi.send(ParamSet(kp, 3.0))  # in range, but a save runs
    pi.send(ParamSet(kp, 1e9))  # bounds are checked first: OUT_OF_RANGE, not BUSY
    pi.send(ParamSave())
    pi.steps(1)
    assert [a.status for a in pi.of(Ack)] == [
        AckStatus.BUSY, AckStatus.OUT_OF_RANGE, AckStatus.BUSY,
    ]  # fmt: skip
    assert pi.sim.p("PID_KP") == 2.0
    pi.steps(40)
    assert _save_acks(pi)[-1].status == AckStatus.OK
    pi.send(ParamSet(kp, 3.0))  # after the save: accepted again
    pi.steps(1)
    assert pi.of(Ack)[-1].status == AckStatus.OK


def test_a_reboot_during_a_save_keeps_only_the_keys_written() -> None:
    pi = Pi(SimEsp32())
    first, last = load_table().params[0], load_table().params[-1]
    pi.send(ParamSet(first.id, first.max))
    pi.send(ParamSet(last.id, last.max))
    pi.send(ParamSave())
    pi.steps(5)  # the first keys are written, the last one is not
    pi.sim.reboot()
    assert pi.sim.p(first.name) == f32(first.max)
    assert pi.sim.p(last.name) == f32(last.default)


def test_calibrate_gyro_and_save_wait_for_300_ms_without_encoder_change() -> None:
    pi = Pi(SimEsp32(Cfg()))
    pi.steps(40, keep=Heartbeat())
    pi.sim.hand_push("left", 0.01)  # somebody touches a track
    pi.steps(10, keep=Heartbeat())
    pi.send(CalibrateGyro())
    pi.steps(1)
    assert pi.of(Ack)[-1].status == AckStatus.BUSY
    pi.steps(30, keep=Heartbeat())
    pi.send(CalibrateGyro())
    pi.steps(1)
    assert pi.of(Ack)[-1].status == AckStatus.OK


# ---------------- speed-sign mismatch (MODE DRIVE only) ----------------
def _fault_args(pi: Pi) -> list[int]:
    return [e.arg for e in pi.of(Event) if e.code == EventCode.ENCODER_FAULT]


def test_inverted_left_motor_trips_the_speed_sign_check() -> None:
    pi = Pi(SimEsp32(Cfg(motor_wiring_inverted=(True, False))))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(100, keep=Drive(0.2, 0.2))
    assert _fault_args(pi) == [10]
    fault = next(e for e in pi.of(Event) if e.code == EventCode.ENCODER_FAULT)
    assert 300 <= fault.t_ms <= 450  # 300 ms of mismatch after the track started to run away
    assert pi.sim.mode == DriveMode.SAFE and pi.sim.speed == (0.0, 0.0)
    assert not pi.of(Telemetry)[-1].flags & Flags.MOTORS_ENABLED
    # SAFE: further DRIVE frames are refused until the Pi sends MODE DRIVE again
    assert pi.of(Ack)[-1].status == AckStatus.REJECTED_MODE


def test_inverted_right_motor_reports_arg_11() -> None:
    pi = Pi(SimEsp32(Cfg(motor_wiring_inverted=(False, True))))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(100, keep=Drive(0.2, 0.2))
    assert _fault_args(pi) == [11] and pi.sim.mode == DriveMode.SAFE


def test_both_encoders_of_a_side_inverted_trip_the_check_too() -> None:
    cfg = Cfg(encoder_wiring_inverted=(True, True, False, False))
    pi = Pi(SimEsp32(cfg))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(100, keep=Drive(0.2, 0.2))
    assert _fault_args(pi) == [10]


def test_inverted_motor_corrected_by_parameter_drives_normally() -> None:
    pi = Pi(SimEsp32(Cfg(motor_wiring_inverted=(True, False))))
    pi.send(ParamSet(_pid("MOTOR_INVERT_LEFT"), 1))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(100, keep=Drive(0.2, 0.2))
    assert not _fault_args(pi) and pi.sim.true.x > 0.15


def test_speed_sign_is_not_checked_in_test_mode() -> None:
    """robotctl motor-test must be able to discover the inversions."""
    pi = Pi(SimEsp32(Cfg(motor_wiring_inverted=(True, False))))
    pi.send(Mode(DriveMode.TEST))
    pi.steps(100, keep=Drive(0.3, 0.3))
    assert not _fault_args(pi) and pi.sim.mode == DriveMode.TEST
    assert pi.sim.speed[0] < 0 < pi.sim.speed[1]


# ---------------- ramp-aware obstacle rule ----------------
def test_a_ramp_down_toward_an_obstacle_is_still_stopped() -> None:
    sim = SimEsp32(Cfg(start=(4.4, 0.0, 0.0)))  # wall at x = 5.0
    pi = Pi(sim)
    pi.send(Mode(DriveMode.DRIVE))
    for _ in range(300):
        pi.steps(1, keep=Drive(0.6, 0.6))
        if 5.0 - sim.true.x < 0.34:
            break
    assert sim.speed[0] > 0.5
    pi.send(Drive(0.0, 0.0))  # targets 0: the ramp-down (0.4 s, about 0.12 m) is still motion
    pi.steps(60, keep=Heartbeat())
    stops = [e for e in pi.of(Event) if e.code == EventCode.OBSTACLE_STOP]
    assert len(stops) == 1 and stops[0].arg < 250
    assert sim.speed == (0.0, 0.0) and 5.0 - sim.true.x > 0.22  # without the rule: about 0.21


def test_reverse_ramp_down_is_stopped_by_the_rear_rule() -> None:
    sim = SimEsp32(Cfg(start=(-0.4, 0.0, 0.0)))  # rear wall at x = -1.0
    pi = Pi(sim)
    pi.send(Mode(DriveMode.DRIVE))
    for _ in range(300):
        pi.steps(1, keep=Drive(-0.6, -0.6))
        if sim.true.x + 1.0 < 0.34:
            break
    pi.send(Drive(0.0, 0.0))
    pi.steps(60, keep=Heartbeat())
    assert EventCode.REAR_OBSTACLE_STOP in pi.events()
    assert sim.true.x + 1.0 > 0.22


# ---------------- centre front sensor ----------------
def test_missing_centre_sensor_blocks_forward_even_with_both_sides() -> None:
    pi = Pi(SimEsp32(Cfg(front_sensors=(True, False, True))))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(50, keep=Drive(0.2, 0.2))
    assert pi.sim.true.x == 0.0
    assert pi.of(Telemetry)[-1].ultrasonic_mm[1] == US_ABSENT
    rejected = [a for a in pi.of(Ack) if a.status == AckStatus.REJECTED_SAFETY]
    assert rejected and all(a.acked_type == MsgType.DRIVE for a in rejected)
    stop = next(e for e in pi.of(Event) if e.code == EventCode.OBSTACLE_STOP)
    assert stop.arg > 250  # the nearest valid side distance, not 0
    pi.steps(50, keep=Drive(-0.08, 0.08))  # turning in place stays allowed
    assert pi.sim.true.theta > 0.1
    pi.send(Stop())


def test_no_front_sensor_at_all_reports_arg_minus_1() -> None:
    pi = Pi(SimEsp32(Cfg(front_sensors=(False, False, False))))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(10, keep=Drive(0.2, 0.2))
    stop = next(e for e in pi.of(Event) if e.code == EventCode.OBSTACLE_STOP)
    assert stop.arg == -1
