"""Timeouts, retries and circuit breakers for the AI providers (sprint 3 S5).

The demo site may have poor internet. Every provider call (speech-to-text, language model,
text-to-speech, embeddings) therefore has:
- a time limit (`PROVIDER_TIMEOUT_*`), after which it counts as failed;
- a small number of retries with a growing pause (`PROVIDER_RETRIES`, `PROVIDER_RETRY_BACKOFF_S`);
- a circuit breaker per provider: after `BREAKER_FAILURES` failures in a row, calls fail at once
  for `BREAKER_OPEN_S` seconds, then one trial call is let through.

A failed call always ends as a `ProviderError`, which the conversation turns into the cached
"weak internet" reply. The status of each provider (for `/health` and the admin) is kept here.

Every attempt is also counted against the daily cap of paid calls (services/cost_guard.py,
DAILY_PAID_CALL_LIMIT) right before it is made. A refused call raises `PaidLimitError` (a
`ProviderError`, so it ends in the same polite reply) without touching the breaker. Fake
providers (FAKE_PROVIDERS=true) go through these same wrappers and are counted too.
"""

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from app.core.config import get_settings
from app.services.cost_guard import take_paid_call
from app.services.providers import LLMResult, ProviderError

T = TypeVar("T")
PROVIDERS = ("stt", "llm", "tts", "embeddings")


class ProviderGuard:
    def __init__(self, name: str) -> None:
        self.name = name
        self.failures = 0  # in a row
        self.opened_at: float | None = None  # monotonic time the breaker opened
        self.calls = 0
        self.failed_calls = 0
        self.last_ok: str | None = None
        self.last_error: str | None = None
        self.errors: deque[dict[str, str]] = deque(maxlen=10)

    # ---------- breaker ----------
    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if time.monotonic() - self.opened_at >= get_settings().breaker_open_s:
            return "half-open"  # one trial call is allowed
        return "open"

    def check(self) -> None:
        if self.state == "open":
            wait = get_settings().breaker_open_s - (time.monotonic() - (self.opened_at or 0.0))
            raise ProviderError(
                f"{self.name} is not reachable (circuit open after {self.failures} failures; "
                f"next try in {max(0.0, wait):.0f} s): {self.last_error}"
            )

    def ok(self) -> None:
        self.failures, self.opened_at = 0, None
        self.last_ok = datetime.now(UTC).isoformat(timespec="seconds")

    def fail(self, error: str) -> None:
        self.failures += 1
        self.failed_calls += 1
        self.last_error = error
        self.errors.appendleft(
            {"at": datetime.now(UTC).isoformat(timespec="seconds"), "error": error[:300]}
        )
        if self.failures >= get_settings().breaker_failures or self.opened_at is not None:
            self.opened_at = time.monotonic()  # open, or re-open after a failed trial call

    # ---------- one guarded call ----------
    async def call(self, fn: Callable[[], Awaitable[T]], timeout_s: float) -> T:
        settings = get_settings()
        attempts = 1 + settings.provider_retries
        for attempt in range(attempts):
            self.check()
            await take_paid_call(self.name)  # daily cap: PaidLimitError, breaker untouched
            self.calls += 1
            try:
                result = await asyncio.wait_for(fn(), timeout_s)
            except TimeoutError:
                error = ProviderError(f"{self.name} did not answer within {timeout_s:g} s")
            except ProviderError as e:
                error = e
            else:
                self.ok()
                return result
            self.fail(str(error))
            if attempt == attempts - 1 or self.state == "open":
                raise error
            await asyncio.sleep(settings.provider_retry_backoff_s * (2**attempt))
        raise ProviderError(f"{self.name} failed")  # not reached

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "ok": self.state == "closed" and self.failures == 0,
            "failures_in_a_row": self.failures,
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "recent_errors": list(self.errors),
        }


_guards: dict[str, ProviderGuard] = {}


def guard(name: str) -> ProviderGuard:
    if name not in _guards:
        _guards[name] = ProviderGuard(name)
    return _guards[name]


def provider_status() -> dict[str, dict[str, Any]]:
    """Per provider: breaker state, counters, last success, recent errors (process lifetime)."""
    return {name: guard(name).status() for name in PROVIDERS}


def reset_guards() -> None:
    _guards.clear()


# ====================================================================================
# Wrappers around the adapters (the rest of the code still sees the same interfaces)
# ====================================================================================
class ResilientEmbedder:
    def __init__(self, inner: Any) -> None:
        self.inner, self.model, self.dim = inner, inner.model, inner.dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await guard("embeddings").call(
            lambda: self.inner.embed(texts), get_settings().provider_timeout_embedding_s
        )


class ResilientSTT:
    def __init__(self, inner: Any) -> None:
        self.inner, self.model = inner, inner.model

    async def transcribe(self, audio: bytes, filename: str = "speech.wav") -> Any:
        return await guard("stt").call(
            lambda: self.inner.transcribe(audio, filename), get_settings().provider_timeout_stt_s
        )


class ResilientTTS:
    """Proxies model, voice, instructions and speed, so the TTS cache key stays the same."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        if hasattr(inner, "stream"):
            self.stream = self._stream  # experimental streaming: no time limit, but counted

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        return await guard("tts").call(
            lambda: self.inner.synthesize(text, voice), get_settings().provider_timeout_tts_s
        )

    async def _stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[bytes]:
        await take_paid_call("tts")
        async for chunk in self.inner.stream(*args, **kwargs):
            yield chunk


class ResilientLLM:
    def __init__(self, inner: Any) -> None:
        self.inner, self.model = inner, inner.model
        if hasattr(inner, "stream_json"):
            self.stream_json = self._stream_json

    async def complete_json(self, instructions: str, user_input: str) -> LLMResult:
        return await guard("llm").call(
            lambda: self.inner.complete_json(instructions, user_input),
            get_settings().provider_timeout_llm_s,
        )

    async def _stream_json(
        self, instructions: str, user_input: str
    ) -> AsyncIterator[str | LLMResult]:
        """Streamed answer with two limits: the first text within PROVIDER_TIMEOUT_LLM_FIRST_S,
        the whole answer within PROVIDER_TIMEOUT_LLM_S. It is retried only while nothing has
        been passed on yet (spoken words cannot be taken back)."""
        settings = get_settings()
        g = guard("llm")
        attempts = 1 + settings.provider_retries
        for attempt in range(attempts):
            g.check()
            await take_paid_call("llm")
            g.calls += 1
            stream = self.inner.stream_json(instructions, user_input)
            deadline = time.monotonic() + settings.provider_timeout_llm_s
            limit, passed = settings.provider_timeout_llm_first_s, False
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(
                            stream.__anext__(), max(0.01, min(limit, deadline - time.monotonic()))
                        )
                    except StopAsyncIteration:
                        break
                    passed = True
                    limit = settings.provider_timeout_llm_s
                    yield item
                g.ok()
                return
            except TimeoutError:
                error = ProviderError(
                    f"llm did not {'finish' if passed else 'start answering'} within "
                    f"{settings.provider_timeout_llm_s if passed else settings.provider_timeout_llm_first_s:g} s"  # noqa: E501
                )
            except ProviderError as e:
                error = e
            finally:
                await _close(stream)
            g.fail(str(error))
            if passed or attempt == attempts - 1 or g.state == "open":
                raise error
            await asyncio.sleep(settings.provider_retry_backoff_s * (2**attempt))


async def _close(stream: Any) -> None:
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception:  # noqa: BLE001, S110 - closing a failed stream must not raise
            pass
