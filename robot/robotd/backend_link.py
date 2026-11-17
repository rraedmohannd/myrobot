"""Backend link: WebSocket client to `/ws/robot` (docs/PROTOCOL.md, protocol version 2).

Sends hello, heartbeats, `robot.status` (1 Hz) and the brain's run messages (run.start,
telemetry, event, run.end); answers every `robot.command` with `robot.command.ack`. Everything
that goes out is validated against `scr_protocol` first.

If the backend is lost, the brain is told at once (it stops roaming by itself), run messages stay
in the brain's bounded outbox, and this task keeps reconnecting. Nothing here is needed for the
robot to stop (CLAUDE.md rule 4).
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp
from scr_protocol import ProtocolError, parse_backend_message, parse_robot_message

from robotd import __version__
from robotd.config import Config
from robotd.core import Brain

log = logging.getLogger("robotd.backend")


class BackendLost(Exception):
    """The connection is open but the backend stopped answering."""


class BackendLink:
    def __init__(self, cfg: Config, brain: Brain, clock: Callable[[], float]) -> None:
        self.cfg, self.brain, self._clock = cfg, brain, clock
        self.connected = False
        self.connects = 0
        self.sent: dict[str, int] = {}  # message type -> count
        self.invalid = 0  # messages we refused to send (not in the contract)
        self.last_error = ""

    def _url(self) -> str:
        url = self.cfg.current_backend_url
        if self.cfg.robot_token:
            url += ("&" if "?" in url else "?") + "token=" + self.cfg.robot_token
        return url

    async def run(self) -> None:
        failing = False
        while True:
            try:
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(self._url(), max_msg_size=8 << 20) as ws:
                        await self._session(ws)
            except (aiohttp.ClientError, OSError, TimeoutError, BackendLost) as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                if not failing:  # the URL is not logged: it may carry the token
                    log.warning("backend unavailable (%s); retrying", self.last_error)
                failing = True
                if len(self.cfg.backend_urls) > 1:  # several addresses: try the next one
                    self.cfg.backend_index = (self.cfg.backend_index + 1) % len(
                        self.cfg.backend_urls
                    )
                    log.info("trying backend address %d of %d", self.cfg.backend_index + 1,
                             len(self.cfg.backend_urls))  # fmt: skip
            else:
                failing = False
            finally:
                if self.connected:
                    log.warning("backend connection closed; robot keeps working locally")
                self.connected = False
                self.brain.set_backend(False)
            await asyncio.sleep(self.cfg.backend_reconnect_s)

    async def _session(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        await self._send(ws, {
            "type": "hello", "robot_id": self.cfg.robot_id, "fw_version": f"robotd-{__version__}",
            "protocol_version": 2,
        })  # fmt: skip
        self._last_rx = self._clock()
        self.connected = True
        self.connects += 1
        self.brain.set_backend(True)
        log.info("backend connected")
        writer = asyncio.create_task(self._writer(ws))
        try:
            async for raw in ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    continue
                self._last_rx = self._clock()
                if writer.done():
                    break
                await self._on_message(ws, raw.data)
            if writer.done() and writer.exception() is not None:
                raise writer.exception()  # type: ignore[misc]
            if ws.close_code == 4401:
                raise BackendLost(
                    "the backend refused the connection (ROBOT_TOKEN missing or wrong)"
                )
        finally:
            writer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await writer

    async def _on_message(self, ws: aiohttp.ClientWebSocketResponse, text: str) -> None:
        try:
            data = json.loads(text)
            msg = parse_backend_message(data)
        except (ValueError, ProtocolError) as exc:
            log.warning("ignoring a backend message outside the contract: %s", exc)
            return
        kind = data.get("type")
        if kind == "robot.command":
            # always answer; the brain decides (accept or refuse with a reason)
            ack = self.brain.on_command(data["id"], data["cmd"], data.get("args"))
            await self._send(ws, ack)
        elif kind == "run.started":
            self.brain.run_id = data["run_id"]
            log.info(
                "backend run %s started (is_simulated=%s)", data["run_id"], data["is_simulated"]
            )
        elif kind == "run.ended":
            log.info(
                "backend run %s ended: %s samples, %s events",
                data["run_id"],
                data["samples"],
                data["events"],
            )
            if self.brain.run_id == data["run_id"] and not self.brain.sup.run_open:
                self.brain.run_id = None
        elif kind == "error":
            log.warning("backend error %s: %s", data.get("code"), data.get("message"))
        del msg

    async def _writer(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        cfg = self.cfg
        next_status = next_heartbeat = self._clock()
        while True:
            now = self._clock()
            if now - self._last_rx > cfg.backend_lost_after_s:
                await ws.close()
                raise BackendLost(f"no message for {cfg.backend_lost_after_s:.0f} s")
            if now >= next_heartbeat:
                next_heartbeat = now + cfg.backend_heartbeat_s
                await self._send(ws, {"type": "heartbeat"})
            report = self.brain.take_calibration_report()  # at connect and on file change
            if report is not None:
                await self._send(ws, report)
            if now >= next_status:
                next_status = now + cfg.status_period_s
                await self._send(ws, self.brain.status(now))
            for msg in self.brain.take_outbox():
                await self._send(ws, msg)
            await asyncio.sleep(0.1)

    async def _send(self, ws: aiohttp.ClientWebSocketResponse, msg: dict[str, Any]) -> None:
        try:
            parse_robot_message(msg)
        except ProtocolError as exc:
            self.invalid += 1
            log.error("not sent, outside the contract (%s): %s", msg.get("type"), exc)
            return
        await ws.send_str(json.dumps(msg))
        self.sent[msg["type"]] = self.sent.get(msg["type"], 0) + 1
