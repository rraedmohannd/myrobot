"""Pi <-> ESP32 serial protocol: the Python codec, and the cross-check with the C++ codec.

`frames_from_cpp.txt` is written by the firmware native test `test_protocol`
(firmware/test); `frames_from_python.txt` by `python -m scr_serial.vectors`.
"""

import random

import pytest
from scr_serial import (
    CANONICAL_VECTORS,
    Drive,
    Event,
    EventCode,
    Flags,
    FrameDecoder,
    FrameError,
    Heartbeat,
    Servo,
    Telemetry,
    cobs_decode,
    cobs_encode,
    crc16_ccitt,
    decode_frame,
    encode_frame,
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


def test_telemetry_body_is_44_bytes_and_carries_the_simulated_flag() -> None:
    _, msg = CANONICAL_VECTORS["telemetry"]
    assert isinstance(msg, Telemetry)
    assert len(msg.pack()) == 44
    assert msg.is_simulated
    assert not Telemetry.unpack(CANONICAL_VECTORS["telemetry_zeros"][1].pack()).is_simulated


def test_units_on_the_wire() -> None:
    assert Drive(0.25, -0.125).pack() == (250).to_bytes(2, "little", signed=True) + (
        -125
    ).to_bytes(2, "little", signed=True)
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
