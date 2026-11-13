"""Development preview of the pages with FAKE AI providers (no OpenAI call, no cost).

    docker compose run --rm -p 8001:8000 backend uvicorn tests.preview_app:app --host 0.0.0.0 --port 8000

It is the real app (same pages, same protocol, same database unless DATABASE_URL points to a
scratch one), but the language model, speech-to-text and text-to-speech are stand-ins: answers
repeat the first retrieved passage and the "voice" is a soft test tone whose length follows the
text. Use it to check screens and timing of scenes. NEVER use it for a demo or a measurement.
"""

import math
import struct

from app.main import app
from app.services.providers import get_embedder, get_llm
from app.services.speech import STREAM_SAMPLE_RATE, get_stt, get_tts, pcm16_to_wav
from tests.fakes import FakeEmbedder, FakeStreamingLLM, FakeSTT

SECONDS_PER_WORD = 0.33


def _tone(text: str) -> bytes:
    """PCM16 mono: a quiet tone with a syllable-like pulse, about as long as the spoken text."""
    n = int(STREAM_SAMPLE_RATE * max(0.6, len(text.split()) * SECONDS_PER_WORD))
    out = bytearray()
    for i in range(n):
        t = i / STREAM_SAMPLE_RATE
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * 3.2 * t)
        out += struct.pack("<h", int(5000 * pulse * math.sin(2 * math.pi * 220 * t)))
    return bytes(out)


class PreviewTTS:
    """Test tone instead of a voice. WAV bytes where the app expects mp3 (browsers accept it)."""

    model = "preview-tone"
    voice = "tone"
    can_stream = True

    def cache_key(self, text: str, voice: str | None = None) -> str:
        return f"preview-{abs(hash(text)) % 10**12}"

    def is_cached(self, text: str, voice: str | None = None) -> bool:
        return True

    async def synthesize(self, text: str, voice: str | None = None, cache: bool = False) -> bytes:
        return pcm16_to_wav(_tone(text), STREAM_SAMPLE_RATE)

    async def stream_pcm(self, text: str, voice: str | None = None):  # noqa: ANN201
        import asyncio

        pcm = _tone(text)
        for i in range(0, len(pcm), 4800):
            yield pcm[i : i + 4800]
            await asyncio.sleep(0.02)  # arrives faster than real time, like the provider


app.dependency_overrides[get_llm] = lambda: FakeStreamingLLM()
app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
app.dependency_overrides[get_stt] = lambda: FakeSTT()
app.dependency_overrides[get_tts] = lambda: PreviewTTS()
