"""Messages of the Pi <-> ESP32 link. Field order and sizes are the contract
(docs/SERIAL_PROTOCOL.md); all integers are little-endian.

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


class MsgType(IntEnum):
    # Pi -> ESP32
    DRIVE = 0x01
    STOP = 0x02
    SERVO = 0x03
    MODE = 0x04
    HEARTBEAT = 0x05
    CALIBRATE_GYRO = 0x06
    # ESP32 -> Pi
    TELEMETRY = 0x81
    EVENT = 0x82
    ACK = 0x83


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


class AckStatus(IntEnum):
    OK = 0
    REJECTED_MODE = 1  # e.g. DRIVE while in SAFE mode
    BAD_ARGUMENT = 2
    BUSY = 3  # e.g. CALIBRATE_GYRO while moving
    UNKNOWN_TYPE = 4
    REJECTED_SAFETY = 5  # e.g. forward DRIVE while the front is blocked, reverse without rear sensing


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
    """Camera pan angle in degrees (wire: centidegrees, i16). 0 = straight ahead, positive = left."""

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
    TYPE: ClassVar[MsgType] = MsgType.TELEMETRY
    _FMT: ClassVar[str] = "<I4ii3h4HHhH"

    def pack(self) -> bytes:
        return struct.pack(
            self._FMT, self.t_ms & 0xFFFFFFFF, *self.encoders, int(round(self.gyro_z * 1e6)),
            *self.accel_mg, *self.ultrasonic_mm, self.battery_mv, self.battery_ma, int(self.flags),
        )  # fmt: skip

    @classmethod
    def unpack(cls, body: bytes) -> "Telemetry":
        v = struct.unpack(cls._FMT, body)
        return cls(
            t_ms=v[0], encoders=(v[1], v[2], v[3], v[4]), gyro_z=v[5] / 1e6,
            accel_mg=(v[6], v[7], v[8]), ultrasonic_mm=(v[9], v[10], v[11], v[12]),
            battery_mv=v[13], battery_ma=v[14], flags=Flags(v[15]),
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


Message = Drive | Stop | Servo | Mode | Heartbeat | CalibrateGyro | Telemetry | Event | Ack

MESSAGE_CLASSES: dict[int, type] = {
    int(c.TYPE): c
    for c in (Drive, Stop, Servo, Mode, Heartbeat, CalibrateGyro, Telemetry, Event, Ack)
}
