"""Deterministic stand-ins for the AI providers (tests never call OpenAI)."""

import hashlib
import json
import math
import re

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


class FakeSTT:
    """Returns the queued transcripts in order (one per utterance)."""

    model = "fake-stt"

    def __init__(self, transcripts: list[str] | None = None) -> None:
        self.transcripts = list(transcripts or [])
        self.received: list[bytes] = []

    async def transcribe(self, audio: bytes, filename: str = "speech.wav"):  # noqa: ANN201
        from app.services.speech import STTResult

        self.received.append(audio)
        return STTResult(text=self.transcripts.pop(0) if self.transcripts else "", ms=5, model=self.model)


class FakeTTS:
    """'mp3' bytes are just the text, so tests can read what would be spoken."""

    model = "fake-tts"
    voice = "fake"

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def synthesize(self, text: str, voice: str | None = None, cache: bool = False) -> bytes:
        self.spoken.append(text)
        return b"ID3" + text.encode()


class OneSession:
    """Stands in for async_sessionmaker: always yields the same (rolled-back) test session."""

    def __init__(self, session) -> None:  # noqa: ANN001
        self.session = session

    def __call__(self) -> "OneSession":
        return self

    async def __aenter__(self):  # noqa: ANN204
        return self.session

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeSpeakerEmbedder:
    """Returns queued voiceprints in order (None = too short / no voice)."""

    available = True

    def __init__(self, vectors: list[list[float] | None]) -> None:
        self.vectors = list(vectors)

    def embed(self, pcm16: bytes, sample_rate: int) -> list[float] | None:
        return self.vectors.pop(0) if self.vectors else None
