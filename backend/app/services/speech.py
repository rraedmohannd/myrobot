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


def split_for_speech(text: str) -> list[str]:
    """Two pieces: a SHORT first piece (synthesized fast, so playback starts early) and the rest.

    The first piece is the first sentence, cut at a comma (or after ~10 words) when it is long.
    The rest is synthesized in parallel while the first piece is already playing.
    """
    text = text.strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?؟])\s+", text) if s.strip()]
    if not sentences:
        return []
    first, rest = sentences[0], " ".join(sentences[1:])
    words = first.split()
    if len(words) > FIRST_PIECE_MAX_WORDS + 2:
        cut = None
        for i, w in enumerate(words[: FIRST_PIECE_MAX_WORDS + 2], start=1):
            if i >= 3 and w.endswith(("،", ",", "؛", ";", ":")):
                cut = i
                break
        cut = cut or FIRST_PIECE_MAX_WORDS
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


class OpenAITTS:
    def __init__(self) -> None:
        s = get_settings()
        self.model = s.tts_model
        self.voice = s.tts_voice
        self.instructions = s.tts_instructions
        self._client_obj = None

    @property
    def _client(self):  # noqa: ANN202
        if self._client_obj is None:
            self._client_obj = _openai_client()
        return self._client_obj

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        kwargs = {"model": self.model, "voice": voice or self.voice, "input": text,
                  "response_format": "mp3"}  # fmt: skip
        if self.instructions and "tts" in self.model and "gpt" in self.model:
            kwargs["instructions"] = self.instructions  # style/accent (gpt-*-tts models only)
        try:
            resp = await self._client.audio.speech.create(**kwargs)
            data = resp.content if isinstance(getattr(resp, "content", None), bytes) else await resp.aread()
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"text-to-speech failed: {type(e).__name__}: {e}") from e
        return data


class CachedTTS:
    """Wraps a TTS; `cache=True` stores the mp3 on disk keyed by model+voice+instructions+text."""

    def __init__(self, inner: TTS, cache_dir: Path = CACHE_DIR) -> None:
        self.inner = inner
        self.model = inner.model
        self.voice = inner.voice
        self.cache_dir = cache_dir
        self._lock = asyncio.Lock()

    def _path(self, text: str, voice: str) -> Path:
        extra = getattr(self.inner, "instructions", "")
        key = hashlib.sha256(f"{self.model}|{voice}|{extra}|{text}".encode()).hexdigest()[:32]
        return self.cache_dir / f"{key}.mp3"

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
    return OpenAISTT()


def get_tts() -> CachedTTS:
    return CachedTTS(OpenAITTS())
