"""robotd application: connects the brain to the ESP32 link, the backend and the local UI server."""

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from scr_serial import Message

from robotd import calibration as calib
from robotd.backend_link import BackendLink
from robotd.blackbox import default_log_dir
from robotd.calibration import SIM_PROFILE, Calibration
from robotd.config import Config, public_dict
from robotd.core import Brain
from robotd.ctl import CtlHub
from robotd.health import read_health
from robotd.link import SerialLink
from robotd.sim_esp32 import SimEsp32, SimEsp32Config, SimEsp32Server
from robotd.transport import SerialTransport, TcpTransport, Transport
from robotd.ui_server import UiServer

log = logging.getLogger("robotd")

TICK_S = 0.02  # brain step: 50 Hz
CAL_POLL_S = 2.0
HEALTH_PERIOD_S = 5.0  # the calibration file is checked for changes this often


def load_calibration(cfg: Config) -> tuple[Calibration, str, Path | None]:
    """(calibration, problem, path). `robotd --sim` without an explicit file uses the SIMULATED
    profile; otherwise the file (default path) is read, and a missing or bad file gives an
    empty calibration plus the reason (the robot then cannot drive, but it can talk)."""
    if cfg.sim and not cfg.calibration_path:
        return calib.simulated(cfg.robot_id), "", None
    path = Path(cfg.calibration_path) if cfg.calibration_path else calib.default_path()
    try:
        return calib.load(path), "", path
    except calib.CalibrationMissing:
        problem = f"no calibration file at {path}"
    except calib.CalibrationError as exc:
        problem = str(exc)
    log.warning("calibration: %s - the robot will not drive until it is calibrated", problem)
    return Calibration(), problem, path


class RobotApp:
    def __init__(self, cfg: Config, sim: SimEsp32 | None = None) -> None:
        self.cfg = cfg
        self.clock = time.monotonic
        cal, problem, self.cal_path = load_calibration(cfg)
        self._cal_mtime = self._mtime()
        self.brain = Brain(cfg, calibration=cal, calibration_problem=problem)
        self.brain.blackbox.directory = Path(cfg.log_dir) if cfg.log_dir else default_log_dir()
        self.sim_server: SimEsp32Server | None = None
        if cfg.sim:
            self.sim_server = SimEsp32Server(
                sim or SimEsp32(SimEsp32Config(seed=cfg.sim_seed, nvs=dict(SIM_PROFILE))),
                cfg.sim_host, cfg.sim_port,
            )  # fmt: skip
        self.ctl = CtlHub(self.brain, self.clock, self.reload_calibration)
        self.link = SerialLink(
            self.brain.link, self._transport, self._on_serial, self.clock, cfg.reconnect_s
        )
        self.link.on_connection = lambda up: self.brain.on_link(up, self.clock())
        self.backend = BackendLink(cfg, self.brain, self.clock)
        self.ui = UiServer(cfg, self.brain, self.clock, ctl=self.ctl, app=self)
        self._tasks: list[asyncio.Task[None]] = []

    def _on_serial(self, msg: Message, now: float) -> None:
        self.brain.on_serial(msg, now)
        self.ctl.on_serial(msg, now)

    def _transport(self) -> Transport:
        if self.sim_server is not None:
            return TcpTransport(self.cfg.sim_host, self.sim_server.port)
        return SerialTransport(self.cfg.serial_port, self.cfg.serial_baud)

    def _mtime(self) -> float | None:
        try:
            return self.cal_path.stat().st_mtime if self.cal_path is not None else None
        except OSError:
            return None

    def reload_calibration(self) -> str | None:
        """Read the calibration file again (robotctl wrote it, or it changed on disk) and push
        it. Returns the problem, or None."""
        if self.cal_path is None:
            return "robotd --sim uses the built-in SIMULATED calibration (no file)"
        try:
            cal = calib.load(self.cal_path)
        except calib.CalibrationError as exc:
            self.brain.set_calibration(Calibration(), str(exc))
            return str(exc)
        self.brain.set_calibration(cal)
        self._cal_mtime = self._mtime()
        return None

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
            asyncio.create_task(self._watch_calibration(), name="calibration-watch"),
            asyncio.create_task(self._watch_health(), name="pi-health"),
        ]

    async def _sync(self) -> None:
        done = await self.ui.sync_assets()
        if done:
            log.info("robot page assets synced from the backend: %d files", len(done))

    async def _watch_calibration(self) -> None:
        while True:
            await asyncio.sleep(CAL_POLL_S)
            mtime = self._mtime()
            if mtime != self._cal_mtime:
                self._cal_mtime = mtime
                log.info("calibration file changed: reloading")
                self.reload_calibration()

    async def _watch_health(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                self.brain.set_health(await asyncio.to_thread(read_health))
            await asyncio.sleep(HEALTH_PERIOD_S)

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
