"""Deterministic SIMULATED stand-ins for the AI providers (no network, no cost).

Used by the tests (tests/fakes.py re-exports them) and, with FAKE_PROVIDERS=true, by the running
backend for rehearsals of the server stack. They are never presented as real results: the
backend logs "SIMULATED providers (FAKE_PROVIDERS=true)" and /health (admin view) shows it.

With FAKE_PROVIDERS=true the fakes are wrapped exactly like the real adapters (resilience.py),
so every fake call DOES count against DAILY_PAID_CALL_LIMIT and goes through the same per-IP
rate limits: a rehearsal can trigger the cost guard without spending OpenAI credit.
"""

import hashlib
import io
import json
import math
import re
import wave

from app.services.providers import LLMResult

DIM = 1536


class FakeEmbedder:
    """Bag-of-words hashing embedder: texts sharing words get similar vectors."""

    model = "fake-embedder"
    dim = DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * DIM
            for word in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


class FakeLLM:
    """Answers with the first passage's first line; records what it was sent."""

    model = "fake-llm"

    def __init__(self, reply: dict | None = None) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def complete_json(self, instructions: str, user_input: str) -> LLMResult:
        self.calls.append((instructions, user_input))
        if self.reply is not None:
            data = self.reply
        else:
            first = re.search(r"\[1\] \(.*?\)\n(.*)", user_input)
            data = {"answer": first.group(1) if first else "", "found": bool(first), "used": [1]}
        return LLMResult(data=json.loads(json.dumps(data)), model=self.model, ms=1)


class FakeStreamingLLM(FakeLLM):
    """Streams its JSON reply in small chunks (like OpenAI streaming), then the parsed result.

    `log` records "llm:done" when the whole reply has been produced, so a test can check that
    speech started earlier."""

    model = "fake-streaming-llm"

    def __init__(self, reply: dict | None = None, chunk: int = 7, log: list | None = None) -> None:
        super().__init__(reply)
        self.chunk = chunk
        self.log = log if log is not None else []

    async def stream_json(self, instructions: str, user_input: str):  # noqa: ANN201
        import asyncio

        result = await self.complete_json(instructions, user_input)
        raw = json.dumps(result.data, ensure_ascii=False)
        for i in range(0, len(raw), self.chunk):
            yield raw[i : i + self.chunk]
            for _ in range(5):  # let the speech tasks run, as they would between network chunks
                await asyncio.sleep(0)
        self.log.append("llm:done")
        yield result


class FakeSTT:
    """Returns the queued transcripts in order (one per utterance), then `default`."""

    model = "fake-stt"

    def __init__(self, transcripts: list[str] | None = None, default: str = "") -> None:
        self.transcripts = list(transcripts or [])
        self.default = default
        self.received: list[bytes] = []

    async def transcribe(self, audio: bytes, filename: str = "speech.wav"):  # noqa: ANN201
        from app.services.speech import STTResult

        self.received.append(audio)
        return STTResult(
            text=self.transcripts.pop(0) if self.transcripts else self.default,
            ms=5,
            model=self.model,
        )


class FakeTTS:
    """'mp3' bytes are just the text, so tests can read what would be spoken. With `wav=True`
    (FAKE_PROVIDERS=true) a short silent WAV instead, so a browser can play it."""

    model = "fake-tts"
    voice = "fake"

    def __init__(self, log: list | None = None, wav: bool = False) -> None:
        self.spoken: list[str] = []
        self.log = log if log is not None else []
        self.wav = wav

    async def synthesize(self, text: str, voice: str | None = None, cache: bool = False) -> bytes:
        self.spoken.append(text)
        self.log.append(f"tts:{text}")
        if self.wav:
            return silent_wav(0.3)
        return b"ID3" + text.encode()


def silent_wav(seconds: float, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(2) * int(seconds * sample_rate))
    return buf.getvalue()


# What FakeSTT hears in a rehearsal (FAKE_PROVIDERS=true): a fixed, harmless question.
REHEARSAL_TRANSCRIPT = "شو هي هندسة الأنظمة الذكية؟"
