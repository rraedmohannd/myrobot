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
