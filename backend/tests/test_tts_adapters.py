"""Sprint 4 P0.2 (sprint 3 S5b): the `openai_realtime` and `elevenlabs` TTS adapters.

No network: the Realtime adapter talks to a fake WebSocket that replays server events in the
shape the OpenAI Realtime API documents, the ElevenLabs adapter to an httpx mock transport.
ElevenLabs is never called for real (it costs the student's credits).
"""

import base64
import io
import json
import wave

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.services.providers import ProviderError
from app.services.resilience import ResilientTTS
from app.services.speech import (
    STREAM_SAMPLE_RATE,
    CachedTTS,
    OpenAITTS,
    audio_media_type,
    get_tts,
    make_tts,
)
from app.services.tts_adapters import (
    READ_ALOUD,
    ElevenLabsTTS,
    OpenAIRealtimeTTS,
    said_as_written,
)

TEXT = "أهلاً وسهلاً فيك بكلية الهندسة والتصميم"
PCM = [bytes([i]) * 4800 for i in (1, 2, 3)]  # three 100 ms chunks


@pytest.fixture
def env(monkeypatch):
    """Settings from the given environment variables for one test."""

    def set_env(**values: str) -> Settings:
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        return get_settings()

    yield set_env
    monkeypatch.undo()
    get_settings.cache_clear()


# ---------------- config ----------------
def test_default_tts_provider_stays_openai(monkeypatch) -> None:
    for name in ("TTS_PROVIDER", "TTS_MODEL", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None)
    assert s.tts_provider == "openai" and s.tts_model == "gpt-4o-mini-tts"
    assert s.tts_realtime_model == "gpt-realtime-2.1-mini"
    assert s.elevenlabs_api_key is None and s.elevenlabs_voice_id == ""


def test_elevenlabs_key_is_secret(monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-test-not-real")
    s = Settings(_env_file=None)
    assert "xi-test-not-real" not in repr(s)
    assert s.elevenlabs_api_key.get_secret_value() == "xi-test-not-real"


def test_provider_is_selected_by_config(env) -> None:
    env(TTS_PROVIDER="openai")
    assert isinstance(make_tts(), OpenAITTS)
    env(TTS_PROVIDER="openai_realtime")
    assert isinstance(make_tts(), OpenAIRealtimeTTS)
    env(TTS_PROVIDER="elevenlabs", ELEVENLABS_VOICE_ID="voice-abc")
    tts = get_tts()  # the dependency: cache -> resilience guard -> adapter
    assert isinstance(tts, CachedTTS) and isinstance(tts.inner, ResilientTTS)
    assert isinstance(tts.inner.inner, ElevenLabsTTS)
    assert (tts.model, tts.voice) == ("eleven_multilingual_v2", "voice-abc")
    with pytest.raises(ProviderError):
        make_tts("no-such-provider")


def test_unknown_provider_is_refused_by_the_settings(monkeypatch) -> None:
    monkeypatch.setenv("TTS_PROVIDER", "somebody-else")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


# ---------------- OpenAI Realtime ----------------
def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def realtime_events(transcript: str = TEXT, status: str = "completed") -> list[dict]:
    """Server events of one audio response, in the documented shapes and order."""
    ids = {"response_id": "resp_1", "item_id": "item_1", "output_index": 0, "content_index": 0}
    return [
        {
            "type": "session.created",
            "event_id": "e1",
            "session": {"id": "sess_1", "type": "realtime"},
        },
        {
            "type": "session.updated",
            "event_id": "e2",
            "session": {"id": "sess_1", "type": "realtime"},
        },
        {
            "type": "response.created",
            "event_id": "e3",
            "response": {"id": "resp_1", "status": "in_progress"},
        },
        {"type": "response.output_item.added", "event_id": "e4", "response_id": "resp_1"},
        {
            "type": "response.output_audio_transcript.delta",
            "event_id": "e5",
            **ids,
            "delta": "أهلاً",
        },
        *[
            {
                "type": "response.output_audio.delta",
                "event_id": f"a{i}",
                **ids,
                "delta": _b64(chunk),
            }
            for i, chunk in enumerate(PCM)
        ],
        {"type": "response.output_audio.done", "event_id": "e6", **ids},
        {
            "type": "response.output_audio_transcript.done",
            "event_id": "e7",
            **ids,
            "transcript": transcript,
        },
        {
            "type": "response.done",
            "event_id": "e8",
            "response": {
                "id": "resp_1",
                "status": status,
                "status_details": None
                if status == "completed"
                else {"type": status, "reason": "x"},
            },
        },
        {"type": "never.read", "event_id": "e9"},  # the adapter stops at response.done
    ]


class FakeRealtimeSocket:
    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.sent: list[dict] = []
        self.read = 0
        self.closed = False

    async def __aenter__(self) -> "FakeRealtimeSocket":
        return self

    async def __aexit__(self, *exc) -> None:
        self.closed = True

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def __aiter__(self) -> "FakeRealtimeSocket":
        return self

    async def __anext__(self) -> str:
        if self.read >= len(self.events):
            raise StopAsyncIteration
        self.read += 1
        return json.dumps(self.events[self.read - 1], ensure_ascii=False)


def realtime(events: list[dict]) -> tuple[OpenAIRealtimeTTS, FakeRealtimeSocket]:
    socket = FakeRealtimeSocket(events)
    return OpenAIRealtimeTTS(connect=lambda: socket), socket


async def test_realtime_sends_the_text_and_returns_wav(env) -> None:
    env(TTS_VOICE="coral", TTS_SPEED="1.12")
    tts, socket = realtime(realtime_events())
    audio = await tts.synthesize(TEXT)

    session, response = socket.sent
    assert session["type"] == "session.update"
    s = session["session"]
    assert s["type"] == "realtime" and s["output_modalities"] == ["audio"]
    assert s["audio"]["output"] == {
        "voice": "coral", "format": {"type": "audio/pcm", "rate": 24000}, "speed": 1.12,
    }  # fmt: skip
    # the accent and pace instructions travel with the order to read word for word
    assert s["instructions"].startswith(READ_ALOUD) and "Jordanian" in s["instructions"]
    assert "slightly fast" in s["instructions"]
    assert response["type"] == "response.create"
    r = response["response"]
    assert r["conversation"] == "none" and r["output_modalities"] == ["audio"]
    assert r["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": TEXT}]}
    ]

    assert audio_media_type(audio) == "audio/wav"
    with wave.open(io.BytesIO(audio)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        assert w.readframes(w.getnframes()) == b"".join(PCM)
    assert socket.closed and socket.events[socket.read - 1]["type"] == "response.done"
    assert tts.last_transcript == TEXT
    assert (tts.provider, tts.model) == ("openai_realtime", "gpt-realtime-2.1-mini")


async def test_realtime_voice_argument_and_default_speed(env) -> None:
    env(TTS_SPEED="1.0")
    tts, socket = realtime(realtime_events())
    await tts.synthesize(TEXT, voice="marin")
    output = socket.sent[0]["session"]["audio"]["output"]
    assert output["voice"] == "marin" and "speed" not in output  # the default is not sent

    env(TTS_SPEED="2.0")
    tts, socket = realtime(realtime_events())
    await tts.synthesize(TEXT)
    assert socket.sent[0]["session"]["audio"]["output"]["speed"] == 1.5  # the API's maximum


async def test_realtime_streams_pcm_chunks_through_the_cache_wrapper(env) -> None:
    env()
    tts, _ = realtime(realtime_events())
    wrapped = CachedTTS(ResilientTTS(tts))
    assert wrapped.can_stream
    chunks = [c async for c in wrapped.stream_pcm(TEXT)]
    assert chunks == PCM and STREAM_SAMPLE_RATE == 24000
    with pytest.raises(ProviderError, match="pcm only"):
        [c async for c in tts.stream(TEXT, response_format="mp3")]


async def test_realtime_error_event_becomes_provider_error(env) -> None:
    env()
    error = {
        "type": "error", "event_id": "e1",
        "error": {"type": "invalid_request_error", "code": "model_not_found",
                  "message": "The model `gpt-realtime-2.1-mini` does not exist", "param": None},
    }  # fmt: skip
    tts, socket = realtime([realtime_events()[0], error])
    with pytest.raises(ProviderError, match="model_not_found: The model"):
        await tts.synthesize(TEXT)
    assert socket.closed


async def test_realtime_failed_or_empty_response_becomes_provider_error(env) -> None:
    env()
    tts, _ = realtime(realtime_events(status="failed"))
    with pytest.raises(ProviderError, match="response failed"):
        await tts.synthesize(TEXT)
    silent = [e for e in realtime_events() if e["type"] != "response.output_audio.delta"]
    tts, _ = realtime(silent)
    with pytest.raises(ProviderError, match="no audio"):
        await tts.synthesize(TEXT)


async def test_realtime_without_key_fails_cleanly(env, monkeypatch) -> None:
    env(OPENAI_API_KEY="")
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        await OpenAIRealtimeTTS().synthesize(TEXT)


async def test_realtime_warns_when_the_model_did_not_read_the_text(env, caplog) -> None:
    env()
    tts, _ = realtime(realtime_events(transcript="أهلاً! كيف بقدر أساعدك؟"))
    with caplog.at_level("WARNING"):
        await tts.synthesize(TEXT)
    assert "transcript differs" in caplog.text
    caplog.clear()
    tts, _ = realtime(realtime_events(transcript="أهلا وسهلا فيك بكلية الهندسة والتصميم."))
    with caplog.at_level("WARNING"):
        await tts.synthesize(TEXT)
    assert "transcript differs" not in caplog.text  # spelling marks and punctuation are ignored


def test_said_as_written() -> None:
    assert said_as_written("The program requires 162 hours.", "the program requires 162 hours")
    assert not said_as_written("The program requires 162 hours.", "The program requires 160 hours.")
    assert not said_as_written("مرحبا", "")


# ---------------- ElevenLabs (fake transport only) ----------------
def elevenlabs(handler) -> ElevenLabsTTS:
    return ElevenLabsTTS(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_elevenlabs_request_shape_and_mp3(env) -> None:
    env(ELEVENLABS_API_KEY="xi-test-not-real", ELEVENLABS_VOICE_ID="voice-abc", TTS_SPEED="1.12")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ID3fake-mp3", headers={"content-type": "audio/mpeg"})

    tts = elevenlabs(handler)
    audio = await tts.synthesize(TEXT)
    assert audio == b"ID3fake-mp3" and audio_media_type(audio) == "audio/mpeg"
    [request] = seen
    assert request.method == "POST"
    assert request.url.path == "/v1/text-to-speech/voice-abc"
    assert request.url.host == "api.elevenlabs.io"
    assert request.url.params["output_format"] == "mp3_44100_128"
    assert request.headers["xi-api-key"] == "xi-test-not-real"
    assert json.loads(request.content) == {
        "text": TEXT, "model_id": "eleven_multilingual_v2", "voice_settings": {"speed": 1.12},
    }  # fmt: skip
    assert (tts.provider, tts.model, tts.voice, tts.instructions) == (
        "elevenlabs", "eleven_multilingual_v2", "voice-abc", "",
    )  # fmt: skip

    await tts.synthesize(TEXT, voice="other-voice")
    assert seen[-1].url.path == "/v1/text-to-speech/other-voice"


async def test_elevenlabs_stream_asks_for_pcm_24k(env) -> None:
    env(ELEVENLABS_API_KEY="xi-test-not-real", ELEVENLABS_VOICE_ID="voice-abc", TTS_SPEED="1.0")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"".join(PCM))

    tts = elevenlabs(handler)
    chunks = [c async for c in CachedTTS(tts).stream_pcm(TEXT)]
    assert b"".join(chunks) == b"".join(PCM)
    assert seen[0].url.path == "/v1/text-to-speech/voice-abc/stream"
    assert seen[0].url.params["output_format"] == "pcm_24000"
    assert "voice_settings" not in json.loads(seen[0].content)  # speed 1.0 is not sent


async def test_elevenlabs_errors_become_provider_errors(env) -> None:
    env(ELEVENLABS_API_KEY="xi-test-not-real", ELEVENLABS_VOICE_ID="voice-abc")
    detail = {"detail": {"status": "quota_exceeded", "message": "This request exceeds your quota."}}
    tts = elevenlabs(lambda request: httpx.Response(401, json=detail))
    with pytest.raises(ProviderError, match="HTTP 401.*quota_exceeded"):
        await tts.synthesize(TEXT)
    with pytest.raises(ProviderError, match="HTTP 401"):
        [c async for c in tts.stream(TEXT)]

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(ProviderError, match="ConnectError"):
        await elevenlabs(offline).synthesize(TEXT)


async def test_elevenlabs_needs_key_and_voice_and_sends_nothing_without_them(env) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"x")

    env(ELEVENLABS_API_KEY="", ELEVENLABS_VOICE_ID="voice-abc")
    with pytest.raises(ProviderError, match="ELEVENLABS_API_KEY"):
        await elevenlabs(handler).synthesize(TEXT)
    env(ELEVENLABS_API_KEY="xi-test-not-real", ELEVENLABS_VOICE_ID="")
    with pytest.raises(ProviderError, match="ELEVENLABS_VOICE_ID"):
        await elevenlabs(handler).synthesize(TEXT)
    assert calls == []


# ---------------- the fixed-phrase cache never mixes providers ----------------
class SameSettingsTTS:
    """Different providers that happen to share model, voice, instructions and speed."""

    model, voice, instructions, speed = "m1", "v1", "calm", 1.0

    def __init__(self, provider: str | None) -> None:
        if provider is not None:
            self.provider = provider
        self.calls = 0

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        self.calls += 1
        return f"{getattr(self, 'provider', 'none')}:{text}".encode()


async def test_cache_key_covers_the_provider(tmp_path) -> None:
    import hashlib

    plain = CachedTTS(SameSettingsTTS(None), tmp_path)
    openai = CachedTTS(SameSettingsTTS("openai"), tmp_path)
    rt = CachedTTS(ResilientTTS(SameSettingsTTS("openai_realtime")), tmp_path)
    eleven = CachedTTS(SameSettingsTTS("elevenlabs"), tmp_path)

    # the default provider keeps the key it had before sprint 4 (cached audio stays valid)
    legacy = hashlib.sha256("m1|v1|calm|speed=1.000|مرحبا".encode()).hexdigest()[:32]
    assert plain.cache_key("مرحبا") == openai.cache_key("مرحبا") == legacy
    assert len({t.cache_key("مرحبا") for t in (openai, rt, eleven)}) == 3

    first = await openai.synthesize("مرحبا", cache=True)
    assert not rt.is_cached("مرحبا") and not eleven.is_cached("مرحبا")
    assert await rt.synthesize("مرحبا", cache=True) == "openai_realtime:مرحبا".encode()
    assert await eleven.synthesize("مرحبا", cache=True) == "elevenlabs:مرحبا".encode()
    assert await openai.synthesize("مرحبا", cache=True) == first  # each one replays only its own
    assert await rt.synthesize("مرحبا", cache=True) == "openai_realtime:مرحبا".encode()
    assert (openai.inner.calls, rt.inner.inner.calls, eleven.inner.calls) == (1, 1, 1)


def test_cache_keys_of_the_real_adapters_differ_in_every_part(env, tmp_path) -> None:
    """With the real settings: provider, model, voice and instructions all differ, and each
    adapter exposes the instructions and speed that go into the key."""
    env(ELEVENLABS_API_KEY="xi-test-not-real", ELEVENLABS_VOICE_ID="voice-abc", TTS_SPEED="1.12")
    adapters = [make_tts(p) for p in ("openai", "openai_realtime", "elevenlabs")]
    for adapter in adapters:
        assert adapter.speed == 1.12 and isinstance(adapter.instructions, str)
    keys = {CachedTTS(ResilientTTS(a), tmp_path).cache_key("مرحبا") for a in adapters}
    assert len(keys) == 3
    faster = make_tts("openai_realtime")
    faster.speed = 1.3
    assert CachedTTS(faster, tmp_path).cache_key("مرحبا") not in keys


async def test_tts_endpoint_labels_wav_audio(client, env, tmp_path) -> None:
    from app.main import app

    env()
    tts, _ = realtime(realtime_events(transcript="مرحبا"))
    app.dependency_overrides[get_tts] = lambda: CachedTTS(tts, tmp_path)
    try:
        r = await client.post("/api/tts", json={"text": "مرحبا"})
    finally:
        app.dependency_overrides.pop(get_tts, None)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"
