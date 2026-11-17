"""Messages of the Pi <-> ESP32 link, protocol version 2. Field order and sizes are the contract
(docs/SERIAL_PROTOCOL.md); all integers are little-endian, f32 is IEEE-754 little-endian.

Python fields use SI units (m/s, degrees, rad/s); the wire uses scaled integers.
"""

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import ClassVar

BAUD_RATE = 460800
WATCHDOG_MS = 300  # no valid frame for this long -> the ESP32 stops the motors
TELEMETRY_HZ = 50
US_NO_ECHO = 0xFFFF  # ultrasonic: nothing within range
US_ABSENT = 0xFFFE  # ultrasonic: sensor missing or faulty
PROTOCOL_VERSION = 2
PARAM_ALL = 0xFFFF  # PARAM_GET of every parameter


class MsgType(IntEnum):
    # Pi -> ESP32
    DRIVE = 0x01
    STOP = 0x02
    SERVO = 0x03
    MODE = 0x04
    HEARTBEAT = 0x05
    CALIBRATE_GYRO = 0x06
    PARAM_SET = 0x07  # version 2
    PARAM_GET = 0x08
    PARAM_SAVE = 0x09
    DIAG_REQUEST = 0x0A
    BRINGUP = 0x0B
    # ESP32 -> Pi
    TELEMETRY = 0x81
    EVENT = 0x82
    ACK = 0x83
    PARAM_REPORT = 0x84  # version 2
    DIAG_REPORT = 0x85
    IMU_RAW = 0x86
    US_RAW = 0x87


class DriveMode(IntEnum):
    SAFE = 0  # motor drivers disabled, DRIVE is rejected (power-on state)
    DRIVE = 1  # closed-loop track speeds
    TEST = 2  # open loop, for the first motor test with the tracks off the ground


class Flags(IntFlag):
    MOTORS_ENABLED = 1 << 0
    WATCHDOG_STOP = 1 << 1  # stopped by the 300 ms timeout; cleared by the next accepted DRIVE
    OBSTACLE_STOP = 1 << 2  # front distance under the threshold while driving forward
    LOW_BATTERY = 1 << 3
    IMU_OK = 1 << 4
    GYRO_CALIBRATING = 1 << 5
    SIMULATED = 1 << 6  # sent by the simulated ESP32 only (CLAUDE.md rule 5)
    ENCODER_FAULT = 1 << 7
    ULTRASONIC_FAULT = 1 << 8
    POWER_SENSOR_OK = 1 << 9
    REAR_OBSTACLE_STOP = 1 << 10  # rear distance under the threshold while reversing
    PARAMS_UNSAVED = 1 << 11  # some parameter differs from what NVS holds
    BRINGUP_ACTIVE = 1 << 12  # a bring-up stream or the servo jog is on (MODE TEST only)
    VOLTAGE_COMP_FALLBACK = 1 << 13  # no INA219 reading: duty cap computed for a full 12.6 V pack


class EventCode(IntEnum):
    BOOT = 1
    WATCHDOG_STOP = 2  # arg: ms since the last valid frame
    OBSTACLE_STOP = 3  # arg: front distance in mm
    REAR_OBSTACLE_STOP = 4  # arg: rear distance in mm
    LOW_BATTERY = 5  # arg: battery mV
    IMU_FAULT = 6
    GYRO_CAL_DONE = 7  # arg: bias in 0.1 mrad/s
    GYRO_CAL_FAILED = 8  # the robot moved during calibration
    ENCODER_FAULT = 9  # arg: encoder index 0..3
    LINK_RESTORED = 10  # first valid frame after a watchdog stop
    PROTOCOL_ERROR = 11  # arg: bad frames since the last report
    SLIP = 12  # arg: side, 0 left, 1 right
    I2C_RECOVERED = 13  # arg: bus-clear attempts
    PARAM_CHANGED = 14  # arg: parameter id
    SERVO_PULSE = 15  # arg: current pulse in us (answer to BRINGUP SERVO_JOG)
    BATTERY_CUTOFF = 16  # arg: battery mV; motors to SAFE


class AckStatus(IntEnum):
    OK = 0
    REJECTED_MODE = 1  # e.g. DRIVE while in SAFE mode
    BAD_ARGUMENT = 2
    BUSY = 3  # e.g. CALIBRATE_GYRO while moving
    UNKNOWN_TYPE = 4
    REJECTED_SAFETY = 5  # forward DRIVE while the front is blocked; reverse without rear sensing
    OUT_OF_RANGE = 6  # PARAM_SET outside the compiled bounds
    UNKNOWN_PARAM = 7


class BringupKind(IntEnum):
    OFF = 0
    IMU_RAW = 1  # arg: rate in Hz, 1..200
    US_RAW = 2
    SERVO_JOG = 3  # arg: pulse step in us, |arg| <= 50


class ParamSource(IntEnum):
    DEFAULT = 0  # compiled default
    NVS = 1  # loaded from NVS at boot
    SET = 2  # set since boot, not saved


class BuildEnv(IntEnum):
    ESP32DEV = 0
    ESP32_COMPILE_CHECK = 1
    ESP32_CI_PINS = 2
    SIM_CHECK = 3
    NATIVE_TEST = 4
    ROBOTD_SIMULATED = 5


class ResetReason(IntEnum):
    UNKNOWN = 0
    POWER_ON = 1
    BROWNOUT = 2
    TASK_WATCHDOG = 3
    PANIC = 4
    SOFTWARE = 5
    EXTERNAL = 6
    OTHER_WATCHDOG = 7
    DEEP_SLEEP = 8


class DiagFlags(IntFlag):
    PINS_CONFIRMED = 1 << 0
    CI_PINS_DO_NOT_FLASH = 1 << 1
    PARAMS_FROM_NVS = 1 << 2
    SIMULATED = 1 << 3


class Sensors(IntFlag):
    IMU = 1 << 0
    INA219 = 1 << 1
    EXPANDER = 1 << 2
    US0 = 1 << 3
    US1 = 1 << 4
    US2 = 1 << 5
    US3 = 1 << 6


def _clamp_i16(value: float) -> int:
    return max(-32768, min(32767, int(round(value))))


@dataclass(frozen=True)
class Drive:
    """Target track speeds in m/s (wire: mm/s, i16). Positive = forward."""

    left: float
    right: float
    TYPE: ClassVar[MsgType] = MsgType.DRIVE
    _FMT: ClassVar[str] = "<hh"

    def pack(self) -> bytes:
        return struct.pack(self._FMT, _clamp_i16(self.left * 1000), _clamp_i16(self.right * 1000))

    @classmethod
    def unpack(cls, body: bytes) -> "Drive":
        left, right = struct.unpack(cls._FMT, body)
        return cls(left / 1000, right / 1000)


@dataclass(frozen=True)
class Servo:
    """Camera pan angle in degrees (wire: centidegrees, i16). 0 = straight ahead, + = left."""

    pan_deg: float
    TYPE: ClassVar[MsgType] = MsgType.SERVO
    _FMT: ClassVar[str] = "<h"

    def pack(self) -> bytes:
        return struct.pack(self._FMT, _clamp_i16(self.pan_deg * 100))

    @classmethod
    def unpack(cls, body: bytes) -> "Servo":
        return cls(struct.unpack(cls._FMT, body)[0] / 100)


@dataclass(frozen=True)
class Mode:
    mode: DriveMode
    TYPE: ClassVar[MsgType] = MsgType.MODE
    _FMT: ClassVar[str] = "<B"

    def pack(self) -> bytes:
        return struct.pack(self._FMT, int(self.mode))

    @classmethod
    def unpack(cls, body: bytes) -> "Mode":
        return cls(DriveMode(struct.unpack(cls._FMT, body)[0]))


class _Empty:
    _FMT: ClassVar[str] = "<"

    def pack(self) -> bytes:
        return b""

    @classmethod
    def unpack(cls, body: bytes):  # noqa: ANN206
        if body:
            raise struct.error("unexpected body")
        return cls()


@dataclass(frozen=True)
class Stop(_Empty):
    TYPE: ClassVar[MsgType] = MsgType.STOP


@dataclass(frozen=True)
class Heartbeat(_Empty):
    TYPE: ClassVar[MsgType] = MsgType.HEARTBEAT


@dataclass(frozen=True)
class CalibrateGyro(_Empty):
    TYPE: ClassVar[MsgType] = MsgType.CALIBRATE_GYRO


@dataclass(frozen=True)
class Telemetry:
    """Raw sensor values, 50 Hz. The Pi computes the pose (docs/ODOMETRY.md); the ESP32 never does.

    encoders: cumulative counts, order front-left, rear-left, front-right, rear-right.
    ultrasonic_mm: front-left, front-centre, front-right, rear; US_NO_ECHO / US_ABSENT are special.
    """

    t_ms: int
    encoders: tuple[int, int, int, int]
    gyro_z: float  # rad/s, counter-clockwise positive (wire: micro-rad/s, i32)
    accel_mg: tuple[int, int, int]
    ultrasonic_mm: tuple[int, int, int, int]
    battery_mv: int
    battery_ma: int  # positive = discharging
    flags: Flags
    param_hash: int = 0  # version 2: FNV-1a of the current parameters (scr_serial.params)
    TYPE: ClassVar[MsgType] = MsgType.TELEMETRY
    _FMT: ClassVar[str] = "<I4ii3h4HHhHI"

    def pack(self) -> bytes:
        return struct.pack(
            self._FMT, self.t_ms & 0xFFFFFFFF, *self.encoders, int(round(self.gyro_z * 1e6)),
            *self.accel_mg, *self.ultrasonic_mm, self.battery_mv, self.battery_ma, int(self.flags),
            self.param_hash & 0xFFFFFFFF,
        )  # fmt: skip

    @classmethod
    def unpack(cls, body: bytes) -> "Telemetry":
        v = struct.unpack(cls._FMT, body)
        return cls(
            t_ms=v[0], encoders=(v[1], v[2], v[3], v[4]), gyro_z=v[5] / 1e6,
            accel_mg=(v[6], v[7], v[8]), ultrasonic_mm=(v[9], v[10], v[11], v[12]),
            battery_mv=v[13], battery_ma=v[14], flags=Flags(v[15]), param_hash=v[16],
        )  # fmt: skip

    @property
    def is_simulated(self) -> bool:
        return bool(self.flags & Flags.SIMULATED)


@dataclass(frozen=True)
class Event:
    t_ms: int
    code: EventCode
    arg: int = 0
    TYPE: ClassVar[MsgType] = MsgType.EVENT
    _FMT: ClassVar[str] = "<IBh"

    def pack(self) -> bytes:
        return struct.pack(self._FMT, self.t_ms & 0xFFFFFFFF, int(self.code), self.arg)

    @classmethod
    def unpack(cls, body: bytes) -> "Event":
        t_ms, code, arg = struct.unpack(cls._FMT, body)
        return cls(t_ms, EventCode(code), arg)


@dataclass(frozen=True)
class Ack:
    """Answer to STOP, MODE, CALIBRATE_GYRO, and to any command that was rejected."""

    acked_type: int
    acked_seq: int
    status: AckStatus
    TYPE: ClassVar[MsgType] = MsgType.ACK
    _FMT: ClassVar[str] = "<BBB"

    def pack(self) -> bytes:
        return struct.pack(self._FMT, self.acked_type, self.acked_seq, int(self.status))

    @classmethod
    def unpack(cls, body: bytes) -> "Ack":
        acked_type, acked_seq, status = struct.unpack(cls._FMT, body)
        return cls(acked_type, acked_seq, AckStatus(status))


# ---------------- version 2: parameters, diagnostics, bring-up ----------------
@dataclass(frozen=True)
class ParamSet:
    id: int
    value: float
    TYPE: ClassVar[MsgType] = MsgType.PARAM_SET
    _FMT: ClassVar[str] = "<Hf"

    def pack(self) -> bytes:
        return struct.pack(self._FMT, self.id, self.value)

    @classmethod
    def unpack(cls, body: bytes) -> "ParamSet":
        return cls(*struct.unpack(cls._FMT, body))


@dataclass(frozen=True)
class ParamGet:
    id: int = PARAM_ALL
    TYPE: ClassVar[MsgType] = MsgType.PARAM_GET
    _FMT: ClassVar[str] = "<H"

    def pack(self) -> bytes:
        return struct.pack(self._FMT, self.id)

    @classmethod
    def unpack(cls, body: bytes) -> "ParamGet":
        return cls(*struct.unpack(cls._FMT, body))


@dataclass(frozen=True)
class ParamSave(_Empty):
    TYPE: ClassVar[MsgType] = MsgType.PARAM_SAVE


@dataclass(frozen=True)
class DiagRequest(_Empty):
    TYPE: ClassVar[MsgType] = MsgType.DIAG_REQUEST


@dataclass(frozen=True)
class Bringup:
    kind: BringupKind
    arg: int = 0
    TYPE: ClassVar[MsgType] = MsgType.BRINGUP
    _FMT: ClassVar[str] = "<Bh"

    def pack(self) -> bytes:
        return struct.pack(self._FMT, int(self.kind), self.arg)

    @classmethod
    def unpack(cls, body: bytes) -> "Bringup":
        kind, arg = struct.unpack(cls._FMT, body)
        return cls(BringupKind(kind), arg)


@dataclass(frozen=True)
class ParamReport:
    id: int
    value: float
    min: float
    max: float
    source: ParamSource
    TYPE: ClassVar[MsgType] = MsgType.PARAM_REPORT
    _FMT: ClassVar[str] = "<HfffB"

    def pack(self) -> bytes:
        return struct.pack(self._FMT, self.id, self.value, self.min, self.max, int(self.source))

    @classmethod
    def unpack(cls, body: bytes) -> "ParamReport":
        pid, value, lo, hi, source = struct.unpack(cls._FMT, body)
        return cls(pid, value, lo, hi, ParamSource(source))


@dataclass(frozen=True)
class DiagReport:
    """Firmware identity and health (37 bytes). Also sent once at boot after EVENT BOOT."""

    fw_version: tuple[int, int, int]
    git_hash: int  # first 8 hex digits of the commit as a number; 0 = unknown
    build_env: BuildEnv
    driver_type: int  # 0 L298N, 1 VNH
    diag_flags: DiagFlags
    param_hash: int
    reset_reason: ResetReason
    uptime_ms: int
    loop_period_max_us: int
    loop_jitter_us: int
    free_heap: int
    i2c_addresses: tuple[int, ...]  # at most 8, as found by the scan
    sensors: Sensors
    TYPE: ClassVar[MsgType] = MsgType.DIAG_REPORT
    _FMT: ClassVar[str] = "<3BIBBBIBIHHIB8BB"

    def pack(self) -> bytes:
        found = list(self.i2c_addresses)[:8]
        return struct.pack(
            self._FMT, *self.fw_version, self.git_hash & 0xFFFFFFFF, int(self.build_env),
            self.driver_type, int(self.diag_flags), self.param_hash & 0xFFFFFFFF,
            int(self.reset_reason), self.uptime_ms & 0xFFFFFFFF,
            min(self.loop_period_max_us, 0xFFFF), min(self.loop_jitter_us, 0xFFFF),
            self.free_heap & 0xFFFFFFFF, len(found), *(found + [0] * (8 - len(found))),
            int(self.sensors),
        )  # fmt: skip

    @classmethod
    def unpack(cls, body: bytes) -> "DiagReport":
        v = struct.unpack(cls._FMT, body)
        count = min(v[13], 8)
        return cls(
            fw_version=(v[0], v[1], v[2]), git_hash=v[3], build_env=BuildEnv(v[4]),
            driver_type=v[5], diag_flags=DiagFlags(v[6]), param_hash=v[7],
            reset_reason=ResetReason(v[8]), uptime_ms=v[9], loop_period_max_us=v[10],
            loop_jitter_us=v[11], free_heap=v[12], i2c_addresses=tuple(v[14 : 14 + count]),
            sensors=Sensors(v[22]),
        )  # fmt: skip

    @property
    def version_text(self) -> str:
        return ".".join(str(n) for n in self.fw_version)

    @property
    def git_text(self) -> str:
        return f"{self.git_hash:08x}"


@dataclass(frozen=True)
class ImuRaw:
    """Bring-up stream: raw gyro (bias NOT removed) and accelerometer, robot axes."""

    t_ms: int
    gyro_urad_s: tuple[int, int, int]
    accel_mg: tuple[int, int, int]
    TYPE: ClassVar[MsgType] = MsgType.IMU_RAW
    _FMT: ClassVar[str] = "<I3i3h"

    def pack(self) -> bytes:
        return struct.pack(self._FMT, self.t_ms & 0xFFFFFFFF, *self.gyro_urad_s, *self.accel_mg)

    @classmethod
    def unpack(cls, body: bytes) -> "ImuRaw":
        v = struct.unpack(cls._FMT, body)
        return cls(v[0], (v[1], v[2], v[3]), (v[4], v[5], v[6]))


@dataclass(frozen=True)
class UsRaw:
    """Bring-up stream: one report per ultrasonic round."""

    t_ms: int
    dist_mm: tuple[int, int, int, int]
    misses: tuple[int, int, int, int]
    TYPE: ClassVar[MsgType] = MsgType.US_RAW
    _FMT: ClassVar[str] = "<I4H4B"

    def pack(self) -> bytes:
        return struct.pack(self._FMT, self.t_ms & 0xFFFFFFFF, *self.dist_mm, *self.misses)

    @classmethod
    def unpack(cls, body: bytes) -> "UsRaw":
        v = struct.unpack(cls._FMT, body)
        return cls(v[0], (v[1], v[2], v[3], v[4]), (v[5], v[6], v[7], v[8]))


Message = (
    Drive
    | Stop
    | Servo
    | Mode
    | Heartbeat
    | CalibrateGyro
    | ParamSet
    | ParamGet
    | ParamSave
    | DiagRequest
    | Bringup
    | Telemetry
    | Event
    | Ack
    | ParamReport
    | DiagReport
    | ImuRaw
    | UsRaw
)

MESSAGE_CLASSES: dict[int, type] = {
    int(c.TYPE): c
    for c in (
        Drive,
        Stop,
        Servo,
        Mode,
        Heartbeat,
        CalibrateGyro,
        ParamSet,
        ParamGet,
        ParamSave,
        DiagRequest,
        Bringup,
        Telemetry,
        Event,
        Ack,
        ParamReport,
        DiagReport,
        ImuRaw,
        UsRaw,
    )  # fmt: skip
}
