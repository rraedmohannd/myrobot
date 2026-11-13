"""Sprint 3 S4: places, labs and directions. Only approved entries typed in by the student are
used; with no approved match the robot says it does not know and refers to the reception.
Fakes only; every row is rolled back after the test."""

from app.db.models import Chunk, Lab, Media, Place, Source
from app.services import curated as cur
from app.services.chat import NOT_FOUND, answer_question
from tests.fakes import FakeEmbedder, FakeLLM
from tests.test_curated import _manager


def test_location_questions() -> None:
    assert cur.is_location_question("وين القبول والتسجيل؟")
    assert cur.is_location_question("Where is the robotics lab?")
    assert cur.is_location_question("كيف أوصل لمكتب العميد؟")
    assert not cur.is_location_question("وين بيشتغلوا خريجين هندسة الأنظمة الذكية؟")
    assert not cur.is_location_question("Where do graduates work?")
    assert not cur.is_location_question("كم ساعة معتمدة؟")
    text_ = "هاتف الجامعة 06 4790222؛ دائرة القبول والتسجيل 222 و223؛ الدائرة المالية 445."
    assert cur.contact_lines(text_, ["دائره القبول والتسجيل", "القبول"]) == ["دائرة القبول والتسجيل 222 و223"]


async def test_places_answer_only_when_approved_and_never_invent(db_session) -> None:
    source = Source(url="test-doc://contacts", title="Handbook contacts", review_status="approved")
    db_session.add(source)
    await db_session.flush()
    contacts = Chunk(source_id=source.id, lang="ar", category="contacts", review_status="approved",
                     text="هاتف الجامعة 06 1112223؛ دائرة الزربطة والتسجيل 222 و223؛ الدائرة المالية 445.")  # fmt: skip
    photo = Media(kind="image", path="p.png", review_status="pending", mime="image/png", alt_ar="مدخل الدائرة")
    db_session.add_all([contacts, photo])
    await db_session.flush()
    place = Place(name_ar="دائرة الزربطة والتسجيل", name_en="Zarbata and Registration Department",
                  building="مبنى الاختبار", floor="1", keywords="الزربطة, zarbata",
                  directions_ar="امشي لآخر الممر، أول باب على اليمين.", directions_en="Walk to the end of the corridor, first door on the right.",
                  contact_chunk_id=contacts.id, media_id=photo.id, review_status="pending")  # fmt: skip
    db_session.add(place)
    await db_session.commit()

    question = "وين الزربطة؟"
    not_found = FakeLLM({"answer": "ما عندي هاي المعلومة.", "found": False, "used": []})
    r = await answer_question(db_session, FakeEmbedder(), not_found, question, log=False)
    assert r.kind == "place_unknown" and not r.found and r.answer == cur.PLACE_UNKNOWN["ar"]  # pending: not used
    assert "الاستقبال" in r.answer and "ممر" not in r.answer

    place.review_status = "approved"
    await db_session.commit()
    llm = FakeLLM()
    r = await answer_question(db_session, FakeEmbedder(), llm, question, log=False)
    assert r.kind == "place" and r.found and not llm.calls
    assert r.answer == "دائرة الزربطة والتسجيل: امشي لآخر الممر، أول باب على اليمين."
    assert r.title == "دائرة الزربطة والتسجيل" and r.lines[0] == "مبنى الاختبار، الطابق 1"
    assert r.lines[1] == "دائرة الزربطة والتسجيل 222 و223"  # copied from the approved contacts passage
    assert r.media is None  # the photo is still pending
    photo.review_status = "approved"
    await db_session.commit()
    r = await answer_question(db_session, FakeEmbedder(), llm, "Where is the Zarbata and Registration Department?", log=False)
    assert r.answer.endswith("first door on the right.") and r.media == {"kind": "image", "url": f"/media/{photo.id}", "alt": ""}

    # in the conversation: the card shows the place, its floor, the contact line and the photo
    dm, sent, tts = _manager(db_session, llm)
    await dm.handle({"type": "text.input", "text": "كيف أوصل لدائرة الزربطة والتسجيل؟"})
    card = next(m["card"] for m in sent if m["type"] == "display.update" and m.get("card"))
    assert card["title"] == "دائرة الزربطة والتسجيل" and card["media"]["url"] == f"/media/{photo.id}"
    assert card["lines"][1:] == ["مبنى الاختبار، الطابق 1", "دائرة الزربطة والتسجيل 222 و223"]
    assert next(m for m in sent if m["type"] == "answer.text")["kind"] == "place"

    # a place the robot was never told about: it does not guess
    r = await answer_question(db_session, FakeEmbedder(), not_found, "وين مكتب الخنفشار؟", log=False)
    assert r.kind == "place_unknown" and r.answer == cur.PLACE_UNKNOWN["ar"]
    # "where do graduates work" is not a place question: the normal not-found line
    r = await answer_question(db_session, FakeEmbedder(), not_found, "وين بيشتغلوا الخريجين؟", log=False)
    assert r.kind == "rag" and r.answer != cur.PLACE_UNKNOWN["ar"]
    assert NOT_FOUND["ar"] != cur.PLACE_UNKNOWN["ar"]
    # a location question the sources DO answer keeps its sourced answer
    found = FakeLLM({"answer": "الجامعة بعمّان.", "found": True, "used": [1]})
    r = await answer_question(db_session, FakeEmbedder(), found, "وين موقع الجامعة؟", log=False)
    assert r.kind == "rag" and r.found


async def test_labs_need_approval_and_a_typed_location(db_session) -> None:
    lab = Lab(name_ar="مختبر الزقازيق الذكية", name_en="Smart Zaqaziq Lab", keywords="الزقازيق",
              location_hint="الطابق الثاني، آخر الممر.", review_status="pending")  # fmt: skip
    silent = Lab(name_ar="مختبر البلابل", name_en="Balabel Lab", review_status="approved")  # no location typed
    db_session.add_all([lab, silent])
    await db_session.commit()
    assert await cur.place_answer(db_session, "وين مختبر الزقازيق الذكية؟", "ar") is None
    lab.review_status = "approved"
    await db_session.commit()
    answer = await cur.place_answer(db_session, "وين مختبر الزقازيق الذكية؟", "ar")
    assert answer.kind == "place" and answer.text == "مختبر الزقازيق الذكية: الطابق الثاني، آخر الممر."
    assert await cur.place_answer(db_session, "وين مختبر البلابل؟", "ar") is None  # nothing to say: no guess


async def test_places_and_labs_admin_crud(admin_client, client, db_session) -> None:
    assert (await client.get("/api/admin/places")).status_code == 401
    created = await admin_client.post("/api/admin/places", json={"name_ar": "قاعة الفحص", "name_en": "Test Hall"})
    assert created.status_code == 201 and created.json()["status"] == "pending"
    place_id = created.json()["id"]
    refused = await admin_client.post(f"/api/admin/places/{place_id}/review", json={"decision": "approve"})
    assert refused.status_code == 409  # a name alone is not a direction
    patched = await admin_client.patch(f"/api/admin/places/{place_id}", json={"floor": "2", "keywords": "الفحص"})
    assert patched.json()["issues"] == [] and patched.json()["floor"] == "2"
    assert (await admin_client.patch(f"/api/admin/places/{place_id}", json={"contact_chunk_id": 999999999})).status_code == 422
    ok = await admin_client.post(f"/api/admin/places/{place_id}/review", json={"decision": "approve"})
    assert ok.json()["status"] == "approved"
    answer = await cur.place_answer(db_session, "وين قاعة الفحص؟", "ar")
    assert answer is not None and answer.text == "قاعة الفحص: الطابق 2"
    edited = await admin_client.patch(f"/api/admin/places/{place_id}", json={"building": "مبنى ب"})
    assert edited.json()["status"] == "pending"  # edited: reviewed again, and unused meanwhile
    assert await cur.place_answer(db_session, "وين قاعة الفحص؟", "ar") is None

    lab = await admin_client.post("/api/admin/labs", json={"name_ar": "مختبر الفحص", "name_en": "Test Lab"})
    assert lab.status_code == 201 and lab.json()["status"] == "pending" and lab.json()["issues"]
    lab_id = lab.json()["id"]
    await admin_client.patch(f"/api/admin/labs/{lab_id}", json={"location_hint": "الطابق الأرضي"})
    assert (await admin_client.post(f"/api/admin/labs/{lab_id}/review", json={"decision": "approve"})).json()["status"] == "approved"
    listing = (await admin_client.get("/api/admin/places")).json()
    assert any(p["id"] == place_id for p in listing["places"]) and any(x["id"] == lab_id for x in listing["labs"])
    assert "contact_passages" in listing and "media" in listing and listing["programs"]
    assert (await admin_client.delete(f"/api/admin/labs/{lab_id}")).status_code == 204
    assert (await admin_client.delete(f"/api/admin/places/{place_id}")).status_code == 204
