"""SIMULATED ESP32 for development without hardware (docs/SERIAL_PROTOCOL.md section 8).

*** Everything here is simulation. Every TELEMETRY frame carries the SIMULATED flag, robotd then
reports `is_simulated: true` everywhere, and nothing produced with it is a physical result
(CLAUDE.md rule 5). The noise and slip values are guesses, NOT measurements of the Rover 5. ***

It speaks exactly the serial protocol (COBS frames) and implements the safety rules of section 5
itself: 300 ms watchdog, front obstacle stop, reversing only with rear sensing, power-on SAFE.

`SimEsp32` is a pure step machine: `receive(bytes)` + `step()` (10 ms of simulated time) ->
bytes to send. Tests drive it without real time; `serve()` runs it in real time behind a TCP
socket (`robotd --sim`, or alone: `python -m robotd.sim_esp32 --port 8766`).
"""

import argparse
import asyncio
import contextlib
import logging
import math
import random
import time
from dataclasses import dataclass, field

from scr_odometry import Box, Pose, range_along, wrap_angle
from scr_serial import (
    US_ABSENT,
    US_NO_ECHO,
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
    Servo,
    Stop,
    Telemetry,
    encode_frame,
)

log = logging.getLogger("robotd.sim_esp32")

STEP_MS = 10  # the firmware control loop: 100 Hz
TELEMETRY_EVERY = 2  # -> 50 Hz
FRONT_OFFSETS = (0.44, 0.0, -0.44)  # front-left, front-centre, front-right (about 25 degrees)


@dataclass
class SimEsp32Config:
    seed: int = 7
    # room in the world frame (start pose = origin): 6 m x 5 m, boxes from the sprint-1 simulator
    room: Box = (-1.0, -1.5, 5.0, 3.5)
    obstacles: tuple[Box, ...] = (
        (1.6, 0.9, 2.2, 1.5),
        (3.3, -0.9, 3.9, -0.3),
        (0.6, 2.3, 1.2, 2.9),
    )
    start: tuple[float, float, float] = (0.0, 0.0, 0.0)
    metres_per_tick: float = 0.0005  # placeholder, the same guess as robotd's config
    track_width_m: float = 0.19  # placeholder
    accel_mps2: float = 1.5  # speed ramp
    max_speed_mps: float = 0.6
    obstacle_stop_mm: int = 250  # firmware default (SERIAL_PROTOCOL.md section 5)
    watchdog_ms: int = 300
    stop_hysteresis_mm: int = 40
    us_max_mm: int = 4000  # beyond this: no echo
    us_noise_mm: float = 4.0
    rear_sensor: bool = True
    front_sensors: tuple[bool, bool, bool] = (True, True, True)
    # noise (guesses): the same values as the sprint-1 simulator
    slip_drive: float = 0.01
    slip_turn: float = 0.25
    slip_noise: float = 0.3
    gyro_bias_rps: float = 0.001
    gyro_noise_rps: float = 0.01
    encoder_noise: float = 0.01  # relative front/rear difference on one side
    battery_mv: int = 11800  # 3S pack, simulated
    battery_low_mv: int = 9900
    battery_drain_mv_per_m: float = 2.0
    boot_t_ms: int = 0  # start value of the clock (tests set it near the u32 wrap)


@dataclass
class SimEsp32:
    cfg: SimEsp32Config = field(default_factory=SimEsp32Config)

    def __post_init__(self) -> None:
        cfg = self.cfg
        self.rng = random.Random(cfg.seed)
        self.true = Pose(*cfg.start)  # where the simulated robot really is
        self.t_ms = cfg.boot_t_ms
        self.mode = DriveMode.SAFE
        self.target = (0.0, 0.0)
        self.speed = (0.0, 0.0)
        self.servo_deg = 0.0
        self.enc = [0.0, 0.0, 0.0, 0.0]
        self.wheel_slip = [1.0, 1.0, 1.0, 1.0]  # tests: 0.5 = this encoder sees half the motion
        self.flags = Flags.SIMULATED | Flags.IMU_OK | Flags.POWER_SENSOR_OK
        self.battery_mv = float(cfg.battery_mv)
        self.last_valid_ms = self.t_ms
        self.watchdog_stopped = False
        self.calibrating_until: int | None = None
        self.collisions = 0  # steps in which a wall or box blocked the true motion
        self.received: list[Message] = []  # every valid frame, for tests
        self._decoder = FrameDecoder()
        self._reported_bad = 0
        self._seq = 0
        self._out = bytearray()
        self._step_no = 0
        self._gyro = 0.0
        self._emit(Event(self._t32(), EventCode.BOOT))

    # ---------------- helpers ----------------
    def _t32(self) -> int:
        return self.t_ms & 0xFFFFFFFF

    def _emit(self, msg: Message) -> None:
        self._out += encode_frame(msg, self._seq)
        self._seq = (self._seq + 1) & 0xFF

    def _ack(self, msg: Message, seq: int, status: AckStatus) -> None:
        self._emit(Ack(int(msg.TYPE), seq, status))

    def _halt(self) -> None:
        self.target = (0.0, 0.0)
        self.speed = (0.0, 0.0)

    def ultrasonic_mm(self) -> tuple[int, int, int, int]:
        """Front-left, front-centre, front-right, rear, from the TRUE simulated pose."""
        cfg, p = self.cfg, self.true
        out: list[int] = []
        present = (*cfg.front_sensors, cfg.rear_sensor)
        for ok, offset in zip(present, (*FRONT_OFFSETS, math.pi), strict=True):
            if not ok:
                out.append(US_ABSENT)
                continue
            dist = range_along(p.x, p.y, p.theta + offset, cfg.room, cfg.obstacles) * 1000
            dist += self.rng.gauss(0, cfg.us_noise_mm)
            out.append(US_NO_ECHO if dist > cfg.us_max_mm else max(0, int(dist)))
        return out[0], out[1], out[2], out[3]

    @staticmethod
    def _front_mm(us: tuple[int, int, int, int]) -> int | None:
        """Nearest front distance; 0 when every front sensor is absent (rule 5); None = clear."""
        if all(v == US_ABSENT for v in us[:3]):
            return 0
        real = [v for v in us[:3] if v not in (US_ABSENT, US_NO_ECHO)]
        return min(real) if real else None

    # ---------------- Pi -> ESP32 ----------------
    def receive(self, data: bytes) -> None:
        for frame in self._decoder.feed(data):
            self.last_valid_ms = self.t_ms
            if self.watchdog_stopped:
                self.watchdog_stopped = False
                self._emit(Event(self._t32(), EventCode.LINK_RESTORED))
            self.received.append(frame.msg)
            self._handle(frame.msg, frame.seq)
        bad = self._decoder.bad_frames - self._reported_bad
        if bad:
            self._reported_bad = self._decoder.bad_frames
            self._emit(Event(self._t32(), EventCode.PROTOCOL_ERROR, min(bad, 32767)))

    def _handle(self, msg: Message, seq: int) -> None:
        cfg = self.cfg
        if isinstance(msg, Drive):
            if self.mode == DriveMode.SAFE:
                self._ack(msg, seq, AckStatus.REJECTED_MODE)
                return
            total = msg.left + msg.right
            us = self.ultrasonic_mm()
            if total > 0:
                front = self._front_mm(us)
                # a little hysteresis, so sensor noise at the threshold does not release the stop
                held = cfg.stop_hysteresis_mm if self.flags & Flags.OBSTACLE_STOP else 0
                if front is not None and front < cfg.obstacle_stop_mm + held:
                    self._front_stop(front)
                    self._ack(msg, seq, AckStatus.REJECTED_SAFETY)
                    return
            elif total < 0:
                rear = us[3]
                if rear == US_ABSENT:
                    self._halt()
                    self._ack(msg, seq, AckStatus.REJECTED_SAFETY)
                    return
                held = cfg.stop_hysteresis_mm if self.flags & Flags.REAR_OBSTACLE_STOP else 0
                if rear != US_NO_ECHO and rear < cfg.obstacle_stop_mm + held:
                    self._rear_stop(rear)
                    self._ack(msg, seq, AckStatus.REJECTED_SAFETY)
                    return
            limit = cfg.max_speed_mps
            self.target = (
                max(-limit, min(limit, msg.left)),
                max(-limit, min(limit, msg.right)),
            )
            self.flags &= ~(Flags.WATCHDOG_STOP | Flags.OBSTACLE_STOP | Flags.REAR_OBSTACLE_STOP)
        elif isinstance(msg, Stop):
            self._halt()
            self._ack(msg, seq, AckStatus.OK)
        elif isinstance(msg, Servo):
            self.servo_deg = max(-90.0, min(90.0, msg.pan_deg))
        elif isinstance(msg, Mode):
            self.mode = msg.mode
            self._halt()
            self._ack(msg, seq, AckStatus.OK)
        elif isinstance(msg, CalibrateGyro):
            if any(abs(v) > 1e-6 for v in self.speed):
                self._ack(msg, seq, AckStatus.BUSY)
            else:
                self.calibrating_until = self.t_ms + 2000
                self.flags |= Flags.GYRO_CALIBRATING
                self._ack(msg, seq, AckStatus.OK)
        elif isinstance(msg, Heartbeat):
            pass
        else:  # an ESP32 -> Pi type sent the wrong way
            self._emit(Ack(int(msg.TYPE), seq, AckStatus.UNKNOWN_TYPE))

    def _front_stop(self, front_mm: int) -> None:
        self._halt()
        if not self.flags & Flags.OBSTACLE_STOP:
            self.flags |= Flags.OBSTACLE_STOP
            self._emit(Event(self._t32(), EventCode.OBSTACLE_STOP, front_mm))

    def _rear_stop(self, rear_mm: int) -> None:
        self._halt()
        if not self.flags & Flags.REAR_OBSTACLE_STOP:
            self.flags |= Flags.REAR_OBSTACLE_STOP
            self._emit(Event(self._t32(), EventCode.REAR_OBSTACLE_STOP, rear_mm))

    # ---------------- one control step: 10 ms of simulated time ----------------
    def step(self) -> bytes:
        """Advance 10 ms. Returns the bytes the ESP32 writes to the port (may be empty)."""
        cfg = self.cfg
        dt = STEP_MS / 1000.0
        self.t_ms += STEP_MS
        self._step_no += 1
        us = self.ultrasonic_mm()

        # safety rule 1: watchdog
        silent = self.t_ms - self.last_valid_ms
        if silent > cfg.watchdog_ms and not self.watchdog_stopped:
            self.watchdog_stopped = True
            self._halt()
            self.flags |= Flags.WATCHDOG_STOP
            self._emit(Event(self._t32(), EventCode.WATCHDOG_STOP, min(silent, 32767)))
        # safety rules 2 and 3: obstacles in the commanded direction
        total = self.target[0] + self.target[1]
        if total > 0:
            front = self._front_mm(us)
            if front is not None and front < cfg.obstacle_stop_mm:
                self._front_stop(front)
        elif total < 0 and (us[3] == US_ABSENT or us[3] < cfg.obstacle_stop_mm):
            self._rear_stop(0 if us[3] == US_ABSENT else us[3])
        if self.mode == DriveMode.SAFE:
            self._halt()

        # speed ramp, then the true motion with slip
        ramp = cfg.accel_mps2 * dt
        self.speed = (
            self.speed[0] + max(-ramp, min(ramp, self.target[0] - self.speed[0])),
            self.speed[1] + max(-ramp, min(ramp, self.target[1] - self.speed[1])),
        )
        left, right = self.speed
        v_cmd, w_cmd = (left + right) / 2, (right - left) / cfg.track_width_m
        v_true = v_cmd * (1 - self._slip(cfg.slip_drive))
        w_true = w_cmd * (1 - self._slip(cfg.slip_turn))
        mid = self.true.theta + w_true * dt / 2
        nx = self.true.x + v_true * dt * math.cos(mid)
        ny = self.true.y + v_true * dt * math.sin(mid)
        if self._free(nx, ny):
            self.true = Pose(nx, ny, wrap_angle(self.true.theta + w_true * dt))
        else:  # pushed against a wall: the tracks turn (encoders count) but the robot stays
            self.collisions += 1
            self.true = Pose(self.true.x, self.true.y, wrap_angle(self.true.theta + w_true * dt))
        for i, side_speed in enumerate((left, left, right, right)):
            noise = 1 + self.rng.gauss(0, cfg.encoder_noise) if side_speed else 1.0
            self.enc[i] += side_speed * dt / cfg.metres_per_tick * noise * self.wheel_slip[i]
        moving = abs(left) + abs(right) > 1e-6
        self._gyro = w_true + cfg.gyro_bias_rps + self.rng.gauss(0, cfg.gyro_noise_rps)
        self.battery_mv -= abs(v_cmd) * dt * cfg.battery_drain_mv_per_m

        if self.calibrating_until is not None and self.t_ms >= self.calibrating_until:
            self.calibrating_until = None
            self.flags &= ~Flags.GYRO_CALIBRATING
            code = EventCode.GYRO_CAL_FAILED if moving else EventCode.GYRO_CAL_DONE
            self._emit(Event(self._t32(), code, int(round(cfg.gyro_bias_rps * 10000))))
        if self.battery_mv < cfg.battery_low_mv and not self.flags & Flags.LOW_BATTERY:
            self.flags |= Flags.LOW_BATTERY
            self._emit(Event(self._t32(), EventCode.LOW_BATTERY, int(self.battery_mv)))

        if self._step_no % TELEMETRY_EVERY == 0:
            flags = self.flags
            if self.mode != DriveMode.SAFE:
                flags |= Flags.MOTORS_ENABLED
            self._emit(
                Telemetry(
                    t_ms=self._t32(),
                    encoders=tuple(self._i32(v) for v in self.enc),  # type: ignore[arg-type]
                    gyro_z=self._gyro,
                    accel_mg=(0, 0, 1000),
                    ultrasonic_mm=us,
                    battery_mv=max(0, int(self.battery_mv)),
                    battery_ma=400 + int(2500 * (abs(left) + abs(right))),
                    flags=flags,
                )
            )
        out = bytes(self._out)
        self._out.clear()
        return out

    @staticmethod
    def _i32(value: float) -> int:
        return (int(round(value)) + (1 << 31)) % (1 << 32) - (1 << 31)

    def _slip(self, mean: float) -> float:
        return min(0.9, max(0.0, self.rng.gauss(mean, mean * self.cfg.slip_noise)))

    def _free(self, x: float, y: float, margin: float = 0.02) -> bool:
        x0, y0, x1, y1 = self.cfg.room
        if not (x0 + margin <= x <= x1 - margin and y0 + margin <= y <= y1 - margin):
            return False
        return not any(
            bx0 - margin < x < bx1 + margin and by0 - margin < y < by1 + margin
            for bx0, by0, bx1, by1 in self.cfg.obstacles
        )


# ---------------- real-time TCP server (robotd --sim) ----------------
class SimEsp32Server:
    """The SIMULATED ESP32 behind a local TCP socket, in real time. One client at a time."""

    def __init__(self, sim: SimEsp32 | None = None, host: str = "127.0.0.1", port: int = 8766):
        self.sim = sim or SimEsp32()
        self.host, self.port = host, port
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._loop_task: asyncio.Task[None] | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        self._loop_task = asyncio.create_task(self._run())
        log.warning(
            "SIMULATED ESP32 listening on %s:%d (not a physical robot)", self.host, self.port
        )
        return self.port

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        self.drop_client()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def drop_client(self) -> None:
        """Close the current connection (tests: 'the port disappeared')."""
        if self._writer:
            self._writer.close()
            self._writer = None

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.drop_client()
        self._writer = writer
        try:
            while data := await reader.read(4096):
                self.sim.receive(data)
        except (ConnectionError, OSError):
            pass
        finally:
            if self._writer is writer:
                self._writer = None
            writer.close()

    async def _run(self) -> None:
        start, steps = time.monotonic(), 0
        while True:
            due = int((time.monotonic() - start) * 1000 / STEP_MS)
            if due - steps > 100:  # the process was suspended: do not replay the gap
                start, steps, due = time.monotonic(), 0, 1
            out = bytearray()
            while steps < due:
                out += self.sim.step()
                steps += 1
            writer = self._writer
            if out and writer is not None:
                try:
                    writer.write(bytes(out))
                    await writer.drain()
                except (ConnectionError, OSError):
                    self.drop_client()
            await asyncio.sleep(STEP_MS / 1000)


async def _main(host: str, port: int, seed: int) -> None:
    server = SimEsp32Server(SimEsp32(SimEsp32Config(seed=seed)), host, port)
    await server.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SIMULATED ESP32 (serial protocol over TCP)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main(args.host, args.port, args.seed))
