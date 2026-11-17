"""AI provider adapters (CLAUDE.md rule 7). The rest of the code only sees these interfaces.

- Embedder: text -> vectors (semantic search)
- LLM: instructions + input -> JSON object (grounded answer), optionally streamed as text deltas

OpenAI implementations are the defaults; tests use fakes (tests/fakes.py).
"""

import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

from app.core.config import get_settings


class ProviderError(RuntimeError):
    """A provider failed or is not configured (shown to the user as 'service unavailable')."""


# ---------------- interfaces ----------------
class Embedder(Protocol):
    model: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    ms: int
    usage: dict[str, int] = field(default_factory=dict)


class LLM(Protocol):
    """`complete_json` is required. Adapters may also offer

        stream_json(instructions, user_input) -> AsyncIterator[str | LLMResult]

    which yields the raw output text as it is generated and, last, the parsed LLMResult.
    Callers use `stream_json()` below, which falls back to `complete_json` when the adapter
    cannot stream, so every provider stays swappable (rule 7)."""

    model: str

    async def complete_json(self, instructions: str, user_input: str) -> LLMResult: ...


async def stream_json(
    llm: LLM, instructions: str, user_input: str, stream: bool = True
) -> AsyncIterator[str | LLMResult]:
    """Text deltas, then the final LLMResult. Without streaming: just the LLMResult."""
    native = getattr(llm, "stream_json", None) if stream else None
    if native is None:
        yield await llm.complete_json(instructions, user_input)
        return
    async for item in native(instructions, user_input):
        yield item


# ---------------- OpenAI ----------------
@lru_cache
def _openai_client():  # noqa: ANN202
    """One client for the whole process: its connections stay open between turns, so only the
    very first request pays for the TLS handshake."""
    settings = get_settings()
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if not key:
        raise ProviderError("OPENAI_API_KEY is not set in .env")
    from openai import AsyncOpenAI  # imported lazily so tests never need the package configured

    return AsyncOpenAI(api_key=key, timeout=30.0, max_retries=2)


class OpenAIEmbedder:
    def __init__(self) -> None:
        settings = get_settings()
        self.model = settings.embedding_model
        self.dim = settings.embedding_dim
        self._client_obj = None

    @property
    def _client(self):  # noqa: ANN202  (created on first use, so a missing key is a ProviderError)
        if self._client_obj is None:
            self._client_obj = _openai_client()
        return self._client_obj

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), 64):
                resp = await self._client.embeddings.create(
                    model=self.model, input=texts[i : i + 64], dimensions=self.dim
                )
                out.extend(d.embedding for d in resp.data)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"embedding failed: {type(e).__name__}: {e}") from e
        return out


def parse_json_object(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ProviderError(f"model did not return JSON: {raw[:200]!r}") from None
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ProviderError("model returned JSON that is not an object")
    return data


CUT_OFF = "LLM answer was cut off (raise LLM_MAX_OUTPUT_TOKENS or set LLM_REASONING_EFFORT=low)"


class OpenAILLM:
    """Uses the Responses API in JSON mode, with or without streaming."""

    def __init__(self) -> None:
        settings = get_settings()
        self.model = settings.llm_model
        self.max_tokens = settings.llm_max_output_tokens
        self.reasoning_effort = settings.llm_reasoning_effort
        self._client_obj = None

    @property
    def _client(self):  # noqa: ANN202
        if self._client_obj is None:
            self._client_obj = _openai_client()
        return self._client_obj

    def _kwargs(self, instructions: str, user_input: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": user_input,
            "max_output_tokens": self.max_tokens,
            "text": {"format": {"type": "json_object"}},
        }
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        return kwargs

    @staticmethod
    def _usage(resp: Any) -> dict[str, int]:
        if getattr(resp, "usage", None) is None:
            return {}
        return {
            "input_tokens": getattr(resp.usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(resp.usage, "output_tokens", 0) or 0,
        }

    async def complete_json(self, instructions: str, user_input: str) -> LLMResult:
        start = time.perf_counter()
        try:
            resp = await self._client.responses.create(**self._kwargs(instructions, user_input))
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"LLM call failed: {type(e).__name__}: {e}") from e
        ms = int((time.perf_counter() - start) * 1000)
        if getattr(resp, "status", "completed") == "incomplete":
            raise ProviderError(CUT_OFF)
        return LLMResult(
            data=parse_json_object(resp.output_text),
            model=self.model,
            ms=ms,
            usage=self._usage(resp),
        )

    async def stream_json(
        self, instructions: str, user_input: str
    ) -> AsyncIterator[str | LLMResult]:
        """Same request with `stream=True`: yields output text deltas, then the parsed result."""
        start = time.perf_counter()
        parts: list[str] = []
        final: Any = None
        try:
            stream = await self._client.responses.create(
                **self._kwargs(instructions, user_input), stream=True
            )
            async for event in stream:
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    parts.append(event.delta)
                    yield event.delta
                elif kind in ("response.completed", "response.incomplete", "response.failed"):
                    final = event.response
                elif kind == "error":
                    raise ProviderError(f"LLM stream error: {getattr(event, 'message', '')}")
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"LLM call failed: {type(e).__name__}: {e}") from e
        ms = int((time.perf_counter() - start) * 1000)
        status = getattr(final, "status", "completed")
        if status == "incomplete":
            raise ProviderError(CUT_OFF)
        if status == "failed":
            raise ProviderError(f"LLM call failed: {getattr(final, 'error', None)}")
        yield LLMResult(
            data=parse_json_object("".join(parts)),
            model=self.model,
            ms=ms,
            usage=self._usage(final),
        )


# ---------------- FastAPI dependencies (overridden in tests) ----------------
def get_embedder() -> Embedder:
    from app.services.resilience import ResilientEmbedder  # time limit, retries, circuit breaker

    return ResilientEmbedder(OpenAIEmbedder())


def get_llm() -> LLM:
    # Only OpenAI is implemented; LLM_PROVIDER=anthropic would need an AnthropicLLM adapter here.
    from app.services.resilience import ResilientLLM

    return ResilientLLM(OpenAILLM())
