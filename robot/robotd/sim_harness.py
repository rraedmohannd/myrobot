"""Run the brain against the SIMULATED ESP32 in simulated time (no sockets, no sleeps).

Used by the tests and for quick experiments. Deterministic for a given seed. Everything it
produces is simulation (CLAUDE.md rule 5).
"""

from collections.abc import Callable
from typing import Any

from scr_serial import Message

from robotd.calibration import SIM_PROFILE
from robotd.config import Config
from robotd.core import Brain
from robotd.sim_esp32 import STEP_MS, SimEsp32, SimEsp32Config


class SimHarness:
    def __init__(self, cfg: Config | None = None, sim_cfg: SimEsp32Config | None = None) -> None:
        self.cfg = cfg or Config(sim=True)
        sim_cfg = sim_cfg or SimEsp32Config(seed=self.cfg.sim_seed, nvs=dict(SIM_PROFILE))
        self.sim = SimEsp32(sim_cfg)
        self.brain = Brain(self.cfg)
        self.brain.set_backend(True)  # tests switch it off to simulate a lost backend
        self.pi_alive = True  # False: the Pi sends nothing (heartbeat loss)
        self.from_esp: list[Message] = []  # every non-telemetry message the Pi received
        self.ui: list[dict[str, Any]] = []  # everything sent to the page
        self._steps = 0

    @property
    def now_s(self) -> float:
        return self._steps * STEP_MS / 1000.0

    def step(self) -> None:
        """10 ms of simulated time: ESP32 step, Pi receives, Pi logic at 50 Hz, Pi sends."""
        out = self.sim.step()
        self._steps += 1
        now = self.now_s
        if self.pi_alive:
            for msg in self.brain.link.feed(out, now):
                if type(msg).__name__ != "Telemetry":
                    self.from_esp.append(msg)
                self.brain.on_serial(msg, now)
        if self._steps % 2 == 0:
            self.brain.tick(now)
            self.ui += self.brain.ui_out
            self.brain.ui_out.clear()
        data = self.brain.link.poll(now)
        if data and self.pi_alive:
            self.sim.receive(data)

    def run(self, seconds: float) -> None:
        for _ in range(int(round(seconds * 1000 / STEP_MS))):
            self.step()

    def run_until(self, condition: Callable[[], bool], timeout_s: float) -> bool:
        for _ in range(int(round(timeout_s * 1000 / STEP_MS))):
            if condition():
                return True
            self.step()
        return condition()

    def command(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.brain.on_command("test", cmd, args)

    def ui_types(self) -> list[str]:
        return [m["type"] for m in self.ui]
