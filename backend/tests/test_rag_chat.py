"""Retrieval, grounded chat service and /api/chat with fake providers (rolled-back DB)."""

import pytest
from sqlalchemy import select, text

from app.db.models import Chunk, Source, Turn
from app.services.chat import INSTRUCTIONS, answer_question, build_input, invented_numbers
from app.services.evaluate import check, load_questions, normalize
from app.services.providers import get_embedder, get_llm, parse_json_object
from app.knowledge.hints import build_hint, refresh_hints
from app.services.rag import embed_missing, retrieve, rrf
from tests.fakes import FakeEmbedder, FakeLLM

UNIQUE = "zorblaxian"  # words no real chunk contains, so real approved data never competes
UNIQUE2 = "quuxitron"


async def _seed(db_session) -> dict[str, int]:
    src = Source(url="test-doc://rag", title="RAG test source", review_status="approved")
    db_session.add(src)
    await db_session.flush()
    rows = {
        "hit": Chunk(
            source_id=src.id,
            lang="en",
            review_status="approved",
            text=f"The {UNIQUE} {UNIQUE2} program requires 162 credit hours and "
            "includes robot programming.",
        ),
        "other": Chunk(
            source_id=src.id,
            lang="en",
            review_status="approved",
            text="Unrelated text about parking spaces near the library.",
        ),
        "pending": Chunk(
            source_id=src.id,
            lang="en",
            review_status="pending",
            text=f"The {UNIQUE} program costs 999 dinars per hour.",
        ),
    }
    db_session.add_all(rows.values())
    await db_session.commit()
    return {k: v.id for k, v in rows.items()}


# ---------- pure units ----------
def test_rrf_prefers_items_found_by_both_searches() -> None:
    scores = rrf([[1, 2, 3], [3, 4]])
    assert max(scores, key=scores.get) == 3


def test_invented_numbers_detects_unsourced_figures() -> None:
    passages = ["Intelligent Systems Engineering requires 162 credit hours (2025/2026)."]
    assert invented_numbers("It needs 162 hours.", passages) == []
    assert invented_numbers("It needs ١٦٢ hours.", passages) == []  # Arabic-Indic digits
    assert invented_numbers("It costs 75 JD per hour.", passages) == ["75"]


def test_instructions_forbid_invention_and_require_json() -> None:
    assert "Never invent" in INSTRUCTIONS
    assert "دائرة القبول والتسجيل" in INSTRUCTIONS
    assert '"found"' in INSTRUCTIONS and "Jordanian Arabic" in INSTRUCTIONS


def test_parse_json_object_tolerates_wrapping() -> None:
    assert parse_json_object('```json\n{"answer": "hi", "found": true}\n```')["answer"] == "hi"


def test_golden_set_is_large_and_bilingual() -> None:
    qs = load_questions()
    assert len(qs) >= 30
    assert {q["lang"] for q in qs} == {"ar", "en"}
    assert sum(q["kind"] == "unanswerable" for q in qs) >= 5
    assert len({q["id"] for q in qs}) == len(qs)


def test_evaluator_check() -> None:
    item = {"kind": "answerable", "must_include": [["162"]], "must_not_include": []}
    assert check(item, "بدها 162 ساعة معتمدة", True, []) == []
    assert check(item, "ما بعرف", False, []) != []
    unans = {"kind": "unanswerable", "must_include": [], "must_not_include": ["دينار"]}
    assert check(unans, "ما عندي هاي المعلومة", False, []) == []
    assert check(unans, "سعر الساعة 80 دينار", True, ["80"]) != []
    assert normalize("أيمن") == normalize("ايمن")
    assert normalize("الأنظمة المضمّنة") == normalize(
        "الأنظمة المضمنة"
    )  # diacritics do not change a word


# ---------- database ----------
async def test_retrieval_uses_only_approved_chunks(db_session) -> None:
    ids = await _seed(db_session)
    emb = FakeEmbedder()
    assert await embed_missing(db_session, emb) >= 2
    r = await retrieve(db_session, emb, f"{UNIQUE} {UNIQUE2}?")
    got = [p.chunk_id for p in r.passages]
    assert got[0] == ids["hit"]
    assert ids["pending"] not in got
    assert r.passages[0].in_fts and r.passages[0].in_vector


async def test_answer_question_logs_turn_with_sources(db_session) -> None:
    ids = await _seed(db_session)
    llm = FakeLLM()
    r = await answer_question(db_session, FakeEmbedder(), llm, f"{UNIQUE} {UNIQUE2}?")
    assert r.found and "162" in r.answer
    assert r.sources[0].chunk_id == ids["hit"]
    assert r.invented_numbers == []
    instructions, sent = llm.calls[0]
    assert instructions == INSTRUCTIONS and "QUESTION:" in sent and "999" not in sent
    turn = await db_session.scalar(select(Turn).where(Turn.session_id == r.session_id))
    assert turn.used_chunk_ids == [ids["hit"]] and turn.retrieval_ms is not None

    # follow-up in the same session gets the previous turn as context
    r2 = await answer_question(
        db_session, FakeEmbedder(), llm, "And robots?", session_id=r.session_id
    )
    assert r2.session_id == r.session_id
    assert "CONVERSATION SO FAR" in llm.calls[1][1]


async def test_llm_invented_number_is_flagged(db_session) -> None:
    await _seed(db_session)
    llm = FakeLLM(reply={"answer": f"{UNIQUE} costs 8642 JD per hour.", "found": True, "used": [1]})
    r = await answer_question(db_session, FakeEmbedder(), llm, f"{UNIQUE} {UNIQUE2}?", log=False)
    assert r.invented_numbers == ["8642"]


async def test_chat_endpoint(client, db_session) -> None:
    from app.api.deps import get_session
    from app.main import app

    await _seed(db_session)

    async def _session():
        yield db_session

    app.dependency_overrides.update(
        {get_session: _session, get_embedder: lambda: FakeEmbedder(), get_llm: lambda: FakeLLM()}
    )
    try:
        r = await client.post("/api/chat", json={"message": f"{UNIQUE} {UNIQUE2}"})
    finally:
        for dep in (get_session, get_embedder, get_llm):
            app.dependency_overrides.pop(dep, None)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True and body["sources"] and body["session_id"]
    assert set(body["timings"]) >= {"retrieval_ms", "llm_ms", "total_ms"}


async def test_editing_chunk_text_clears_its_embedding(admin_client, db_session) -> None:
    ids = await _seed(db_session)
    await embed_missing(db_session, FakeEmbedder())
    r = await admin_client.patch(f"/api/admin/chunks/{ids['hit']}", json={"text": "edited text"})
    assert r.status_code == 200
    emb = await db_session.scalar(
        text("SELECT embedding IS NULL FROM chunks WHERE id = :i"), {"i": ids["hit"]}
    )
    assert emb is True


def test_build_input_numbers_passages() -> None:
    class P:
        def __init__(self, t: str) -> None:
            self.text, self.source_title, self.source_url, self.category = t, "S", "u", None

    out = build_input("Q?", [P("first"), P("second")], [("hi", "hello")])
    assert "[1] (S)\nfirst" in out and "[2] (S)\nsecond" in out and "Visitor: hi" in out


def test_build_hint_is_bilingual() -> None:
    h = build_hint("careers", "Intelligent Systems Engineering", "هندسة الأنظمة الذكية")
    assert "هندسة الأنظمة الذكية" in h and "Intelligent Systems Engineering" in h
    assert "وين بيشتغل الخريج" in h and "careers" in h
    assert build_hint(None, None, None) is None


async def test_arabic_question_finds_english_passage_through_hint(db_session) -> None:
    src = Source(url="test-doc://hint", title="Hint test", review_status="approved")
    db_session.add(src)
    await db_session.flush()
    careers = Chunk(source_id=src.id, lang="en", review_status="approved", category="careers",
                    text=f"{UNIQUE} graduates work in banking and telecommunication.")  # fmt: skip
    db_session.add(careers)
    await db_session.commit()
    assert await refresh_hints(db_session) >= 1
    await db_session.refresh(careers)
    assert "وين بيشتغل الخريج" in careers.search_hint
    emb = FakeEmbedder()
    await embed_missing(db_session, emb)
    # Arabic words only: they match the hint, not the English text
    r = await retrieve(db_session, emb, f"وين بيشتغل الخريج {UNIQUE}")
    top = {p.chunk_id: p for p in r.passages[:3]}  # real careers chunks share the same hint words
    assert careers.id in top and top[careers.id].in_fts


async def test_course_passages_are_capped(db_session) -> None:
    src = Source(url="test-doc://cap", title="Cap test", review_status="approved")
    db_session.add(src)
    await db_session.flush()
    db_session.add_all(
        [Chunk(source_id=src.id, lang="en", review_status="approved", category="courses",
               text=f"{UNIQUE} course list part {i}") for i in range(6)]
        + [Chunk(source_id=src.id, lang="en", review_status="approved", category="study_plan",
                 text=f"{UNIQUE} requires 162 credit hours in total")]
    )  # fmt: skip
    await db_session.commit()
    emb = FakeEmbedder()
    await embed_missing(db_session, emb)
    r = await retrieve(db_session, emb, f"{UNIQUE}", top_k=8)
    cats = [p.category for p in r.passages]
    assert cats.count("courses") <= 3 and "study_plan" in cats


def test_top_hits_of_each_search_are_never_dropped() -> None:
    """A chunk found by one search only (cross-language match) must not lose its place to
    chunks that are merely present in both lists."""
    from app.services.rag import rrf, select_passages

    fts = list(range(100, 120))  # keyword search: 20 passages, none of them the right one
    vec = [1, 2, *range(100, 118)]  # meaning search: the right passages first
    scores = rrf([fts, vec])
    ranked = sorted(scores, key=lambda cid: (-scores[cid], cid))
    categories: dict[int, str | None] = {cid: None for cid in ranked}

    fusion_only = select_passages(ranked, categories, [fts, vec], top_k=8, keep_top=0)
    assert 1 not in fusion_only and 2 not in fusion_only  # the weakness being fixed

    kept = select_passages(ranked, categories, [fts, vec], top_k=8, keep_top=2)
    assert {1, 2} <= set(kept) and {100, 101} <= set(kept)  # top 2 of each search
    assert len(kept) == 8 and kept == [cid for cid in ranked if cid in set(kept)]  # fused order

    # nothing changes when the top hits are already in
    both = select_passages(ranked, categories, [fts, fts], top_k=8, keep_top=2)
    assert both == select_passages(ranked, categories, [fts, fts], top_k=8, keep_top=0)
    # category caps still apply to the rest
    capped = select_passages(ranked, {cid: "courses" for cid in ranked}, [fts, vec], 8, 0)
    assert len(capped) == 3
    assert select_passages([], {}, [[], []], 8, 2) == []


# ---------- sprint 4 P0.1: the source status switches its chunks off ----------
async def _seed_unapproved_source(db_session, status: str, word: str) -> tuple[Source, Chunk]:
    """An APPROVED chunk (with an embedding) under a source that is approved for now."""
    src = Source(url=f"test-doc://rag-{status}-{word}", title="Old list", review_status="approved")
    db_session.add(src)
    await db_session.flush()
    chunk = Chunk(source_id=src.id, lang="en", review_status="approved",
                  text=f"The {word} {UNIQUE2} program requires 777 credit hours.")  # fmt: skip
    db_session.add(chunk)
    await db_session.commit()
    return src, chunk


@pytest.mark.parametrize("status", ["rejected", "stale", "pending"])
async def test_approved_chunk_of_unapproved_source_is_never_retrieved(db_session, status) -> None:
    """The bug of 2026-11-13: source 33 was rejected, its approved chunk 48 kept answering."""
    word = f"glimmerfax{status}"
    emb = FakeEmbedder()
    src, chunk = await _seed_unapproved_source(db_session, status, word)
    await embed_missing(db_session, emb)
    before = await retrieve(db_session, emb, f"{word} {UNIQUE2}?")
    hit = next(p for p in before.passages if p.chunk_id == chunk.id)
    assert hit.in_fts and hit.in_vector  # found by both searches while the source is approved

    src.review_status = status  # the chunk itself stays 'approved' and keeps its embedding
    await db_session.commit()
    after = await retrieve(db_session, emb, f"{word} {UNIQUE2}?", top_k=50)
    assert chunk.id not in [p.chunk_id for p in after.passages]

    # ... and the whole answer path does not use it either
    r = await answer_question(db_session, emb, FakeLLM(), f"{word} {UNIQUE2}?")
    assert chunk.id not in [s.chunk_id for s in r.sources]
    assert "777" not in r.answer


async def test_embed_missing_skips_chunks_of_unapproved_sources(db_session) -> None:
    src = Source(url="test-doc://rag-rejected-embed", title="Old list", review_status="rejected")
    db_session.add(src)
    await db_session.flush()
    chunk = Chunk(source_id=src.id, lang="en", review_status="approved",
                  text=f"The wobblequark {UNIQUE2} program requires 777 credit hours.")  # fmt: skip
    db_session.add(chunk)
    await db_session.commit()

    class Recording(FakeEmbedder):
        seen: list[str] = []

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.seen += texts
            return await super().embed(texts)

    emb = Recording()
    await embed_missing(db_session, emb)
    assert not any("wobblequark" in t for t in emb.seen)  # never sent to the provider
    has_vector = await db_session.scalar(
        text("SELECT embedding IS NOT NULL FROM chunks WHERE id = :id"), {"id": chunk.id}
    )
    assert has_vector is False


async def test_curated_checks_ignore_chunks_of_unapproved_sources(db_session) -> None:
    """Numbers of a rejected source do not make a FAQ or pitch number 'known'."""
    from app.services.curated import known_numbers

    src, _ = await _seed_unapproved_source(db_session, "rejected", "plinthavox")
    assert "777" in await known_numbers(db_session, None)
    src.review_status = "rejected"
    await db_session.commit()
    assert "777" not in await known_numbers(db_session, None)
