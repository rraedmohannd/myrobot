"""Deterministic stand-ins for the AI providers (tests never call OpenAI).

The provider fakes live in app/services/fake_providers.py (also used with FAKE_PROVIDERS=true);
they are re-exported here so the tests keep importing them from tests.fakes."""

from app.services.fake_providers import (  # noqa: F401
    DIM,
    FakeEmbedder,
    FakeLLM,
    FakeStreamingLLM,
    FakeSTT,
    FakeTTS,
)


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
