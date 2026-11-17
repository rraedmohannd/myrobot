"""One robotctl run: the device, the operator, the calibration store, and the safety frame.

Safety frame (CLAUDE.md rule 4): `motion()` asks for the typed confirmation BEFORE anything is
enabled, and on every exit (normal end, refusal, exception, Ctrl-C) sends STOP, bring-up OFF and
MODE SAFE. Drives are bounded in time and duty here, and the ESP32 bounds them again (its
watchdog, TEST_MAX_DUTY, obstacle stop). This program never relaxes any of the ESP32's rules.
"""

import asyncio
import contextlib
import math
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any

from scr_serial import (
    PARAM_ALL,
    Ack,
    AckStatus,
    Bringup,
    BringupKind,
    Drive,
    DriveMode,
    Message,
    Mode,
    ParamGet,
    ParamReport,
    ParamSave,
    ParamSet,
    ParamTable,
    Stop,
    Telemetry,
    f32,
    load_table,
)

from robotctl.calstore import CalStore
from robotctl.device import Device
from robotctl.operator import Operator, Refused

MAX_TEST_DUTY = 0.5  # the compiled maximum of TEST_MAX_DUTY (params.json); robotctl never asks more
MAX_DRIVE_S = 30.0  # no single drive command of robotctl lasts longer than this
DRIVE_REPEAT_S = 0.05
TEST_RAMP_MM_S2 = 1000.0  # ramp used for bring-up tests when the firmware's is still 0
RAISE_CAPS = "raise the duty caps for this test"
SIM_BANNER = (
    "*** SIMULATED ESP32: everything below is SIMULATED, not a physical measurement "
    "(CLAUDE.md rule 5) ***"
)


class StepError(RuntimeError):
    """The step could not finish (the ESP32 refused, a check failed). Motors are stopped."""


class Session:
    def __init__(
        self, dev: Device, op: Operator, store: CalStore, *,
        out: Callable[[str], None] = print, table: ParamTable | None = None,
        hand_secs: float | None = None, save_params: bool | None = None,
        accept: bool | None = None,
    ) -> None:  # fmt: skip
        self.dev = dev
        self.op = op
        self.store = store
        self._out = out
        self.table = table or load_table()
        self.hand_secs = hand_secs  # non-interactive: how long the operator's hand action lasts
        self.save_params = save_params  # --save-params / --no-save-params (None: ask)
        self.accept = accept  # --accept / --reject proposals (None: ask)
        self.in_motion_mode = False
        self.wrote: list[str] = []  # calibration keys written in this run

    # ---------------------------------------------------------------- output
    @property
    def sim(self) -> bool:
        return self.dev.is_simulated

    def say(self, text: str = "") -> None:
        if self.sim and text:
            text = "\n".join(f"[SIMULATED] {line}" if line else line for line in text.split("\n"))
        self._out(text)

    def banner(self, step: str) -> None:
        self._out(f"robotctl {step} -> {self.dev.name}")
        if self.sim:
            self._out(SIM_BANNER)

    # ---------------------------------------------------------------- parameters
    async def read_params(self) -> dict[str, ParamReport]:
        got = await self.dev.request(
            ParamGet(PARAM_ALL),
            lambda m: isinstance(m, Ack) and m.acked_type == int(ParamGet.TYPE),
            timeout=3.0,
        )
        ack = got[-1]
        assert isinstance(ack, Ack)
        if ack.status != AckStatus.OK:
            raise StepError(f"PARAM_GET refused: {ack.status.name}")
        out: dict[str, ParamReport] = {}
        for m in got:
            if isinstance(m, ParamReport):
                with contextlib.suppress(KeyError):
                    out[self.table.by_id(m.id).name] = m
        return out

    async def param(self, name: str) -> float:
        pid = self.table.by_name(name).id
        got = await self.dev.request(
            ParamGet(pid), lambda m: isinstance(m, ParamReport) and m.id == pid, timeout=1.5
        )
        rep = got[-1]
        assert isinstance(rep, ParamReport)
        return float(rep.value)

    async def set_param(self, name: str, value: float) -> None:
        spec = self.table.by_name(name)
        if not spec.in_range(value):
            raise Refused(f"{name} = {value:g} is outside [{spec.min:g}, {spec.max:g}]")
        ack = await self.dev.command(ParamSet(spec.id, float(value)), timeout=1.5)
        if ack.status != AckStatus.OK:
            hint = {
                AckStatus.BUSY: "the tracks are moving or a target is not zero",
                AckStatus.OUT_OF_RANGE: "outside the bounds compiled into the firmware",
                AckStatus.UNKNOWN_PARAM: "the firmware does not know this parameter (old build?)",
            }.get(ack.status, "")
            raise StepError(f"PARAM_SET {name} = {value:g} refused: {ack.status.name} {hint}")
        self.say(f"  PARAM_SET {name} = {value:g} (not saved yet)")

    async def offer_param_save(self) -> bool:
        """PARAM_SAVE only after the operator says yes (and only while stopped)."""
        save = await self.op.yes_no(
            "Save the parameters in the ESP32's flash now (PARAM_SAVE)?", self.save_params
        )
        if not save:
            self.say(
                "  Not saved in the ESP32's flash. robotd pushes the calibration file at every "
                "connection anyway."
            )
            return False
        await self.stop_motors()
        ack = await self.dev.command(ParamSave(), timeout=3.0)
        if ack.status != AckStatus.OK:
            raise StepError(f"PARAM_SAVE refused: {ack.status.name} (allowed only when stopped)")
        self.say("  PARAM_SAVE: OK (written to the ESP32's flash)")
        return True

    async def propose(
        self, step: str, values: dict[str, float], *, method: str, raw: Any,
        pi_values: dict[str, Any] | None = None, firmware: bool = True,
    ) -> bool:  # fmt: skip
        """Show proposed values; after the operator accepts, PARAM_SET them, write the
        calibration file (with the raw data), log, and offer PARAM_SAVE."""
        pi_values = pi_values or {}
        values = {
            k: (int(round(v)) if self.table.by_name(k).type != "float" else rnd(v, 7))
            for k, v in values.items()
        }
        self.say("Proposed values:")
        for key, value in {**values, **pi_values}.items():
            self.say(f"  {key} = {value}")
        for key, value in values.items():
            spec = self.table.by_name(key)
            if not spec.in_range(value):
                raise Refused(
                    f"{key} = {value:g} is outside [{spec.min:g}, {spec.max:g}]: something is "
                    "wrong with the set-up or the measurement. Stop and ask (BRINGUP.md)."
                )
        accepted = await self.op.yes_no("Accept and save these values?", self.accept)
        self.log(step, {"proposed": {**values, **pi_values}, "accepted": accepted, "raw": raw})
        if not accepted:
            self.say("Not accepted: nothing changed.")
            return False
        if firmware and values:
            await self.stop_motors()
            for key, value in values.items():
                await self.set_param(key, value)
        path = self.store.record(self.sim, {**values, **pi_values}, method=method, raw=raw)
        self.wrote += [*values, *pi_values]
        self.say(f"Written to the calibration file {path}")
        await self._reload_robotd()
        if firmware and values:
            await self.offer_param_save()
        return True

    def record(self, step: str, values: dict[str, Any], *, method: str, raw: Any) -> None:
        """Results of a check (no proposal): calibration file + log."""
        path = self.store.record(self.sim, values, method=method, raw=raw)
        self.wrote += list(values)
        self.log(step, {"values": values, "raw": raw})
        self.say(f"Written to the calibration file {path}")

    def log(self, step: str, result: dict[str, Any]) -> None:
        self.store.log(self.sim, step, result)

    async def _reload_robotd(self) -> None:
        send_json = getattr(self.dev, "_send_json", None)
        if send_json is not None:
            with contextlib.suppress(Exception):
                await send_json({"type": "ctl.reload_calibration"})

    # ---------------------------------------------------------------- safety frame
    async def stop_motors(self) -> None:
        await self.dev.send(Stop())
        await self.dev.pump(0.3)

    async def safe_exit(self) -> None:
        """STOP, bring-up OFF, MODE SAFE. Best effort: each one even if another fails."""
        for msg in (Stop(), Bringup(BringupKind.OFF), Mode(DriveMode.SAFE)):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.dev.send(msg), 1.0)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.dev.pump(0.2), 1.0)
        self.in_motion_mode = False

    @contextlib.asynccontextmanager
    async def mode(
        self, mode: DriveMode, confirm: str | None, why: str = ""
    ) -> AsyncIterator[None]:
        """Enter `mode` (TEST or DRIVE) after the typed confirmation; always leave in SAFE."""
        if confirm is not None:
            await self.op.confirm(confirm, why)
        try:
            await self.dev.send(Stop())
            ack = await self.dev.command(Mode(mode), timeout=1.5)
            if ack.status != AckStatus.OK:
                raise StepError(
                    f"the ESP32 refused MODE {mode.name}: {ack.status.name}"
                    + (
                        " (MODE DRIVE needs METRES_PER_TICK: run distance-cal first)"
                        if mode == DriveMode.DRIVE and ack.status == AckStatus.REJECTED_MODE
                        else " (unassigned motor pins? see BRINGUP.md section 2)"
                        if ack.status == AckStatus.REJECTED_SAFETY
                        else ""
                    )
                )
            self.in_motion_mode = True
            self.say(f"MODE {mode.name}: on (leaves to SAFE on every exit)")
            yield
        finally:
            await self.safe_exit()
            self.say("STOP + MODE SAFE sent")

    @contextlib.asynccontextmanager
    async def test_caps(self, duty: float) -> AsyncIterator[dict[str, ParamReport]]:
        """An uncalibrated firmware drives nothing (TEST_MAX_DUTY, MOTOR_MAX_DUTY, MAX_SPEED_MM_S
        and RAMP_MM_S2 are 0). For one bring-up test, raise the ones that are too low, only as
        far as `duty` needs, after a typed confirmation; put the old values back afterwards."""
        params = await self.read_params()
        need = {
            "TEST_MAX_DUTY": duty, "MOTOR_MAX_DUTY": duty,
            "MAX_SPEED_MM_S": duty * 1000.0, "RAMP_MM_S2": TEST_RAMP_MM_S2,
        }  # fmt: skip
        raise_to = {k: v for k, v in need.items() if params[k].value < v - 1e-6}
        if raise_to:
            await self.op.confirm(
                RAISE_CAPS,
                "The firmware's caps are too low for this test ("
                + ", ".join(f"{k} = {params[k].value:g}" for k in raise_to)
                + "). For THIS test only robotctl sets "
                + ", ".join(f"{k} = {v:g}" for k, v in raise_to.items())
                + " (not saved) and puts the old values back at the end.",
            )
        restore: dict[str, float] = {}
        try:
            for key, value in raise_to.items():
                await self.set_param(key, value)
                restore[key] = params[key].value
            yield params
        finally:
            if self.in_motion_mode:
                await self.safe_exit()
            for key, value in restore.items():
                with contextlib.suppress(Exception):
                    await self.set_param(key, value)
                    self.say(f"  {key} restored to {value:g}")

    @contextlib.asynccontextmanager
    async def driven_test(self, phrase: str, why: str, duty: float) -> AsyncIterator[None]:
        """Typed confirmation, then the caps the test needs, then MODE TEST; SAFE on exit."""
        await self.op.confirm(phrase, why)
        async with self.test_caps(duty), self.mode(DriveMode.TEST, None):
            yield

    async def drive_for(
        self, left: float, right: float, secs: float,
        stop_when: Callable[[list[Message]], bool] | None = None,
    ) -> list[Message]:  # fmt: skip
        """Repeat DRIVE (left, right) for at most `secs`, then STOP. In MODE TEST the values are
        open-loop duties (the firmware maps 1.0 m/s to duty 1.0, TEST_FULL_SCALE_MM_S)."""
        if not self.in_motion_mode:
            raise Refused("internal: drive outside a confirmed MODE TEST/DRIVE")
        secs = min(max(0.0, secs), MAX_DRIVE_S)
        got: list[Message] = [self.dev.last_telemetry] if self.dev.last_telemetry else []
        end = self.dev.now() + secs
        try:
            while self.dev.now() < end:
                await self.dev.send(Drive(left, right))
                batch = await self.dev.pump(DRIVE_REPEAT_S)
                got += batch
                for m in batch:
                    if isinstance(m, Ack) and m.acked_type == int(Drive.TYPE):
                        raise StepError(f"the ESP32 refused DRIVE: {m.status.name}")
                if stop_when is not None and stop_when(got):
                    break
        finally:
            with contextlib.suppress(Exception):
                await self.dev.send(Stop())
        got += await self.dev.pump(0.4)
        return got

    async def bringup(self, kind: BringupKind, arg: int = 0) -> None:
        ack = await self.dev.command(Bringup(kind, arg), timeout=1.5)
        if ack.status != AckStatus.OK:
            raise StepError(f"BRINGUP {kind.name} refused: {ack.status.name} (only in MODE TEST)")

    async def wait_operator(
        self, prompt: str, label: str = "", max_s: float = 180.0
    ) -> list[Message]:
        """'Do X by hand, press Enter when done' while collecting everything the ESP32 sends.
        Non-interactive: the hand action lasts --hand-secs (tests: a simulated hand)."""
        self.say(prompt)
        before: list[Message] = [self.dev.last_telemetry] if self.dev.last_telemetry else []
        if self.hand_secs is not None:
            got = await self.dev.hand_phase(self.hand_secs, label)
            return before + got + await self.dev.pump(0.1)
        if not self.op.interactive:
            raise Refused("this step needs the operator (interactive) or --hand-secs")
        task = asyncio.ensure_future(self.op.line("  ...press Enter when done "))
        got = list(before)
        end = self.dev.now() + max_s
        try:
            while not task.done() and self.dev.now() < end:
                got += await self.dev.pump(0.1)
        finally:
            if not task.done():
                self.say("  time limit reached")
                task.cancel()
        return got + await self.dev.pump(0.1)


# ---------------------------------------------------------------- small helpers
def telemetry(msgs: Iterable[Message]) -> list[Telemetry]:
    return [m for m in msgs if isinstance(m, Telemetry)]


def enc_delta(msgs: Iterable[Message]) -> tuple[int, int, int, int]:
    tel = telemetry(msgs)
    if len(tel) < 2:
        return (0, 0, 0, 0)
    a, b = tel[0].encoders, tel[-1].encoders
    return tuple(_i32(b[i] - a[i]) for i in range(4))  # type: ignore[return-value]


def _i32(v: int) -> int:
    return (v + (1 << 31)) % (1 << 32) - (1 << 31)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def rnd(value: float, digits: int = 6) -> float:
    return float(f"{f32(value):.{digits}g}")
