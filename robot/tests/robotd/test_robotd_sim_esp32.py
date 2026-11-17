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
    Servo,
    Stop,
    Telemetry,
    encode_frame,
)

from robotd.link import LinkCore, SerialLink
from robotd.sim_esp32 import SimEsp32, SimEsp32Config, SimEsp32Server
from robotd.transport import TcpTransport


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
    pi = Pi(SimEsp32())
    pi.steps(100)
    frames = pi.of(Telemetry)
    assert len(frames) == 50
    assert all(f.is_simulated for f in frames)
    assert frames[1].t_ms - frames[0].t_ms == 20
    assert pi.events()[0] == EventCode.BOOT
    assert not frames[-1].flags & Flags.MOTORS_ENABLED  # power-on state is SAFE


def test_power_on_safe_mode_rejects_drive() -> None:
    pi = Pi(SimEsp32())
    pi.steps(50, keep=Drive(0.2, 0.2))
    acks = pi.of(Ack)
    assert acks
    assert all(a.status == AckStatus.REJECTED_MODE and a.acked_type == MsgType.DRIVE for a in acks)
    assert pi.sim.true.x == 0.0 and pi.of(Telemetry)[-1].encoders == (0, 0, 0, 0)


def test_mode_stop_are_acknowledged_and_drive_moves() -> None:
    pi = Pi(SimEsp32())
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
    pi = Pi(SimEsp32())
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
    sim = SimEsp32()
    pi = Pi(sim)
    for _ in range(40):
        sim.receive(b"\x05\x07\x01\x02\x03\x00")  # bad CRC
        pi.got += [f.msg for f in pi.decoder.feed(sim.step())]
    assert EventCode.WATCHDOG_STOP in pi.events()
    assert EventCode.PROTOCOL_ERROR in pi.events()


def test_front_obstacle_stop_and_rejected_safety() -> None:
    sim = SimEsp32(SimEsp32Config(start=(4.4, 0.0, 0.0)))  # wall at x = 5.0
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
    blind = Pi(SimEsp32(SimEsp32Config(rear_sensor=False)))
    blind.send(Mode(DriveMode.DRIVE))
    blind.steps(50, keep=Drive(-0.1, -0.1))
    assert blind.of(Telemetry)[-1].ultrasonic_mm[3] == US_ABSENT
    acks = blind.of(Ack)[1:]  # after the MODE ack
    assert acks and all(a.status == AckStatus.REJECTED_SAFETY for a in acks)
    assert blind.sim.true.x == 0.0

    seeing = Pi(SimEsp32())  # rear wall at x = -1.0
    seeing.send(Mode(DriveMode.DRIVE))
    seeing.steps(100, keep=Drive(-0.1, -0.1))
    assert seeing.sim.true.x < -0.05
    seeing.steps(1500, keep=Drive(-0.1, -0.1))
    assert EventCode.REAR_OBSTACLE_STOP in seeing.events()
    assert 0.20 < seeing.sim.true.x + 1.0 <= 0.26 and seeing.sim.speed == (0.0, 0.0)


def test_all_front_sensors_absent_counts_as_blocked() -> None:
    pi = Pi(SimEsp32(SimEsp32Config(front_sensors=(False, False, False))))
    pi.send(Mode(DriveMode.DRIVE))
    pi.steps(50, keep=Drive(0.2, 0.2))
    assert pi.sim.true.x == 0.0
    assert any(a.status == AckStatus.REJECTED_SAFETY for a in pi.of(Ack))


def test_servo_and_gyro_calibration() -> None:
    pi = Pi(SimEsp32())
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
        pi = Pi(SimEsp32(SimEsp32Config(seed=seed)))
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
    server = SimEsp32Server(SimEsp32(), port=0)
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
