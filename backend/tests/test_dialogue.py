"""Voice conversation flow (B4) with fake STT/TTS/LLM — no microphone, no OpenAI."""

import base64

from sqlalchemy import select
from starlette.testclient import TestClient

from app.db.models import Chunk, Session, Source, Turn
from app.services.dialogue import PHRASES, DialogueManager, classify
from app.services.speech import pcm16_to_wav, split_for_speech
from tests.fakes import FakeEmbedder, FakeLLM, FakeSTT, FakeTTS, OneSession

UNIQUE = "زربلوخي"  # an Arabic nonsense word: keeps replies in Arabic, no real chunk contains it
SPEECH = base64.b64encode(b"\x01\x00" * 16000).decode()  # 1 s of (silent-ish) PCM16 @16 kHz


def test_classify_intents() -> None:
    assert classify("شكراً", "FOLLOW_UP") == "done"
    assert classify("لا شكرا", "FOLLOW_UP") == "done"
    assert classify("لأ", "FOLLOW_UP") == "done"
    assert classify("شكرا، وكم ساعة العمارة؟", "FOLLOW_UP") == "question"
    assert classify("ما في تخصص روبوتات", "LISTENING") == "question"
    assert classify("مرحبا", "LISTENING") == "greeting"
    assert classify("مرحبا بدي اعرف عن العمارة", "LISTENING") == "question"
    assert classify("عيدها", "FOLLOW_UP") == "repeat"
    assert classify("", "LISTENING") == "unclear"
    assert classify("no thanks", "FOLLOW_UP") == "done"
    assert classify("Tell me about architecture", "LISTENING") == "question"


def test_split_for_speech_and_wav() -> None:
    assert split_for_speech("جملة أولى. جملة ثانية؟ وثالثة.") == ["جملة أولى.", "جملة ثانية؟ وثالثة."]
    assert split_for_speech("one sentence") == ["one sentence"]
    long = "خريجين هندسة الأنظمة الذكية بشتغلوا بمجالات زي تكنولوجيا المعلومات والاتصالات، والبنوك والرقابة المالية."
    first, rest = split_for_speech(long)
    assert first.endswith("والاتصالات،") and len(first.split()) <= 12 and rest.startswith("والبنوك")
    wav = pcm16_to_wav(b"\x00\x00" * 160)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"


async def _seed(db_session) -> None:
    src = Source(url="test-doc://dialogue", title="Dialogue test", review_status="approved")
    db_session.add(src)
    await db_session.flush()
    db_session.add(Chunk(source_id=src.id, lang="en", review_status="approved",
                         text=f"The {UNIQUE} program requires 162 credit hours."))  # fmt: skip
    await db_session.commit()


def _manager(db_session, transcripts: list[str]):  # noqa: ANN202
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    tts = FakeTTS()
    dm = DialogueManager(send, OneSession(db_session), FakeEmbedder(), FakeLLM(), FakeSTT(transcripts), tts)
    return dm, sent, tts


def said(tts: FakeTTS) -> str:
    """Everything spoken so far; long phrases are sent as several pieces (first sentence first)."""
    return " ".join(tts.spoken)


def _types(sent: list[dict]) -> list[str]:
    return [m["type"] for m in sent]


async def _speak(dm: DialogueManager) -> None:
    await dm.handle({"type": "audio.chunk", "pcm16_b64": SPEECH, "sample_rate": 16000})
    await dm.handle({"type": "audio.end"})


async def test_full_voice_conversation(db_session) -> None:
    await _seed(db_session)
    dm, sent, tts = _manager(db_session, [f"{UNIQUE}؟", "لا شكرا"])

    await dm.handle({"type": "hello", "robot_id": "test"})
    assert sent[-1]["type"] == "hello.ack" and sent[-1]["protocol_version"] == 1

    await dm.handle({"type": "session.start", "lang": "ar"})
    assert said(tts).startswith(PHRASES["greeting"]["ar"])
    assert len(tts.spoken) == 2  # greeting split: first sentence is synthesized/sent first
    assert dm.state == "LISTENING" and dm.session_id

    await _speak(dm)  # question
    types = _types(sent)
    assert "transcript" in types and "answer.text" in types and "turn.timings" in types
    answer = next(m for m in sent if m["type"] == "answer.text")
    assert answer["found"] and "162" in answer["text"]
    assert PHRASES["filler_0"]["ar"] in said(tts)  # instant cached filler before the answer
    timing = next(m for m in sent if m["type"] == "turn.timings")
    assert timing["ack_ms"] is not None and timing["ack_ms"] <= timing["tts_ms"]
    # screen card: built only from the answer and its sources (title, <= 3 lines, source titles)
    card = next(m["card"] for m in sent if m["type"] == "display.update" and m.get("card"))
    assert card["title"] == "كلية الهندسة والتصميم"  # no single program: faculty name
    assert card["lines"] and len(card["lines"]) <= 3
    assert all(line.rstrip("…") in answer["text"] for line in card["lines"])
    assert card["sources"] == ["Dialogue test"]
    assert said(tts).endswith(PHRASES["follow_up"]["ar"])
    assert dm.state == "FOLLOW_UP"

    session_id = dm.session_id
    await _speak(dm)  # "لا شكرا"
    assert said(tts).endswith(PHRASES["goodbye"]["ar"])
    assert dm.state == "IDLE" and dm.session_id is None
    assert sent[-3]["type"] == "session.ended" and sent[-3]["reason"] == "user_done"

    s = await db_session.get(Session, session_id)
    await db_session.refresh(s)
    assert s.end_reason == "user_done" and s.ended_at is not None
    turns = (await db_session.scalars(select(Turn).where(Turn.session_id == session_id).order_by(Turn.idx))).all()
    assert [t.intent for t in turns] == ["question", "done"]
    assert turns[0].stt_ms == 5 and turns[0].tts_ms is not None and turns[0].used_chunk_ids


async def test_silence_after_follow_up_ends_politely(db_session) -> None:
    await _seed(db_session)
    dm, sent, tts = _manager(db_session, [f"{UNIQUE}؟"])
    await dm.handle({"type": "session.start"})
    await _speak(dm)
    await dm.handle({"type": "listen.timeout"})
    assert said(tts).endswith(PHRASES["timeout_goodbye"]["ar"])
    assert any(m["type"] == "session.ended" and m["reason"] == "timeout" for m in sent)


async def test_unclear_and_repeat(db_session) -> None:
    await _seed(db_session)
    dm, sent, tts = _manager(db_session, ["", f"{UNIQUE}؟", "عيدها"])
    await dm.handle({"type": "session.start"})
    await _speak(dm)  # empty transcript
    assert said(tts).endswith(PHRASES["unclear"]["ar"]) and dm.state == "LISTENING"
    await _speak(dm)  # question
    first_answer = dm.last_answer
    await _speak(dm)  # repeat
    assert first_answer and tts.spoken.count(first_answer) == 2
    assert dm.state == "FOLLOW_UP"


async def test_audio_is_ignored_while_robot_is_not_listening(db_session) -> None:
    dm, sent, tts = _manager(db_session, ["should not be used"])
    await dm.handle({"type": "audio.chunk", "pcm16_b64": SPEECH})  # IDLE: ignored
    await dm.handle({"type": "audio.end"})
    assert not dm.stt.received and len(dm.audio) == 0


async def test_typed_question_starts_session_without_greeting(db_session) -> None:
    await _seed(db_session)
    dm, sent, tts = _manager(db_session, [])
    await dm.handle({"type": "text.input", "text": f"How many hours is {UNIQUE}?"})
    assert dm.session_id and "Welcome" not in said(tts) and "أهلاً وسهلاً فيك! أنا" not in said(tts)
    assert any(m["type"] == "answer.text" for m in sent)


def test_websocket_hello_and_heartbeat() -> None:
    from app.main import app

    with TestClient(app) as tc, tc.websocket_connect("/ws/robot") as ws:
        ws.send_json({"type": "hello", "robot_id": "test", "protocol_version": 1})
        ack = ws.receive_json()
        assert ack["type"] == "hello.ack" and ack["id"] and ack["ts"]
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["type"] == "heartbeat"
        ws.send_json({"type": "nonsense"})
        assert ws.receive_json()["code"] == "unknown_type"


def test_card_uses_only_the_answer_and_its_sources() -> None:
    from app.services.display import build_card, dominant_program

    answer = "التخصص بدّه 162 ساعة معتمدة. فيه مواد عن الروبوتات؟ نعم. وكمان ذكاء اصطناعي. وجملة خامسة."
    card = build_card(answer, ["Study Plan", "Study Plan", None], "ar", "هندسة الأنظمة الذكية")
    assert card == {
        "title": "هندسة الأنظمة الذكية",
        "lines": ["التخصص بدّه 162 ساعة معتمدة.", "فيه مواد عن الروبوتات؟", "نعم."],
        "sources": ["Study Plan"],
    }
    assert build_card("x " * 80, ["S"], "en")["lines"][0].endswith("…")
    assert build_card("Answer.", ["S"], "en")["title"] == "Faculty of Engineering & Design"
    assert build_card("Answer.", [], "en") is None  # no sources: no card

    class P:
        def __init__(self, program_id: int | None) -> None:
            self.program_id = program_id

    assert dominant_program([P(3), P(3), P(None)]) == 3
    assert dominant_program([P(3), P(4)]) is None  # mixed programs
    assert dominant_program([P(3), P(None), P(None)]) is None  # mostly general passages
    assert dominant_program([]) is None


async def test_not_found_answer_has_no_card(db_session) -> None:
    await _seed(db_session)
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    llm = FakeLLM({"answer": "ما عندي هاي المعلومة.", "found": False, "used": []})
    dm = DialogueManager(send, OneSession(db_session), FakeEmbedder(), llm, FakeSTT([]), FakeTTS())
    await dm.handle({"type": "text.input", "text": f"كم رسوم {UNIQUE}؟"})
    assert any(m["type"] == "answer.text" and not m["found"] for m in sent)
    assert not any(m.get("card") for m in sent if m["type"] == "display.update")


async def test_robot_and_voice_pages_and_shared_client(client) -> None:
    robot = await client.get("/robot")
    assert robot.status_code == 200 and "محاكاة لشاشة الروبوت" in robot.text
    voice = await client.get("/voice")
    assert voice.status_code == 200
    for page in (robot.text, voice.text):
        assert "/static/robot-client.js" in page
        assert "https://" not in page and "http://" not in page.replace("http://www.w3.org", "")
    js = await client.get("/static/robot-client.js")
    assert js.status_code == 200 and "class RobotClient" in js.text


async def test_camera_prototype_files_are_local(client) -> None:
    """The face model and its runtime are served from /static (no CDN), with their licence."""
    robot = (await client.get("/robot")).text
    assert "/static/speaker-camera.js" in robot
    js = (await client.get("/static/speaker-camera.js")).text
    assert "class SpeakerCamera" in js and "/static/vendor/mediapipe" in js
    assert "https://" not in js and "http://" not in js
    for name in ("vision_bundle.mjs", "vision_wasm_internal.js", "LICENSE", "README.md"):
        assert (await client.get(f"/static/vendor/mediapipe/{name}")).status_code == 200, name
    for name, minimum in (("vision_wasm_internal.wasm", 1_000_000), ("face_landmarker.task", 1_000_000)):
        r = await client.get(f"/static/vendor/mediapipe/{name}")
        assert r.status_code == 200 and len(r.content) > minimum, name


async def test_audio_end_passes_face_track_and_overlap(db_session) -> None:
    """What the camera sends: a track id links a face to a speaker; overlap asks for one at a time."""
    await _seed(db_session)
    dm, sent, tts = _manager(db_session, [f"{UNIQUE}؟"])
    await dm.handle({"type": "session.start"})
    await dm.handle({"type": "audio.chunk", "pcm16_b64": SPEECH, "sample_rate": 16000})
    await dm.handle({"type": "audio.end", "face_track_id": 3, "overlap": False})
    transcript = next(m for m in sent if m["type"] == "transcript")
    assert transcript["speaker"]["label"] == 1 and "face" in transcript["speaker"]["source"]

    await dm.handle({"type": "audio.chunk", "pcm16_b64": SPEECH, "sample_rate": 16000})
    await dm.handle({"type": "audio.end", "face_track_id": 3, "overlap": True})
    assert said(tts).endswith(PHRASES["one_at_a_time"]["ar"])
    assert len([m for m in sent if m["type"] == "answer.text"]) == 1  # the mixed audio is not answered
