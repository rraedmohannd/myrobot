"""Multi-speaker conversations (docs/SPEAKERS.md): tracker logic and dialogue behaviour."""

import base64
import random

from sqlalchemy import select

from app.db.models import Chunk, Source, Turn
from app.services.dialogue import PHRASES, DialogueManager
from app.services.speakers import NullSpeakerEmbedder, SpeakerTracker, cosine
from tests.fakes import FakeEmbedder, FakeLLM, FakeSpeakerEmbedder, FakeSTT, FakeTTS, OneSession

random.seed(7)
VOICE = {n: [random.gauss(0, 1) for _ in range(192)] for n in "ABC"}
SPEECH = base64.b64encode(b"\x01\x00" * 32000).decode()  # 2 s
UNIQUE = "زربلوخي"


def sample(name: str, noise: float = 0.35) -> list[float]:
    return [x + random.gauss(0, noise) for x in VOICE[name]]


# ---------- tracker ----------
def test_tracker_separates_three_voices() -> None:
    t = SpeakerTracker(threshold=0.5)
    order = ["A", "B", "A", "C", "B", "C", "A", "B"]
    labels = [t.assign(sample(n)).label for n in order]
    assert labels == [1, 2, 1, 3, 2, 3, 1, 2]


def test_tracker_never_exceeds_max_speakers() -> None:
    t = SpeakerTracker(threshold=0.99, max_speakers=2)
    labels = {t.assign(sample(n)).label for n in "ABCABC"}
    assert labels <= {1, 2}


def test_face_links_short_utterances_to_the_right_person() -> None:
    t = SpeakerTracker()
    a = t.assign(sample("A"), face_track=7)
    b = t.assign(sample("B"), face_track=9)
    assert (a.label, b.label) == (1, 2)
    short = t.assign(None, face_track=9)  # "عيدها" — too short for a voiceprint
    assert short.label == 2 and short.source == "face"
    assert t.assign(None).source == "last"


def test_clearly_different_voice_beats_wrong_face() -> None:
    t = SpeakerTracker()
    t.assign(sample("A"), face_track=1)
    t.assign(sample("B"), face_track=2)
    r = t.assign(sample("A"), face_track=2)  # camera picked the wrong mouth
    assert r.label == 1 and r.source == "voice"


def test_null_embedder_and_cosine() -> None:
    assert NullSpeakerEmbedder().embed(b"\x00" * 64000, 16000) is None
    assert round(cosine([1, 0], [1, 0]), 3) == 1.0 and round(cosine([1, 0], [0, 1]), 3) == 0.0


# ---------- dialogue ----------
async def _seed(db_session) -> None:
    src = Source(url="test-doc://speakers", title="Speakers test", review_status="approved")
    db_session.add(src)
    await db_session.flush()
    db_session.add(Chunk(source_id=src.id, lang="en", review_status="approved",
                         text=f"The {UNIQUE} program requires 162 credit hours."))  # fmt: skip
    await db_session.commit()


async def _say(dm: DialogueManager, **extra: object) -> None:
    await dm.handle({"type": "audio.chunk", "pcm16_b64": SPEECH, "sample_rate": 16000})
    await dm.handle({"type": "audio.end", **extra})


async def test_two_visitors_get_numbers_greeting_and_own_history(db_session) -> None:
    await _seed(db_session)
    sent: list[dict] = []

    async def send(m: dict) -> None:
        sent.append(m)

    tts, llm = FakeTTS(), FakeLLM()
    dm = DialogueManager(
        send, OneSession(db_session), FakeEmbedder(), llm,
        FakeSTT([f"{UNIQUE}؟", f"شو {UNIQUE}؟", f"وكم {UNIQUE}؟"]), tts,
        speaker_embedder=FakeSpeakerEmbedder([sample("A"), sample("B"), sample("A")]),
    )  # fmt: skip
    await dm.handle({"type": "session.start"})
    await _say(dm, face_track_id=3)  # visitor A
    await _say(dm, face_track_id=4)  # visitor B (new)
    await _say(dm, face_track_id=3)  # visitor A again

    speakers = [m["speaker"] for m in sent if m["type"] == "transcript"]
    assert [s["label"] for s in speakers] == [1, 2, 1]
    assert speakers[1]["is_new"] and not speakers[2]["is_new"]
    assert " ".join(tts.spoken).count(PHRASES["new_speaker"]["ar"]) == 1  # greeted B once
    # A's follow-up only sees A's own earlier question, not B's
    last_prompt = llm.calls[-1][1]
    assert f"Visitor: {UNIQUE}؟" in last_prompt and f"شو {UNIQUE}" not in last_prompt

    turns = (await db_session.scalars(
        select(Turn).where(Turn.session_id == dm.session_id).order_by(Turn.idx)
    )).all()  # fmt: skip
    assert [t.speaker_label for t in turns] == [1, 2, 1]
    assert all(t.speaker_source for t in turns)


async def test_overlap_asks_one_at_a_time_and_answers_nothing(db_session) -> None:
    await _seed(db_session)
    sent: list[dict] = []

    async def send(m: dict) -> None:
        sent.append(m)

    tts, stt = FakeTTS(), FakeSTT(["should not be transcribed"])
    dm = DialogueManager(send, OneSession(db_session), FakeEmbedder(), FakeLLM(), stt, tts)
    await dm.handle({"type": "session.start"})
    await _say(dm, overlap=True)
    assert " ".join(tts.spoken).endswith(PHRASES["one_at_a_time"]["ar"])
    assert not stt.received and dm.state == "LISTENING"
    assert not any(m["type"] == "answer.text" for m in sent)


async def test_voiceprints_are_forgotten_when_the_session_ends(db_session) -> None:
    async def send(m: dict) -> None:
        return None

    dm = DialogueManager(
        send,
        OneSession(db_session),
        FakeEmbedder(),
        FakeLLM(),
        FakeSTT(["لا شكرا"]),
        FakeTTS(),
        speaker_embedder=FakeSpeakerEmbedder([sample("A")]),
    )
    await dm.handle({"type": "session.start"})
    assert dm.tracker is not None
    await dm.handle({"type": "session.end", "reason": "lost_face"})
    assert dm.tracker is None


def test_weak_sample_of_same_person_does_not_create_new_speaker() -> None:
    """Hysteresis: in-between similarity goes to the closest visitor; only clearly different =
    new."""
    t = SpeakerTracker(threshold=0.45, new_threshold=0.3)
    assert t.assign(sample("A")).label == 1
    weak_a = [0.4 * a + 0.92 * c for a, c in zip(VOICE["A"], VOICE["C"], strict=True)]  # cos ≈ 0.37
    r = t.assign(weak_a)
    assert r.label == 1 and not r.is_new and 0.3 <= r.confidence < 0.45
    assert 1 in r.sims
    assert t.assign(sample("B")).is_new  # a clearly different voice still becomes Speaker 2
