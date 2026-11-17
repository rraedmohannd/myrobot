"""Byte transports to the ESP32: a real serial port, or TCP to the SIMULATED ESP32."""

import asyncio
from typing import Any, Protocol


class TransportError(OSError):
    """The port or socket is gone; the link closes it and opens it again."""


class Transport(Protocol):
    name: str

    async def open(self) -> None: ...
    async def read(self) -> bytes: ...  # at least one byte, or b"" after a short wait
    async def write(self, data: bytes) -> None: ...
    async def close(self) -> None: ...


class SerialTransport:
    """The ESP32's USB-UART (pyserial). Blocking calls run in worker threads."""

    def __init__(self, port: str, baud: int = 460800) -> None:
        self.port, self.baud = port, baud
        self.name = f"serial:{port}@{baud}"
        self._serial: Any = None

    async def open(self) -> None:
        import serial  # pyserial; imported here so --sim works without it

        def _open() -> Any:
            # serial_for_url also takes plain port names (/dev/esp32, COM5) and pyserial URLs
            return serial.serial_for_url(
                self.port, baudrate=self.baud, timeout=0.05, write_timeout=0.5
            )

        try:
            self._serial = await asyncio.to_thread(_open)
        except (OSError, ValueError) as exc:  # SerialException is an OSError
            raise TransportError(f"cannot open {self.port}: {exc}") from exc

    async def read(self) -> bytes:
        port = self._serial
        if port is None:
            raise TransportError("port is closed")

        def _read() -> bytes:
            return port.read(max(1, port.in_waiting))

        try:
            return await asyncio.to_thread(_read)
        except (OSError, TypeError, AttributeError) as exc:
            raise TransportError(f"read failed: {exc}") from exc

    async def write(self, data: bytes) -> None:
        port = self._serial
        if port is None:
            raise TransportError("port is closed")
        try:
            await asyncio.to_thread(port.write, data)
        except (OSError, TypeError, AttributeError) as exc:
            raise TransportError(f"write failed: {exc}") from exc

    async def close(self) -> None:
        port, self._serial = self._serial, None
        if port is not None:
            try:
                await asyncio.to_thread(port.close)
            except OSError:
                pass


class TcpTransport:
    """TCP to the SIMULATED ESP32 (robotd.sim_esp32). Never a physical robot."""

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self.name = f"tcp:{host}:{port} (SIMULATED ESP32)"
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        try:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        except OSError as exc:
            raise TransportError(f"cannot connect to {self.host}:{self.port}: {exc}") from exc

    async def read(self) -> bytes:
        if self._reader is None:
            raise TransportError("socket is closed")
        try:
            data = await self._reader.read(4096)
        except OSError as exc:
            raise TransportError(f"read failed: {exc}") from exc
        if not data:
            raise TransportError("connection closed by the simulated ESP32")
        return data

    async def write(self, data: bytes) -> None:
        if self._writer is None:
            raise TransportError("socket is closed")
        try:
            self._writer.write(data)
            await self._writer.drain()
        except OSError as exc:
            raise TransportError(f"write failed: {exc}") from exc

    async def close(self) -> None:
        writer, self._reader, self._writer = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
