"""SIMULATED ESP32 for development without hardware (docs/SERIAL_PROTOCOL.md sections 8 and 10).

*** Everything here is simulation. Every TELEMETRY frame carries the SIMULATED flag, every
DIAG_REPORT says build_env 5 (robotd simulated ESP32) with the SIMULATED diag flag, robotd then
reports `is_simulated: true` everywhere, and nothing produced with it is a physical result
(CLAUDE.md rule 5). The noise, slip, motor and battery values are guesses, NOT measurements. ***

It speaks serial protocol version 2 and follows the firmware's rules:
- safety (section 5): 300 ms watchdog, front obstacle stop, reversing only with rear sensing,
  power-on SAFE;
- runtime parameters (section 10) from `shared/scr_serial/params.json`, with the firmware's
  rules: bounds -> OUT_OF_RANGE, unknown id -> UNKNOWN_PARAM, BUSY while the tracks move,
  PARAM_SAVE only when SAFE or idle, an in-memory "NVS" that survives `reboot()`, the parameter
  hash in every telemetry frame and in DIAG_REPORT;
- it USES the values it holds, like the firmware: METRES_PER_TICK 0 -> MODE DRIVE refused,
  TEST_MAX_DUTY 0 -> nothing moves in TEST, MAX_SPEED_MM_S / RAMP_MM_S2 shape the targets,
  MOTOR_MAX_DUTY and the voltage compensation cap the duty, the obstacle thresholds, servo limits,
  inversions, GYRO_SCALE, battery scales, SLIP_RATIO / SLIP_MIN_TICKS, LOW_BATTERY_MV and
  BATTERY_CUTOFF_MV;
- bring-up streams IMU_RAW / US_RAW and SERVO_JOG (MODE TEST only), CALIBRATE_GYRO with a
  simulated (optionally drifting) bias, EVENT SLIP, a slowly falling simulated 3S pack.

A fresh `SimEsp32()` is a NEVER-CALIBRATED firmware (compiled defaults: nothing moves). Give it
`SimEsp32Config(nvs=SIM_PROFILE)` to boot as if a SIMULATED calibration had been saved, or let
robotd push its calibration (it does at every connection).

The simulated world has its own "true" values (`true_metres_per_tick`, `gyro_scale_true`,
wiring inversions...) so the bring-up tools can find them, as they would on the real robot.

`SimEsp32` is a pure step machine: `receive(bytes)` + `step()` (10 ms of simulated time) ->
bytes to send. Tests drive it without real time; `serve()` runs it in real time behind a TCP
socket (`robotd --sim`, or alone: `python -m robotd.sim_esp32 --port 8766 [--profile sim]`).
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
    PARAM_ALL,
    US_ABSENT,
    US_NO_ECHO,
    Ack,
    AckStatus,
    Bringup,
    BringupKind,
    BuildEnv,
    CalibrateGyro,
    DiagFlags,
    DiagReport,
    DiagRequest,
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
    f32,
    load_table,
    param_hash,
)

from robotd.calibration import SIM_PROFILE

log = logging.getLogger("robotd.sim_esp32")

STEP_MS = 10  # the firmware control loop: 100 Hz
TELEMETRY_EVERY = 2  # -> 50 Hz
US_ROUND_STEPS = 6  # one ultrasonic round every 60 ms
FRONT_OFFSETS = (0.44, 0.0, -0.44)  # front-left, front-centre, front-right (about 25 degrees)
TEST_FULL_SCALE_MM_S = 1000.0  # firmware config.h: a DRIVE of 1000 mm/s means duty 1.0 in TEST
FULL_PACK_V = 12.6  # voltage compensation fallback without an INA219 reading
GYRO_CAL_MS = 2000
STILL_MPS = 0.01  # "the tracks move" above 10 mm/s
SIM_FW_VERSION = (0, 5, 0)

__all__ = ["SIM_PROFILE", "SimEsp32", "SimEsp32Config", "SimEsp32Server"]


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
    # "NVS" at power-on: {parameter name: value}. None/{} = a never-calibrated firmware.
    nvs: dict[str, float] | None = None
    # ---- the simulated world's TRUE values (guesses; found by the bring-up tools) ----
    true_metres_per_tick: float = 0.0005
    track_width_m: float = 0.19
    rated_speed_mps: float = 0.9  # track speed at duty 1.0 with MOTOR_RATED_V on the motor
    gyro_scale_true: float = 1.0  # the chip's scale error (GYRO_SCALE corrects it)
    motor_wiring_inverted: tuple[bool, bool] = (False, False)  # left, right
    encoder_wiring_inverted: tuple[bool, bool, bool, bool] = (False, False, False, False)
    power_sensor: bool = True  # INA219 present
    ina_voltage_error: float = 1.0  # INA219 reading / true voltage
    watchdog_ms: int = 300
    us_max_mm: int = 4000  # beyond this: no echo
    us_noise_mm: float = 4.0
    rear_sensor: bool = True
    front_sensors: tuple[bool, bool, bool] = (True, True, True)
    # noise (guesses): the same values as the sprint-1 simulator
    slip_drive: float = 0.01
    slip_turn: float = 0.25
    slip_noise: float = 0.3
    gyro_bias_rps: float = 0.001
    gyro_bias_drift_rps_per_s: float = 0.0  # tests: a bias that wanders while the robot runs
    gyro_noise_rps: float = 0.01
    encoder_noise: float = 0.01  # relative front/rear difference on one side
    battery_mv: int = 11800  # 3S pack, simulated
    battery_drain_mv_per_s: float = 0.05  # idle drain (about 3 mV per minute)
    battery_drain_mv_per_m: float = 2.0
    i2c_addresses: tuple[int, ...] = (0x40, 0x68)  # INA219, MPU-6050 (simulated scan)
    reset_reason: ResetReason = ResetReason.POWER_ON
    boot_t_ms: int = 0  # start value of the clock (tests set it near the u32 wrap)


@dataclass
class SimEsp32:
    cfg: SimEsp32Config = field(default_factory=SimEsp32Config)

    def __post_init__(self) -> None:
        cfg = self.cfg
        self.table = load_table()
        self.rng = random.Random(cfg.seed)
        self.true = Pose(*cfg.start)  # where the simulated robot really is
        self.battery_mv = float(cfg.battery_mv)  # TRUE pack voltage
        self.nvs: dict[int, float] = {
            self.table.by_name(k).id: f32(v) for k, v in (cfg.nvs or {}).items()
        }
        self.received: list[Message] = []  # every valid frame, for tests
        self.collisions = 0  # steps in which a wall or box blocked the true motion
        self.wheel_slip = [1.0, 1.0, 1.0, 1.0]  # tests: 0.5 = this encoder sees half the motion
        self.servo_deg = 0.0
        self.boots = 0
        self._seq = 0
        self._out = bytearray()
        self._world_s = 0.0
        self._boot(cfg.boot_t_ms, cfg.reset_reason)

    # ---------------- power-on / reset ----------------
    def _boot(self, t_ms: int, reason: ResetReason) -> None:
        self.boots += 1
        self.t_ms = t_ms
        self._boot_t = t_ms
        self.reset_reason = reason
        self.values: dict[int, float] = {**self.table.defaults(), **self.nvs}
        self.sources: dict[int, ParamSource] = {
            p.id: ParamSource.NVS if p.id in self.nvs else ParamSource.DEFAULT
            for p in self.table.params
        }
        self._hash_cache: int | None = None
        self.mode = DriveMode.SAFE
        self.target = (0.0, 0.0)  # m/s, as accepted (clamped to MAX_SPEED_MM_S)
        self._ramped = [0.0, 0.0]
        self.speed = (0.0, 0.0)  # TRUE track speeds, m/s
        self.enc = [0.0, 0.0, 0.0, 0.0]  # TRUE counts (before the firmware's inversion)
        self.flags = Flags.SIMULATED | Flags.IMU_OK
        if self.cfg.power_sensor:
            self.flags |= Flags.POWER_SENSOR_OK
        else:
            self.flags |= Flags.VOLTAGE_COMP_FALLBACK
        self.last_valid_ms = self.t_ms
        self.watchdog_stopped = False
        self.calibrating_until: int | None = None
        self.gyro_cal_bias = 0.0  # what the firmware subtracts (rad/s)
        self._cal_sum, self._cal_n, self._cal_moved = 0.0, 0, False
        self.servo_pulse_us = self.p("SERVO_CENTER_PULSE_US")
        self.imu_rate_hz = 0
        self.us_stream = False
        self._imu_acc = 0.0
        self._us_misses = [0, 0, 0, 0]
        self._slip_win = [0, 0, 0, 0]
        self._slip_steps = 0
        self._slip_active = [False, False]
        self._low_reported = False
        self._cutoff_reported = False
        self._decoder = FrameDecoder()
        self._reported_bad = 0
        self._step_no = 0
        self._gyro = 0.0
        self._last_us: tuple[int, int, int, int] = (US_ABSENT,) * 4
        self._emit(Event(self._t32(), EventCode.BOOT))
        self._emit(self.diag_report())

    def reboot(self, reason: ResetReason = ResetReason.SOFTWARE) -> None:
        """A simulated reset: unsaved parameters are lost, the NVS stays, the clock and the
        encoder counters start again from 0, the mode is SAFE."""
        self._boot(0, reason)

    # ---------------- parameters ----------------
    def p(self, name: str) -> float:
        return self.values[self.table.by_name(name).id]

    @property
    def param_hash(self) -> int:
        if self._hash_cache is None:
            self._hash_cache = param_hash(self.values)
        return self._hash_cache

    def _unsaved(self) -> bool:
        saved = {**self.table.defaults(), **self.nvs}
        return any(f32(saved[k]) != f32(v) for k, v in self.values.items())

    def _report(self, pid: int) -> None:
        spec = self.table.by_id(pid)
        self._emit(ParamReport(pid, self.values[pid], spec.min, spec.max, self.sources[pid]))

    # ---------------- test helpers (simulation only) ----------------
    def slip(self, side: str, factor: float = 0.4) -> None:
        """Make the FRONT encoder of one side see only `factor` of the motion (a slipping
        track); factor 1.0 ends it."""
        self.wheel_slip[0 if side == "left" else 2] = factor

    def hand_push(self, side: str, metres: float) -> None:
        """Somebody pushes one track by hand (encoders count; the robot does not drive)."""
        for i in (0, 1) if side == "left" else (2, 3):
            self.enc[i] += metres / self.cfg.true_metres_per_tick

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
        self._ramped = [0.0, 0.0]
        self.speed = (0.0, 0.0)

    def _moving(self) -> bool:
        return self.target != (0.0, 0.0) or any(abs(v) > STILL_MPS for v in self.speed)

    def _bringup_off(self) -> None:
        self.imu_rate_hz = 0
        self.us_stream = False
        self.flags &= ~Flags.BRINGUP_ACTIVE

    def _bias_now(self) -> float:
        return self.cfg.gyro_bias_rps + self.cfg.gyro_bias_drift_rps_per_s * self._world_s

    def diag_report(self) -> DiagReport:
        cfg = self.cfg
        sensors = Sensors.IMU
        if cfg.power_sensor:
            sensors |= Sensors.INA219
        if 0x20 in cfg.i2c_addresses:
            sensors |= Sensors.EXPANDER
        for i, ok in enumerate((*cfg.front_sensors, cfg.rear_sensor)):
            if ok:
                sensors |= Sensors(Sensors.US0 << i)
        dflags = DiagFlags.SIMULATED | (DiagFlags.PARAMS_FROM_NVS if self.nvs else DiagFlags(0))
        addresses = tuple(a for a in cfg.i2c_addresses if cfg.power_sensor or a != 0x40)
        return DiagReport(
            fw_version=SIM_FW_VERSION, git_hash=0, build_env=BuildEnv.ROBOTD_SIMULATED,
            driver_type=0, diag_flags=dflags, param_hash=self.param_hash,
            reset_reason=self.reset_reason, uptime_ms=(self.t_ms - self._boot_t) & 0xFFFFFFFF,
            loop_period_max_us=10_000, loop_jitter_us=0, free_heap=200_000,
            i2c_addresses=addresses, sensors=sensors,
        )  # fmt: skip

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
        if isinstance(msg, Drive):
            self._on_drive(msg, seq)
        elif isinstance(msg, Stop):
            self._halt()
            self._ack(msg, seq, AckStatus.OK)
        elif isinstance(msg, Servo):
            self._on_servo(msg.pan_deg)
        elif isinstance(msg, Mode):
            if msg.mode == DriveMode.DRIVE and self.p("METRES_PER_TICK") <= 0:
                self._ack(msg, seq, AckStatus.REJECTED_MODE)  # not calibrated: no closed loop
                return
            if msg.mode != DriveMode.TEST:
                self._bringup_off()
            self.mode = msg.mode
            self._halt()
            self._ack(msg, seq, AckStatus.OK)
        elif isinstance(msg, CalibrateGyro):
            if self.calibrating_until is not None or self._moving():
                self._ack(msg, seq, AckStatus.BUSY)
            else:
                self.calibrating_until = self.t_ms + GYRO_CAL_MS
                self._cal_sum, self._cal_n, self._cal_moved = 0.0, 0, False
                self.flags |= Flags.GYRO_CALIBRATING
                self._ack(msg, seq, AckStatus.OK)
        elif isinstance(msg, Heartbeat):
            pass
        elif isinstance(msg, ParamSet):
            self._on_param_set(msg, seq)
        elif isinstance(msg, ParamGet):
            if msg.id == PARAM_ALL:
                for spec in self.table.params:
                    self._report(spec.id)
            elif msg.id in self.values:
                self._report(msg.id)
            else:
                self._ack(msg, seq, AckStatus.UNKNOWN_PARAM)
                return
            self._ack(msg, seq, AckStatus.OK)
        elif isinstance(msg, ParamSave):
            idle = self.target == (0.0, 0.0) and not self._moving()
            if self.mode != DriveMode.SAFE and not idle:
                self._ack(msg, seq, AckStatus.REJECTED_MODE)
                return
            defaults = self.table.defaults()
            self.nvs = {k: v for k, v in self.values.items() if f32(defaults[k]) != f32(v)}
            self.sources = {k: ParamSource.NVS if k in self.nvs else ParamSource.DEFAULT
                            for k in self.values}  # fmt: skip
            self._ack(msg, seq, AckStatus.OK)
        elif isinstance(msg, DiagRequest):
            self._emit(self.diag_report())
        elif isinstance(msg, Bringup):
            self._on_bringup(msg, seq)
        else:  # an ESP32 -> Pi type sent the wrong way
            self._emit(Ack(int(msg.TYPE), seq, AckStatus.UNKNOWN_TYPE))

    def _on_drive(self, msg: Drive, seq: int) -> None:
        if self.mode == DriveMode.SAFE:
            self._ack(msg, seq, AckStatus.REJECTED_MODE)
            return
        if self.calibrating_until is not None:
            self._halt()
            self._ack(msg, seq, AckStatus.BUSY)
            return
        total = msg.left + msg.right
        us = self.ultrasonic_mm()
        threshold = self.p("OBSTACLE_THRESHOLD_MM")
        hysteresis = self.p("OBSTACLE_CLEAR_HYSTERESIS_MM")
        if total > 0:
            front = self._front_mm(us)
            # a little hysteresis, so sensor noise at the threshold does not release the stop
            held = hysteresis if self.flags & Flags.OBSTACLE_STOP else 0
            if front is not None and front < threshold + held:
                self._front_stop(front)
                self._ack(msg, seq, AckStatus.REJECTED_SAFETY)
                return
        elif total < 0:
            rear = us[3]
            if rear == US_ABSENT:
                self._halt()
                self._ack(msg, seq, AckStatus.REJECTED_SAFETY)
                return
            held = hysteresis if self.flags & Flags.REAR_OBSTACLE_STOP else 0
            if rear != US_NO_ECHO and rear < threshold + held:
                self._rear_stop(rear)
                self._ack(msg, seq, AckStatus.REJECTED_SAFETY)
                return
        limit = self.p("MAX_SPEED_MM_S") / 1000.0
        self.target = (max(-limit, min(limit, msg.left)), max(-limit, min(limit, msg.right)))
        self.flags &= ~(Flags.WATCHDOG_STOP | Flags.OBSTACLE_STOP | Flags.REAR_OBSTACLE_STOP)

    def _on_servo(self, pan_deg: float) -> None:
        us_per_deg = self.p("SERVO_US_PER_DEG")
        if us_per_deg <= 0:
            return  # 0 = the servo never moves
        lo, hi = self.p("SERVO_MIN_CDEG") / 100.0, self.p("SERVO_MAX_CDEG") / 100.0
        self.servo_deg = max(lo, min(hi, pan_deg))
        sign = -1.0 if self.p("SERVO_INVERT") else 1.0
        pulse = self.p("SERVO_CENTER_PULSE_US") + sign * self.servo_deg * us_per_deg
        self.servo_pulse_us = max(
            self.p("SERVO_PULSE_MIN_US"), min(self.p("SERVO_PULSE_MAX_US"), pulse)
        )

    def _on_param_set(self, msg: ParamSet, seq: int) -> None:
        if msg.id not in self.values:
            self._ack(msg, seq, AckStatus.UNKNOWN_PARAM)
            return
        spec = self.table.by_id(msg.id)
        if self._moving():
            self._ack(msg, seq, AckStatus.BUSY)
            return
        if not math.isfinite(msg.value) or not spec.in_range(msg.value):
            self._ack(msg, seq, AckStatus.OUT_OF_RANGE)
            return
        value = f32(msg.value)
        changed = value != self.values[msg.id]
        self.values[msg.id] = value
        self.sources[msg.id] = ParamSource.SET
        self._hash_cache = None
        self._ack(msg, seq, AckStatus.OK)
        self._report(msg.id)
        if changed:
            self._emit(Event(self._t32(), EventCode.PARAM_CHANGED, msg.id))

    def _on_bringup(self, msg: Bringup, seq: int) -> None:
        if msg.kind == BringupKind.OFF:
            self._bringup_off()
            self._ack(msg, seq, AckStatus.OK)
            return
        if self.mode != DriveMode.TEST:
            self._ack(msg, seq, AckStatus.REJECTED_MODE)
            return
        if msg.kind == BringupKind.IMU_RAW:
            if not 1 <= msg.arg <= 200:
                self._ack(msg, seq, AckStatus.BAD_ARGUMENT)
                return
            self.imu_rate_hz = msg.arg
        elif msg.kind == BringupKind.US_RAW:
            self.us_stream = True
        elif msg.kind == BringupKind.SERVO_JOG:
            if abs(msg.arg) > 50:
                self._ack(msg, seq, AckStatus.BAD_ARGUMENT)
                return
            self.servo_pulse_us = max(500.0, min(2500.0, self.servo_pulse_us + msg.arg))
        self.flags |= Flags.BRINGUP_ACTIVE
        self._ack(msg, seq, AckStatus.OK)
        if msg.kind == BringupKind.SERVO_JOG:
            self._emit(Event(self._t32(), EventCode.SERVO_PULSE, int(round(self.servo_pulse_us))))

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

    # ---------------- motors (the firmware's control, idealised) ----------------
    def _track_speeds(self, dt: float, v_batt: float) -> tuple[float, float]:
        """TRUE track speeds from the ramped targets, the mode and the parameters."""
        ramp = self.p("RAMP_MM_S2") / 1000.0 * dt
        for i in range(2):
            self._ramped[i] += max(-ramp, min(ramp, self.target[i] - self._ramped[i]))
        if self.mode == DriveMode.SAFE:
            return 0.0, 0.0
        drop = self.p("V_DRIVER_DROP")
        rated = self.p("MOTOR_RATED_V")
        cap = min(self.p("MOTOR_MAX_DUTY"), rated / max(v_batt - drop, 0.1), 1.0)
        per_duty = self.cfg.rated_speed_mps * max(v_batt - drop, 0.0) / rated  # m/s at duty 1
        out: list[float] = []
        for i in range(2):
            if self.mode == DriveMode.TEST:
                lim = min(self.p("TEST_MAX_DUTY"), cap)
                duty = max(-lim, min(lim, self._ramped[i] * 1000.0 / TEST_FULL_SCALE_MM_S))
                speed = duty * per_duty
            else:  # DRIVE: an ideal PID tracks the speed it MEASURES with METRES_PER_TICK
                if self.p("PID_KP") <= 0 and self.p("PID_KFF") <= 0:
                    speed = 0.0
                else:
                    mpt = self.p("METRES_PER_TICK")
                    speed = self._ramped[i] * self.cfg.true_metres_per_tick / mpt if mpt else 0.0
                    top = cap * per_duty
                    speed = max(-top, min(top, speed))
            inverted = bool(self.p("MOTOR_INVERT_LEFT" if i == 0 else "MOTOR_INVERT_RIGHT"))
            if inverted != self.cfg.motor_wiring_inverted[i]:
                speed = -speed
            out.append(speed)
        return out[0], out[1]

    # ---------------- one control step: 10 ms of simulated time ----------------
    def step(self) -> bytes:
        """Advance 10 ms. Returns the bytes the ESP32 writes to the port (may be empty)."""
        cfg = self.cfg
        dt = STEP_MS / 1000.0
        self.t_ms += STEP_MS
        self._world_s += dt
        self._step_no += 1
        us = self.ultrasonic_mm()
        threshold = self.p("OBSTACLE_THRESHOLD_MM")

        # safety rule 1: watchdog
        silent = self.t_ms - self.last_valid_ms
        if silent > cfg.watchdog_ms and not self.watchdog_stopped:
            self.watchdog_stopped = True
            self._halt()
            self._bringup_off()
            self.flags |= Flags.WATCHDOG_STOP
            self._emit(Event(self._t32(), EventCode.WATCHDOG_STOP, min(silent, 32767)))
        # safety rules 2 and 3: obstacles in the commanded direction
        total = self.target[0] + self.target[1]
        if total > 0:
            front = self._front_mm(us)
            if front is not None and front < threshold:
                self._front_stop(front)
        elif total < 0 and (us[3] == US_ABSENT or us[3] < threshold):
            self._rear_stop(0 if us[3] == US_ABSENT else us[3])
        if self.mode == DriveMode.SAFE:
            self._halt()

        # battery: TRUE pack voltage, what the INA219 reads, hard cut
        v_true = self.battery_mv / 1000.0
        v_batt = v_true * cfg.ina_voltage_error if cfg.power_sensor else FULL_PACK_V
        cutoff = self.p("BATTERY_CUTOFF_MV")
        read_mv = v_true * 1000 * cfg.ina_voltage_error * self.p("BATTERY_VOLTAGE_SCALE")
        if cfg.power_sensor and cutoff > 0 and read_mv < cutoff:
            if self.mode != DriveMode.SAFE:
                self.mode = DriveMode.SAFE
                self._halt()
            if not self._cutoff_reported:
                self._cutoff_reported = True
                self._emit(Event(self._t32(), EventCode.BATTERY_CUTOFF, int(read_mv)))

        # the true motion with slip
        left, right = self.speed = self._track_speeds(dt, v_batt)
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
        before = [self._count(i) for i in range(4)]
        for i, side_speed in enumerate((left, left, right, right)):
            noise = 1 + self.rng.gauss(0, cfg.encoder_noise) if side_speed else 1.0
            self.enc[i] += side_speed * dt / cfg.true_metres_per_tick * noise * self.wheel_slip[i]
        moving = abs(left) + abs(right) > 1e-6
        raw_gyro = w_true * cfg.gyro_scale_true + self._bias_now()
        raw_gyro += self.rng.gauss(0, cfg.gyro_noise_rps)
        self._gyro = (raw_gyro - self.gyro_cal_bias) * self.p("GYRO_SCALE")
        self.battery_mv -= abs(v_cmd) * dt * cfg.battery_drain_mv_per_m
        self.battery_mv -= cfg.battery_drain_mv_per_s * dt

        # gyro calibration: average the raw rate for 2 s
        if self.calibrating_until is not None:
            self._cal_sum += raw_gyro
            self._cal_n += 1
            self._cal_moved |= moving
            if self.t_ms >= self.calibrating_until:
                self.calibrating_until = None
                self.flags &= ~Flags.GYRO_CALIBRATING
                if self._cal_moved or not self._cal_n:
                    self._emit(Event(self._t32(), EventCode.GYRO_CAL_FAILED))
                else:
                    self.gyro_cal_bias = self._cal_sum / self._cal_n
                    arg = max(-32768, min(32767, int(round(self.gyro_cal_bias * 10000))))
                    self._emit(Event(self._t32(), EventCode.GYRO_CAL_DONE, arg))

        self._slip_check([self._count(i) - before[i] for i in range(4)])
        self._battery_events(read_mv)
        self._bringup_streams(raw_gyro, us)

        if self._step_no % TELEMETRY_EVERY == 0:
            flags = self.flags
            if self.mode != DriveMode.SAFE:
                flags |= Flags.MOTORS_ENABLED
            if self._unsaved():
                flags |= Flags.PARAMS_UNSAVED
            ma = (400 + 2500 * (abs(left) + abs(right))) * self.p("BATTERY_CURRENT_SCALE")
            if self.p("INA219_INVERT_CURRENT"):
                ma = -ma
            self._emit(
                Telemetry(
                    t_ms=self._t32(),
                    encoders=tuple(self._i32(self._count(i)) for i in range(4)),  # type: ignore[arg-type]
                    gyro_z=self._gyro,
                    accel_mg=(0, 0, 1000),
                    ultrasonic_mm=us,
                    battery_mv=max(0, min(0xFFFF, int(read_mv))) if cfg.power_sensor else 0,
                    battery_ma=max(-32768, min(32767, int(ma))) if cfg.power_sensor else 0,
                    flags=flags,
                    param_hash=self.param_hash,
                )
            )
        out = bytes(self._out)
        self._out.clear()
        return out

    def _count(self, i: int) -> float:
        """Encoder count as the firmware reports it (after its ENCODER_INVERT_* parameter)."""
        name = ("ENCODER_INVERT_FL", "ENCODER_INVERT_RL", "ENCODER_INVERT_FR", "ENCODER_INVERT_RR")
        flip = bool(self.p(name[i])) != self.cfg.encoder_wiring_inverted[i]
        return -self.enc[i] if flip else self.enc[i]

    def _slip_check(self, delta: list[float]) -> None:
        for i in range(4):
            self._slip_win[i] += delta[i]
        self._slip_steps += 1
        if self._slip_steps * STEP_MS < 500:
            return
        win, self._slip_win, self._slip_steps = self._slip_win, [0, 0, 0, 0], 0
        ratio, min_ticks = self.p("SLIP_RATIO"), self.p("SLIP_MIN_TICKS")
        for side, (front, rear) in enumerate(((win[0], win[1]), (win[2], win[3]))):
            biggest = max(abs(front), abs(rear))
            slipping = biggest > min_ticks and abs(front - rear) > ratio * biggest
            if slipping and not self._slip_active[side]:
                self._emit(Event(self._t32(), EventCode.SLIP, side))
            self._slip_active[side] = slipping

    def _battery_events(self, read_mv: float) -> None:
        if not self.cfg.power_sensor:
            return
        low, recover = self.p("LOW_BATTERY_MV"), self.p("LOW_BATTERY_RECOVER_MV")
        if low > 0 and read_mv < low and not self.flags & Flags.LOW_BATTERY:
            self.flags |= Flags.LOW_BATTERY
            self._emit(Event(self._t32(), EventCode.LOW_BATTERY, int(read_mv)))
        elif self.flags & Flags.LOW_BATTERY and (low <= 0 or read_mv > max(recover, low)):
            self.flags &= ~Flags.LOW_BATTERY

    def _bringup_streams(self, raw_gyro: float, us: tuple[int, int, int, int]) -> None:
        if self.imu_rate_hz:
            self._imu_acc += self.imu_rate_hz * STEP_MS / 1000.0
            while self._imu_acc >= 1.0:
                self._imu_acc -= 1.0
                noise = self.cfg.gyro_noise_rps
                gyro = (
                    int(self.rng.gauss(0, noise) * 1e6),
                    int(self.rng.gauss(0, noise) * 1e6),
                    int(raw_gyro * 1e6),
                )
                self._emit(ImuRaw(self._t32(), gyro, (0, 0, 1000)))
        if self._step_no % US_ROUND_STEPS == 0:
            for i, v in enumerate(us):
                self._us_misses[i] = min(255, self._us_misses[i] + 1) if v == US_NO_ECHO else 0
            if self.us_stream:
                self._emit(UsRaw(self._t32(), us, tuple(self._us_misses)))  # type: ignore[arg-type]

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


# ---------------- real-time TCP server (robotd --sim, robotctl --sim-tcp) ----------------
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


async def _main(host: str, port: int, seed: int, profile: str) -> None:
    nvs = dict(SIM_PROFILE) if profile == "sim" else None
    server = SimEsp32Server(SimEsp32(SimEsp32Config(seed=seed, nvs=nvs)), host, port)
    await server.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SIMULATED ESP32 (serial protocol v2 over TCP)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--profile", choices=("fresh", "sim"), default="fresh",
        help="fresh: never calibrated (compiled defaults, nothing moves); sim: boots with the "
        "SIMULATED calibration profile in its NVS",
    )  # fmt: skip
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main(args.host, args.port, args.seed, args.profile))
