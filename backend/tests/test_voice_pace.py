"""Sprint 2 S1: speaking pace, TTS cache key, client settings in hello.ack, streamed answer audio.
Fakes only, no OpenAI."""

import base64

import pytest

from app.core.config import Settings, get_settings
from app.db.models import Chunk, Source
from app.services.dialogue import DialogueManager
from app.services.speech import STREAM_SAMPLE_RATE, CachedTTS, OpenAITTS, split_for_speech
from app.services.streaming import SpeechPieces
from tests.fakes import FakeEmbedder, FakeStreamingLLM, FakeSTT, FakeTTS, OneSession

UNIQUE = "qwarzlip"
GOOD = f"The {UNIQUE} program requires 162 credit hours."
BAD = "The fees are 987654 dinars per year."
LAST = "Graduates work in many fields."


class CountingTTS:
    """A TTS whose sound settings can be changed, to see what the cache does."""

    def __init__(self, model="m1", voice="v1", instructions="calm", speed=1.0) -> None:
        self.model, self.voice, self.instructions, self.speed = model, voice, instructions, speed
        self.calls = 0

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        self.calls += 1
        return f"{self.model}|{voice}|{self.instructions}|{self.speed}|{text}".encode()


async def test_cache_key_covers_model_voice_instructions_and_speed(tmp_path) -> None:
    base = CachedTTS(CountingTTS(), tmp_path)
    variants = [
        CachedTTS(CountingTTS(model="m2"), tmp_path),
        CachedTTS(CountingTTS(voice="v2"), tmp_path),
        CachedTTS(CountingTTS(instructions="lively, slightly fast"), tmp_path),
        CachedTTS(CountingTTS(speed=1.12), tmp_path),
    ]
    keys = {base.cache_key("مرحبا")} | {v.cache_key("مرحبا") for v in variants}
    assert len(keys) == 5  # every setting that changes the sound changes the key
    assert base.cache_key("مرحبا") != base.cache_key("مرحبا!") != base.cache_key("مرحبا", "v9")

    first = await base.synthesize("مرحبا", cache=True)
    assert base.is_cached("مرحبا") and await base.synthesize("مرحبا", cache=True) == first
    assert base.inner.calls == 1  # the second time comes from disk

    faster = variants[3]
    assert not faster.is_cached("مرحبا")  # the old, slower audio is never replayed
    audio = await faster.synthesize("مرحبا", cache=True)
    assert b"1.12" in audio and faster.inner.calls == 1
    assert len(list(tmp_path.glob("*.mp3"))) == 2


def test_tts_speed_and_pace_instructions_are_sent_to_the_provider() -> None:
    settings = get_settings()
    assert "slightly fast" in settings.tts_instructions and "Jordanian" in settings.tts_instructions
    tts = OpenAITTS()
    tts.speed = 1.12
    kwargs = tts._kwargs("نص", None, "mp3")
    assert kwargs["speed"] == 1.12 and kwargs["instructions"] == settings.tts_instructions
    tts.speed = 1.0
    assert "speed" not in tts._kwargs("نص", None, "mp3")  # the provider default is not sent


def test_pace_and_vad_settings_come_from_the_environment(monkeypatch) -> None:
    defaults = Settings(_env_file=None)
    assert defaults.tts_streaming is False  # experimental: not enabled without a measured gain
    assert defaults.speech_rate == 1.0 and defaults.tts_speed == 1.12
    assert defaults.vad_silence_ms == 700 and defaults.tts_first_piece_words == 6
    monkeypatch.setenv("SPEECH_RATE", "1.12")
    monkeypatch.setenv("VAD_SILENCE_MS", "900")
    changed = Settings(_env_file=None)
    assert changed.speech_rate == 1.12 and changed.vad_silence_ms == 900


async def test_hello_ack_carries_pace_and_vad_settings(db_session, monkeypatch) -> None:
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    monkeypatch.setenv("SPEECH_RATE", "1.2")
    monkeypatch.setenv("VAD_SILENCE_MS", "650")
    get_settings.cache_clear()
    try:
        dm = DialogueManager(send, OneSession(db_session), FakeEmbedder(), FakeStreamingLLM(), FakeSTT([]), FakeTTS())
        await dm.handle({"type": "hello", "robot_id": "test", "protocol_version": 1})
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    ack = sent[-1]
    assert ack["type"] == "hello.ack"
    assert ack["speech_rate"] == 1.2 and ack["vad_silence_ms"] == 650
    assert ack["audio_stream"] is False  # this client did not ask for PCM chunks


async def test_client_applies_rate_and_vad_silence(client) -> None:
    js = (await client.get("/static/robot-client.js")).text
    assert "m.speech_rate" in js and "preservesPitch = true" in js and "playbackRate = this.speechRate" in js
    assert "m.vad_silence_ms" in js and "this.silenceFrames >= this.silenceFramesEnd" in js
    assert "answer.audio.chunk" in js and "pcm16" in js


def test_first_piece_length_is_configurable() -> None:
    long = "خريجين هندسة الأنظمة الذكية بشتغلوا بمجالات زي تكنولوجيا المعلومات والاتصالات، والبنوك والرقابة المالية."
    first, rest = split_for_speech(long, 6)
    assert len(first.split()) == 6 and f"{first} {rest}" == long
    assert split_for_speech("جملة قصيرة من خمس كلمات فقط.", 6) == ["جملة قصيرة من خمس كلمات فقط."]
    sp = SpeechPieces(6)  # released as soon as the words after the cut have started
    assert sp.feed(" ".join(long.split()[:10]) + " ") == [first]


# ---------------- streamed answer audio ----------------
class StreamingFakeTTS(FakeTTS):
    """FakeTTS that can also stream: two PCM chunks per piece."""

    can_stream = True

    async def stream_pcm(self, text: str, voice: str | None = None):  # noqa: ANN201
        self.log.append(f"stream:{text}")
        yield b"\x01\x00" * 10
        yield text.encode("utf-16-le")


async def _seed(db_session) -> None:
    src = Source(url="test-doc://pace", title="Pace test", review_status="approved")
    db_session.add(src)
    await db_session.flush()
    db_session.add(Chunk(source_id=src.id, lang="en", review_status="approved", text=GOOD))
    await db_session.commit()


@pytest.fixture
def streaming_on(monkeypatch):
    """TTS_STREAMING=true for one test (the default is off)."""
    monkeypatch.setenv("TTS_STREAMING", "true")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


async def _ask(db_session, hello: dict, reply: dict, tts: FakeTTS) -> tuple[list[dict], DialogueManager]:
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    dm = DialogueManager(send, OneSession(db_session), FakeEmbedder(), FakeStreamingLLM(reply), FakeSTT([]), tts)
    await dm.handle({"type": "hello", **hello})
    await dm.handle({"type": "text.input", "text": f"{UNIQUE}?"})
    return sent, dm


async def test_answer_is_streamed_as_pcm_chunks_when_the_client_asks(db_session, streaming_on) -> None:
    await _seed(db_session)
    tts = StreamingFakeTTS()
    sent, dm = await _ask(db_session, {"audio_stream": ["pcm16"]},
                          {"answer": f"{GOOD} {LAST}", "found": True, "used": [1]}, tts)  # fmt: skip
    assert next(m for m in sent if m["type"] == "hello.ack")["audio_stream"] is True
    stream = [m for m in sent if m["type"].startswith("answer.audio.")]
    assert [(m["type"].rsplit(".", 1)[1], m["seq"]) for m in stream] == [
        ("start", 0), ("chunk", 0), ("chunk", 0), ("end", 0),
        ("start", 1), ("chunk", 1), ("chunk", 1), ("end", 1),
    ]  # fmt: skip
    starts = [m for m in stream if m["type"] == "answer.audio.start"]
    assert [m["text"] for m in starts] == [GOOD, LAST]
    assert all(m["format"] == "pcm16" and m["sample_rate"] == STREAM_SAMPLE_RATE for m in starts)
    assert [m["last"] for m in stream if m["type"] == "answer.audio.end"] == [False, True]
    chunks = [base64.b64decode(m["pcm16_b64"]) for m in stream if m["type"] == "answer.audio.chunk"]
    assert chunks[1].decode("utf-16-le") == GOOD and all(len(c) % 2 == 0 for c in chunks)
    # the answer pieces were not also sent as mp3; cached phrases (filler, follow-up) still are
    mp3_texts = [m["text"] for m in sent if m["type"] == "answer.audio"]
    assert GOOD not in mp3_texts and LAST not in mp3_texts and mp3_texts
    timings = next(m for m in sent if m["type"] == "turn.timings")
    assert timings["tts_ms"] is not None and dm.state == "FOLLOW_UP"


async def test_streaming_is_off_by_default(db_session) -> None:
    await _seed(db_session)
    reply = {"answer": f"{GOOD} {LAST}", "found": True, "used": [1]}
    sent, _ = await _ask(db_session, {"audio_stream": ["pcm16"]}, reply, StreamingFakeTTS())
    assert next(m for m in sent if m["type"] == "hello.ack")["audio_stream"] is False
    assert not any(m["type"].startswith("answer.audio.") for m in sent)
    assert GOOD in [m["text"] for m in sent if m["type"] == "answer.audio"]  # whole mp3 pieces


async def test_streaming_needs_the_client_flag_and_a_streaming_tts(db_session, streaming_on) -> None:
    await _seed(db_session)
    reply = {"answer": f"{GOOD} {LAST}", "found": True, "used": [1]}
    sent, _ = await _ask(db_session, {}, reply, StreamingFakeTTS())  # an older client
    assert not any(m["type"].startswith("answer.audio.") for m in sent)
    assert GOOD in [m["text"] for m in sent if m["type"] == "answer.audio"]

    sent, _ = await _ask(db_session, {"audio_stream": ["pcm16"]}, reply, FakeTTS())  # TTS cannot stream
    assert not any(m["type"].startswith("answer.audio.") for m in sent)


async def test_invented_number_is_never_streamed(db_session, streaming_on) -> None:
    await _seed(db_session)
    tts = StreamingFakeTTS()
    sent, _ = await _ask(db_session, {"audio_stream": ["pcm16"]},
                         {"answer": f"{GOOD} {BAD} {LAST}", "found": True, "used": [1]}, tts)  # fmt: skip
    assert not any("987654" in entry for entry in tts.log)
    assert not any("987654" in m.get("text", "") for m in sent)
    assert [m["text"] for m in sent if m["type"] == "answer.audio.start"] == [GOOD, LAST]


async def test_stream_failure_ends_the_piece_and_reports_it(db_session, streaming_on) -> None:
    from app.services.providers import ProviderError

    class BrokenTTS(StreamingFakeTTS):
        async def stream_pcm(self, text: str, voice: str | None = None):  # noqa: ANN201
            yield b"\x01\x00" * 10
            raise ProviderError("text-to-speech failed: boom")

    await _seed(db_session)
    sent, dm = await _ask(db_session, {"audio_stream": ["pcm16"]},
                          {"answer": f"{GOOD} {LAST}", "found": True, "used": [1]}, BrokenTTS())  # fmt: skip
    kinds = [m["type"] for m in sent if m["type"].startswith("answer.audio.")]
    assert kinds == ["answer.audio.start", "answer.audio.chunk", "answer.audio.end"]
    assert any(m["type"] == "error" and m["code"] == "tts_failed" for m in sent)
    assert dm.state == "FOLLOW_UP"  # the conversation goes on


@pytest.mark.parametrize("audio_stream", [True, False])
async def test_latency_through_the_conversation_manager(db_session, streaming_on, audio_stream) -> None:
    from app.services.latency import measure_dialogue

    await _seed(db_session)
    questions = [{"id": "q1", "question": f"{UNIQUE}?"}, {"id": "q2", "question": f"{UNIQUE}?"}]
    reply = {"answer": f"{GOOD} {LAST}", "found": True, "used": [1]}
    rows, session_ids = await measure_dialogue(
        OneSession(db_session), FakeEmbedder(), FakeStreamingLLM(reply), FakeSTT([]), StreamingFakeTTS(), questions,
        audio_stream,
    )
    assert len(rows) == 2 and len(set(session_ids)) == 2
    assert all(r.first_piece == GOOD and r.pieces == 2 and r.found and r.error is None for r in rows)
    assert all(r.first_audio_ms is not None and r.llm_first_ms is not None for r in rows)
