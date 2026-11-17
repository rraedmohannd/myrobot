"""Sprint 3 S5: poor internet. Failing and slow providers must end in the cached "weak internet"
reply within the time limit, without a crash, and the conversation must listen again.
Fakes only; the time limits are shortened through the environment."""

import asyncio
import time

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Chunk, Explainer, ExplainerSegment, Faculty, Faq, Program, Source, Turn
from app.services import resilience
from app.services.dialogue import PHRASES, DialogueManager, fixed_phrase_pieces
from app.services.providers import LLMResult, ProviderError
from app.services.resilience import (
    ProviderGuard,
    ResilientEmbedder,
    ResilientLLM,
    ResilientSTT,
    ResilientTTS,
    guard,
    provider_status,
)
from app.services.speech import CachedTTS
from tests.fakes import FakeEmbedder, FakeLLM, FakeStreamingLLM, FakeSTT, FakeTTS, OneSession

UNIQUE = "resilixo"
SPEECH = "AQAB" * 8000  # about 1.5 s of PCM16 at 16 kHz (base64)


@pytest.fixture(autouse=True)
def fast_limits(monkeypatch):
    """Short time limits and a fresh breaker for every test."""
    for key, value in {
        "PROVIDER_TIMEOUT_EMBEDDING_S": "0.2",
        "PROVIDER_TIMEOUT_STT_S": "0.2",
        "PROVIDER_TIMEOUT_TTS_S": "0.2",
        "PROVIDER_TIMEOUT_LLM_FIRST_S": "0.2",
        "PROVIDER_TIMEOUT_LLM_S": "0.4",
        "PROVIDER_RETRIES": "1",
        "PROVIDER_RETRY_BACKOFF_S": "0.01",
        "BREAKER_FAILURES": "3",
        "BREAKER_OPEN_S": "0.3",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    resilience.reset_guards()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()
    resilience.reset_guards()


class Failing:
    """A provider that always fails (as when the network is down)."""

    model, dim, voice = "down", 1536, "down"

    def __init__(self) -> None:
        self.calls = 0

    async def _fail(self, *a: object, **k: object):  # noqa: ANN202
        self.calls += 1
        raise ProviderError("network unreachable")

    embed = complete_json = transcribe = synthesize = _fail


class Slow(Failing):
    """A provider that hangs (as on a very poor connection)."""

    async def _hang(self, *a: object, **k: object):  # noqa: ANN202
        self.calls += 1
        await asyncio.sleep(30)

    embed = complete_json = transcribe = synthesize = _hang

    async def stream_json(self, instructions: str, user_input: str):  # noqa: ANN201
        self.calls += 1
        await asyncio.sleep(30)
        yield "never"


# ---------------- the guard itself ----------------
async def test_timeout_retry_and_breaker() -> None:
    g = ProviderGuard("stt")
    slow = Slow()
    started = time.monotonic()
    with pytest.raises(ProviderError, match="did not answer within 0.2 s"):
        await g.call(slow._hang, 0.2)
    assert slow.calls == 2 and time.monotonic() - started < 1.0  # one retry, both cut off
    assert g.state == "closed" and g.failures == 2

    with pytest.raises(ProviderError):
        await g.call(slow._hang, 0.2)  # third failure in a row opens the breaker
    assert g.state == "open" and slow.calls == 3
    started = time.monotonic()
    with pytest.raises(ProviderError, match="circuit open"):
        await g.call(slow._hang, 0.2)
    assert (
        slow.calls == 3 and time.monotonic() - started < 0.05
    )  # fails at once, the provider is not called

    await asyncio.sleep(0.35)
    assert g.state == "half-open"

    async def fine() -> str:
        return "ok"

    assert await g.call(fine, 0.2) == "ok"  # the trial call succeeds: closed again
    assert g.state == "closed" and g.failures == 0 and g.status()["last_ok"]
    status = g.status()
    assert status["failed_calls"] == 3 and len(status["recent_errors"]) == 3


async def test_a_failed_trial_call_reopens_the_breaker() -> None:
    g, down = ProviderGuard("tts"), Failing()
    for _ in range(2):
        with pytest.raises(ProviderError):
            await g.call(down._fail, 0.2)
    assert g.state == "open"
    await asyncio.sleep(0.35)
    with pytest.raises(ProviderError):
        await g.call(down._fail, 0.2)
    assert g.state == "open"  # still down: no hammering


async def test_wrappers_keep_the_interfaces() -> None:
    embedder = ResilientEmbedder(FakeEmbedder())
    assert embedder.dim == 1536 and len((await embedder.embed(["a"]))[0]) == 1536
    stt = ResilientSTT(FakeSTT(["مرحبا"]))
    assert (await stt.transcribe(b"x")).text == "مرحبا"

    class Voice(FakeTTS):
        instructions, speed = "lively", 1.12

        async def synthesize(self, text: str, voice: str | None = None) -> bytes:  # noqa: ARG002
            return b"ID3" + text.encode()

    plain, wrapped = CachedTTS(Voice()), CachedTTS(ResilientTTS(Voice()))
    assert plain.cache_key("مرحبا") == wrapped.cache_key(
        "مرحبا"
    )  # the wrapper never changes the cache
    assert await wrapped.synthesize("مرحبا") == b"ID3" + "مرحبا".encode()

    llm = ResilientLLM(FakeLLM({"answer": "x", "found": True, "used": []}))
    assert (await llm.complete_json("i", "u")).data["answer"] == "x" and not hasattr(
        llm, "stream_json"
    )
    streaming = ResilientLLM(
        FakeStreamingLLM({"answer": "Hello there.", "found": True, "used": []})
    )
    items = [item async for item in streaming.stream_json("i", "u")]
    assert isinstance(items[-1], LLMResult) and "".join(i for i in items[:-1]).startswith(
        '{"answer"'
    )
    assert all(s["ok"] for s in provider_status().values()) and set(provider_status()) == {
        "stt",
        "llm",
        "tts",
        "embeddings",
    }

    with pytest.raises(ProviderError, match="did not start answering within 0.2 s"):
        async for _ in ResilientLLM(Slow()).stream_json("i", "u"):
            pass
    assert (
        provider_status()["llm"]["failures_in_a_row"] == 2
        and provider_status()["llm"]["last_error"]
    )


# ---------------- the conversation ----------------
async def _seed(db_session) -> None:
    src = Source(url="test-doc://resilience", title="Resilience test", review_status="approved")
    db_session.add(src)
    await db_session.flush()
    db_session.add(Chunk(source_id=src.id, lang="en", review_status="approved",
                         text=f"The {UNIQUE} program requires 162 credit hours."))  # fmt: skip
    await db_session.commit()


def _manager(db_session, embedder=None, llm=None, stt=None, tts=None):  # noqa: ANN001, ANN202
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    tts = tts or FakeTTS()
    dm = DialogueManager(
        send, OneSession(db_session), ResilientEmbedder(embedder or FakeEmbedder()),
        ResilientLLM(llm or FakeLLM()), ResilientSTT(stt or FakeSTT([f"{UNIQUE}?"])), tts,
    )  # fmt: skip
    return dm, sent, tts


def _offline_reply(sent: list[dict], tts: FakeTTS) -> dict:
    assert PHRASES["offline"]["ar"] in " ".join(tts.spoken) or PHRASES["offline"]["en"] in " ".join(
        tts.spoken
    )
    display = next(
        m for m in reversed(sent) if m["type"] == "display.update" and m.get("offline") is not None
    )
    assert (
        display["mode"] == "unavailable"
        and "programs" in display["offline"]
        and "qr_url" in display["offline"]
    )
    assert [m["state"] for m in sent if m["type"] == "dialogue.state"][-1] == "LISTENING"
    return display["offline"]


@pytest.mark.parametrize(
    "broken", ["slow_llm", "failing_llm", "slow_embeddings", "failing_embeddings"]
)
async def test_provider_down_gives_the_cached_fallback_in_time(db_session, broken) -> None:
    await _seed(db_session)
    kind, what = broken.split("_")
    provider = Slow() if kind == "slow" else Failing()
    dm, sent, tts = _manager(
        db_session, **({"llm": provider} if what == "llm" else {"embedder": provider})
    )
    started = time.monotonic()
    await dm.handle({"type": "text.input", "text": f"{UNIQUE}?"})
    assert time.monotonic() - started < 2.5, "the fallback must come within the time limit"
    _offline_reply(sent, tts)
    assert dm.state == "LISTENING" and dm.session_id  # no crash, the conversation goes on
    assert (
        PHRASES["follow_up"]["ar"] not in tts.spoken
        and PHRASES["follow_up"]["en"] not in tts.spoken
    )
    answer = next(m for m in sent if m["type"] == "answer.text")
    assert answer["kind"] == "offline" and not answer["found"]
    turn = await db_session.scalar(select(Turn).where(Turn.session_id == dm.session_id))
    assert turn.error and turn.answer_kind == "offline" and turn.found is None
    assert not provider_status()["llm" if what == "llm" else "embeddings"]["ok"]

    # the next question works again once the provider is back
    resilience.reset_guards()
    dm.embedder, dm.llm = ResilientEmbedder(FakeEmbedder()), ResilientLLM(FakeLLM())
    await dm.handle({"type": "text.input", "text": f"{UNIQUE}?"})
    assert [m for m in sent if m["type"] == "answer.text"][-1]["found"] and dm.state == "FOLLOW_UP"


@pytest.mark.parametrize("stt", [Slow, Failing])
async def test_speech_to_text_down_also_falls_back(db_session, stt) -> None:
    dm, sent, tts = _manager(db_session, stt=stt())
    await dm.handle({"type": "session.start"})
    started = time.monotonic()
    await dm.handle({"type": "audio.chunk", "pcm16_b64": SPEECH, "sample_rate": 16000})
    await dm.handle({"type": "audio.end"})
    assert time.monotonic() - started < 2.0
    assert any(m["type"] == "error" and m["code"] == "stt_failed" for m in sent)
    _offline_reply(sent, tts)
    assert dm.state == "LISTENING"


async def test_text_to_speech_down_does_not_crash(db_session) -> None:
    await _seed(db_session)
    dm, sent, tts = _manager(db_session, tts=CachedTTS(ResilientTTS(Failing())))
    await dm.handle({"type": "text.input", "text": f"{UNIQUE}?"})
    assert any(m["type"] == "error" and m["code"] == "tts_failed" for m in sent)
    answer = next(m for m in sent if m["type"] == "answer.text")
    assert answer["found"] and "162" in answer["text"]  # the answer is still shown as text
    assert dm.state == "FOLLOW_UP"


async def test_cached_content_still_works_offline(db_session, tmp_path) -> None:
    """With every provider down: an identical approved FAQ still answers, an approved explainer
    still plays from the speech cache, and the offline screen offers it."""
    faculty = await db_session.scalar(select(Faculty).limit(1))
    program = Program(
        faculty_id=faculty.id,
        slug="offline-program",
        name_ar="برنامج الأوفلاين",
        name_en="Offline Program",
    )
    source = Source(
        url="https://example.test/offline", title="Offline source", review_status="approved"
    )
    db_session.add_all([program, source])
    await db_session.flush()
    db_session.add(
        Chunk(
            source_id=source.id,
            program_id=program.id,
            lang="en",
            category="study_plan",
            review_status="approved",
            text="Offline Program requires 162 credit hours.",
        )
    )
    explainer = Explainer(program_id=program.id, lang="ar", status="approved")
    db_session.add(explainer)
    await db_session.flush()
    texts = ["تعريف بالبرنامج.", "الخطة 162 ساعة.", "وهيك خلصنا."]
    for i, body in enumerate(texts, start=1):
        db_session.add(
            ExplainerSegment(
                explainer_id=explainer.id, position=i, text=body, scene={"type": "title"}
            )
        )
    question, answer = "شو ساعات دوام مكتبة الزرنيخ؟", "اسأل موظف المكتبة."
    db_session.add(Faq(question_ar=question, answer_ar=answer, review_status="approved"))
    await db_session.commit()

    # the speech cache was filled while the internet worked
    online = CachedTTS(FakeTTS(), tmp_path)
    for piece in [*texts, answer, *fixed_phrase_pieces()]:
        await online.synthesize(piece, cache=True)
    down, voice_down, llm_down = Failing(), Failing(), Failing()
    voice_down.model, voice_down.voice = (
        online.model,
        online.voice,
    )  # the same voice: the same cache keys
    dm, sent, _ = _manager(db_session, embedder=down, llm=llm_down, stt=Failing(),
                           tts=CachedTTS(ResilientTTS(voice_down), tmp_path))  # fmt: skip

    await dm.handle({"type": "text.input", "text": question})
    reply = next(m for m in sent if m["type"] == "answer.text")
    assert (
        reply["kind"] == "faq" and reply["text"] == answer and llm_down.calls == 0
    )  # no model was needed
    assert any(m["type"] == "answer.audio" and m["text"] == answer for m in sent)

    await dm.handle(
        {"type": "text.input", "text": f"{UNIQUE} سؤال ثاني؟"}
    )  # needs the embeddings: offline
    offline = next(
        m["offline"] for m in reversed(sent) if m["type"] == "display.update" and m.get("offline")
    )
    assert {"slug": "offline-program", "name": "برنامج الأوفلاين", "lang": "ar"} in offline[
        "programs"
    ]
    assert any(
        m["type"] == "answer.audio" and PHRASES["offline"]["ar"].startswith(m["text"]) for m in sent
    )

    await dm.handle(
        {"type": "explainer.start", "program": "offline-program", "lang": "ar"}
    )  # the touch button
    segments = [m for m in sent if m["type"] == "answer.audio" and m.get("segment")]
    assert [m["text"] for m in segments] == texts
    assert voice_down.calls == 0 and llm_down.calls == 0  # every word came from the speech cache
    assert not any(m["type"] == "error" and m["code"] == "tts_failed" for m in sent)


async def test_health_reports_provider_status(client) -> None:
    with pytest.raises(ProviderError):
        await guard("llm").call(Failing()._fail, 0.2)
    body = (await client.get("/health")).json()
    assert body["status"] == "ok"  # no internet does not make the backend unhealthy
    assert set(body["providers"]) == {"stt", "llm", "tts", "embeddings"}
    assert (
        body["providers"]["llm"]["ok"] is False
        and body["providers"]["llm"]["recent_errors"][0]["error"] == "network unreachable"
    )
    assert body["providers"]["stt"]["ok"] is True
