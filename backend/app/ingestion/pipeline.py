"""Fetch -> clean -> chunk -> store. Everything new or changed lands as 'pending' for review.

Change handling (re-crawl):
- same content hash        -> 'unchanged' (only fetched_at updated)
- different content        -> 'changed': old approved chunks become 'stale', old pending/rejected
                              chunks are deleted, new chunks are 'pending'; an approved source
                              becomes 'stale' until it is reviewed again
- fetch fails for a known page -> an approved source becomes 'stale' (needs a human look)
"""

import hashlib
import logging
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Source
from app.ingestion.chunker import chunk_text
from app.ingestion.cleaner import CleanPage, clean_html
from app.ingestion.config import CrawlConfig
from app.ingestion.fetcher import FetchResult, Fetcher
from app.ingestion.lang import detect_lang
from app.ingestion.urls import in_scope, normalize_url
from app.knowledge.store import ChunkSpec, add_chunks, retire_old_chunks

log = logging.getLogger(__name__)

NEW, UNCHANGED, CHANGED, FAILED, EMPTY = "new", "unchanged", "changed", "failed", "empty"


@dataclass
class CrawlReport:
    outcomes: Counter = field(default_factory=Counter)
    pages: list[tuple[str, str]] = field(default_factory=list)  # (url, outcome)

    def add(self, url: str, outcome: str) -> None:
        self.outcomes[outcome] += 1
        self.pages.append((url, outcome))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def store_page(
    session: AsyncSession, result: FetchResult, cfg: CrawlConfig
) -> tuple[str, CleanPage | None]:
    now = datetime.now(UTC)
    src = await session.scalar(select(Source).where(Source.url == result.url))

    if not result.ok:
        if src is not None:
            src.http_status = result.status
            src.fetched_at = now
            src.notes = f"last fetch failed: {result.error or result.status}"
            if src.review_status == "approved":
                src.review_status = "stale"
        return FAILED, None

    assert result.html is not None
    page = clean_html(result.html)
    digest = content_hash(page.text)

    if src is not None and src.content_hash == digest:
        src.fetched_at = now
        src.http_status = 200
        return UNCHANGED, page

    if not page.text.strip():
        return EMPTY, page

    if src is None:
        outcome = NEW
        src = Source(url=result.url)
        session.add(src)
        src.review_status = "pending"
    else:
        outcome = CHANGED
        await retire_old_chunks(session, src)

    src.title = page.title
    src.lang = page.html_lang if page.html_lang in ("ar", "en") else detect_lang(page.text)
    src.fetched_at = now
    src.http_status = 200
    src.content_hash = digest
    src.raw_html = result.html
    src.clean_text = page.text
    src.notes = None
    await session.flush()

    texts = chunk_text(page.text, cfg.chunk_max_words, cfg.chunk_min_words)
    await add_chunks(session, src, [ChunkSpec(text=t, lang=detect_lang(t)) for t in texts])
    return outcome, page


async def crawl(session: AsyncSession, fetcher: Fetcher, cfg: CrawlConfig) -> CrawlReport:
    """Breadth-first crawl from the seeds, staying inside the engineering scope."""
    report = CrawlReport()
    queue: deque[tuple[str, int]] = deque()
    seen: set[str] = set()
    for seed in cfg.seeds:
        url = normalize_url(seed, cfg)
        if url and in_scope(url, cfg) and url not in seen:
            seen.add(url)
            queue.append((url, 0))

    fetched = 0
    while queue and fetched < cfg.max_pages:
        url, depth = queue.popleft()
        result = await fetcher.fetch(url)
        fetched += 1
        outcome, page = await store_page(session, result, cfg)
        await session.commit()
        report.add(url, outcome)
        log.info("%-9s %s", outcome, url)
        if page is None or depth >= cfg.max_depth:
            continue
        for href in page.links:
            link = normalize_url(href, cfg, base=url)
            if link and link not in seen and in_scope(link, cfg):
                seen.add(link)
                queue.append((link, depth + 1))
    return report


async def recrawl(session: AsyncSession, fetcher: Fetcher, cfg: CrawlConfig) -> CrawlReport:
    """Re-fetch every known source (no link following) and flag what changed."""
    report = CrawlReport()
    urls = (await session.scalars(select(Source.url).order_by(Source.id))).all()
    for url in urls:
        result = await fetcher.fetch(url)
        outcome, _ = await store_page(session, result, cfg)
        await session.commit()
        report.add(url, outcome)
        log.info("%-9s %s", outcome, url)
    return report
