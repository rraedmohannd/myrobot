"""Sprint 3 S3: FAQs, pitches and comparisons (places and labs: test_places.py).

Only approved rows are ever used; FAQs answer before retrieval with no model call; a pitch with
an invented number or a claim about others cannot be approved; the robot never invents a place.
Fakes only; every row is rolled back after the test.
"""

import pytest
from sqlalchemy import select

from app.db.models import Chunk, Faculty, Faq, Pitch, Program, Source, Turn
from app.main import app
from app.services import curated as cur
from app.services.chat import answer_question
from app.services.dialogue import DialogueManager
from app.services.speech import get_tts
from tests.fakes import FakeEmbedder, FakeLLM, FakeSTT, FakeTTS, OneSession

SLUG = "intelligent-systems-engineering"  # a real slug, so the spoken-name aliases apply
Q_AR, Q_EN = "كم ساعة معتمدة بدها هندسة الأنظمة الذكية؟", "How many credit hours is Intelligent Systems Engineering?"
A_AR, A_EN = "بدها 162 ساعة معتمدة حسب الخطة.", "It requires 162 credit hours according to the plan."


async def _program(db_session) -> Program:
    program = await db_session.scalar(select(Program).where(Program.slug == SLUG))
    if program is None:
        faculty = await db_session.scalar(select(Faculty).limit(1))
        program = Program(faculty_id=faculty.id, slug=SLUG, name_ar="هندسة الأنظمة الذكية",
                          name_en="Intelligent Systems Engineering")  # fmt: skip
        db_session.add(program)
        await db_session.flush()
    return program


async def _faq(db_session, status: str = "approved", program: Program | None = None, **fields) -> Faq:  # noqa: ANN003
    data = {"question_ar": Q_AR, "question_en": Q_EN, "answer_ar": A_AR, "answer_en": A_EN} | fields
    faq = Faq(program_id=program.id if program else None, review_status=status, **data)
    db_session.add(faq)
    await db_session.commit()
    return faq


def _manager(db_session, llm: FakeLLM | None = None):  # noqa: ANN202
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    tts = FakeTTS()
    dm = DialogueManager(send, OneSession(db_session), FakeEmbedder(), llm or FakeLLM(), FakeSTT([]), tts)
    return dm, sent, tts


@pytest.fixture
def fake_tts():
    tts = FakeTTS()
    app.dependency_overrides[get_tts] = lambda: tts
    yield tts
    app.dependency_overrides.pop(get_tts, None)


# ---------------- FAQs ----------------
async def test_faq_has_priority_over_rag_and_needs_no_model(db_session) -> None:
    program = await _program(db_session)
    faq = await _faq(db_session, program=program)
    llm = FakeLLM({"answer": "RAG answer that must not be used.", "found": True, "used": [1]})
    embedder = FakeEmbedder()
    calls: list = []
    original = embedder.embed

    async def counting(texts: list[str]):  # noqa: ANN202
        calls.append(texts)
        return await original(texts)

    embedder.embed = counting
    r = await answer_question(db_session, embedder, llm, "كم ساعة معتمدة بدها هندسة الأنظمة الذكية", log=False)
    assert r.kind == "faq" and r.answer == A_AR and r.found and r.faq_id == faq.id
    assert not llm.calls and not calls  # identical question: no model, not even an embedding
    assert r.sources[0].category == "faq" and r.invented_numbers == []

    r = await answer_question(db_session, embedder, llm, Q_EN.lower().rstrip("?") + " please", log=False)
    assert r.kind == "faq" and r.answer == A_EN and r.lang == "en"


async def test_pending_and_rejected_faqs_are_never_used(db_session) -> None:
    program = await _program(db_session)
    pending = await _faq(db_session, status="pending", program=program)
    llm = FakeLLM({"answer": "From the passages.", "found": True, "used": [1]})
    r = await answer_question(db_session, FakeEmbedder(), llm, Q_AR, log=False)
    assert r.kind == "rag" and r.faq_id is None and llm.calls
    pending.review_status = "rejected"
    await db_session.commit()
    assert (await answer_question(db_session, FakeEmbedder(), llm, Q_AR, log=False)).kind == "rag"
    assert await cur.faq_exact(db_session, Q_AR, "ar") is None
    pending.review_status = "approved"
    await db_session.commit()
    assert (await answer_question(db_session, FakeEmbedder(), llm, Q_AR, log=False)).kind == "faq"


async def test_near_match_needs_similarity_the_same_program_and_the_same_numbers(db_session) -> None:
    program = await _program(db_session)
    faq = await _faq(db_session, program=program)
    embedder = FakeEmbedder()
    assert await cur.embed_missing_faqs(db_session, embedder) == 2  # both questions get a vector
    assert await cur.embed_missing_faqs(db_session, embedder) == 0

    async def near(question: str, threshold: float = 0.6):  # noqa: ANN202
        [vec] = await embedder.embed([question])
        return await cur.faq_near(db_session, question, "ar", vec, threshold)

    hit = await near("هندسة الأنظمة الذكية كم ساعة معتمدة بدها؟")  # the same words in another order
    assert hit is not None and hit.faq_id == faq.id and hit.similarity > 0.9
    assert await near("كم ساعة معتمدة بدها هندسة العمارة؟") is None  # another program: never this answer
    assert await near("كم ساعة معتمدة بدها؟") is None  # no program named: ambiguous
    assert await near("كم ساعة معتمدة بدها هندسة الأنظمة الذكية بالسنة 2؟") is None  # an extra number
    assert await near("وين بيشتغلوا خريجين هندسة الأنظمة الذكية؟", threshold=0.9) is None  # not similar

    # end to end: the reworded question is answered by the FAQ, with one embedding call and no LLM
    llm = FakeLLM({"answer": "RAG.", "found": True, "used": [1]})
    import app.services.curated as module

    original = module.get_settings().faq_match_threshold
    module.get_settings().faq_match_threshold = 0.6
    try:
        r = await answer_question(db_session, embedder, llm, "هندسة الأنظمة الذكية كم ساعة معتمدة بدها؟", log=False)
    finally:
        module.get_settings().faq_match_threshold = original
    assert r.kind == "faq" and not llm.calls


async def test_faq_in_the_conversation_is_spoken_from_cache_and_logged(db_session) -> None:
    program = await _program(db_session)
    faq = await _faq(db_session, program=program)
    llm = FakeLLM()
    dm, sent, tts = _manager(db_session, llm)
    await dm.handle({"type": "text.input", "text": Q_AR})
    answer = next(m for m in sent if m["type"] == "answer.text")
    assert answer["kind"] == "faq" and answer["text"] == A_AR and answer["found"] and not llm.calls
    assert A_AR in " ".join(tts.spoken)
    turn = await db_session.scalar(select(Turn).where(Turn.session_id == dm.session_id))
    assert turn.used_faq_id == faq.id and turn.answer_kind == "faq" and turn.found is True


async def test_faq_review_api(admin_client, client, db_session, fake_tts) -> None:
    assert (await client.get("/api/admin/faqs")).status_code == 401
    program = await _program(db_session)
    created = await admin_client.post("/api/admin/faqs", json={
        "question_ar": "كم رسوم الساعة؟", "answer_ar": "الساعة ب 98765 دينار.",
    })  # fmt: skip
    vague = await admin_client.post("/api/admin/faqs", json={
        "program_id": program.id, "question_ar": "كم ساعة بتطلب الخطة؟", "answer_ar": "اسأل القبول والتسجيل.",
    })  # fmt: skip
    assert any("does not name the program" in i for i in vague.json()["issues"])  # which program?
    refused_vague = await admin_client.post(f"/api/admin/faqs/{vague.json()['id']}/review", json={"decision": "approve"})
    assert refused_vague.status_code == 409
    assert created.status_code == 201 and created.json()["status"] == "pending"
    faq_id = created.json()["id"]
    assert created.json()["issues"] == ["98765 is not in the approved sources"]
    refused = await admin_client.post(f"/api/admin/faqs/{faq_id}/review", json={"decision": "approve"})
    assert refused.status_code == 409 and not fake_tts.spoken
    fixed = await admin_client.patch(f"/api/admin/faqs/{faq_id}", json={"answer_ar": "اسأل دائرة القبول والتسجيل."})
    assert fixed.json()["issues"] == []
    ok = await admin_client.post(f"/api/admin/faqs/{faq_id}/review", json={"decision": "approve"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved" and ok.json()["audio_error"] is None
    assert fake_tts.spoken == ["اسأل دائرة القبول والتسجيل."]  # the speech is generated at approval
    edited = await admin_client.patch(f"/api/admin/faqs/{faq_id}", json={"question_ar": "قديش رسوم الساعة؟"})
    assert edited.json()["status"] == "pending"  # edited content is reviewed again
    assert any(i["id"] == faq_id for i in (await admin_client.get("/api/admin/faqs")).json()["items"])
    assert (await admin_client.delete(f"/api/admin/faqs/{faq_id}")).status_code == 204


# ---------------- pitches and comparisons ----------------
async def _pitch(db_session, program: Program, kind: str, status: str = "approved", **text) -> Pitch:  # noqa: ANN003
    data = {"text_ar": "ما بقدر أحكي عن تخصصات كليات ثانية، بس تخصصنا فيه مواد روبوتات وذكاء اصطناعي.",
            "text_en": "I can only speak about our faculty; our program has robotics and AI courses."} | text  # fmt: skip
    pitch = Pitch(program_id=program.id, topic=kind, review_status=status, **data)
    db_session.add(pitch)
    await db_session.commit()
    return pitch


async def test_comparison_is_answered_from_the_approved_pitch_only(db_session) -> None:
    program = await _program(db_session)
    question = "هندسة الأنظمة الذكية ولا هندسة البرمجيات؟"
    assert cur.is_comparison(question) and cur.programs_in(question) == [SLUG]
    pending = await _pitch(db_session, program, "compare", status="pending")
    llm = FakeLLM({"answer": "From the passages.", "found": True, "used": [1]})
    r = await answer_question(db_session, FakeEmbedder(), llm, question, log=False)
    assert r.kind == "rag" and not r.promotional  # a pending pitch is never used

    pending.review_status = "approved"
    await db_session.commit()
    llm.calls.clear()
    r = await answer_question(db_session, FakeEmbedder(), llm, question, log=False)
    assert r.kind == "pitch" and r.promotional and r.pitch_ids == [pending.id] and not llm.calls
    assert r.answer == pending.text_ar and r.sources[0].title == cur.PITCH_LABEL["ar"]
    r = await answer_question(db_session, FakeEmbedder(), llm, "Intelligent Systems Engineering vs Software Engineering?", log=False)
    assert r.kind == "pitch" and r.answer == pending.text_en

    # two programs of the faculty are compared from the sources, not from one program's pitch
    both = "شو الفرق بين هندسة الأنظمة الذكية وهندسة الطاقة المتجددة؟"
    assert len(cur.programs_in(both)) == 2 and await cur.comparison_answer(db_session, both, "ar") is None
    assert not cur.is_comparison("كم ساعة هندسة الأنظمة الذكية؟")

    dm, sent, tts = _manager(db_session, llm)
    await dm.handle({"type": "text.input", "text": question})
    answer = next(m for m in sent if m["type"] == "answer.text")
    assert answer["kind"] == "pitch" and answer["promotional"] is True
    turn = await db_session.scalar(select(Turn).where(Turn.session_id == dm.session_id))
    assert turn.used_pitch_ids == [pending.id] and turn.answer_kind == "pitch" and turn.used_chunk_ids is None


async def test_why_pitch_is_attached_to_the_passages(db_session) -> None:
    program = await _program(db_session)
    why = await _pitch(db_session, program, "why", text_ar="نقطة ترويجية فريدة زقزوقية.")
    await _pitch(db_session, program, "why", status="pending", text_ar="نقطة مش معتمدة.")
    llm = FakeLLM()
    question = "ليش أختار هندسة الأنظمة الذكية؟"
    assert cur.is_why(question)
    r = await answer_question(db_session, FakeEmbedder(), llm, question, log=False)
    prompt = llm.calls[0][1]
    assert "نقطة ترويجية فريدة زقزوقية." in prompt and "نقطة مش معتمدة." not in prompt
    assert any(p.source_url == f"pitch://{why.id}" for p in r.passages)
    llm2 = FakeLLM()
    await answer_question(db_session, FakeEmbedder(), llm2, "كم ساعة هندسة الأنظمة الذكية؟", log=False)
    assert "زقزوقية" not in llm2.calls[0][1]  # only for "why" and comparison questions


async def test_pitch_with_invented_number_or_claims_about_others_is_blocked(admin_client, db_session, fake_tts) -> None:
    program = await _program(db_session)
    created = await admin_client.post("/api/admin/pitches", json={
        "program_id": program.id, "kind": "compare",
        "text_ar": "تخصصنا أفضل من هندسة البرمجيات، و97531 خريج اشتغلوا، والشغل مضمون.",
    })  # fmt: skip
    assert created.status_code == 201 and created.json()["status"] == "pending" and created.json()["is_promotional"]
    pitch_id, issues = created.json()["id"], created.json()["issues"]
    assert "97531 is not in the program's approved sources" in issues
    assert any("promises an outcome" in i for i in issues) and any("speaks about others" in i for i in issues)
    refused = await admin_client.post(f"/api/admin/pitches/{pitch_id}/review", json={"decision": "approve"})
    assert refused.status_code == 409 and len(refused.json()["detail"]["issues"]) == len(issues)
    pitch = await db_session.get(Pitch, pitch_id)
    await db_session.refresh(pitch)
    assert pitch.review_status == "pending"

    fixed = await admin_client.patch(f"/api/admin/pitches/{pitch_id}", json={
        "text_ar": "ما بقدر أحكي عن كليات ثانية، بس بقدر أحكيلك عن تخصصنا وشو فيه."})  # fmt: skip
    assert fixed.json()["issues"] == []
    ok = await admin_client.post(f"/api/admin/pitches/{pitch_id}/review", json={"decision": "approve"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved" and fake_tts.spoken
    assert any(p["id"] == pitch_id for p in (await admin_client.get("/api/admin/pitches")).json()["items"])
    assert (await admin_client.post("/api/admin/pitches", json={"program_id": program.id, "kind": "interest:x"})).status_code == 422


async def test_drafting_stores_pending_faqs_and_pitches(db_session) -> None:
    faculty = await db_session.scalar(select(Faculty).limit(1))
    program = Program(faculty_id=faculty.id, slug="draft-test-program", name_ar="برنامج", name_en="Draft Program")
    source = Source(url="test-doc://draft", title="Draft plan", review_status="approved")
    db_session.add_all([program, source])
    await db_session.flush()
    db_session.add_all([
        Chunk(source_id=source.id, program_id=program.id, lang="en", category="study_plan",
              review_status="approved", text="Draft Program requires 150 credit hours."),
        Chunk(source_id=source.id, program_id=program.id, lang="en", review_status="pending", text="Fees are 77777."),
    ])  # fmt: skip
    await db_session.commit()
    reply = {
        "faqs": [
            {"question_ar": "كم ساعة برنامج المسودة؟", "question_en": "How many hours is Draft Program?",
             "answer_ar": "150 ساعة معتمدة.", "answer_en": "150 credit hours.", "passage": 1},
            {"question_ar": "كم الرسوم ببرنامج المسودة؟", "question_en": "What are the Draft Program fees?",
             "answer_ar": "77777 دينار.", "answer_en": "77777 dinars.", "passage": 1},
        ],
        "pitches": {"why_ar": "برنامج عملي.", "why_en": "A practical program.",
                    "compare_ar": "بحكي بس عن كليتنا. برنامجنا عملي.", "compare_en": "I only speak about our faculty."},
    }  # fmt: skip
    llm = FakeLLM(reply)
    report = await cur.draft_curated(db_session, llm, program)
    assert report.outcome == "drafted" and report.faqs_created == 2 and report.pitches_created == ["why", "compare"]
    assert any("77777" in i for i in report.issues)  # flagged for the reviewer; still only a draft
    prompt = llm.calls[0][1]
    assert "150 credit hours" in prompt and "77777" not in prompt  # pending passages are not given to the model
    faqs = (await db_session.scalars(select(Faq).where(Faq.program_id == program.id))).all()
    pitches = (await db_session.scalars(select(Pitch).where(Pitch.program_id == program.id))).all()
    assert {f.review_status for f in faqs} == {"pending"} and {p.review_status for p in pitches} == {"pending"}
    assert all(f.source_id == source.id for f in faqs)

    again = await cur.draft_curated(db_session, llm, program)  # a second run duplicates nothing
    assert again.faqs_created == 0 and again.faqs_skipped == 2 and again.pitches_created == []
    assert "no pitches" in llm.calls[1][1]
    empty = Program(faculty_id=faculty.id, slug="draft-empty", name_ar="فارغ", name_en="Empty")
    db_session.add(empty)
    await db_session.commit()
    # a program without its own approved passages still gets the general study rules as context;
    # the handbook draft (program=None) uses only categorised general passages
    general = await cur.draft_curated(db_session, FakeLLM({"faqs": []}), None)
    assert general.target == "handbook" and general.outcome in ("drafted", "no-sources")
