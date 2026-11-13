"""Speech adapters (CLAUDE.md rule 7): STT (audio -> text) and TTS (text -> mp3).

OpenAI implementations by default; tests use fakes. Fixed phrases (greeting, follow-up,
goodbye...) go through a disk cache so they cost nothing after the first time and play instantly.
"""

import asyncio
import hashlib
import io
import re
import time
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.services.providers import ProviderError, _openai_client

VOICES = (
    "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer",
    "verse", "marin", "cedar",
)  # fmt: skip
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "tts_cache"


@dataclass
class STTResult:
    text: str
    ms: int
    model: str


class STT(Protocol):
    model: str

    async def transcribe(self, audio: bytes, filename: str = "speech.wav") -> STTResult: ...


class TTS(Protocol):
    model: str
    voice: str

    async def synthesize(self, text: str, voice: str | None = None) -> bytes: ...


# ---------------- helpers ----------------
def pcm16_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


FIRST_PIECE_MAX_WORDS = 10


def split_for_speech(text: str, max_words: int = FIRST_PIECE_MAX_WORDS) -> list[str]:
    """Two pieces: a SHORT first piece (synthesized fast, so playback starts early) and the rest.

    The first piece is the first sentence, cut at a comma (or after `max_words` words) when it is
    long. The rest is synthesized in parallel while the first piece is already playing.
    """
    text = text.strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?؟])\s+", text) if s.strip()]
    if not sentences:
        return []
    first, rest = sentences[0], " ".join(sentences[1:])
    words = first.split()
    if len(words) > max_words + 2:
        cut = None
        for i, w in enumerate(words[: max_words + 2], start=1):
            if i >= 3 and w.endswith(("،", ",", "؛", ";", ":")):
                cut = i
                break
        cut = cut or max_words
        first, rest = " ".join(words[:cut]), " ".join([*words[cut:], rest]).strip()
    return [first, rest] if rest else [first]


# ---------------- OpenAI ----------------
class OpenAISTT:
    def __init__(self) -> None:
        s = get_settings()
        self.model = s.stt_model
        self.prompt = s.stt_prompt
        self._client_obj = None

    @property
    def _client(self):  # noqa: ANN202
        if self._client_obj is None:
            self._client_obj = _openai_client()
        return self._client_obj

    async def transcribe(self, audio: bytes, filename: str = "speech.wav") -> STTResult:
        start = time.perf_counter()
        kwargs = {"model": self.model, "file": (filename, audio)}
        try:
            try:
                resp = await self._client.audio.transcriptions.create(**kwargs, prompt=self.prompt)
            except Exception as e:  # noqa: BLE001  some models reject `prompt`: retry without it
                if "prompt" not in str(e).lower():
                    raise
                resp = await self._client.audio.transcriptions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"speech-to-text failed: {type(e).__name__}: {e}") from e
        text = getattr(resp, "text", resp if isinstance(resp, str) else "")
        return STTResult(text=(text or "").strip(), ms=int((time.perf_counter() - start) * 1000),
                         model=self.model)  # fmt: skip


STREAM_SAMPLE_RATE = 24000  # OpenAI "pcm": raw 16-bit little-endian mono at 24 kHz
STREAM_CHUNK_BYTES = 4800  # 100 ms per message


class OpenAITTS:
    def __init__(self) -> None:
        s = get_settings()
        self.model = s.tts_model
        self.voice = s.tts_voice
        self.instructions = s.tts_instructions
        self.speed = s.tts_speed
        self._client_obj = None

    @property
    def _client(self):  # noqa: ANN202
        if self._client_obj is None:
            self._client_obj = _openai_client()
        return self._client_obj

    def _kwargs(self, text: str, voice: str | None, response_format: str) -> dict:
        kwargs = {"model": self.model, "voice": voice or self.voice, "input": text,
                  "response_format": response_format}  # fmt: skip
        if self.instructions and "tts" in self.model and "gpt" in self.model:
            kwargs["instructions"] = self.instructions  # style/accent (gpt-*-tts models only)
        if self.speed and abs(self.speed - 1.0) > 1e-6:
            kwargs["speed"] = self.speed
        return kwargs

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        try:
            resp = await self._client.audio.speech.create(**self._kwargs(text, voice, "mp3"))
            data = resp.content if isinstance(getattr(resp, "content", None), bytes) else await resp.aread()
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"text-to-speech failed: {type(e).__name__}: {e}") from e
        return data

    async def stream(
        self, text: str, voice: str | None = None, response_format: str = "mp3"
    ) -> AsyncIterator[bytes]:
        """The same audio in chunks, as the provider produces it (chunked transfer)."""
        try:
            async with self._client.audio.speech.with_streaming_response.create(
                **self._kwargs(text, voice, response_format)
            ) as resp:
                async for chunk in resp.iter_bytes(4096):
                    if chunk:
                        yield chunk
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"text-to-speech failed: {type(e).__name__}: {e}") from e


class CachedTTS:
    """Wraps a TTS; `cache=True` stores the mp3 on disk. The key covers everything that changes
    the sound (model, voice, instructions, speed) and the text, so a new voice or pace never
    replays old audio: the phrases are simply generated again on first use."""

    def __init__(self, inner: TTS, cache_dir: Path = CACHE_DIR) -> None:
        self.inner = inner
        self.model = inner.model
        self.voice = inner.voice
        self.cache_dir = cache_dir
        self._lock = asyncio.Lock()

    def cache_key(self, text: str, voice: str | None = None) -> str:
        instructions = getattr(self.inner, "instructions", "")
        speed = float(getattr(self.inner, "speed", 1.0) or 1.0)
        raw = f"{self.model}|{voice or self.voice}|{instructions}|speed={speed:.3f}|{text}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _path(self, text: str, voice: str) -> Path:
        return self.cache_dir / f"{self.cache_key(text, voice)}.mp3"

    def is_cached(self, text: str, voice: str | None = None) -> bool:
        return self._path(text, voice or self.voice).exists()

    @property
    def can_stream(self) -> bool:
        return hasattr(self.inner, "stream")

    async def stream_pcm(self, text: str, voice: str | None = None) -> AsyncIterator[bytes]:
        """PCM16 mono at STREAM_SAMPLE_RATE, in chunks of whole samples (about 100 ms each),
        yielded while the provider is still generating. Not cached: it is for fresh answers."""
        buffer = b""
        async for chunk in self.inner.stream(text, voice or self.voice, response_format="pcm"):  # type: ignore[attr-defined]
            buffer += chunk
            while len(buffer) >= STREAM_CHUNK_BYTES:
                yield buffer[:STREAM_CHUNK_BYTES]
                buffer = buffer[STREAM_CHUNK_BYTES:]
        if len(buffer) >= 2:
            yield buffer[: len(buffer) - len(buffer) % 2]

    async def synthesize(self, text: str, voice: str | None = None, cache: bool = False) -> bytes:
        voice = voice or self.voice
        if not cache:
            return await self.inner.synthesize(text, voice)
        path = self._path(text, voice)
        if path.exists():
            return path.read_bytes()
        data = await self.inner.synthesize(text, voice)
        async with self._lock:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return data


# ---------------- FastAPI dependencies (overridden in tests) ----------------
def get_stt() -> STT:
    from app.services.resilience import ResilientSTT  # time limit, retries, circuit breaker

    return ResilientSTT(OpenAISTT())


def get_tts() -> CachedTTS:
    from app.services.resilience import ResilientTTS

    return CachedTTS(ResilientTTS(OpenAITTS()))
