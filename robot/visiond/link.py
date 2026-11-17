"""WebSocket client to robotd (/ws/vision). Keeps retrying while robotd is absent.

Only JSON text built by visiond.messages goes out. Inbound: `vad` and `servo` messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from typing import Any

import aiohttp

from visiond.messages import ServoMessage, VadMessage, parse_inbound

InboundHandler = Callable[[VadMessage | ServoMessage], None]


class RobotdLink:
    def __init__(
        self,
        url: str,
        on_message: InboundHandler,
        *,
        retry_s: float = 2.0,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self.url = url
        self.retry_s = retry_s
        self.connected = False
        self.connects = 0
        self.sent = 0
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    async def run(self) -> None:
        """Connect, read until the connection ends, wait, connect again. Runs until cancelled."""
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                try:
                    async with session.ws_connect(self.url, heartbeat=10) as ws:
                        self._ws = ws
                        self.connected = True
                        self.connects += 1
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                parsed = parse_inbound(msg.data)
                                if parsed is not None:
                                    self._on_message(parsed)
                            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                break
                except (aiohttp.ClientError, OSError, TimeoutError):
                    pass  # robotd is not there (yet): try again below
                finally:
                    was_connected = self.connected
                    self.connected = False
                    self._ws = None
                    if was_connected and self._on_disconnect is not None:
                        self._on_disconnect()
                await asyncio.sleep(self.retry_s)

    async def send(self, message: dict[str, Any]) -> bool:
        """Send one message; False when robotd is not connected (the message is dropped)."""
        ws = self._ws
        if ws is None or ws.closed:
            return False
        with contextlib.suppress(aiohttp.ClientError, ConnectionError, RuntimeError):
            await ws.send_str(json.dumps(message, separators=(",", ":")))
            self.sent += 1
            return True
        return False
