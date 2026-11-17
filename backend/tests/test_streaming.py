"""Streaming answers (demo sprint M2): speech starts while the LLM is still writing, and no
sentence with an invented number ever reaches text-to-speech. Fakes only, no OpenAI."""

import json

from app.db.models import Chunk, Source
from app.services.chat import NUMBER_SAFE, answer_events, answer_question
from app.services.dialogue import DialogueManager
from app.services.latency import LatencyRow, format_report, measure, percentile, summarize
from app.services.speech import split_for_speech
from app.services.streaming import AnswerExtractor, SpeechPieces
from tests.fakes import FakeEmbedder, FakeLLM, FakeStreamingLLM, FakeSTT, FakeTTS, OneSession

UNIQUE = "zorblaxiq"  # in no real chunk, so the seeded passage is always passage [1]
GOOD = f"The {UNIQUE} program requires 162 credit hours."
BAD = "The fees are 987654 dinars per year."
LAST = "Graduates work in many fields."


# ---------------- answer extraction from partial JSON ----------------
def _extract(raw: str, chunk: int) -> tuple[str, list[str]]:
    ex, steps = AnswerExtractor(), []
    for i in range(0, len(raw), chunk):
        steps.append(ex.feed(raw[i : i + chunk]))
    assert "".join(steps) == ex.text
    return ex.text, steps


def test_answer_extractor_any_chunking() -> None:
    answer = 'قال: "أهلاً" \\ 162 ساعة.\nNew line, tab\t, emoji 😀 and é.'
    for ensure_ascii in (False, True):  # raw UTF-8 and \\uXXXX escapes (incl. surrogate pairs)
        raw = json.dumps(
            {"answer": answer, "found": True, "used": [1, 2]}, ensure_ascii=ensure_ascii
        )
        for chunk in (1, 2, 3, 5, 7, 11, 1000):
            text, _ = _extract(raw, chunk)
            assert text == answer, (ensure_ascii, chunk)


def test_answer_extractor_key_order_and_missing_answer() -> None:
    raw = json.dumps({"found": True, "used": [1], "answer": "Late answer."})
    assert _extract(raw, 4)[0] == "Late answer."
    ex = AnswerExtractor()
    assert ex.feed('{"found": false, "used": []}') == "" and ex.text == "" and not ex.done
    done = AnswerExtractor()
    done.feed('{"answer": "A." , "found": true}')
    assert done.done and done.feed(' "answer": "B"') == ""


def test_answer_text_is_available_before_the_json_is_complete() -> None:
    ex = AnswerExtractor()
    assert ex.feed('{"answer": "First sentence. Sec') == "First sentence. Sec"
    assert not ex.done


# ---------------- speech pieces ----------------
def _pieces(text: str, chunk: int) -> list[str]:
    sp, out = SpeechPieces(), []
    for i in range(0, len(text), chunk):
        out += sp.feed(text[i : i + chunk])
    return out + sp.flush()


def test_speech_pieces_release_sentences_as_they_complete() -> None:
    sp = SpeechPieces()
    assert sp.feed("جملة أولى. جملة") == ["جملة أولى."]
    assert sp.feed(" ثانية؟ وثا") == ["جملة ثانية؟"]
    assert sp.feed("لثة.") == []  # the last sentence is not known to be complete yet
    assert sp.flush() == ["وثالثة."]


def test_speech_pieces_first_piece_matches_split_for_speech() -> None:
    long = (
        "خريجين هندسة الأنظمة الذكية بشتغلوا بمجالات زي تكنولوجيا المعلومات والاتصالات، والبنوك "
        "والرقابة المالية."
    )
    expected_first = split_for_speech(long)[0]
    for chunk in (1, 4, 9, 500):
        pieces = _pieces(long + " جملة ثانية.", chunk)
        assert pieces[0] == expected_first, chunk
        assert " ".join(pieces) == long + " جملة ثانية."
    sp = SpeechPieces()  # the short first piece is released before its sentence has ended
    assert sp.feed(long.rstrip(".") + " وشركات التقنية الحديثة ") == [expected_first]


def test_speech_pieces_never_cut_a_number() -> None:
    text = "The plan has 162 credit hours and 3.5 is not a sentence end. Second one."
    for chunk in (1, 2, 3, 7):
        pieces = _pieces(text, chunk)
        assert " ".join(pieces) == text
        assert any("162" in p for p in pieces) and any("3.5" in p for p in pieces)


# ---------------- grounded answers ----------------
async def _seed(db_session) -> None:
    src = Source(url="test-doc://streaming", title="Streaming test", review_status="approved")
    db_session.add(src)
    await db_session.flush()
    db_session.add(Chunk(source_id=src.id, lang="en", review_status="approved", text=GOOD))
    await db_session.commit()


async def _events(db_session, llm, stream: bool | None = None) -> tuple[list[str], object]:
    pieces, result = [], None
    async for e in answer_events(db_session, FakeEmbedder(), llm, f"{UNIQUE}?",
                                 log=False, stream=stream):  # fmt: skip
        if e.kind == "piece":
            pieces.append(e.text)
        else:
            result = e.result
    return pieces, result


async def test_streamed_and_whole_answers_are_the_same(db_session) -> None:
    await _seed(db_session)
    reply = {"answer": f"{GOOD} {LAST}", "found": True, "used": [1]}
    streamed, r1 = await _events(db_session, FakeStreamingLLM(reply))
    whole, r2 = await _events(db_session, FakeLLM(reply))  # adapter without stream_json
    off, r3 = await _events(db_session, FakeStreamingLLM(reply), stream=False)
    assert streamed == whole == off == [GOOD, LAST]
    for r in (r1, r2, r3):
        assert r.found and r.answer == r.spoken == f"{GOOD} {LAST}" and not r.dropped
        assert [s.title for s in r.sources] == ["Streaming test"]
    assert "llm_first_ms" in r1.timings and "llm_ms" in r1.timings


async def test_piece_with_invented_number_is_replaced_by_the_safe_line(db_session) -> None:
    await _seed(db_session)
    reply = {"answer": f"{GOOD} {BAD} It costs 876543 more. {LAST}", "found": True, "used": [1]}
    for llm in (FakeStreamingLLM(reply), FakeLLM(reply)):
        pieces, r = await _events(db_session, llm)
        assert pieces == [GOOD, NUMBER_SAFE["en"], LAST]  # the safe line is said once
        assert r.invented_numbers == ["876543", "987654"] and len(r.dropped) == 2
        assert r.spoken == f"{GOOD} {NUMBER_SAFE['en']} {LAST}"
        assert r.answer.count("987654") == 1  # the raw model answer stays visible to the evaluator
        assert r.found


async def test_only_invented_numbers_means_not_found(db_session) -> None:
    await _seed(db_session)
    pieces, r = await _events(
        db_session, FakeStreamingLLM({"answer": BAD, "found": True, "used": [1]})
    )
    assert pieces == [NUMBER_SAFE["en"]] and not r.found


async def test_not_found_and_empty_answers_still_speak(db_session) -> None:
    await _seed(db_session)
    pieces, r = await _events(
        db_session, FakeStreamingLLM({"answer": "", "found": False, "used": []})
    )
    assert not r.found and pieces and "Admission and Registration" in " ".join(pieces)
    r2 = await answer_question(
        db_session, FakeEmbedder(), FakeStreamingLLM(), f"{UNIQUE}?", log=False
    )
    assert r2.found and "162" in r2.answer


# ---------------- dialogue: speech before the LLM has finished ----------------
def _manager(db_session, llm, log: list):  # noqa: ANN202
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)
        if msg["type"] == "answer.audio":
            log.append(f"audio:{msg['text']}")

    tts = FakeTTS(log)
    return (
        DialogueManager(send, OneSession(db_session), FakeEmbedder(), llm, FakeSTT([]), tts),
        sent,
        tts,
    )


async def test_first_sentence_is_spoken_while_the_llm_is_still_writing(db_session) -> None:
    await _seed(db_session)
    log: list[str] = []
    llm = FakeStreamingLLM({"answer": f"{GOOD} {LAST}", "found": True, "used": [1]}, log=log)
    dm, sent, tts = _manager(db_session, llm, log)
    await dm.handle({"type": "text.input", "text": f"{UNIQUE}?"})

    assert log.index(f"tts:{GOOD}") < log.index(f"audio:{GOOD}") < log.index("llm:done")
    audio = [m for m in sent if m["type"] == "answer.audio" and m["text"] in (GOOD, LAST)]
    assert [(m["seq"], m["last"], m["text"]) for m in audio] == [(0, False, GOOD), (1, True, LAST)]
    answer = next(m for m in sent if m["type"] == "answer.text")
    assert answer["found"] and answer["text"] == f"{GOOD} {LAST}"
    assert [s["title"] for s in answer["sources"]] == ["Streaming test"]
    timings = next(m for m in sent if m["type"] == "turn.timings")
    assert timings["tts_ms"] is not None and timings["llm_first_ms"] is not None
    assert timings["ack_ms"] <= timings["tts_ms"]
    assert dm.state == "FOLLOW_UP" and dm.last_answer == f"{GOOD} {LAST}"


async def test_invented_number_never_reaches_tts(db_session) -> None:
    await _seed(db_session)
    log: list[str] = []
    llm = FakeStreamingLLM({"answer": f"{GOOD} {BAD} {LAST}", "found": True, "used": [1]}, log=log)
    dm, sent, tts = _manager(db_session, llm, log)
    await dm.handle({"type": "text.input", "text": f"{UNIQUE}?"})

    assert not any("987654" in text for text in tts.spoken)
    assert not any("987654" in m.get("text", "") for m in sent if m["type"] == "answer.audio")
    assert NUMBER_SAFE["en"] in tts.spoken and GOOD in tts.spoken and LAST in tts.spoken
    answer = next(m for m in sent if m["type"] == "answer.text")
    assert "987654" not in answer["text"] and answer["invented_numbers"] == ["987654"]
    card = next(m["card"] for m in sent if m["type"] == "display.update" and m.get("card"))
    assert not any("987654" in line for line in card["lines"])
    assert "987654" not in (dm.last_answer or "")


# ---------------- latency command ----------------
async def test_latency_measure_and_summary(db_session) -> None:
    await _seed(db_session)
    questions = [{"id": f"q{i}", "question": f"{UNIQUE}?"} for i in range(3)]
    reply = {"answer": f"{GOOD} {LAST}", "found": True, "used": [1]}
    rows = await measure(
        db_session, FakeEmbedder(), FakeStreamingLLM(reply), FakeTTS(), questions, True
    )
    assert len(rows) == 3 and all(r.first_piece == GOOD and r.pieces == 2 and r.found for r in rows)
    assert all(r.first_audio_ms is not None and r.tts_first_ms is not None for r in rows)
    summary = summarize(rows)
    assert summary["first_audio_ms"]["n"] == 3 and summary["llm_ms"]["median"] is not None
    assert "question text -> first answer audio" in format_report(rows, "fake")


def test_latency_statistics() -> None:
    rows = [
        LatencyRow(id=str(v), question="", first_audio_ms=v) for v in (100, 200, 300, 400, 1000)
    ]
    rows.append(LatencyRow(id="failed", question="", first_audio_ms=9, error="boom"))  # not counted
    s = summarize(rows)["first_audio_ms"]
    assert s == {"median": 300, "p90": 1000.0, "n": 5}
    assert percentile([], 0.9) is None and summarize([])["llm_ms"]["median"] is None
