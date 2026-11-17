"""Local control API for robotctl while robotd runs (sprint 5 interface C).

WebSocket `ws://127.0.0.1:<ui_port>/ws/ctl`, localhost only (same server as the robot page).

client -> robotd
- `{"type":"ctl.hello","client":"robotctl"}`
- `{"type":"ctl.bringup","on":true|false}`: only from IDLE; robotd then commands nothing
  (state PAUSED, mode "bringup"); HEARTBEAT goes on. Off: STOP, MODE SAFE, the calibration
  parameters are pushed again.
- `{"type":"ctl.serial","frame_type":"PARAM_SET","fields":{"id":1,"value":0.0005}}`: forwarded
  to the ESP32. Allowed: MODE, DRIVE, STOP, SERVO, PARAM_SET, PARAM_GET, PARAM_SAVE,
  DIAG_REQUEST, BRINGUP, CALIBRATE_GYRO; MODE / DRIVE / BRINGUP only while bring-up is on.
- `{"type":"ctl.reload_calibration"}`: read the calibration file again and push it.
- `{"type":"ctl.telemetry_rate","hz":50}`: telemetry forwarding rate (default 10 Hz).

robotd -> client
- `{"type":"ctl.serial_rx","frame_type":"TELEMETRY","fields":{...}}` for every frame from the
  ESP32 (telemetry decimated);
- `{"type":"ctl.status", ...robot.status..., "bringup", "params_ok", "expected_hash", ...}` 2 Hz;
- `{"type":"ctl.ack","request":..., "ok":bool, "reason"?}` for every client request;
- `{"type":"ctl.hello.ack", "robotd": version, "is_simulated": bool}`.

When a client that turned bring-up on (or sent MODE / DRIVE / BRINGUP) disconnects: bring-up
off, STOP, MODE SAFE. A read-only client (status, diag) leaves the robot alone.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import fields as dc_fields
from enum import Enum, IntFlag
from typing import Any

from aiohttp import WSMsgType, web
from scr_serial import (
    Bringup,
    BringupKind,
    CalibrateGyro,
    DiagRequest,
    Drive,
    DriveMode,
    Message,
    Mode,
    MsgType,
    ParamGet,
    ParamSave,
    ParamSet,
    Servo,
    Stop,
    Telemetry,
)

from robotd import __version__
from robotd.core import Brain

log = logging.getLogger("robotd.ctl")

ALLOWED: dict[str, type] = {
    "MODE": Mode, "DRIVE": Drive, "STOP": Stop, "SERVO": Servo, "PARAM_SET": ParamSet,
    "PARAM_GET": ParamGet, "PARAM_SAVE": ParamSave, "DIAG_REQUEST": DiagRequest,
    "BRINGUP": Bringup, "CALIBRATE_GYRO": CalibrateGyro,
}  # fmt: skip
NEEDS_BRINGUP = ("MODE", "DRIVE", "BRINGUP")
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost")


def is_local(request: web.Request) -> bool:
    return (request.remote or "") in LOCAL_HOSTS


def message_fields(msg: Message) -> dict[str, Any]:
    """A decoded frame as JSON-friendly fields (enums by name, flags as numbers)."""
    out: dict[str, Any] = {}
    for f in dc_fields(msg):  # type: ignore[arg-type]
        value = getattr(msg, f.name)
        if isinstance(value, IntFlag):
            value = int(value)
        elif isinstance(value, Enum):
            value = value.name
        elif isinstance(value, tuple):
            value = list(value)
        out[f.name] = value
    if isinstance(msg, Telemetry):
        out["param_hash_hex"] = f"{msg.param_hash:08x}"
    return out


def build_message(frame_type: str, fields: dict[str, Any]) -> Message:
    """`ctl.serial` -> a scr_serial message. Raises ValueError with a reason."""
    cls = ALLOWED.get(frame_type)
    if cls is None:
        raise ValueError(f"frame type {frame_type!r} not allowed")
    args = dict(fields or {})
    try:
        if cls is Mode:
            mode = args.get("mode")
            args["mode"] = DriveMode[mode] if isinstance(mode, str) else DriveMode(int(mode))
        elif cls is Bringup:
            kind = args.get("kind")
            args["kind"] = BringupKind[kind] if isinstance(kind, str) else BringupKind(int(kind))
            args["arg"] = int(args.get("arg", 0))
        elif cls is ParamSet:
            args = {"id": int(args["id"]), "value": float(args["value"])}
        elif cls is ParamGet:
            args = {"id": int(args.get("id", 0xFFFF))}
        msg = cls(**args)
        msg.pack()  # range check of the wire format
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"bad fields for {frame_type}: {exc}") from exc
    return msg  # type: ignore[no-any-return]


class CtlClient:
    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)
        self.telemetry_hz = 10.0
        self.last_tel_s = -1e9
        self.drives = False  # this client turned bring-up on or moved something
        self.name = "?"


class CtlHub:
    def __init__(
        self, brain: Brain, clock: Callable[[], float],
        reload_calibration: Callable[[], str | None] | None = None,
    ) -> None:  # fmt: skip
        self.brain, self._clock = brain, clock
        self.reload_calibration = reload_calibration
        self.clients: set[CtlClient] = set()

    # ---------------- frames from the ESP32 ----------------
    def on_serial(self, msg: Message, now_s: float) -> None:
        if not self.clients:
            return
        is_tel = isinstance(msg, Telemetry)
        item: dict[str, Any] | None = None
        for client in list(self.clients):
            if is_tel:
                if now_s - client.last_tel_s < 1.0 / client.telemetry_hz - 1e-6:
                    continue
                client.last_tel_s = now_s
            if item is None:
                item = {
                    "type": "ctl.serial_rx", "frame_type": MsgType(int(msg.TYPE)).name,
                    "fields": message_fields(msg),
                }  # fmt: skip
            with contextlib.suppress(asyncio.QueueFull):
                client.queue.put_nowait(item)

    def status(self) -> dict[str, Any]:
        b = self.brain
        out = b.status(self._clock())
        out.update({
            "type": "ctl.status", "bringup": b.bringup, "params_ok": b.params_ok,
            "expected_hash": f"{b.expected_hash:08x}",
            "telemetry_hash": f"{b.frame.param_hash:08x}" if b.frame is not None else None,
            "calibration_problem": b.cal_problem, "link": b.link.stats(),
            "gyro_bias_residual": round(b.gyro_bias_residual, 5),
        })  # fmt: skip
        return out

    # ---------------- the WebSocket ----------------
    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        if not is_local(request):
            raise web.HTTPForbidden(text="ctl is localhost only")
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        client = CtlClient(ws)
        self.clients.add(client)
        sender = asyncio.create_task(self._sender(client))
        try:
            async for raw in ws:
                if raw.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(raw.data)
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    await self._reply(client, "?", False, "not a JSON object")
                    continue
                await self._on_request(client, data)
        except (ConnectionError, RuntimeError):
            pass
        finally:
            self.clients.discard(client)
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender
            if client.drives:
                self._release()
        return ws

    def _release(self) -> None:
        """A driving client went away: bring-up off, STOP, MODE SAFE (robotd takes over)."""
        b, now = self.brain, self._clock()
        if b.bringup:
            b.set_bringup(False, now)
        b.link.set_drive(0.0, 0.0)
        b.link.send(Stop())
        b.link.send(Mode(DriveMode.SAFE))
        log.info("ctl client gone: bring-up off, STOP, MODE SAFE")

    async def _sender(self, client: CtlClient) -> None:
        next_status = 0.0
        while True:
            now = self._clock()
            if now >= next_status:
                next_status = now + 0.5
                await client.ws.send_str(json.dumps(self.status()))
            try:
                item = await asyncio.wait_for(client.queue.get(), timeout=0.1)
            except TimeoutError:
                continue
            await client.ws.send_str(json.dumps(item))

    async def _reply(self, client: CtlClient, request: str, ok: bool, reason: str | None) -> None:
        msg: dict[str, Any] = {"type": "ctl.ack", "request": request, "ok": ok}
        if reason:
            msg["reason"] = reason
        with contextlib.suppress(asyncio.QueueFull):
            client.queue.put_nowait(msg)

    async def _on_request(self, client: CtlClient, data: dict[str, Any]) -> None:
        kind = str(data.get("type"))
        b, now = self.brain, self._clock()
        if kind == "ctl.hello":
            client.name = str(data.get("client") or "?")[:32]
            log.info("ctl client: %s", client.name)
            with contextlib.suppress(asyncio.QueueFull):
                client.queue.put_nowait({
                    "type": "ctl.hello.ack", "robotd": __version__,
                    "is_simulated": b.is_simulated,
                })  # fmt: skip
            return
        if kind == "ctl.bringup":
            on = bool(data.get("on"))
            ok, reason = b.set_bringup(on, now)
            if ok and on:
                client.drives = True
            log.info("bring-up %s by %s: %s", "on" if on else "off", client.name, ok)
            await self._reply(client, kind, ok, reason)
            return
        if kind == "ctl.telemetry_rate":
            try:
                client.telemetry_hz = max(1.0, min(50.0, float(data.get("hz", 10))))
            except (TypeError, ValueError):
                await self._reply(client, kind, False, "hz must be a number")
                return
            await self._reply(client, kind, True, None)
            return
        if kind == "ctl.reload_calibration":
            problem = self.reload_calibration() if self.reload_calibration else "not available"
            await self._reply(client, kind, not problem, problem)
            return
        if kind == "ctl.serial":
            frame_type = str(data.get("frame_type", ""))
            if frame_type in NEEDS_BRINGUP and not b.bringup:
                await self._reply(client, kind, False, f"{frame_type} needs bring-up on")
                return
            try:
                msg = build_message(frame_type, data.get("fields") or {})
            except ValueError as exc:
                await self._reply(client, kind, False, str(exc))
                return
            if frame_type in NEEDS_BRINGUP:
                client.drives = True
            b.link.send(msg)
            await self._reply(client, kind, True, None)
            return
        await self._reply(client, kind, False, f"unknown request {kind}")
