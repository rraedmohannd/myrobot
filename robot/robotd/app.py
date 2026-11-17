"""robotd application: connects the brain to the ESP32 link, the backend and the local UI server."""

import asyncio
import contextlib
import logging
import time

from robotd.backend_link import BackendLink
from robotd.config import Config, public_dict
from robotd.core import Brain
from robotd.link import SerialLink
from robotd.sim_esp32 import SimEsp32, SimEsp32Config, SimEsp32Server
from robotd.transport import SerialTransport, TcpTransport, Transport
from robotd.ui_server import UiServer

log = logging.getLogger("robotd")

TICK_S = 0.02  # brain step: 50 Hz


class RobotApp:
    def __init__(self, cfg: Config, sim: SimEsp32 | None = None) -> None:
        self.cfg = cfg
        self.clock = time.monotonic
        self.brain = Brain(cfg)
        self.sim_server: SimEsp32Server | None = None
        if cfg.sim:
            self.sim_server = SimEsp32Server(
                sim or SimEsp32(SimEsp32Config(
                    seed=cfg.sim_seed, metres_per_tick=cfg.metres_per_tick,
                    track_width_m=cfg.track_width_m,
                )),
                cfg.sim_host, cfg.sim_port,
            )  # fmt: skip
        self.link = SerialLink(
            self.brain.link, self._transport, self.brain.on_serial, self.clock, cfg.reconnect_s
        )
        self.backend = BackendLink(cfg, self.brain, self.clock)
        self.ui = UiServer(cfg, self.brain, self.clock)
        self._tasks: list[asyncio.Task[None]] = []

    def _transport(self) -> Transport:
        if self.sim_server is not None:
            return TcpTransport(self.cfg.sim_host, self.sim_server.port)
        return SerialTransport(self.cfg.serial_port, self.cfg.serial_baud)

    async def start(self) -> None:
        log.info("robotd config: %s", public_dict(self.cfg))
        if self.sim_server is not None:
            await self.sim_server.start()
            log.warning(
                "*** SIMULATED ESP32 in use: every status, run and event is marked simulated; "
                "nothing produced here is a physical result ***"
            )
        await self.ui.start()
        self._tasks = [
            asyncio.create_task(self.link.run(), name="esp32-link"),
            asyncio.create_task(self.backend.run(), name="backend-link"),
            asyncio.create_task(self._loop(), name="brain"),
            asyncio.create_task(self._sync(), name="asset-sync"),
        ]

    async def _sync(self) -> None:
        done = await self.ui.sync_assets()
        if done:
            log.info("robot page assets synced from the backend: %d files", len(done))

    async def _loop(self) -> None:
        while True:
            self.brain.tick(self.clock())
            await self.ui.flush()
            await asyncio.sleep(TICK_S)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.ui.stop()
        if self.sim_server is not None:
            await self.sim_server.stop()

    async def run_forever(self) -> None:
        await self.start()
        try:
            await asyncio.Event().wait()
        finally:
            await self.stop()
