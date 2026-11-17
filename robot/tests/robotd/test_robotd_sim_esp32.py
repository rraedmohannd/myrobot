"""The SIMULATED ESP32: protocol, and the safety rules of docs/SERIAL_PROTOCOL.md section 5.

Everything here is simulation (CLAUDE.md rule 5); no physical robot is involved.
"""

import asyncio
import time
from collections.abc import Callable

from scr_serial import (
    US_ABSENT,
    Ack,
    AckStatus,
    Bringup,
    BringupKind,
    BuildEnv,
    CalibrateGyro,
    DiagFlags,
    DiagReport,
    Drive,
    DriveMode,
    Event,
    EventCode,
    Flags,
    FrameDecoder,
    Heartbeat,
    ImuRaw,
    Message,
    Mode,
    MsgType,
    ParamGet,
    ParamReport,
    ParamSave,
    ParamSet,
    ParamSource,
    ResetReason,
    Sensors,
    Servo,
    Stop,
    Telemetry,
    UsRaw,
    encode_frame,
    load_table,
    param_hash,
)

from robotd.calibration import SIM_PROFILE
from robotd.link import LinkCore, SerialLink
from robotd.sim_esp32 import SimEsp32, SimEsp32Config, SimEsp32Server
from robotd.transport import TcpTransport


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


def test_telemetry_is_50_hz_and_always_flagged_simulated() -> None:
    pi = Pi(SimEsp32(Cfg()))
    pi.steps(100)
    frames = pi.of(Telemetry)
    assert len(frames) == 50
    assert all(f.is_simulated for f in frames)
    assert frames[1].t_ms - frames[0].t_ms == 20
    assert pi.events()[0] == EventCode.BOOT
    assert not frames[-1].flags & Flags.MOTORS_ENABLED  # power-on state is SAFE


def test_power_on_safe_mode_rejects_drive() -> None:
    pi = Pi(SimEsp32(Cfg()))
    pi.steps(50, keep=Drive(0.2, 0.2))
    acks = pi.of(Ack)
    assert acks
    assert all(a.status == AckStatus.REJECTED_MODE and a.acked_type == MsgType.DRIVE for a in acks)
    assert pi.sim.true.x == 0.0 and pi.of(Telemetry)[-1].encoders == (0, 0, 0, 0)


def test_mode_stop_are_acknowledged_and_drive_moves() -> None:
    pi = Pi(SimEsp32(Cfg()))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(100, keep=Drive(0.2, 0.2))
    assert pi.of(Ack)[0] == Ack(MsgType.MODE, 0, AckStatus.OK)
    assert 0.15 < pi.sim.true.x < 0.21
    last = pi.of(Telemetry)[-1]
    assert last.flags & Flags.MOTORS_ENABLED and all(e > 300 for e in last.encoders)
    pi.send(Stop())
    pi.steps(2)
    assert pi.of(Ack)[-1].acked_type == MsgType.STOP and pi.sim.speed == (0.0, 0.0)


def test_watchdog_stops_after_300_ms_without_frames() -> None:
    pi = Pi(SimEsp32(Cfg()))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(21, keep=Drive(0.2, 0.2))  # the last DRIVE is sent at step 20
    assert pi.sim.speed[0] > 0.19
    pi.steps(100)  # the Pi goes silent (heartbeat loss)
    assert pi.sim.speed == (0.0, 0.0)
    assert pi.of(Telemetry)[-1].flags & Flags.WATCHDOG_STOP
    stops = [m for m in pi.of(Event) if m.code == EventCode.WATCHDOG_STOP]
    assert len(stops) == 1 and 300 < stops[0].arg <= 320  # sent once, with the silent time
    x_stopped = pi.sim.true.x
    assert x_stopped < 0.2 * (0.21 + 0.32) + 0.01  # it did not keep driving
    # the next valid frame restores the link, but the robot moves only after a new DRIVE
    pi.steps(20, keep=Heartbeat())
    assert EventCode.LINK_RESTORED in pi.events()
    assert pi.sim.speed == (0.0, 0.0) and pi.sim.true.x == x_stopped
    pi.steps(20, keep=Drive(0.2, 0.2))
    assert pi.sim.speed[0] > 0.1 and not pi.of(Telemetry)[-1].flags & Flags.WATCHDOG_STOP


def test_damaged_frames_do_not_feed_the_watchdog() -> None:
    sim = SimEsp32(Cfg())
    pi = Pi(sim)
    for _ in range(40):
        sim.receive(b"\x05\x07\x01\x02\x03\x00")  # bad CRC
        pi.got += [f.msg for f in pi.decoder.feed(sim.step())]
    assert EventCode.WATCHDOG_STOP in pi.events()
    assert EventCode.PROTOCOL_ERROR in pi.events()


def test_front_obstacle_stop_and_rejected_safety() -> None:
    sim = SimEsp32(Cfg(start=(4.4, 0.0, 0.0)))  # wall at x = 5.0
    pi = Pi(sim)
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(400, keep=Drive(0.2, 0.2))
    assert sim.speed == (0.0, 0.0)
    assert 0.20 < 5.0 - sim.true.x <= 0.26  # stopped by the ESP32 at about 250 mm
    stops = [m for m in pi.of(Event) if m.code == EventCode.OBSTACLE_STOP]
    assert len(stops) == 1 and stops[0].arg < 250
    assert pi.of(Telemetry)[-1].flags & Flags.OBSTACLE_STOP
    rejected = [a for a in pi.of(Ack) if a.status == AckStatus.REJECTED_SAFETY]
    assert rejected and all(a.acked_type == MsgType.DRIVE for a in rejected)
    # turning in place stays allowed, so the Pi can turn away
    pi.got.clear()
    theta = sim.true.theta
    pi.steps(100, keep=Drive(-0.08, 0.08))
    assert not pi.of(Ack) and sim.true.theta > theta + 0.3


def test_reverse_needs_rear_sensing() -> None:
    blind = Pi(SimEsp32(Cfg(rear_sensor=False)))
    blind.send(Mode(DriveMode.DRIVE))
    blind.steps(50, keep=Drive(-0.1, -0.1))
    assert blind.of(Telemetry)[-1].ultrasonic_mm[3] == US_ABSENT
    acks = blind.of(Ack)[1:]  # after the MODE ack
    assert acks and all(a.status == AckStatus.REJECTED_SAFETY for a in acks)
    assert blind.sim.true.x == 0.0

    seeing = Pi(SimEsp32(Cfg()))  # rear wall at x = -1.0
    seeing.send(Mode(DriveMode.DRIVE))
    seeing.steps(100, keep=Drive(-0.1, -0.1))
    assert seeing.sim.true.x < -0.05
    seeing.steps(1500, keep=Drive(-0.1, -0.1))
    assert EventCode.REAR_OBSTACLE_STOP in seeing.events()
    assert 0.20 < seeing.sim.true.x + 1.0 <= 0.26 and seeing.sim.speed == (0.0, 0.0)


def test_all_front_sensors_absent_counts_as_blocked() -> None:
    pi = Pi(SimEsp32(Cfg(front_sensors=(False, False, False))))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(50, keep=Drive(0.2, 0.2))
    assert pi.sim.true.x == 0.0
    assert any(a.status == AckStatus.REJECTED_SAFETY for a in pi.of(Ack))


def test_servo_and_gyro_calibration() -> None:
    pi = Pi(SimEsp32(Cfg()))
    pi.send(Servo(120.0))
    pi.send(CalibrateGyro())
    pi.steps(210, keep=Heartbeat())
    assert pi.sim.servo_deg == 90.0  # clamped
    assert EventCode.GYRO_CAL_DONE in pi.events()
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(30, keep=Drive(0.2, 0.2))
    pi.send(CalibrateGyro())
    pi.steps(1)
    assert pi.of(Ack)[-1].status == AckStatus.BUSY


def test_same_seed_gives_the_same_bytes() -> None:
    def run(seed: int) -> bytes:
        pi = Pi(SimEsp32(Cfg(seed=seed)))
        pi.send(Mode(DriveMode.DRIVE))
        out = bytearray()
        for i in range(300):
            if i % 5 == 0:
                pi.send(Drive(0.2, 0.1))
            out += pi.sim.step()
        return bytes(out)

    assert run(3) == run(3)
    assert run(3) != run(4)


async def _wait(condition: Callable[[], bool], within_s: float = 5.0) -> bool:
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return condition()


async def test_real_socket_link_and_reconnect() -> None:
    """Over a real TCP socket, in real time: telemetry arrives, a port loss is survived."""
    server = SimEsp32Server(SimEsp32(Cfg()), port=0)
    port = await server.start()
    core = LinkCore()
    frames: list[Message] = []
    link = SerialLink(
        core, lambda: TcpTransport("127.0.0.1", port), lambda m, t: frames.append(m),
        time.monotonic, reconnect_s=0.05,
    )  # fmt: skip
    task = asyncio.create_task(link.run())
    try:
        assert await _wait(lambda: sum(isinstance(m, Telemetry) for m in frames) >= 10)
        assert all(m.is_simulated for m in frames if isinstance(m, Telemetry))
        assert not core.telemetry_lost(time.monotonic())
        assert await _wait(lambda: any(isinstance(m, Heartbeat) for m in server.sim.received))
        # the "port" disappears: the link notices, reconnects and telemetry flows again
        server.drop_client()
        assert await _wait(lambda: core.reconnects >= 1)
        frames.clear()
        assert await _wait(lambda: sum(isinstance(m, Telemetry) for m in frames) >= 10)
        assert core.bad_frames == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await server.stop()


# ---------------- protocol version 2: parameters, diagnostics, bring-up ----------------
def _pid(name: str) -> int:
    return load_table().by_name(name).id


def test_fresh_firmware_is_uncalibrated_and_does_not_move() -> None:
    pi = Pi(SimEsp32())  # compiled defaults, empty NVS
    pi.steps(5)
    assert pi.of(Telemetry)[-1].param_hash == param_hash(load_table().defaults())
    pi.send(Mode(DriveMode.DRIVE))  # METRES_PER_TICK 0: closed loop refused
    pi.steps(2)
    assert pi.of(Ack)[-1] == Ack(MsgType.MODE, 0, AckStatus.REJECTED_MODE)
    pi.send(Mode(DriveMode.TEST))  # TEST is allowed, but TEST_MAX_DUTY 0 drives nothing
    pi.steps(100, keep=Drive(0.2, 0.2))
    assert pi.sim.speed == (0.0, 0.0) and pi.sim.true.x == 0.0
    diag = pi.of(DiagReport)[0]  # sent once at boot, after EVENT BOOT
    assert diag.build_env == BuildEnv.ROBOTD_SIMULATED and diag.diag_flags & DiagFlags.SIMULATED
    assert not diag.diag_flags & DiagFlags.PINS_CONFIRMED
    assert diag.i2c_addresses == (0x40, 0x68) and not diag.sensors & Sensors.EXPANDER


def test_param_set_rules_hash_save_and_reboot() -> None:
    pi = Pi(SimEsp32())
    mpt = _pid("METRES_PER_TICK")
    pi.send(ParamSet(mpt, 0.02))  # above the compiled max 0.01
    pi.send(ParamSet(999, 1.0))
    pi.send(ParamSet(_pid("MOTOR_INVERT_LEFT"), 0.5))  # bool: 0 or 1 only
    pi.send(ParamSet(mpt, 0.0005))
    pi.steps(2)
    statuses = [a.status for a in pi.of(Ack)]
    assert statuses == [AckStatus.OUT_OF_RANGE, AckStatus.UNKNOWN_PARAM, AckStatus.OUT_OF_RANGE,
                        AckStatus.OK]  # fmt: skip
    report = pi.of(ParamReport)[-1]
    assert report.id == mpt and report.source == ParamSource.SET
    assert abs(report.value - 0.0005) < 1e-9 and abs(report.max - 0.01) < 1e-9
    assert EventCode.PARAM_CHANGED in pi.events()
    expected = param_hash({**load_table().defaults(), mpt: 0.0005})
    last = pi.of(Telemetry)[-1]
    assert last.param_hash == expected and last.flags & Flags.PARAMS_UNSAVED
    # a reboot before saving loses the value; after PARAM_SAVE it survives
    pi.sim.reboot()
    pi.steps(2)
    assert pi.of(Telemetry)[-1].param_hash == param_hash(load_table().defaults())
    pi.send(ParamSet(mpt, 0.0005))
    pi.send(ParamSave())
    pi.steps(2)
    assert pi.of(Ack)[-1].status == AckStatus.OK
    assert not pi.of(Telemetry)[-1].flags & Flags.PARAMS_UNSAVED
    pi.sim.reboot(ResetReason.BROWNOUT)
    pi.got.clear()
    pi.steps(2)
    assert pi.of(Telemetry)[-1].param_hash == expected
    diag = pi.of(DiagReport)[0]
    assert diag.reset_reason == ResetReason.BROWNOUT and diag.diag_flags & DiagFlags.PARAMS_FROM_NVS
    pi.send(ParamGet())  # all
    pi.steps(1)
    reports = pi.of(ParamReport)
    assert len(reports) == len(load_table().params)
    assert next(r for r in reports if r.id == mpt).source == ParamSource.NVS
    assert pi.of(Ack)[-1].status == AckStatus.OK


def test_param_set_busy_while_moving_and_save_only_when_idle() -> None:
    pi = Pi(SimEsp32(Cfg()))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(30, keep=Drive(0.2, 0.2))
    pi.send(ParamSet(_pid("PID_KP"), 2.0))
    pi.send(ParamSave())
    pi.steps(1)
    assert [a.status for a in pi.of(Ack)[-2:]] == [AckStatus.BUSY, AckStatus.REJECTED_MODE]
    pi.send(Stop())
    pi.steps(20, keep=Heartbeat())
    pi.send(ParamSave())  # DRIVE mode, targets 0, tracks still: allowed
    pi.steps(1)
    assert pi.of(Ack)[-1].status == AckStatus.OK


def test_the_values_it_holds_are_used() -> None:
    pi = Pi(SimEsp32(Cfg()))
    pi.send(ParamSet(_pid("MAX_SPEED_MM_S"), 100.0))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(100, keep=Drive(0.3, 0.3))
    assert abs(pi.sim.speed[0] - 0.1) < 1e-6  # clamped by MAX_SPEED_MM_S
    # a wrong METRES_PER_TICK: the firmware's speed loop drives the TRUE speed wrongly
    pi.send(Stop())
    pi.steps(10, keep=Heartbeat())
    pi.send(ParamSet(_pid("METRES_PER_TICK"), 0.001))  # twice the true value
    pi.steps(100, keep=Drive(0.1, 0.1))
    assert abs(pi.sim.speed[0] - 0.05) < 1e-6
    # TEST mode: open-loop duty capped by TEST_MAX_DUTY
    pi.send(Mode(DriveMode.TEST))
    pi.steps(100, keep=Drive(0.1, 0.1))
    assert pi.sim.speed[0] > 0.0


def test_inversions_affect_what_is_reported() -> None:
    cfg = Cfg(encoder_wiring_inverted=(True, False, False, False))
    pi = Pi(SimEsp32(cfg))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(100, keep=Drive(0.2, 0.2))
    enc = pi.of(Telemetry)[-1].encoders
    assert enc[0] < 0 < enc[1]  # the front-left encoder is wired the wrong way round
    assert EventCode.SLIP in pi.events()  # front and rear of the left side disagree
    pi.send(Stop())
    pi.steps(20, keep=Heartbeat())
    pi.send(ParamSet(_pid("ENCODER_INVERT_FL"), 1))
    pi.steps(2)
    before = pi.of(Telemetry)[-1].encoders
    pi.steps(100, keep=Drive(0.2, 0.2))
    after = pi.of(Telemetry)[-1].encoders
    assert after[0] > before[0] and after[1] > before[1]


def test_slip_on_demand_and_battery_cutoff() -> None:
    pi = Pi(SimEsp32(Cfg()))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(100, keep=Drive(0.2, 0.2))
    assert EventCode.SLIP not in pi.events()
    pi.sim.slip("right", 0.3)
    pi.steps(100, keep=Drive(0.2, 0.2))
    slips = [e for e in pi.of(Event) if e.code == EventCode.SLIP]
    assert len(slips) == 1 and slips[0].arg == 1  # once per episode, right side
    pi.send(ParamSet(_pid("BATTERY_CUTOFF_MV"), 11900))  # above the simulated 11.8 V pack
    pi.steps(1)
    assert pi.of(Ack)[-1].status == AckStatus.BUSY  # still moving
    pi.send(Stop())
    pi.steps(20, keep=Heartbeat())
    pi.send(ParamSet(_pid("BATTERY_CUTOFF_MV"), 11900))
    pi.steps(5, keep=Heartbeat())
    assert EventCode.BATTERY_CUTOFF in pi.events() and pi.sim.mode == DriveMode.SAFE


def test_battery_is_a_slowly_falling_simulated_pack() -> None:
    pi = Pi(SimEsp32(Cfg()))
    pi.steps(10, keep=Heartbeat())
    first = pi.of(Telemetry)[0]
    pi.steps(3000, keep=Heartbeat())  # 30 s idle
    last = pi.of(Telemetry)[-1]
    assert 11700 < last.battery_mv < first.battery_mv <= 11800 and last.battery_ma > 0


def test_bringup_streams_only_in_test_mode() -> None:
    pi = Pi(SimEsp32(Cfg()))
    pi.send(Bringup(BringupKind.IMU_RAW, 100))
    pi.steps(1)
    assert pi.of(Ack)[-1].status == AckStatus.REJECTED_MODE
    pi.send(Mode(DriveMode.TEST))
    pi.send(Bringup(BringupKind.IMU_RAW, 100))
    pi.send(Bringup(BringupKind.US_RAW))
    pi.send(Bringup(BringupKind.SERVO_JOG, 20))
    pi.send(Bringup(BringupKind.SERVO_JOG, 80))  # too big a step
    pi.steps(100, keep=Heartbeat())
    assert [a.status for a in pi.of(Ack)[-4:]] == [AckStatus.OK] * 3 + [AckStatus.BAD_ARGUMENT]
    assert 90 <= len(pi.of(ImuRaw)) <= 101 and len(pi.of(UsRaw)) >= 15
    pulses = [e.arg for e in pi.of(Event) if e.code == EventCode.SERVO_PULSE]
    assert pulses == [1520]
    assert pi.of(Telemetry)[-1].flags & Flags.BRINGUP_ACTIVE
    imu = pi.of(ImuRaw)[-1]
    assert abs(imu.gyro_urad_s[2] - 1000) < 60000 and imu.accel_mg == (0, 0, 1000)  # raw: bias in
    pi.send(Mode(DriveMode.SAFE))  # leaving TEST ends every stream
    pi.steps(2, keep=Heartbeat())
    count = len(pi.of(ImuRaw))
    pi.steps(50, keep=Heartbeat())
    assert len(pi.of(ImuRaw)) == count
    assert not pi.of(Telemetry)[-1].flags & Flags.BRINGUP_ACTIVE


def test_gyro_calibration_removes_a_drifting_bias() -> None:
    pi = Pi(SimEsp32(Cfg(gyro_bias_rps=0.02, gyro_bias_drift_rps_per_s=0.001)))
    pi.steps(50, keep=Heartbeat())
    raw = sum(t.gyro_z for t in pi.of(Telemetry)) / len(pi.of(Telemetry))
    assert raw > 0.015  # before calibration the bias is in the telemetry
    pi.send(CalibrateGyro())
    pi.steps(205, keep=Heartbeat())
    done = [e for e in pi.of(Event) if e.code == EventCode.GYRO_CAL_DONE]
    assert done and 200 <= done[0].arg <= 230  # 0.1 mrad/s units: about 0.021 rad/s
    pi.got.clear()
    pi.steps(50, keep=Heartbeat())
    after = sum(t.gyro_z for t in pi.of(Telemetry)) / len(pi.of(Telemetry))
    assert abs(after) < 0.005
    pi.steps(1000, keep=Heartbeat())  # 10 s later the bias has drifted by 0.01 rad/s
    pi.got.clear()
    pi.steps(100, keep=Heartbeat())
    drifted = sum(t.gyro_z for t in pi.of(Telemetry)) / len(pi.of(Telemetry))
    assert 0.007 < drifted < 0.013
