"""Retrieval over APPROVED chunks of APPROVED sources only: full-text (Arabic + English
stemming) + vector similarity, merged with reciprocal rank fusion (RRF).

A chunk answers only while its own status AND its source's status are 'approved'
(`app.knowledge.store.usable_chunk` is the same rule for ORM queries). So rejecting a source,
or a source going stale, switches off all its chunks at once, whatever their own status says.
"""

import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.types import to_pgvector
from app.services.providers import Embedder

RRF_K = 60
CATEGORY_CAPS = {"courses": 3}  # max passages of a category per answer

# Natural questions contain many words; plainto_tsquery would AND them all and rarely match.
# The query lexemes are therefore OR-ed (cast straight to tsquery, so they are not re-stemmed).
_TSQUERY = (
    "(replace(plainto_tsquery('arabic', :q)::text, '&', '|')::tsquery || "
    "replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery)"
)

FTS_SQL = text(f"""
    SELECT c.id
    FROM chunks c JOIN sources s ON s.id = c.source_id, (SELECT {_TSQUERY} AS tq) q
    WHERE c.review_status = 'approved' AND s.review_status = 'approved' AND c.tsv @@ q.tq
    ORDER BY ts_rank_cd(c.tsv, q.tq) DESC, c.id
    LIMIT :k
""")

VECTOR_SQL = text("""
    SELECT c.id
    FROM chunks c JOIN sources s ON s.id = c.source_id
    WHERE c.review_status = 'approved' AND s.review_status = 'approved'
      AND c.embedding IS NOT NULL
    ORDER BY c.embedding <=> CAST(:e AS vector), c.id
    LIMIT :k
""")

DETAILS_SQL = text("""
    SELECT c.id, c.text, c.lang, c.category, c.program_id, s.title, s.url
    FROM chunks c JOIN sources s ON s.id = c.source_id
    WHERE c.id = ANY(:ids)
""")


@dataclass
class Passage:
    chunk_id: int
    text: str
    lang: str
    category: str | None
    program_id: int | None
    source_title: str | None
    source_url: str
    score: float
    in_fts: bool
    in_vector: bool


@dataclass
class Retrieval:
    passages: list[Passage]
    embed_ms: int
    search_ms: int


async def embed_missing(session: AsyncSession, embedder: Embedder, batch: int = 64) -> int:
    """Compute embeddings for approved chunks of approved sources that have none (new, or
    edited since). Chunks of a source that is not approved are never sent to the provider."""
    rows = (
        await session.execute(
            text(
                "SELECT c.id, c.text, c.search_hint FROM chunks c "
                "JOIN sources s ON s.id = c.source_id "
                "WHERE c.review_status = 'approved' AND s.review_status = 'approved' "
                "AND c.embedding IS NULL ORDER BY c.id"
            )
        )
    ).all()
    done = 0
    for i in range(0, len(rows), batch):
        part = rows[i : i + batch]
        # the bilingual hint is embedded with the text so Arabic questions find English passages
        vectors = await embedder.embed(
            [f"{r.search_hint}\n{r.text}" if r.search_hint else r.text for r in part]
        )
        for row, vec in zip(part, vectors, strict=True):
            await session.execute(
                text(
                    "UPDATE chunks SET embedding = CAST(:e AS vector), embedding_model = :m "
                    "WHERE id = :id"
                ),
                {"e": to_pgvector(vec), "m": embedder.model, "id": row.id},
            )
        done += len(part)
    if done:
        await session.commit()
    return done


def rrf(rank_lists: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranks in rank_lists:
        for position, chunk_id in enumerate(ranks):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + position + 1)
    return scores


def select_passages(
    ranked: list[int],
    categories: dict[int, str | None],
    rank_lists: list[list[int]],
    top_k: int,
    keep_top: int = 0,
) -> list[int]:
    """The chunk ids given to the LLM, in fused-rank order.

    Two rules on top of the fused ranking:
    - Category caps: course lists are long and numerous; capping them leaves room for summaries.
    - The best `keep_top` hits of EACH search are never dropped. Fusion favours chunks that
      appear in both lists, so a chunk found by only one search can lose even at rank 1. That
      happens across languages: an English question matches an Arabic passage by meaning
      (vector rank 1), while the keyword search is filled with English passages that merely
      share common words. Keeping each search's top hits fixes this for every such question.
    """
    best: list[int] = []
    per_category: dict[str | None, int] = {}
    for cid in ranked:
        cat = categories.get(cid)
        if cat in CATEGORY_CAPS and per_category.get(cat, 0) >= CATEGORY_CAPS[cat]:
            continue
        per_category[cat] = per_category.get(cat, 0) + 1
        best.append(cid)
        if len(best) == top_k:
            break
    must_keep = list(dict.fromkeys(cid for ranks in rank_lists for cid in ranks[:keep_top]))[:top_k]
    missing = [cid for cid in must_keep if cid not in best]
    if missing:
        droppable = [cid for cid in best if cid not in must_keep]
        keep = droppable[: max(0, top_k - len(must_keep))]
        chosen = set(keep) | set(must_keep)
        best = [cid for cid in ranked if cid in chosen]
    return best


async def retrieve(
    session: AsyncSession, embedder: Embedder, query: str, top_k: int | None = None,
    query_vec: list[float] | None = None,
) -> Retrieval:  # fmt: skip
    """`query_vec`: the embedding of the query when the caller already has it (no second call)."""
    settings = get_settings()
    top_k = top_k or settings.rag_top_k
    n = settings.rag_candidates

    t0 = time.perf_counter()
    if query_vec is None:
        [query_vec] = await embedder.embed([query])
    t1 = time.perf_counter()

    fts_ids = list((await session.scalars(FTS_SQL, {"q": query, "k": n})).all())
    vec_ids = list((await session.scalars(VECTOR_SQL, {"e": to_pgvector(query_vec), "k": n})).all())
    scores = rrf([fts_ids, vec_ids])
    ranked = sorted(scores, key=lambda cid: (-scores[cid], cid))

    passages: list[Passage] = []
    if ranked:
        rows = {r.id: r for r in (await session.execute(DETAILS_SQL, {"ids": ranked})).all()}
        fts_set, vec_set = set(fts_ids), set(vec_ids)
        best = select_passages(
            ranked, {cid: rows[cid].category for cid in ranked}, [fts_ids, vec_ids], top_k,
            settings.rag_keep_top,
        )  # fmt: skip
        for cid in best:
            r = rows[cid]
            passages.append(
                Passage(
                    chunk_id=cid,
                    text=r.text,
                    lang=r.lang,
                    category=r.category,
                    program_id=r.program_id,
                    source_title=r.title,
                    source_url=r.url,
                    score=round(scores[cid], 5),
                    in_fts=cid in fts_set,
                    in_vector=cid in vec_set,
                )  # fmt: skip
            )
    t2 = time.perf_counter()
    return Retrieval(
        passages=passages, embed_ms=int((t1 - t0) * 1000), search_ms=int((t2 - t1) * 1000)
    )
