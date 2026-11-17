"""Sprint 2 S3: "what do you like?" interest matching. The interest -> program mapping is
curated, promotional data (pitches table) and only approved rows are ever used.
Fakes only; every row is rolled back after the test."""

from sqlalchemy import select

from app.db.models import Pitch, Program, Turn
from app.services import interests
from app.services.dialogue import PHRASES
from tests.test_explainers import SLUG, _explainer, _manager, _program


def test_interest_words() -> None:
    assert interests.is_interest_request("مش عارف شو أدرس")
    assert (
        interests.is_interest_request("ممكن تساعدني أختار تخصص؟") is False
    )  # different wording: RAG
    assert interests.is_interest_request("ساعدني أختار")
    assert interests.is_interest_request("I'm not sure what to study")
    assert not interests.is_interest_request("كم ساعة هندسة العمارة؟")
    assert interests.match_interest("بحب الكمبيوتر والبرمجة") == "computers_ai"
    assert interests.match_interest("الطب والأحياء") == "medicine_biology"
    assert interests.match_interest("I like art and design") == "art_design"
    assert interests.match_interest("بحب الرياضيات") == "maths_physics"
    assert interests.match_interest("ما بعرف") is None
    assert (
        interests.is_yes("اه")
        and interests.is_yes("نعم لو سمحت")
        and interests.is_yes("Yes please")
    )
    assert not interests.is_yes("لا") and not interests.is_yes("كم ساعة؟")
    assert set(interests.DEFAULT_MAPPING) == set(interests.INTERESTS)


async def _mapping(db_session, program: Program, key: str, status: str = "approved") -> Pitch:
    pitch = Pitch(
        program_id=program.id, topic=f"interest:{key}", text_ar="أنظمة ذكية", review_status=status
    )
    db_session.add(pitch)
    await db_session.commit()
    return pitch


async def test_interest_flow_suggests_approved_mapping_and_offers_the_explainer(db_session) -> None:
    program = await _program(db_session)
    await _explainer(db_session, program)
    pitch = await _mapping(db_session, program, "computers_ai")
    hidden = await _program(
        db_session, slug="hidden-program", plan_url="https://example.test/hidden"
    )
    await _mapping(db_session, hidden, "energy_environment", status="pending")  # never suggested

    dm, sent, tts = _manager(db_session)
    await dm.handle({"type": "text.input", "text": "مش عارف شو أدرس"})
    q1 = next(m for m in sent if m["type"] == "interest.question")
    assert (
        q1["step"] == 1
        and len(q1["options"]) == 6
        and " ".join(tts.spoken).endswith(PHRASES["interest_q1"]["ar"])
    )
    assert dm.state == "FOLLOW_UP" and not any(m["type"] == "answer.text" for m in sent)

    await dm.handle({"type": "text.input", "text": "بحب الكمبيوتر"})
    q2 = [m for m in sent if m["type"] == "interest.question"][-1]
    assert q2["step"] == 2 and "computers_ai" not in [o["key"] for o in q2["options"]]
    assert q2["options"][-1]["key"] == "none"

    await dm.handle({"type": "interest.choice", "key": "energy_environment"})  # a screen button
    result = next(m for m in sent if m["type"] == "interest.result")
    assert [p["slug"] for p in result["programs"]] == [SLUG]  # the pending mapping is not used
    assert result["programs"][0] == {
        "slug": SLUG,
        "name": "هندسة الزوربولوجي",
        "line": "أنظمة ذكية",
        "has_explainer": True,
    }
    assert (
        "بنصحك تتعرف على هندسة الزوربولوجي" in result["text"] and "بدك أحكيلك عن" in result["text"]
    )
    assert "بضمن" not in result["text"] and "مضمون" not in result["text"]
    assert dm.state == "FOLLOW_UP" and dm.interest.step == 3

    await dm.handle({"type": "text.input", "text": "اه"})  # the explainer is offered at the end
    assert [m["index"] for m in sent if m["type"] == "explainer.segment"] == [1, 2, 3]
    turns = (
        await db_session.scalars(
            select(Turn).where(Turn.session_id == dm.session_id).order_by(Turn.idx)
        )
    ).all()
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
    monkeypatch.setattr(
        interests, "DEFAULT_MAPPING", {"computers_ai": (SLUG,), "art_design": ("no-such",)}
    )
    report = (await admin_client.post("/api/admin/interests/seed")).json()
    assert report["created"] == [] and report["no_approved_explainer"] == [
        f"computers_ai -> {SLUG}"
    ]

    await _explainer(db_session, program)
    report = (await admin_client.post("/api/admin/interests/seed")).json()
    assert report["created"] == [f"computers_ai -> {SLUG}"]
    assert (await admin_client.post("/api/admin/interests/seed")).json()["exists"] == [
        f"computers_ai -> {SLUG}"
    ]
    items = (await admin_client.get("/api/admin/interests")).json()["items"]
    row = next(i for i in items if i["program"] and i["program"]["slug"] == SLUG)
    assert row["status"] == "pending" and row["text_ar"] == "أنظمة ذكية" and row["is_promotional"]
    assert not await interests.has_approved_mapping(db_session) or row["status"] == "pending"

    bad = await admin_client.patch(
        f"/api/admin/interests/{row['id']}", json={"text_ar": "شغل مضمون وراتب 4321"}
    )
    assert len(bad.json()["issues"]) == 2
    refused = await admin_client.post(
        f"/api/admin/interests/{row['id']}/review", json={"decision": "approve"}
    )
    assert refused.status_code == 409
    await admin_client.patch(
        f"/api/admin/interests/{row['id']}", json={"text_ar": "أنظمة ذكية وروبوتات"}
    )
    ok = await admin_client.post(
        f"/api/admin/interests/{row['id']}/review", json={"decision": "approve"}
    )
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert [s.slug for s in await interests.suggest(db_session, ["computers_ai"])] == [SLUG]
    edited = await admin_client.patch(
        f"/api/admin/interests/{row['id']}", json={"text_ar": "نص ثاني"}
    )
    assert edited.json()["status"] == "pending"  # edited text is reviewed again
    assert await interests.suggest(db_session, ["computers_ai"]) == []

    added = await admin_client.post(
        "/api/admin/interests", json={"interest": "maths_physics", "program_id": program.id}
    )
    assert added.status_code == 201 and added.json()["status"] == "pending"
    assert (
        await admin_client.post(
            "/api/admin/interests", json={"interest": "cooking", "program_id": program.id}
        )
    ).status_code == 422
    assert (
        await admin_client.delete(f"/api/admin/interests/{added.json()['id']}")
    ).status_code == 204
