"""The Pi side of the ESP32 link (docs/SERIAL_PROTOCOL.md).

`LinkCore` is pure (no I/O, time is passed in): it frames outgoing messages, decodes incoming
bytes, keeps the DRIVE (20 Hz while moving) / HEARTBEAT (10 Hz otherwise) rhythm, counts bad and
lost frames and knows when telemetry stopped. `SerialLink` runs it over a `Transport` with
asyncio and reopens the port when it disappears.

Safety note (CLAUDE.md rule 4): when telemetry is lost the Pi stops commanding; the ESP32 then
stops by its own 300 ms watchdog. Nothing here is needed for the robot to stop.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from scr_serial import (
    Drive,
    FrameDecoder,
    Heartbeat,
    Message,
    Stop,
    Telemetry,
    encode_frame,
)

from robotd.transport import Transport, TransportError

log = logging.getLogger("robotd.link")


@dataclass
class LinkCore:
    drive_hz: float = 20.0
    heartbeat_hz: float = 10.0
    telemetry_timeout_ms: int = 500
    target: tuple[float, float] = (0.0, 0.0)
    last_telemetry_s: float | None = None
    sent_frames: int = 0
    received_frames: int = 0
    reconnects: int = 0
    _seq: int = 0
    _queue: list[Message] = field(default_factory=list)
    _last_periodic_s: float = -1e9
    _decoder: FrameDecoder = field(default_factory=FrameDecoder)
    _bad_before: int = 0  # counters of decoders that were replaced after a reconnect
    _lost_before: int = 0

    # ---- counters ----
    @property
    def bad_frames(self) -> int:
        return self._bad_before + self._decoder.bad_frames

    @property
    def lost_frames(self) -> int:
        return self._lost_before + self._decoder.lost_frames

    def stats(self) -> dict[str, int]:
        return {
            "sent": self.sent_frames, "received": self.received_frames,
            "bad": self.bad_frames, "lost": self.lost_frames, "reconnects": self.reconnects,
        }  # fmt: skip

    # ---- commands ----
    @property
    def moving(self) -> bool:
        return self.target != (0.0, 0.0)

    def set_drive(self, left: float, right: float) -> None:
        """Target track speeds in m/s. (0, 0) sends one STOP, then heartbeats."""
        new = (float(left), float(right))
        if new == (0.0, 0.0) and self.moving:
            self.send(Stop())
        self.target = new

    def send(self, msg: Message) -> None:
        """Queue a one-shot message (STOP, MODE, SERVO, CALIBRATE_GYRO)."""
        self._queue.append(msg)

    # ---- I/O, driven by the owner ----
    def feed(self, data: bytes, now_s: float) -> list[Message]:
        """Bytes from the port -> valid messages. Damaged frames are counted and dropped."""
        out: list[Message] = []
        for frame in self._decoder.feed(data):
            self.received_frames += 1
            if isinstance(frame.msg, Telemetry):
                self.last_telemetry_s = now_s
            out.append(frame.msg)
        return out

    def telemetry_lost(self, now_s: float) -> bool:
        last = self.last_telemetry_s
        return last is None or (now_s - last) * 1000.0 > self.telemetry_timeout_ms

    def poll(self, now_s: float) -> bytes:
        """Frames to write now: queued one-shots, then DRIVE or HEARTBEAT when one is due."""
        msgs, self._queue = self._queue, []
        period = 1.0 / (self.drive_hz if self.moving else self.heartbeat_hz)
        if now_s - self._last_periodic_s >= period - 1e-6:
            self._last_periodic_s = now_s
            msgs.append(Drive(*self.target) if self.moving else Heartbeat())
        out = bytearray()
        for msg in msgs:
            out += encode_frame(msg, self._seq)
            self._seq = (self._seq + 1) & 0xFF
            self.sent_frames += 1
        return bytes(out)

    def reset_stream(self) -> None:
        """After a reconnect: start decoding from a clean state, forget queued commands."""
        self._bad_before += self._decoder.bad_frames
        self._lost_before += self._decoder.lost_frames
        self._decoder = FrameDecoder()
        self._queue.clear()
        self.target = (0.0, 0.0)
        self.last_telemetry_s = None
        self.reconnects += 1


class SerialLink:
    """Runs a LinkCore over a Transport: reader, 100 Hz writer, reconnect when the port is lost."""

    def __init__(
        self,
        core: LinkCore,
        make_transport: Callable[[], Transport],
        on_message: Callable[[Message, float], None],
        clock: Callable[[], float],
        reconnect_s: float = 1.0,
    ) -> None:
        self.core, self._make, self._on_message, self._clock = (
            core,
            make_transport,
            on_message,
            clock,
        )
        self.reconnect_s = reconnect_s
        self.connected = False
        self.on_connection: Callable[[bool], None] | None = None

    async def run(self) -> None:
        opened_before, failing = False, False
        while True:
            transport = self._make()
            try:
                await transport.open()
                if opened_before:
                    self.core.reset_stream()
                opened_before, failing = True, False
                self._set_connected(True)
                log.info("ESP32 link open: %s", transport.name)
                writer = asyncio.create_task(self._writer(transport))
                try:
                    while True:
                        data = await transport.read()
                        if writer.done():
                            writer.result()  # raises the writer's TransportError
                        if data:
                            now = self._clock()
                            for msg in self.core.feed(data, now):
                                self._on_message(msg, now)
                finally:
                    writer.cancel()
                    with contextlib.suppress(asyncio.CancelledError, TransportError):
                        await writer
            except TransportError as exc:
                if not failing:
                    log.warning("ESP32 link down (%s): %s; retrying", transport.name, exc)
                failing = True
            finally:
                self._set_connected(False)
                await transport.close()
            await asyncio.sleep(self.reconnect_s)

    def _set_connected(self, value: bool) -> None:
        if value != self.connected:
            self.connected = value
            if self.on_connection:
                self.on_connection(value)

    async def _writer(self, transport: Transport) -> None:
        while True:
            data = self.core.poll(self._clock())
            if data:
                await transport.write(data)
            await asyncio.sleep(0.01)
