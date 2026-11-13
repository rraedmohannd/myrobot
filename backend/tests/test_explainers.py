"""Sprint 2 S2-S4: program explainers, interest matching and the idle-screen content.
Fakes only: no OpenAI call, and every row is rolled back after the test."""

import pytest
from sqlalchemy import select

from app.db.models import Chunk, Explainer, ExplainerSegment, Faculty, Media, Pitch, Program, Source, Turn
from app.main import app
from app.services import interests
from app.services.dialogue import PHRASES, DialogueManager
from app.services.explainers import (
    clean_scene,
    draft_explainer,
    explainer_request,
    find_issues,
    load_playable,
    match_program,
    screen_content,
    study_plan_url,
)
from app.services.speech import get_tts
from tests.fakes import FakeEmbedder, FakeLLM, FakeSTT, FakeTTS, OneSession

SLUG = "zorbology-engineering"  # a program that exists in no real data
PLAN_URL = "https://example.test/plan/zorbology"
COURSE = "Introduction to Zorbology"
GOOD_SEGMENTS = [
    {"text": "تخصص الزوربولوجي بيعلمك تبني أنظمة ذكية.", "scene": {"type": "title", "tagline": "أنظمة ذكية"}},
    {"text": "الخطة بدها 162 ساعة معتمدة.", "scene": {"type": "stat", "value": "162", "unit": "ساعة معتمدة"}},
    {"text": "من المواد اللي بتدرسها مقدمة بالزوربولوجي.", "scene": {"type": "courses", "courses": [COURSE]}},
    {"text": "وفيه مشروع تخرج وتدريب ميداني.", "scene": {"type": "keypoints", "points": ["مشروع تخرج", "تدريب ميداني"]}},
    {"text": "بتطلع بمهارات عملية.", "scene": {"type": "diagram", "nodes": ["حساسات", "معالجة", "حركة"]}},
    {"text": "صوّر الكود وشوف الخطة.", "scene": {"type": "qr", "caption": "صوّر الكود"}},
]


async def _program(db_session, slug: str = SLUG, plan_url: str = PLAN_URL) -> Program:
    faculty = await db_session.scalar(select(Faculty).limit(1))
    if faculty is None:
        faculty = Faculty(slug="test-faculty", name_ar="كلية", name_en="Faculty")
        db_session.add(faculty)
        await db_session.flush()
    program = Program(faculty_id=faculty.id, slug=slug, name_ar="هندسة الزوربولوجي", name_en="Zorbology Engineering")
    plan = Source(url=plan_url, title="Zorbology study plan", review_status="approved")
    db_session.add_all([program, plan])
    await db_session.flush()
    db_session.add_all([
        Chunk(source_id=plan.id, program_id=program.id, lang="en", category="study_plan", review_status="approved",
              text="Zorbology Engineering requires 162 credit hours in total. Graduation project and field training."),
        Chunk(source_id=plan.id, program_id=program.id, lang="en", category="courses", review_status="approved",
              text=f"1900101 {COURSE} (مقدمة بالزوربولوجي): 3 credit hours."),
        Chunk(source_id=plan.id, program_id=program.id, lang="en", category="careers", review_status="pending",
              text="Graduates earn 98765 dinars."),  # pending: must never be used
    ])  # fmt: skip
    await db_session.commit()
    return program


async def _explainer(db_session, program: Program, status: str = "approved", lang: str = "ar",
                     segments: list[dict] | None = None) -> Explainer:  # fmt: skip
    explainer = Explainer(program_id=program.id, lang=lang, status=status)
    db_session.add(explainer)
    await db_session.flush()
    for i, seg in enumerate(segments or GOOD_SEGMENTS, start=1):
        db_session.add(ExplainerSegment(explainer_id=explainer.id, position=i, text=seg["text"],
                                        scene=clean_scene(seg["scene"])))  # fmt: skip
    await db_session.commit()
    return explainer


def _manager(db_session, llm: FakeLLM | None = None):  # noqa: ANN202
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    tts = FakeTTS()
    dm = DialogueManager(send, OneSession(db_session), FakeEmbedder(), llm or FakeLLM(), FakeSTT([]), tts)
    return dm, sent, tts


@pytest.fixture
def alias(monkeypatch):
    """Lets the test program be named in a spoken request."""
    from app.services import explainers

    monkeypatch.setitem(explainers.PROGRAM_ALIASES, SLUG, ("الزوربولوجي", "zorbology"))


@pytest.fixture
def fake_tts():
    tts = FakeTTS()
    app.dependency_overrides[get_tts] = lambda: tts
    yield tts
    app.dependency_overrides.pop(get_tts, None)


# ---------------- requests and scenes ----------------
def test_explainer_requests_are_recognised() -> None:
    ise = "intelligent-systems-engineering"
    assert explainer_request("احكيلي عن تخصص هندسة الأنظمة الذكية") == ise
    assert explainer_request("شو هو تخصص الأنظمة الذكية؟") == ise
    assert explainer_request("ليش أدرس هندسة الطاقة المتجددة؟") == "renewable-energy-engineering"
    assert explainer_request("Tell me about Biomedical Engineering") == "biomedical-engineering"
    assert explainer_request("what is graphic design?") == "graphic-design"
    assert explainer_request("احكيلي عن التصميم الداخلي لو سمحت") == "interior-design"
    # a specific question that only mentions a program is NOT a request for the presentation
    assert explainer_request("كم ساعة معتمدة بدها هندسة الأنظمة الذكية؟") is None
    assert explainer_request("شو هي شروط القبول بهندسة العمارة؟") is None
    assert explainer_request("what is the tuition for architecture?") is None
    assert explainer_request("احكيلي عن الرسوم") is None
    assert match_program("بدي أسأل عن العمارة") == "architectural-engineering"
    assert match_program("وين بيشتغلوا خريجين الجرافيك؟") == "graphic-design"
    assert match_program("كم رسوم الجامعة؟") is None


def test_clean_scene_keeps_known_fields_only() -> None:
    scene = clean_scene({"type": "qr", "caption": "صوّر الكود", "url": "https://evil.example", "x": 1})
    assert scene == {"type": "qr", "caption": "صوّر الكود"}  # the address never comes from a draft
    assert clean_scene({"type": "hologram"}) == {"type": "title"}
    assert clean_scene({"type": "keypoints", "points": ["a", "", "b", "c", "d", "e"]})["points"] == ["a", "b", "c", "d"]
    steps = clean_scene({"type": "timeline", "steps": [{"label": "L1", "items": ["x"] * 9}, "junk"]})["steps"]
    assert steps == [{"label": "L1", "items": ["x", "x", "x"]}]
    assert clean_scene({"type": "media", "media": {"url": "/media/1"}}) == {"type": "media"}


def test_find_issues_numbers_courses_and_promises() -> None:
    sources = [f"The program requires 162 credit hours. 1900101 {COURSE}: 3 credit hours."]
    ok = [(s["text"], clean_scene(s["scene"])) for s in GOOD_SEGMENTS]
    assert find_issues(ok, sources) == []
    bad = [
        ("الخطة بدها 170 ساعة.", {"type": "stat", "value": "5", "unit": "سنوات"}),
        ("مواد حلوة.", {"type": "courses", "courses": [COURSE, "Quantum Zorbing"]}),
        ("الشغل مضمون بعد التخرج.", {"type": "title"}),
    ]
    issues = find_issues(bad, sources)
    assert {(i.segment, i.kind) for i in issues} == {(1, "number"), (2, "course"), (3, "claim")}
    assert sorted(i.detail.split()[0] for i in issues if i.kind == "number") == ["170", "5"]
    assert any(i.kind == "structure" for i in find_issues(ok[:2], sources))  # too short


# ---------------- drafting ----------------
async def test_drafting_stores_pending_only_and_uses_approved_chunks(db_session) -> None:
    program = await _program(db_session)
    llm = FakeLLM({"segments": [*GOOD_SEGMENTS[:5], {"text": "الراتب 98765 دينار.", "scene": {"type": "keypoints"}}]})
    result = await draft_explainer(db_session, llm, program, "ar")
    assert result.outcome == "created" and result.segments == 6
    assert result.explainer.status == "pending"  # a draft is never approved by the tool
    assert [(i.segment, i.kind) for i in result.issues] == [(6, "number")]  # 98765 is only in a PENDING chunk
    instructions, prompt = llm.calls[0]
    assert "162 credit hours" in prompt and COURSE in prompt and "LANGUAGE: ar" in prompt
    assert "98765" not in prompt  # pending chunks are not given to the model
    assert "ONLY" in instructions and "salaries" in instructions
    assert await load_playable(db_session, SLUG, "ar") is None  # pending: not playable

    # an approved explainer is not overwritten without --force; with it, it goes back to pending
    result.explainer.status = "approved"
    await db_session.commit()
    again = await draft_explainer(db_session, llm, program, "ar")
    assert again.outcome == "skipped-approved" and len(llm.calls) == 1
    forced = await draft_explainer(db_session, llm, program, "ar", force=True)
    assert forced.outcome == "replaced" and forced.explainer.status == "pending"
    rows = await db_session.scalars(select(ExplainerSegment).where(ExplainerSegment.explainer_id == forced.explainer.id))
    assert len(rows.all()) == 6


async def test_drafting_without_approved_sources_does_nothing(db_session) -> None:
    faculty = await db_session.scalar(select(Faculty).limit(1))
    program = Program(faculty_id=faculty.id, slug="empty-program", name_ar="فارغ", name_en="Empty")
    db_session.add(program)
    await db_session.commit()
    llm = FakeLLM({"segments": GOOD_SEGMENTS})
    result = await draft_explainer(db_session, llm, program, "en")
    assert result.outcome == "no-sources" and result.explainer is None and not llm.calls


# ---------------- review API ----------------
async def test_explainer_admin_needs_the_token(client) -> None:
    for path in ("/api/admin/explainers", "/api/admin/explainers/1", "/api/admin/interests"):
        assert (await client.get(path)).status_code == 401
    assert (await client.post("/api/admin/explainers/1/review", json={"decision": "approve"})).status_code == 401


async def test_invented_number_blocks_approval(admin_client, db_session, fake_tts) -> None:
    program = await _program(db_session)
    segments = [*GOOD_SEGMENTS[:5], {"text": "معدل القبول 85 بالمية.", "scene": {"type": "keypoints", "points": ["85%"]}}]
    explainer = await _explainer(db_session, program, status="pending", segments=segments)

    listing = (await admin_client.get("/api/admin/explainers")).json()
    row = next(i for i in listing["items"] if i["id"] == explainer.id)
    assert row["status"] == "pending" and row["issues"] == 1 and row["segments"] == 6

    r = await admin_client.post(f"/api/admin/explainers/{explainer.id}/review", json={"decision": "approve"})
    assert r.status_code == 409
    assert r.json()["detail"]["issues"][0] == {
        "segment": 6, "kind": "number", "detail": "85 is not in the program's approved sources",
    }  # fmt: skip
    await db_session.refresh(explainer)
    assert explainer.status == "pending" and not fake_tts.spoken

    # the reviewer fixes the segment; then approval works and the speech is generated
    detail = (await admin_client.get(f"/api/admin/explainers/{explainer.id}")).json()
    assert not detail["can_approve"]
    seg6 = detail["segments"][5]["id"]
    fixed = await admin_client.patch(f"/api/admin/explainers/segments/{seg6}", json={
        "text": "اسأل دائرة القبول والتسجيل عن معدل القبول.",
        "scene": {"type": "qr", "caption": "صوّر الكود", "url": "https://evil.example"},
    })  # fmt: skip
    assert fixed.status_code == 200 and fixed.json()["can_approve"]
    assert fixed.json()["segments"][5]["preview_scene"]["url"] == PLAN_URL  # from the source record
    ok = await admin_client.post(f"/api/admin/explainers/{explainer.id}/review", json={"decision": "approve"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert len(fake_tts.spoken) == 6  # one cached speech file per segment

    # editing approved content sends it back to review
    edited = await admin_client.patch(f"/api/admin/explainers/segments/{seg6}", json={"text": "نص جديد."})
    assert edited.json()["status"] == "pending"
    rejected = await admin_client.post(f"/api/admin/explainers/{explainer.id}/review", json={"decision": "reject"})
    assert rejected.json()["status"] == "rejected"


# ---------------- playback ----------------
async def test_only_approved_explainers_play_and_the_rest_is_rag(db_session, alias) -> None:
    program = await _program(db_session)
    explainer = await _explainer(db_session, program, status="pending")
    dm, sent, tts = _manager(db_session)
    await dm.handle({"type": "text.input", "text": "احكيلي عن تخصص الزوربولوجي"})
    assert not any(m["type"].startswith("explainer.") for m in sent)
    assert any(m["type"] == "answer.text" for m in sent)  # the normal grounded answer instead
    assert dm.state == "FOLLOW_UP" and dm.explainer is None

    explainer.status = "rejected"
    await db_session.commit()
    assert await load_playable(db_session, SLUG, "ar") is None
    explainer.status = "approved"
    await db_session.commit()
    assert await load_playable(db_session, SLUG, "en") is None  # no English explainer: RAG for English
    assert (await load_playable(db_session, SLUG, "ar")).name == "هندسة الزوربولوجي"


async def test_approved_explainer_stops_playing_when_its_source_is_withdrawn(db_session) -> None:
    program = await _program(db_session)
    await _explainer(db_session, program)
    assert await load_playable(db_session, SLUG, "ar") is not None
    plan_chunk = await db_session.scalar(
        select(Chunk).where(Chunk.program_id == program.id, Chunk.category == "study_plan")
    )
    plan_chunk.review_status = "stale"  # the page changed: "162" is no longer approved content
    await db_session.commit()
    assert await load_playable(db_session, SLUG, "ar") is None
    content = await screen_content(db_session)
    assert SLUG not in [p["slug"] for p in content["programs"]]
    assert not any(s["program"]["slug"] == SLUG for s in content["attract"])


async def test_explainer_segments_scenes_pause_and_continue(db_session, alias) -> None:
    program = await _program(db_session)
    await _explainer(db_session, program)
    llm = FakeLLM()
    dm, sent, tts = _manager(db_session, llm)
    await dm.handle({"type": "text.input", "text": "احكيلي عن تخصص الزوربولوجي"})

    assert not llm.calls  # pre-approved content: no LLM wait
    assert not any(m["type"] == "answer.text" for m in sent)
    start = next(m for m in sent if m["type"] == "explainer.start")
    assert start["total"] == 6 and start["program"] == {"slug": SLUG, "name": "هندسة الزوربولوجي"}
    segs = [m for m in sent if m["type"] == "explainer.segment"]
    assert [(m["index"], m["total"], m["scene"]["type"]) for m in segs] == [(1, 6, "title"), (2, 6, "stat"), (3, 6, "courses")]
    assert segs[1]["scene"]["value"] == "162" and segs[0]["scene"]["program"]["slug"] == SLUG
    # each scene message comes right before the audio of its segment
    for seg in segs:
        audio = sent[sent.index(seg) + 1]
        assert audio["type"] == "answer.audio" and audio["segment"] == seg["index"]
        assert audio["text"] == GOOD_SEGMENTS[seg["index"] - 1]["text"]
    assert sent[-3]["type"] == "explainer.pause" or any(m["type"] == "explainer.pause" for m in sent)
    assert tts.spoken[-1] == PHRASES["explainer_ask"]["ar"] and dm.state == "FOLLOW_UP"
    states = [m["state"] for m in sent if m["type"] == "dialogue.state"]
    assert "EXPLAINING" in states

    await dm.handle({"type": "text.input", "text": "اه كمل"})
    segs = [m for m in sent if m["type"] == "explainer.segment"]
    assert [m["index"] for m in segs] == [1, 2, 3, 4, 5, 6]
    assert segs[5]["scene"]["type"] == "qr" and segs[5]["scene"]["url"] == PLAN_URL
    assert [m for m in sent if m["type"] == "answer.audio" and m.get("segment") == 6][0]["last"] is True
    assert any(m["type"] == "explainer.end" and m["reason"] == "finished" for m in sent)
    assert tts.spoken[-1] == PHRASES["follow_up"]["ar"] and dm.state == "FOLLOW_UP" and dm.explainer is None

    turns = (await db_session.scalars(select(Turn).where(Turn.session_id == dm.session_id).order_by(Turn.idx))).all()
    assert [t.intent for t in turns] == ["explainer", "explainer_more"]
    assert turns[0].found is None and GOOD_SEGMENTS[0]["text"] in turns[0].answer_text


async def test_question_during_the_pause_ends_the_explainer(db_session, alias) -> None:
    program = await _program(db_session)
    await _explainer(db_session, program)
    dm, sent, tts = _manager(db_session)
    await dm.handle({"type": "text.input", "text": "شو هو تخصص الزوربولوجي؟"})
    assert dm.explainer is not None
    await dm.handle({"type": "text.input", "text": "كم ساعة معتمدة بده التخصص؟"})
    assert dm.explainer is None
    assert any(m["type"] == "explainer.end" and m["reason"] == "question" for m in sent)
    assert any(m["type"] == "answer.text" for m in sent)
    assert len([m for m in sent if m["type"] == "explainer.segment"]) == 3


async def test_silence_during_the_pause_continues_and_stop_button_stops(db_session) -> None:
    program = await _program(db_session)
    await _explainer(db_session, program)
    dm, sent, tts = _manager(db_session)
    await dm.handle({"type": "explainer.start", "program": SLUG})  # the program button, no session yet
    assert dm.session_id and PHRASES["greeting"]["ar"] not in " ".join(tts.spoken)
    assert len([m for m in sent if m["type"] == "explainer.segment"]) == 3
    await dm.handle({"type": "listen.timeout"})
    assert len([m for m in sent if m["type"] == "explainer.segment"]) == 6 and dm.session_id

    await dm.handle({"type": "explainer.start", "program": SLUG})
    await dm.handle({"type": "explainer.stop"})
    assert dm.explainer is None and sent[-1] == {"type": "dialogue.state", "state": "FOLLOW_UP"}
    assert any(m["type"] == "explainer.end" and m["reason"] == "stopped" for m in sent)
    await dm.handle({"type": "explainer.continue"})  # nothing left to continue
    assert len([m for m in sent if m["type"] == "explainer.segment"]) == 9

    await dm.handle({"type": "explainer.start", "program": "no-such-program"})
    assert sent[-1]["type"] == "error" and sent[-1]["code"] == "no_explainer"


async def test_short_explainer_plays_without_a_pause(db_session) -> None:
    program = await _program(db_session)
    await _explainer(db_session, program, segments=GOOD_SEGMENTS[:4])
    dm, sent, tts = _manager(db_session)
    await dm.handle({"type": "explainer.start", "program": SLUG})
    assert [m["index"] for m in sent if m["type"] == "explainer.segment"] == [1, 2, 3, 4]
    assert not any(m["type"] == "explainer.pause" for m in sent) and dm.explainer is None


async def test_qr_uses_the_source_url_and_media_must_be_approved(db_session) -> None:
    program = await _program(db_session)
    assert await study_plan_url(db_session, program) == PLAN_URL
    program.study_plan_url = "https://example.test/official-plan.pdf"  # the program record wins
    await db_session.commit()
    assert await study_plan_url(db_session, program) == "https://example.test/official-plan.pdf"

    other = await _program(db_session, slug="document-only", plan_url="meu-document://study-plans/document-only")
    assert await study_plan_url(db_session, other) is None  # an identifier is not a web address

    pending = Media(kind="image", path="x.png", program_id=program.id, review_status="pending", mime="image/png")
    db_session.add(pending)
    segments = [*GOOD_SEGMENTS[:3], {"text": "شوف الصورة.", "scene": {"type": "media"}}]
    await _explainer(db_session, program, segments=segments)
    playable = await load_playable(db_session, SLUG, "ar")
    assert playable.segments[3].scene["type"] == "media" and playable.segments[3].scene["media"] is None
    pending.review_status = "approved"
    await db_session.commit()
    playable = await load_playable(db_session, SLUG, "ar")
    assert playable.segments[3].scene["media"]["url"] == f"/media/{pending.id}"


# ---------------- interest matching (S3) ----------------
def test_interest_words() -> None:
    assert interests.is_interest_request("مش عارف شو أدرس")
    assert interests.is_interest_request("ممكن تساعدني أختار تخصص؟") is False  # different wording: RAG
    assert interests.is_interest_request("ساعدني أختار")
    assert interests.is_interest_request("I'm not sure what to study")
    assert not interests.is_interest_request("كم ساعة هندسة العمارة؟")
    assert interests.match_interest("بحب الكمبيوتر والبرمجة") == "computers_ai"
    assert interests.match_interest("الطب والأحياء") == "medicine_biology"
    assert interests.match_interest("I like art and design") == "art_design"
    assert interests.match_interest("بحب الرياضيات") == "maths_physics"
    assert interests.match_interest("ما بعرف") is None
    assert interests.is_yes("اه") and interests.is_yes("نعم لو سمحت") and interests.is_yes("Yes please")
    assert not interests.is_yes("لا") and not interests.is_yes("كم ساعة؟")
    assert set(interests.DEFAULT_MAPPING) == set(interests.INTERESTS)


async def _mapping(db_session, program: Program, key: str, status: str = "approved") -> Pitch:
    pitch = Pitch(program_id=program.id, topic=f"interest:{key}", text_ar="أنظمة ذكية", review_status=status)
    db_session.add(pitch)
    await db_session.commit()
    return pitch


async def test_interest_flow_suggests_approved_mapping_and_offers_the_explainer(db_session) -> None:
    program = await _program(db_session)
    await _explainer(db_session, program)
    pitch = await _mapping(db_session, program, "computers_ai")
    hidden = await _program(db_session, slug="hidden-program", plan_url="https://example.test/hidden")
    await _mapping(db_session, hidden, "energy_environment", status="pending")  # never suggested

    dm, sent, tts = _manager(db_session)
    await dm.handle({"type": "text.input", "text": "مش عارف شو أدرس"})
    q1 = next(m for m in sent if m["type"] == "interest.question")
    assert q1["step"] == 1 and len(q1["options"]) == 6 and " ".join(tts.spoken).endswith(PHRASES["interest_q1"]["ar"])
    assert dm.state == "FOLLOW_UP" and not any(m["type"] == "answer.text" for m in sent)

    await dm.handle({"type": "text.input", "text": "بحب الكمبيوتر"})
    q2 = [m for m in sent if m["type"] == "interest.question"][-1]
    assert q2["step"] == 2 and "computers_ai" not in [o["key"] for o in q2["options"]]
    assert q2["options"][-1]["key"] == "none"

    await dm.handle({"type": "interest.choice", "key": "energy_environment"})  # a screen button
    result = next(m for m in sent if m["type"] == "interest.result")
    assert [p["slug"] for p in result["programs"]] == [SLUG]  # the pending mapping is not used
    assert result["programs"][0] == {"slug": SLUG, "name": "هندسة الزوربولوجي", "line": "أنظمة ذكية", "has_explainer": True}
    assert "بنصحك تتعرف على هندسة الزوربولوجي" in result["text"] and "بدك أحكيلك عن" in result["text"]
    assert "بضمن" not in result["text"] and "مضمون" not in result["text"]
    assert dm.state == "FOLLOW_UP" and dm.interest.step == 3

    await dm.handle({"type": "text.input", "text": "اه"})  # the explainer is offered at the end
    assert [m["index"] for m in sent if m["type"] == "explainer.segment"] == [1, 2, 3]
    turns = (await db_session.scalars(select(Turn).where(Turn.session_id == dm.session_id).order_by(Turn.idx))).all()
    assert [t.intent for t in turns] == ["interest", "interest", "interest_result", "explainer"]
    assert turns[2].used_pitch_ids == [pitch.id]


async def test_interest_flow_without_approved_mapping_falls_back(db_session) -> None:
    program = await _program(db_session)
    await _mapping(db_session, program, "computers_ai", status="pending")
    dm, sent, tts = _manager(db_session)
    await dm.handle({"type": "text.input", "text": "مش عارف شو أدرس"})
    assert not any(m["type"] == "interest.question" for m in sent)
    assert any(m["type"] == "answer.text" for m in sent)  # normal answer path
    await dm.handle({"type": "interest.start"})
    assert sent[-1]["type"] == "error" and sent[-1]["code"] == "no_interests"


async def test_interest_flow_no_second_interest_and_real_questions_leave_it(db_session) -> None:
    program = await _program(db_session)
    await _mapping(db_session, program, "art_design")  # approved, but the program has no explainer
    dm, sent, tts = _manager(db_session)
    await dm.handle({"type": "interest.start"})
    assert dm.interest is not None and dm.session_id
    await dm.handle({"type": "text.input", "text": "الفن والتصميم"})
    await dm.handle({"type": "text.input", "text": "لا"})  # "that's all" is not a goodbye here
    result = next(m for m in sent if m["type"] == "interest.result")
    assert result["programs"][0]["has_explainer"] is False and "اسألني عنه" in result["text"]
    assert dm.session_id and dm.interest is None

    await dm.handle({"type": "interest.start"})
    await dm.handle({"type": "text.input", "text": "كم ساعة معتمدة بده تخصص الزوربولوجي؟"})
    assert dm.interest is None and any(m["type"] == "answer.text" for m in sent)

    await dm.handle({"type": "interest.start"})
    await dm.handle({"type": "interest.choice", "key": "medicine_biology"})
    await dm.handle({"type": "interest.choice", "key": "none"})
    assert [m for m in sent if m["type"] == "interest.result"][-1]["programs"] == []
    assert PHRASES["interest_none"]["ar"] in " ".join(tts.spoken)


async def test_interest_seed_and_review_api(admin_client, db_session, monkeypatch) -> None:
    program = await _program(db_session)
    monkeypatch.setattr(interests, "DEFAULT_MAPPING", {"computers_ai": (SLUG,), "art_design": ("no-such",)})
    report = (await admin_client.post("/api/admin/interests/seed")).json()
    assert report["created"] == [] and report["no_approved_explainer"] == [f"computers_ai -> {SLUG}"]

    await _explainer(db_session, program)
    report = (await admin_client.post("/api/admin/interests/seed")).json()
    assert report["created"] == [f"computers_ai -> {SLUG}"]
    assert (await admin_client.post("/api/admin/interests/seed")).json()["exists"] == [f"computers_ai -> {SLUG}"]
    items = (await admin_client.get("/api/admin/interests")).json()["items"]
    row = next(i for i in items if i["program"] and i["program"]["slug"] == SLUG)
    assert row["status"] == "pending" and row["text_ar"] == "أنظمة ذكية" and row["is_promotional"]
    assert not await interests.has_approved_mapping(db_session) or row["status"] == "pending"

    bad = await admin_client.patch(f"/api/admin/interests/{row['id']}", json={"text_ar": "شغل مضمون وراتب 4321"})
    assert len(bad.json()["issues"]) == 2
    refused = await admin_client.post(f"/api/admin/interests/{row['id']}/review", json={"decision": "approve"})
    assert refused.status_code == 409
    await admin_client.patch(f"/api/admin/interests/{row['id']}", json={"text_ar": "أنظمة ذكية وروبوتات"})
    ok = await admin_client.post(f"/api/admin/interests/{row['id']}/review", json={"decision": "approve"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert [s.slug for s in await interests.suggest(db_session, ["computers_ai"])] == [SLUG]
    edited = await admin_client.patch(f"/api/admin/interests/{row['id']}", json={"text_ar": "نص ثاني"})
    assert edited.json()["status"] == "pending"  # edited text is reviewed again
    assert await interests.suggest(db_session, ["computers_ai"]) == []

    added = await admin_client.post("/api/admin/interests", json={"interest": "maths_physics", "program_id": program.id})
    assert added.status_code == 201 and added.json()["status"] == "pending"
    assert (await admin_client.post("/api/admin/interests", json={"interest": "cooking", "program_id": program.id})).status_code == 422
    assert (await admin_client.delete(f"/api/admin/interests/{added.json()['id']}")).status_code == 204


# ---------------- attract mode content (S4) ----------------
async def test_idle_screen_content_is_approved_only(admin_client, db_session) -> None:
    program = await _program(db_session)
    explainer = await _explainer(db_session, program, status="pending")
    content = await screen_content(db_session)
    assert SLUG not in [p["slug"] for p in content["programs"]]
    assert not any(s["program"]["slug"] == SLUG for s in content["attract"])

    explainer.status = "approved"
    await db_session.commit()
    r = await admin_client.get("/api/robot/screen")  # public endpoint (the token is not needed)
    assert r.status_code == 200
    body = r.json()
    entry = next(p for p in body["programs"] if p["slug"] == SLUG)
    assert entry["name_ar"] == "هندسة الزوربولوجي" and entry["langs"] == ["ar"]
    mine = [s for s in body["attract"] if s["program"]["slug"] == SLUG]
    assert [s["type"] for s in mine] == ["title", "stat"] and mine[1]["value"] == "162"
    assert isinstance(body["interests"], bool)

    explainer.status = "rejected"
    await db_session.commit()
    assert SLUG not in [p["slug"] for p in (await screen_content(db_session))["programs"]]


async def test_robot_page_has_explainer_scenes_and_local_qr(client) -> None:
    robot = (await client.get("/robot")).text
    for needle in ("/static/explainer-scenes.js", "/static/vendor/qrcode/qrcode.js", "ساعدني أختار", "قرّب واحكي معي"):
        assert needle in robot, needle
    assert "https://" not in robot and "http://" not in robot.replace("http://www.w3.org", "")
    scenes = (await client.get("/static/explainer-scenes.js")).text
    for kind in ("title", "keypoints", "stat", "timeline", "courses", "diagram", "media", "qr"):
        assert f"    {kind}(scene" in scenes, kind
    for slug in ("intelligent-systems-engineering", "renewable-energy-engineering", "architectural-engineering",
                 "interior-design", "graphic-design", "biomedical-engineering"):  # fmt: skip
        assert slug in scenes, slug
    assert "https://" not in scenes and "http://" not in scenes.replace("http://www.w3.org", "")
    assert (await client.get("/static/vendor/qrcode/qrcode.js")).status_code == 200
    assert (await client.get("/static/vendor/qrcode/LICENSE")).status_code == 200
    admin = (await client.get("/admin")).text
    assert 'data-view="explainers"' in admin and "/static/explainer-scenes.js" in admin
