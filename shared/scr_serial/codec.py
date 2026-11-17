"""Framing: COBS + CRC-16/CCITT-FALSE + sequence number (docs/SERIAL_PROTOCOL.md section 2)."""

import struct
from dataclasses import dataclass

from scr_serial.messages import MESSAGE_CLASSES, Message

MAX_BODY = 64  # bytes; the largest message today (TELEMETRY) has 44
_MAX_ENCODED = MAX_BODY + 4 + 2  # type, seq, crc (2) + COBS overhead for < 254 bytes (1) + spare


class FrameError(ValueError):
    """A received frame is damaged or not part of the protocol."""


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: polynomial 0x1021, start 0xFFFF, no reflection. "123456789" -> 0x29B1."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    """Consistent Overhead Byte Stuffing: the result contains no zero byte."""
    out = bytearray([0])
    code_at, code = 0, 1
    for byte in data:
        if byte == 0:
            out[code_at] = code
            code_at, code = len(out), 1
            out.append(0)
        else:
            out.append(byte)
            code += 1
            if code == 0xFF:
                out[code_at] = code
                code_at, code = len(out), 1
                out.append(0)
    out[code_at] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        code = data[i]
        if code == 0:
            raise FrameError("zero byte inside a COBS frame")
        end = i + code
        if end > len(data):
            raise FrameError("COBS frame is cut short")
        out += data[i + 1 : end]
        i = end
        if code < 0xFF and i < len(data):
            out.append(0)
    return bytes(out)


@dataclass(frozen=True)
class Frame:
    seq: int
    msg: Message


def encode_frame(msg: Message, seq: int) -> bytes:
    """Bytes to write to the serial port, ending with the 0x00 delimiter."""
    body = msg.pack()
    if len(body) > MAX_BODY:
        raise FrameError("body too long")
    payload = bytes([int(msg.TYPE), seq & 0xFF]) + body
    payload += struct.pack("<H", crc16_ccitt(payload))
    return cobs_encode(payload) + b"\x00"


def decode_frame(encoded: bytes) -> Frame:
    """One frame without its 0x00 delimiter. Raises FrameError for anything that is not valid."""
    payload = cobs_decode(encoded)
    if len(payload) < 4:
        raise FrameError("frame too short")
    (crc,) = struct.unpack("<H", payload[-2:])
    if crc != crc16_ccitt(payload[:-2]):
        raise FrameError("CRC mismatch")
    cls = MESSAGE_CLASSES.get(payload[0])
    if cls is None:
        raise FrameError(f"unknown message type 0x{payload[0]:02X}")
    try:
        msg = cls.unpack(payload[2:-2])
    except (struct.error, ValueError) as exc:
        raise FrameError(f"bad body for type 0x{payload[0]:02X}: {exc}") from exc
    return Frame(seq=payload[1], msg=msg)


class FrameDecoder:
    """Feed it whatever the serial port returns; it gives back complete, valid frames.

    Damaged frames are counted and dropped; the stream resynchronises at the next 0x00.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._overflow = False
        self._last_seq: int | None = None
        self.bad_frames = 0  # CRC, COBS, length or unknown-type errors
        self.lost_frames = 0  # gaps in the sender's sequence numbers
        self.errors: list[str] = []  # the last few error texts, for the log

    def feed(self, data: bytes) -> list[Frame]:
        frames: list[Frame] = []
        for byte in data:
            if byte != 0:
                if len(self._buf) >= _MAX_ENCODED:
                    self._overflow = True  # garbage without a delimiter: drop until the next 0x00
                else:
                    self._buf.append(byte)
                continue
            encoded, overflow = bytes(self._buf), self._overflow
            self._buf.clear()
            self._overflow = False
            if overflow:
                self._bad("frame longer than the maximum")
                continue
            if not encoded:
                continue  # two delimiters in a row
            try:
                frame = decode_frame(encoded)
            except FrameError as exc:
                self._bad(str(exc))
                continue
            if self._last_seq is not None:
                self.lost_frames += (frame.seq - self._last_seq - 1) & 0xFF
            self._last_seq = frame.seq
            frames.append(frame)
        return frames

    def _bad(self, text: str) -> None:
        self.bad_frames += 1
        self.errors = (self.errors + [text])[-10:]
