"""Talking to the operator: typed confirmations, typed measurements, "press Enter when done".

Every question has a non-interactive answer from a command-line flag (tests, scripts). Without
the flag and without a terminal, the step refuses: nothing moves on a guess.
"""

import asyncio
import math
from collections.abc import Callable, Sequence


class Refused(RuntimeError):
    """The step refuses to continue (no confirmation, bad value, wrong mode). Nothing moved."""


def _norm(text: str) -> str:
    return " ".join(text.strip().lower().split())


class Operator:
    def __init__(
        self, interactive: bool, confirmations: Sequence[str] = (),
        out: Callable[[str], None] = print, input_fn: Callable[[str], str] = input,
    ) -> None:  # fmt: skip
        self.interactive = interactive
        self.confirmations = [_norm(c) for c in confirmations]
        self.out = out
        self._input = input_fn

    async def line(self, prompt: str) -> str:
        try:
            return await asyncio.to_thread(self._input, prompt)
        except EOFError as exc:
            raise Refused("no answer (end of input)") from exc

    async def confirm(self, phrase: str, why: str = "") -> None:
        """The operator must type exactly `phrase` (case and spaces do not matter)."""
        if why:
            self.out(why)
        if _norm(phrase) in self.confirmations:
            self.out(f'Confirmed by flag: "{phrase}"')
            return
        if self.confirmations and not self.interactive:
            raise Refused(
                f'the confirmation given does not match "{phrase}"; nothing was moved or changed'
            )
        if not self.interactive:
            raise Refused(
                f'this step needs the typed confirmation "{phrase}" '
                f'(interactive, or --yes-i-confirm "{phrase}"); nothing was moved or changed'
            )
        typed = await self.line(f'Type exactly "{phrase}" to continue (anything else stops): ')
        if _norm(typed) != _norm(phrase):
            raise Refused(f'confirmation "{typed.strip()}" does not match; nothing was moved')

    async def number(
        self, question: str, given: float | None, lo: float, hi: float, unit: str = ""
    ) -> float:
        """A typed measurement in [lo, hi]."""
        if given is not None:
            if not (math.isfinite(given) and lo <= given <= hi):
                raise Refused(f"{question}: {given} {unit} is outside [{lo:g}, {hi:g}] {unit}")
            return float(given)
        if not self.interactive:
            raise Refused(f"{question}: no value given (flag missing)")
        for _ in range(3):
            text = (await self.line(f"{question} [{lo:g}..{hi:g} {unit}]: ")).strip()
            if not text:
                raise Refused(f"{question}: no value typed")
            try:
                value = float(text.replace(",", "."))
            except ValueError:
                self.out("  not a number, try again")
                continue
            if math.isfinite(value) and lo <= value <= hi:
                return value
            self.out(f"  outside [{lo:g}, {hi:g}], try again (or measure again)")
        raise Refused(f"{question}: no valid value after 3 tries")

    async def yes_no(self, question: str, given: bool | None, default_no: bool = True) -> bool:
        if given is not None:
            return given
        if not self.interactive:
            return False if default_no else True
        text = await self.line(f"{question} [y/N]: " if default_no else f"{question} [Y/n]: ")
        text = text.strip().lower()
        if not text:
            return not default_no
        return text in ("y", "yes", "نعم", "اه", "اي")

    async def choice(self, question: str, given: str | None, options: Sequence[str]) -> str:
        if given is not None:
            if given not in options:
                raise Refused(f"{question}: {given!r} is not one of {', '.join(options)}")
            return given
        if not self.interactive:
            raise Refused(f"{question}: no answer given (flag missing)")
        for _ in range(3):
            text = (await self.line(f"{question} ({'/'.join(options)}): ")).strip().lower()
            if text in options:
                return text
        raise Refused(f"{question}: no valid answer")
