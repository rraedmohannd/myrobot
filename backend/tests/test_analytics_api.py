"""Admin analytics and tools (demo sprint M3). Every endpoint: without the token -> 401, with it
-> correct numbers. The test rows live in a rolled-back transaction."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.api.rest.analytics import (
    _stats,
    get_results_dir,
    normalize_question,
    read_evaluation_runs,
)
from app.db.models import Chunk, Program, Session, Source, Turn
from app.main import app
from app.services.providers import get_embedder, get_llm
from tests.fakes import FakeEmbedder, FakeLLM

UNIQUE = "zorblaxiq"
PROTECTED = [
    ("get", "/api/admin/analytics/overview"),
    ("get", "/api/admin/analytics/gaps"),
    ("get", "/api/admin/conversations"),
    ("get", "/api/admin/conversations/1"),
    ("post", "/api/admin/ask"),
    ("get", "/api/admin/evaluation"),
    ("get", "/api/admin/coverage"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
async def test_endpoints_need_the_admin_token(client, method: str, path: str) -> None:
    kwargs = {"json": {"question": "x"}} if method == "post" else {}
    assert (await getattr(client, method)(path, **kwargs)).status_code == 401
    wrong = await getattr(client, method)(path, headers={"X-Admin-Token": "wrong"}, **kwargs)
    assert wrong.status_code == 401


async def _seed(db_session) -> dict:
    """One approved chunk, and one conversation of 5 turns between two speakers, 1 minute ago."""
    src = Source(url="test-doc://analytics", title="Analytics test", review_status="approved")
    db_session.add(src)
    await db_session.flush()
    chunk = Chunk(source_id=src.id, lang="en", review_status="approved", category="zz-test",
                  text=f"The {UNIQUE} program requires 162 credit hours.")  # fmt: skip
    db_session.add(chunk)
    now = datetime.now(UTC) - timedelta(minutes=1)
    s = Session(lang_detected="ar", started_at=now, ended_at=now, end_reason="user_done")
    empty = Session(lang_detected="ar", started_at=now)
    db_session.add_all([s, empty])
    await db_session.flush()

    def turn(idx: int, **kw) -> Turn:
        return Turn(session_id=s.id, idx=idx, user_lang="ar", created_at=now, **kw)

    db_session.add_all(
        [
            turn(
                0,
                intent="question",
                user_text=f"كم ساعة {UNIQUE}؟",
                answer_text="162 ساعة.",
                found=True,
                used_chunk_ids=[chunk.id, 999999999],
                speaker_label=1,
                speaker_source="voice",
                stt_ms=1000,
                retrieval_ms=200,
                llm_first_ms=1100,
                llm_ms=2000,
                ack_ms=1300,
                tts_ms=4000,
                total_ms=4000,
            ),
            turn(
                1,
                intent="question",
                user_text=f"كم رسوم {UNIQUE}؟",
                answer_text="ما عندي.",
                found=False,
                speaker_label=2,
                stt_ms=1400,
                retrieval_ms=300,
                llm_ms=3000,
                ack_ms=1500,
                tts_ms=5000,
                total_ms=5000,
            ),
            turn(
                2,
                intent="question",
                user_text=f"كم رُسوم {UNIQUE} ؟!",
                answer_text="ما عندي.",
                found=False,
                speaker_label=2,
                stt_ms=1200,
                tts_ms=6000,
            ),
            turn(
                3, intent="question", user_text=f"سؤال قديم {UNIQUE}", answer_text="جواب"
            ),  # found unknown
            turn(4, intent="done", user_text="شكرا", answer_text="مع السلامة"),
        ]
    )
    await db_session.commit()
    return {"session": s.id, "empty": empty.id, "chunk": chunk.id, "source": src.id}


async def test_overview(admin_client, db_session) -> None:
    before = (await admin_client.get("/api/admin/analytics/overview")).json()
    await _seed(db_session)
    r = await admin_client.get("/api/admin/analytics/overview", params={"days": 7})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["days"] == 7 and len(d["per_day"]) == 7
    for scope in ("today", "period"):
        assert d[scope]["sessions"] - before[scope]["sessions"] == 2
        assert d[scope]["turns"] - before[scope]["turns"] == 5
        assert d[scope]["questions"] - before[scope]["questions"] == 4
    assert d["per_day"][-1]["turns"] - before["per_day"][-1]["turns"] == 5
    a, b = d["answers"], before["answers"]
    assert (a["found"] - b["found"], a["not_found"] - b["not_found"]) == (1, 2)
    assert a["unknown"] - b["unknown"] == 1
    assert a["found_rate"] == round(a["found"] / (a["found"] + a["not_found"]), 3)
    assert set(d["latency_ms"]) == {"stt", "retrieval", "llm_first", "llm", "ack", "answer_audio"}
    assert d["latency_ms"]["stt"]["n"] - before["latency_ms"]["stt"]["n"] == 3
    assert d["latency_ms"]["answer_audio"]["median"] is not None
    assert d["latency_ms"]["answer_audio"]["p90"] >= d["latency_ms"]["answer_audio"]["median"]
    sp = d["speakers"]
    assert sp["sessions_with_speaker_labels"] >= 1 and sp["max_per_session"] >= 2
    assert sp["distribution"].get("2", 0) >= 1
    assert (await admin_client.get("/api/admin/analytics/overview?days=0")).status_code == 422


def test_latency_statistics_median_and_p90() -> None:
    assert _stats([1000, 1400, 1200]) == {"median": 1200, "p90": 1400.0, "n": 3}
    assert _stats([]) == {"median": None, "p90": None, "n": 0}


async def test_knowledge_gaps_group_the_same_question(admin_client, db_session) -> None:
    await _seed(db_session)
    r = await admin_client.get("/api/admin/analytics/gaps")
    assert r.status_code == 200, r.text
    ours = [g for g in r.json()["items"] if UNIQUE in g["question"]]
    assert len(ours) == 1  # two spellings of the same question are one gap
    assert ours[0]["count"] == 2 and len(ours[0]["variants"]) == 2
    assert ours[0]["last_answer"] == "ما عندي."
    assert normalize_question("كم رُسوم التخصص ؟!") == normalize_question("كم رسوم التخصص؟")


async def test_conversations_list_and_detail(admin_client, db_session) -> None:
    ids = await _seed(db_session)
    r = await admin_client.get("/api/admin/conversations", params={"limit": 200})
    assert r.status_code == 200, r.text
    items = {i["id"]: i for i in r.json()["items"]}
    assert items[ids["session"]]["turns"] == 5 and items[ids["session"]]["speakers"] == 2
    assert UNIQUE in items[ids["session"]]["first_question"]
    assert items[ids["empty"]]["turns"] == 0 and items[ids["empty"]]["first_question"] is None

    r = await admin_client.get(f"/api/admin/conversations/{ids['session']}")
    assert r.status_code == 200, r.text
    turns = r.json()["turns"]
    assert [t["intent"] for t in turns] == ["question"] * 4 + ["done"]
    first = turns[0]
    assert first["found"] is True and first["speaker_label"] == 1
    assert first["sources"][0] == {
        "chunk_id": ids["chunk"],
        "category": "zz-test",
        "title": "Analytics test",
        "url": "test-doc://analytics",
    }
    assert first["sources"][1]["title"] is None  # a chunk that no longer exists
    assert first["timings_ms"] == {"stt": 1000, "retrieval": 200, "llm_first": 1100, "llm": 2000,
                                   "ack": 1300, "answer_audio": 4000}  # fmt: skip
    assert turns[1]["found"] is False and turns[3]["found"] is None
    assert (await admin_client.get("/api/admin/conversations/999999999")).status_code == 404


async def test_ask_the_robot_returns_answer_and_passages(admin_client, db_session) -> None:
    ids = await _seed(db_session)
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    try:
        sessions_before = (await admin_client.get("/api/admin/conversations")).json()["total"]
        r = await admin_client.post("/api/admin/ask", json={"question": f"{UNIQUE}?"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["found"] and "162" in d["answer"] and d["error"] is None
        top = d["passages"][0]
        assert top["chunk_id"] == ids["chunk"] and top["used"] and top["in_fts"]
        assert (
            top["source_title"] == "Analytics test" and top["source_url"] == "test-doc://analytics"
        )
        assert top["score"] > 0 and "162" in top["text"]
        assert "retrieval_ms" in d["timings_ms"]
        after = (await admin_client.get("/api/admin/conversations")).json()["total"]
        assert after == sessions_before  # the test box stores nothing
        assert (await admin_client.post("/api/admin/ask", json={"question": ""})).status_code == 422
    finally:
        app.dependency_overrides.pop(get_embedder, None)
        app.dependency_overrides.pop(get_llm, None)


async def test_evaluation_runs(admin_client, tmp_path) -> None:
    run = {
        "model": "fake", "started": "2026-11-13T10:00:00", "total": 2, "passed": 1,
        "accuracy": 0.5, "invented_numbers_total": 1, "latency_median_ms": 2000,
        "latency_p90_ms": 3000,
        "results": [
            {"id": "ok", "passed": True, "question": "q1", "answer": "a1", "reasons": []},
            {"id": "bad", "lang": "ar", "kind": "answerable", "passed": False, "question": "q2",
             "answer": "a2", "reasons": ["missing one of ['162']"]},
        ],
    }  # fmt: skip
    (tmp_path / "20261113-100000.json").write_text(json.dumps(run), encoding="utf-8")
    (tmp_path / "20261112-100000.json").write_text(
        json.dumps({**run, "passed": 2}), encoding="utf-8"
    )
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "20261113-100000.md").write_text("# report", encoding="utf-8")
    app.dependency_overrides[get_results_dir] = lambda: tmp_path
    try:
        r = await admin_client.get("/api/admin/evaluation")
    finally:
        app.dependency_overrides.pop(get_results_dir, None)
    assert r.status_code == 200, r.text
    runs = r.json()["runs"]
    assert [x["file"] for x in runs] == [
        "20261113-100000.json",
        "20261112-100000.json",
    ]  # newest first
    assert runs[0]["passed"] == 1 and runs[0]["total"] == 2 and runs[0]["accuracy"] == 0.5
    assert runs[0]["failed"] == [
        {
            "id": "bad",
            "lang": "ar",
            "kind": "answerable",
            "question": "q2",
            "answer": "a2",
            "reasons": ["missing one of ['162']"],
        }
    ]
    assert read_evaluation_runs(tmp_path / "missing") == []


async def test_real_evaluation_results_are_readable(admin_client) -> None:
    runs = (await admin_client.get("/api/admin/evaluation")).json()["runs"]
    assert runs and all(r["total"] and r["passed"] is not None for r in runs)


async def test_coverage(admin_client, db_session) -> None:
    before = (await admin_client.get("/api/admin/coverage")).json()
    ids = await _seed(db_session)
    program = (await db_session.scalars(Program.__table__.select().limit(1))).first()
    r = await admin_client.get("/api/admin/coverage")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["by_status"]["sources"]["approved"] - before["by_status"]["sources"]["approved"] == 1
    assert d["by_status"]["chunks"]["approved"] - before["by_status"]["chunks"]["approved"] == 1
    assert set(d["by_status"]["chunks"]) == {"pending", "approved", "rejected", "stale"}
    category = next(c for c in d["chunks_by_category"] if c["category"] == "zz-test")
    assert category["approved"] == 1 and category["pending"] == 0
    general = next(p for p in d["chunks_by_program"] if p["program_id"] is None)
    general_before = next((p for p in before["chunks_by_program"] if p["program_id"] is None), None)
    assert general["approved"] - (general_before["approved"] if general_before else 0) == 1
    if program is not None:  # every program is listed, with its Arabic and English name
        assert any(p["program_id"] == program for p in d["chunks_by_program"])
    assert ids["source"]


async def test_admin_page_has_the_new_tabs(client) -> None:
    page = (await client.get("/admin")).text
    for label in ("Overview", "Knowledge gaps", "Conversations", "Ask the robot", "Evaluation",
                  "Knowledge coverage"):  # fmt: skip
        assert label in page
