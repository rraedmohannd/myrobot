"""Pi <-> ESP32 serial protocol: the Python codec, and the cross-check with the C++ codec.

`frames_from_cpp.txt` is written by the firmware native test `test_protocol`
(firmware/test); `frames_from_python.txt` by `python -m scr_serial.vectors`.
"""

import random

import pytest
from scr_serial import (
    CANONICAL_VECTORS,
    AckStatus,
    DiagReport,
    Drive,
    Event,
    EventCode,
    Flags,
    FrameDecoder,
    FrameError,
    Heartbeat,
    MsgType,
    Servo,
    Telemetry,
    cobs_decode,
    cobs_encode,
    crc16_ccitt,
    decode_frame,
    encode_frame,
    f32,
    load_table,
    param_hash,
    read_fixture,
)
from scr_serial.vectors import CPP_FIXTURE, PYTHON_FIXTURE


def test_crc_check_value() -> None:
    assert crc16_ccitt(b"123456789") == 0x29B1


@pytest.mark.parametrize(
    ("raw", "encoded"),
    [
        (b"", b"\x01"),
        (b"\x00", b"\x01\x01"),
        (b"\x11\x22\x00\x33", b"\x03\x11\x22\x02\x33"),
        (b"\x11\x00\x00\x00", b"\x02\x11\x01\x01\x01"),
    ],
)
def test_cobs_known_vectors(raw: bytes, encoded: bytes) -> None:
    assert cobs_encode(raw) == encoded
    assert cobs_decode(encoded) == raw


def test_cobs_round_trip_random_and_long_runs() -> None:
    rng = random.Random(4)
    samples = [bytes(rng.randrange(256) for _ in range(rng.randrange(0, 600))) for _ in range(200)]
    samples += [bytes(range(1, 255)), bytes(range(1, 256)), b"\x01" * 254, b"\x01" * 255 + b"\x00"]
    for raw in samples:
        encoded = cobs_encode(raw)
        assert 0 not in encoded
        assert cobs_decode(encoded) == raw


def test_every_canonical_message_round_trips() -> None:
    for name, (seq, msg) in CANONICAL_VECTORS.items():
        raw = encode_frame(msg, seq)
        assert raw.endswith(b"\x00") and 0 not in raw[:-1], name
        frame = decode_frame(raw[:-1])
        assert frame.seq == seq and frame.msg == msg, name


def test_telemetry_body_is_48_bytes_and_carries_the_simulated_flag() -> None:
    # protocol version 2 appended param_hash (u32) to the 44 bytes of version 1
    _, msg = CANONICAL_VECTORS["telemetry"]
    assert isinstance(msg, Telemetry)
    assert len(msg.pack()) == 48
    assert msg.pack()[:44] == Telemetry.unpack(msg.pack()).pack()[:44]
    assert msg.is_simulated
    assert not Telemetry.unpack(CANONICAL_VECTORS["telemetry_zeros"][1].pack()).is_simulated


def test_units_on_the_wire() -> None:
    assert Drive(0.25, -0.125).pack() == (250).to_bytes(2, "little", signed=True) + (-125).to_bytes(
        2, "little", signed=True
    )
    assert Servo(-12.34).pack() == (-1234).to_bytes(2, "little", signed=True)
    assert Drive(99.0, -99.0).pack() == Drive(32.767, -32.768).pack()  # clamped, never wraps


def test_python_fixture_file_is_up_to_date() -> None:
    stored = read_fixture(PYTHON_FIXTURE)
    assert set(stored) == set(CANONICAL_VECTORS)
    for name, (seq, msg) in CANONICAL_VECTORS.items():
        assert stored[name] == (seq, encode_frame(msg, seq)), name


def test_frames_written_by_the_cpp_side_decode_to_the_canonical_messages() -> None:
    assert CPP_FIXTURE.exists(), (
        "shared/serial_fixtures/frames_from_cpp.txt is missing: run the firmware native tests"
    )
    stored = read_fixture(CPP_FIXTURE)
    assert set(stored) == set(CANONICAL_VECTORS)
    for name, (seq, msg) in CANONICAL_VECTORS.items():
        cpp_seq, raw = stored[name]
        frame = decode_frame(raw[:-1])
        assert raw.endswith(b"\x00"), name
        assert cpp_seq == seq and frame.seq == seq, name
        assert frame.msg == msg, name
        assert raw == encode_frame(msg, seq), f"{name}: C++ and Python bytes differ"


def test_decoder_resynchronises_after_garbage_and_drops_bad_crc() -> None:
    good = encode_frame(Heartbeat(), 7)
    damaged = bytearray(encode_frame(Drive(0.1, 0.1), 8))
    damaged[3] ^= 0x40
    decoder = FrameDecoder()
    stream = b"\x13\x37\x99" + b"\x00" + good + bytes(damaged) + b"\x55" * 300 + b"\x00" + good
    frames: list = []
    for i in range(0, len(stream), 5):  # arrives in arbitrary pieces
        frames += decoder.feed(stream[i : i + 5])
    assert [f.msg for f in frames] == [Heartbeat(), Heartbeat()]
    assert decoder.bad_frames == 3  # leading garbage, the damaged frame, the over-long run


def test_decoder_counts_lost_frames_from_sequence_gaps() -> None:
    decoder = FrameDecoder()
    for seq in (254, 255, 0, 3):
        decoder.feed(encode_frame(Heartbeat(), seq))
    assert decoder.lost_frames == 2


def test_unknown_type_and_wrong_length_are_rejected() -> None:
    payload = bytes([0x7F, 1])
    with pytest.raises(FrameError, match="unknown"):
        decode_frame(cobs_encode(payload + crc16_ccitt(payload).to_bytes(2, "little")))
    payload = bytes([0x01, 1, 0xAA])  # DRIVE needs 4 body bytes
    with pytest.raises(FrameError, match="bad body"):
        decode_frame(cobs_encode(payload + crc16_ccitt(payload).to_bytes(2, "little")))
    payload = bytes([0x82, 1]) + Event(1, EventCode.BOOT).pack()[:4] + bytes([99, 0, 0])
    with pytest.raises(FrameError, match="bad body"):  # event code outside the protocol
        decode_frame(cobs_encode(payload + crc16_ccitt(payload).to_bytes(2, "little")))


def test_flags_match_the_document() -> None:
    assert int(Flags.MOTORS_ENABLED | Flags.IMU_OK | Flags.SIMULATED) == 0x0051
    assert int(Flags.REAR_OBSTACLE_STOP) == 1 << 10


# ---------------- protocol version 2 ----------------
def test_version_2_body_sizes_match_the_document() -> None:
    sizes = {
        "param_set": 6, "param_get_all": 2, "param_save": 0, "diag_request": 0,
        "bringup_imu": 3, "param_report": 15, "diag_report": 37, "imu_raw": 22, "us_raw": 16,
    }  # fmt: skip
    for name, size in sizes.items():
        assert len(CANONICAL_VECTORS[name][1].pack()) == size, name


def test_diag_report_round_trip_and_text() -> None:
    _, diag = CANONICAL_VECTORS["diag_report"]
    assert isinstance(diag, DiagReport)
    again = DiagReport.unpack(diag.pack())
    assert again == diag
    assert again.version_text == "2.0.1" and again.git_text == "1a2b3c4d"
    assert again.i2c_addresses == (0x40, 0x68)
    many = DiagReport(**{**diag.__dict__, "i2c_addresses": tuple(range(0x10, 0x1C))})
    assert DiagReport.unpack(many.pack()).i2c_addresses == tuple(range(0x10, 0x18))  # 8 slots


def test_parameter_table_is_consistent_and_does_not_move_the_robot() -> None:
    table = load_table()
    ids = [p.id for p in table.params]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    for p in table.params:
        assert p.min <= p.default <= p.max, p.name
    defaults = {p.name: p.default for p in table.params}
    # sprint-4 rule: the compiled defaults cannot move the robot
    for name in ("METRES_PER_TICK", "PID_KP", "MAX_SPEED_MM_S", "RAMP_MM_S2", "TEST_MAX_DUTY",
                 "MOTOR_MAX_DUTY", "SERVO_US_PER_DEG", "SERVO_SLEW_CDEG_PER_S"):  # fmt: skip
        assert defaults[name] == 0, name
    assert table.by_name("OBSTACLE_THRESHOLD_MM").min >= 150  # cannot be tuned away
    assert table.by_name("METRES_PER_TICK").required


def test_param_hash_is_fnv1a_over_id_and_f32() -> None:
    assert param_hash({}) == 0x811C9DC5
    table = load_table()
    base = table.defaults()
    h = param_hash(base)
    assert h == param_hash(dict(reversed(list(base.items()))))  # order of the dict is irrelevant
    assert param_hash({**base, 1: 0.0005}) != h
    assert param_hash({**base, 1: 0.0005}) == param_hash({**base, 1: f32(0.0005)})  # f32 bytes


def test_param_spec_range_check_uses_the_firmware_precision() -> None:
    spec = load_table().by_name("TEST_MAX_DUTY")
    assert spec.in_range(0.5) and not spec.in_range(0.5001)
    assert load_table().by_name("SLIP_MIN_TICKS").in_range(30)
    assert not load_table().by_name("SLIP_MIN_TICKS").in_range(30.5)


def test_firmware_parameter_header_is_up_to_date() -> None:
    from scr_serial.gen_params_header import HEADER, render

    assert HEADER.read_text(encoding="utf-8") == render(), (
        "run: python -m scr_serial.gen_params_header"
    )


def test_new_codes_match_the_document() -> None:
    assert int(EventCode.SLIP) == 12 and int(EventCode.BATTERY_CUTOFF) == 16
    assert int(AckStatus.OUT_OF_RANGE) == 6 and int(AckStatus.UNKNOWN_PARAM) == 7
    assert int(Flags.VOLTAGE_COMP_FALLBACK) == 1 << 13
    assert int(MsgType.BRINGUP) == 0x0B and int(MsgType.US_RAW) == 0x87
