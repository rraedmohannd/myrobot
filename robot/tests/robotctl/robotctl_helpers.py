"""robotctl tests: every step runs against the SIMULATED ESP32 (robotd.sim_esp32) in simulated
time. Nothing here is a physical measurement (CLAUDE.md rule 5)."""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from robotctl import cli
from robotctl.device import HandFn, SimPipeDevice
from robotd.sim_esp32 import SIM_PROFILE, SimEsp32, SimEsp32Config


class Runner:
    def __init__(self, tmp_path: Path) -> None:
        self.cal = tmp_path / "calibration.json"
        self.lines: list[str] = []
        self.dev: SimPipeDevice | None = None
        self.sim: SimEsp32 | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    async def __call__(
        self, argv: list[str], sim: SimEsp32 | None = None, hand: HandFn | None = None,
        input_fn: Callable[[str], str] | None = None,
    ) -> int:  # fmt: skip
        self.sim = sim or SimEsp32()
        self.dev = SimPipeDevice(self.sim, hand)
        extra = ["--calibration", str(self.cal), "--operator", "pytest"]
        if input_fn is None:
            extra.append("--non-interactive")
            return await cli.run(argv + extra, device=self.dev, out=self.lines.append)
        return await cli.run(
            argv + extra, device=self.dev, out=self.lines.append, input_fn=input_fn
        )


def calibrated_sim(**cfg: Any) -> SimEsp32:
    """A SIMULATED ESP32 booting with the SIMULATED profile in its 'NVS'."""
    return SimEsp32(SimEsp32Config(nvs=dict(SIM_PROFILE), **cfg))


def fake_operator(answers: dict[str, Callable[[], str]] | None = None) -> Callable[[str], str]:
    """Types the confirmation it is asked for, says yes, and answers measurements from
    `answers` (prompt substring -> function)."""

    def answer(prompt: str) -> str:
        m = re.search(r'Type exactly "([^"]+)"', prompt)
        if m:
            return m.group(1)
        for key, fn in (answers or {}).items():
            if key.lower() in prompt.lower():
                return fn()
        if "[y/N]" in prompt or "[Y/n]" in prompt:
            return "y"
        return ""

    return answer
