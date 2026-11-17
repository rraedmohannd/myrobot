"""Canonical test frames shared by the Python and the C++ implementation.

Both sides encode exactly these messages (docs/SERIAL_PROTOCOL.md section 6) into a fixture file:

    shared/serial_fixtures/frames_from_python.txt   written by `python -m scr_serial.vectors`
    shared/serial_fixtures/frames_from_cpp.txt      written by the firmware native tests

and each side's tests decode the other side's file. One line per frame:

    <name> <seq> <hex of the frame, including the final 00>
"""

from pathlib import Path

from scr_serial.codec import encode_frame
from scr_serial.messages import (
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

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "serial_fixtures"
PYTHON_FIXTURE = FIXTURE_DIR / "frames_from_python.txt"
CPP_FIXTURE = FIXTURE_DIR / "frames_from_cpp.txt"

# name -> (seq, message). Keep in step with firmware/test/test_protocol and the doc table.
CANONICAL_VECTORS: dict[str, tuple[int, Message]] = {
    "drive": (1, Drive(left=0.250, right=-0.125)),
    "stop": (2, Stop()),
    "servo": (3, Servo(pan_deg=-12.34)),
    "mode": (4, Mode(DriveMode.DRIVE)),
    "heartbeat": (5, Heartbeat()),
    "calibrate_gyro": (6, CalibrateGyro()),
    "telemetry": (200, Telemetry(
        t_ms=123456, encoders=(1000, -2000, 300000, -4), gyro_z=-0.012345,
        accel_mg=(12, -34, 1001), ultrasonic_mm=(250, 1200, US_NO_ECHO, US_ABSENT),
        battery_mv=11850, battery_ma=1520,
        flags=Flags.MOTORS_ENABLED | Flags.IMU_OK | Flags.SIMULATED, param_hash=0xDEADBEEF,
    )),
    "telemetry_zeros": (0, Telemetry(
        t_ms=0, encoders=(0, 0, 0, 0), gyro_z=0.0, accel_mg=(0, 0, 0),
        ultrasonic_mm=(0, 0, 0, 0), battery_mv=0, battery_ma=0, flags=Flags(0),
    )),
    "event": (201, Event(t_ms=5000, code=EventCode.OBSTACLE_STOP, arg=180)),
    "ack": (202, Ack(acked_type=int(MsgType.MODE), acked_seq=4, status=AckStatus.OK)),
    # ---- version 2 ----
    "param_set": (7, ParamSet(id=9, value=0.25)),  # exact in f32: round trips are equal
    "param_get_all": (8, ParamGet()),
    "param_save": (9, ParamSave()),
    "diag_request": (10, DiagRequest()),
    "bringup_imu": (11, Bringup(BringupKind.IMU_RAW, 100)),
    "bringup_jog": (12, Bringup(BringupKind.SERVO_JOG, -25)),
    "param_report": (203, ParamReport(
        id=27, value=250.0, min=150.0, max=1000.0, source=ParamSource.NVS,
    )),
    "diag_report": (204, DiagReport(
        fw_version=(2, 0, 1), git_hash=0x1A2B3C4D, build_env=BuildEnv.ESP32DEV, driver_type=0,
        diag_flags=DiagFlags.PINS_CONFIRMED | DiagFlags.PARAMS_FROM_NVS, param_hash=0x01020304,
        reset_reason=ResetReason.BROWNOUT, uptime_ms=987654, loop_period_max_us=10450,
        loop_jitter_us=310, free_heap=201234, i2c_addresses=(0x40, 0x68),
        sensors=Sensors.IMU | Sensors.INA219 | Sensors.US0 | Sensors.US3,
    )),
    "imu_raw": (205, ImuRaw(t_ms=4242, gyro_urad_s=(-150, 2300, -987654), accel_mg=(5, -12, 998))),
    "us_raw": (206, UsRaw(t_ms=777, dist_mm=(310, US_NO_ECHO, 0, US_ABSENT), misses=(0, 2, 0, 3))),
    "event_slip": (207, Event(t_ms=65000, code=EventCode.SLIP, arg=1)),
    "ack_out_of_range": (208, Ack(
        acked_type=int(MsgType.PARAM_SET), acked_seq=7, status=AckStatus.OUT_OF_RANGE,
    )),
}  # fmt: skip


def write_fixture(path: Path = PYTHON_FIXTURE) -> None:
    lines = [
        f"{name} {seq} {encode_frame(msg, seq).hex()}"
        for name, (seq, msg) in CANONICAL_VECTORS.items()
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def read_fixture(path: Path) -> dict[str, tuple[int, bytes]]:
    """name -> (seq, frame bytes including the 0x00 delimiter)."""
    frames: dict[str, tuple[int, bytes]] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        if line.strip() and not line.startswith("#"):
            name, seq, hex_frame = line.split()
            frames[name] = (int(seq), bytes.fromhex(hex_frame))
    return frames


if __name__ == "__main__":
    write_fixture()
    print(f"wrote {PYTHON_FIXTURE}")
