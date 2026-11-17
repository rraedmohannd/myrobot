"""Pi <-> ESP32 serial protocol, version 2 (docs/SERIAL_PROTOCOL.md): Python encoder / decoder.

The C++ twin is firmware/lib/protocol. Both sides are checked against each other with the
frame files in shared/serial_fixtures/ (written by one side, decoded by the other).

    frame on the wire = COBS( type u8 | seq u8 | body | crc16 LE ) + 0x00

Standard library only, so it runs on the Pi, on the laptop and in the backend container.
"""

from scr_serial.codec import (
    MAX_BODY,
    Frame,
    FrameDecoder,
    FrameError,
    cobs_decode,
    cobs_encode,
    crc16_ccitt,
    decode_frame,
    encode_frame,
)
from scr_serial.messages import (
    BAUD_RATE,
    PARAM_ALL,
    PROTOCOL_VERSION,
    TELEMETRY_HZ,
    US_ABSENT,
    US_NO_ECHO,
    WATCHDOG_MS,
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
)
from scr_serial.params import ParamSpec, ParamTable, f32, load_table, param_hash
from scr_serial.vectors import CANONICAL_VECTORS, read_fixture, write_fixture

__all__ = [
    "PARAM_ALL", "PROTOCOL_VERSION", "Bringup", "BringupKind", "BuildEnv", "DiagFlags",
    "DiagReport", "DiagRequest", "ImuRaw", "ParamGet", "ParamReport", "ParamSave", "ParamSet",
    "ParamSource", "ParamSpec", "ParamTable", "ResetReason", "Sensors", "UsRaw", "f32",
    "load_table", "param_hash",
    "BAUD_RATE", "CANONICAL_VECTORS", "MAX_BODY", "TELEMETRY_HZ", "US_ABSENT", "US_NO_ECHO",
    "WATCHDOG_MS", "Ack", "AckStatus", "CalibrateGyro", "Drive", "DriveMode", "Event",
    "EventCode", "Flags", "Frame", "FrameDecoder", "FrameError", "Heartbeat", "Message", "Mode",
    "MsgType", "Servo", "Stop", "Telemetry", "cobs_decode", "cobs_encode", "crc16_ccitt",
    "decode_frame", "encode_frame", "read_fixture", "write_fixture",
]  # fmt: skip
