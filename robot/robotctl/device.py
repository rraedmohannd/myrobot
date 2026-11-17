"""The ways robotctl reaches the ESP32.

- `DirectDevice`: the serial port (robotd stopped) or TCP to the SIMULATED ESP32 (`--sim-tcp`).
  A background task sends HEARTBEAT every 100 ms; if robotctl hangs or dies, the ESP32's own
  300 ms watchdog stops the motors (CLAUDE.md rule 4: safety never depends on this program).
- `CtlDevice`: robotd's local control API `ws://127.0.0.1:<ui_port>/ws/ctl` (robotd running;
  robotd keeps the HEARTBEAT going and puts itself in bring-up mode while we are connected).
- `SimPipeDevice`: an in-process SIMULATED ESP32 in simulated time (tests).

All of them give the same small API: `send`, `pump`, `request`, `command`, `hand_phase`.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from scr_serial import (
    Ack,
    DiagFlags,
    DiagReport,
    Event,
    Flags,
    FrameDecoder,
    Heartbeat,
    Message,
    ParamReport,
    Telemetry,
    encode_frame,
)

from robotctl import wire

log = logging.getLogger("robotctl.device")

KEEPALIVE_S = 0.1


class LinkError(RuntimeError):
    """The ESP32 (or robotd) cannot be reached, or did not answer."""


class Device:
    """Common state: the last telemetry, parameters and diagnostics seen, the SIMULATED mark."""

    name = "device"
    simulated_by_construction = False

    def __init__(self) -> None:
        self.seen_simulated = False
        self.last_telemetry: Telemetry | None = None
        self.last_diag: DiagReport | None = None
        self.params: dict[int, ParamReport] = {}
        self.events: list[Event] = []
        self.ctl_status: dict[str, Any] | None = None
        self.vision: dict[str, Any] | None = None

    @property
    def is_simulated(self) -> bool:
        return self.simulated_by_construction or self.seen_simulated

    def now(self) -> float:
        return time.monotonic()

    # ---- implemented by subclasses ----
    async def open(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def send(self, msg: Message) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def pump(self, seconds: float) -> list[Message]:  # pragma: no cover - interface
        """Everything received during the next `seconds` (the link is kept alive meanwhile)."""
        raise NotImplementedError

    async def hand_phase(self, seconds: float, label: str = "") -> list[Message]:
        """The operator moves the robot by hand meanwhile (tests: a simulated hand)."""
        return await self.pump(seconds)

    # ---- shared ----
    def _note(self, msg: Message) -> None:
        if isinstance(msg, Telemetry):
            self.last_telemetry = msg
            if msg.flags & Flags.SIMULATED:
                self.seen_simulated = True
        elif isinstance(msg, DiagReport):
            self.last_diag = msg
            if msg.diag_flags & DiagFlags.SIMULATED:
                self.seen_simulated = True
        elif isinstance(msg, ParamReport):
            self.params[msg.id] = msg
        elif isinstance(msg, Event):
            self.events = (self.events + [msg])[-200:]

    async def request(
        self,
        msg: Message,
        done: Callable[[Message], bool],
        timeout: float = 1.0,  # noqa: ASYNC109
    ) -> list[Message]:
        """Send `msg`, then collect messages until `done(message)` is true (included)."""
        await self.send(msg)
        got: list[Message] = []
        end = self.now() + timeout
        while self.now() < end:
            for m in await self.pump(0.02):
                got.append(m)
                if done(m):
                    return got
        raise LinkError(f"no answer to {type(msg).__name__} within {timeout:.1f} s")

    async def command(self, msg: Message, timeout: float = 1.0) -> Ack:  # noqa: ASYNC109
        """Send and wait for the ACK of this message type."""
        got = await self.request(
            msg, lambda m: isinstance(m, Ack) and m.acked_type == int(msg.TYPE), timeout
        )
        ack = got[-1]
        assert isinstance(ack, Ack)
        return ack

    async def wait_telemetry(self, timeout: float = 2.0) -> Telemetry:  # noqa: ASYNC109
        end = self.now() + timeout
        while self.now() < end:
            for m in await self.pump(0.05):
                if isinstance(m, Telemetry):
                    return m
        raise LinkError(
            "no TELEMETRY from the ESP32: is it powered, flashed, and is the cable the data one?"
        )


# ------------------------------------------------------------------ direct: serial or TCP
class Transport(Protocol):
    name: str

    async def open(self) -> None: ...
    async def read(self) -> bytes: ...
    async def write(self, data: bytes) -> None: ...
    async def close(self) -> None: ...


class DirectDevice(Device):
    def __init__(self, transport: Transport, simulated: bool) -> None:
        super().__init__()
        self.transport = transport
        self.name = transport.name
        self.simulated_by_construction = simulated
        self._decoder = FrameDecoder()
        self._queue: asyncio.Queue[Message] = asyncio.Queue()
        self._seq = 0
        self._tasks: list[asyncio.Task[None]] = []
        self._write_lock = asyncio.Lock()
        self._error: BaseException | None = None

    async def open(self) -> None:
        try:
            await self.transport.open()
        except OSError as exc:
            raise LinkError(str(exc)) from exc
        self._tasks = [asyncio.create_task(self._reader()), asyncio.create_task(self._keepalive())]

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        await self.transport.close()

    async def send(self, msg: Message) -> None:
        if self._error is not None:
            raise LinkError(f"link lost: {self._error}")
        async with self._write_lock:
            data = encode_frame(msg, self._seq)
            self._seq = (self._seq + 1) & 0xFF
            try:
                await self.transport.write(data)
            except OSError as exc:
                raise LinkError(str(exc)) from exc

    async def pump(self, seconds: float) -> list[Message]:
        out: list[Message] = []
        end = self.now() + max(0.0, seconds)
        while True:
            if self._error is not None:
                raise LinkError(f"link lost: {self._error}")
            remaining = end - self.now()
            if remaining <= 0:
                while not self._queue.empty():
                    out.append(self._queue.get_nowait())
                return out
            try:
                out.append(await asyncio.wait_for(self._queue.get(), min(remaining, 0.05)))
            except TimeoutError:
                pass

    async def _reader(self) -> None:
        try:
            while True:
                data = await self.transport.read()
                for frame in self._decoder.feed(data):
                    self._note(frame.msg)
                    self._queue.put_nowait(frame.msg)
        except OSError as exc:
            self._error = exc

    async def _keepalive(self) -> None:
        with contextlib.suppress(LinkError):
            while True:
                await self.send(Heartbeat())
                await asyncio.sleep(KEEPALIVE_S)


# ------------------------------------------------------------------ in-process simulation
class SimLike(Protocol):
    def receive(self, data: bytes) -> None: ...
    def step(self) -> bytes: ...


HandFn = Callable[[Any, float, str], None]


class SimPipeDevice(Device):
    """The SIMULATED ESP32 in-process, in simulated time (10 ms steps). For tests and demos.

    `hand(sim, t_s, label)` is called at every step of a `hand_phase`: it stands in for the
    operator's hand (pushing a track, turning the robot). SIMULATED, never a measurement."""

    name = "in-process SIMULATED ESP32"
    simulated_by_construction = True
    STEP_S = 0.01

    def __init__(self, sim: SimLike, hand: HandFn | None = None) -> None:
        super().__init__()
        self.sim = sim
        self.hand = hand
        self._t = 0.0
        self._last_keepalive = -1.0
        self._seq = 0
        self._decoder = FrameDecoder()
        self._hand_on = ""
        self.sent: list[Message] = []  # everything robotctl sent, for tests
        self.closed = False

    def now(self) -> float:
        return self._t

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    async def send(self, msg: Message) -> None:
        self.sent.append(msg)
        self.sim.receive(encode_frame(msg, self._seq))
        self._seq = (self._seq + 1) & 0xFF

    async def pump(self, seconds: float) -> list[Message]:
        out: list[Message] = []
        steps = max(1, round(seconds / self.STEP_S))
        for _ in range(steps):
            if self._t - self._last_keepalive >= KEEPALIVE_S - 1e-9:
                self._last_keepalive = self._t
                self.sim.receive(encode_frame(Heartbeat(), self._seq))
                self._seq = (self._seq + 1) & 0xFF
            if self._hand_on and self.hand is not None:
                self.hand(self.sim, self._t, self._hand_on)
            data = self.sim.step()
            self._t += self.STEP_S
            for frame in self._decoder.feed(data):
                self._note(frame.msg)
                out.append(frame.msg)
        await asyncio.sleep(0)
        return out

    async def hand_phase(self, seconds: float, label: str = "") -> list[Message]:
        self._hand_on = label or "hand"
        try:
            return await self.pump(seconds)
        finally:
            self._hand_on = ""


# ------------------------------------------------------------------ robotd /ws/ctl
class CtlDevice(Device):
    """robotd's local control API (docs: interface section C). robotd must be IDLE."""

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url
        self.name = f"robotd {url}"
        self._session: Any = None
        self._ws: Any = None
        self._queue: asyncio.Queue[Message] = asyncio.Queue()
        self._replies: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None

    async def open(self) -> None:
        import aiohttp

        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(self.url, heartbeat=10)
        except (aiohttp.ClientError, OSError) as exc:
            await self._session.close()
            raise LinkError(f"cannot reach robotd at {self.url}: {exc}") from exc
        self._task = asyncio.create_task(self._reader())
        try:
            await self._send_json({"type": "ctl.hello", "client": "robotctl"})
            await self._send_json({"type": "ctl.bringup", "on": True})
            await self._wait_bringup()
            await self._send_json({"type": "ctl.telemetry_rate", "hz": 50})
        except BaseException:
            await self.close()
            raise

    async def _wait_bringup(self, timeout: float = 3.0) -> None:  # noqa: ASYNC109
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                reply = await asyncio.wait_for(self._replies.get(), end - time.monotonic())
            except TimeoutError:
                break
            kind = reply.get("type")
            if kind == "ctl.status":
                if reply.get("bringup") is True or reply.get("mode") == "bringup":
                    return
                continue
            if kind in ("ctl.bringup", "ctl.ack", "ctl.error"):
                if reply.get("ok", reply.get("accepted", kind != "ctl.error")) and reply.get(
                    "on", True
                ):
                    return
                raise LinkError(
                    "robotd refused bring-up mode: "
                    f"{reply.get('reason') or reply.get('error') or reply} "
                    "(it is accepted only in IDLE: stop roaming first)"
                )
        raise LinkError("robotd did not confirm bring-up mode; nothing was sent to the ESP32")

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._send_json({"type": "ctl.bringup", "on": False})
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        with contextlib.suppress(Exception):
            if self._ws is not None:
                await self._ws.close()
        with contextlib.suppress(Exception):
            if self._session is not None:
                await self._session.close()

    async def _send_json(self, data: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            raise LinkError("robotd connection is closed")
        await self._ws.send_str(json.dumps(data))

    async def send(self, msg: Message) -> None:
        if self._error is not None:
            raise LinkError(f"robotd connection lost: {self._error}")
        await self._send_json({"type": "ctl.serial", **wire.to_json(msg)})

    async def pump(self, seconds: float) -> list[Message]:
        out: list[Message] = []
        end = self.now() + max(0.0, seconds)
        while True:
            if self._error is not None:
                raise LinkError(f"robotd connection lost: {self._error}")
            remaining = end - self.now()
            if remaining <= 0:
                while not self._queue.empty():
                    out.append(self._queue.get_nowait())
                return out
            try:
                out.append(await asyncio.wait_for(self._queue.get(), min(remaining, 0.05)))
            except TimeoutError:
                pass

    async def _reader(self) -> None:
        import aiohttp

        try:
            async for raw in self._ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(raw.data)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                kind = data.get("type")
                if kind == "ctl.serial_rx":
                    try:
                        msg = wire.from_json(data.get("frame_type", ""), data.get("fields") or {})
                    except ValueError as exc:
                        log.debug("ignored ctl.serial_rx: %s", exc)
                        continue
                    self._note(msg)
                    self._queue.put_nowait(msg)
                    continue
                if kind == "ctl.status":
                    self.ctl_status = data
                    if data.get("is_simulated"):
                        self.seen_simulated = True
                    if isinstance(data.get("vision"), dict):
                        self.vision = data["vision"]
                elif kind in ("ctl.vision", "vision.state"):
                    self.vision = data
                self._replies.put_nowait(data)
            self._error = ConnectionError("robotd closed the connection")
        except Exception as exc:  # noqa: BLE001 - reported through pump()/send()
            self._error = exc
