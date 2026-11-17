"""Crawler + change detection against a fake website (httpx.MockTransport, no internet).

Uses the host `meu.test` so it can never collide with real crawled data; all writes are
rolled back by the db_session fixture.
"""

from collections import Counter

import httpx
from sqlalchemy import select, update

from app.db.models import Chunk, Source
from app.ingestion.config import CrawlConfig
from app.ingestion.fetcher import Fetcher
from app.ingestion.pipeline import crawl, recrawl

CFG = CrawlConfig(
    seeds=("https://meu.test/faculty-of-engineering/",),
    allowed_hosts=frozenset({"meu.test"}),
    host_aliases={},
    delay_s=0,
    max_pages=10,
)

HOME = """<html lang="en"><head><title>Faculty of Engineering</title></head><body>
<nav><a href="/engineering-programs/">Programs</a> <a href="/faculty-of-business/">Business</a>
<a href="/engineering-private/x">Private</a> <a href="https://other.test/engineering/">Other</a>
<a href="/engineering-programs/#top">Programs again</a></nav>
<main><h1>Faculty of Engineering</h1><p>The faculty has two departments.</p></main></body></html>"""

PROGRAMS_V1 = """<html lang="ar"><head><title>البرامج</title></head><body><main>
<h1>برامج كلية الهندسة</h1><p>هندسة الأنظمة الذكية.</p><p>الهندسة المدنية.</p></main>\
</body></html>"""

PROGRAMS_V2 = PROGRAMS_V1.replace("الهندسة المدنية.", "الهندسة المدنية وهندسة الطاقة المتجددة.")


def make_site(pages: dict[str, str], hits: Counter) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        hits[f"{request.url.host}{path}"] += 1
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /engineering-private/\n")
        if request.url.host == "meu.test" and path in pages:
            return httpx.Response(
                200, text=pages[path], headers={"content-type": "text/html; charset=utf-8"}
            )
        return httpx.Response(404, text="not found")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_crawl_stays_in_scope_and_stores_pending(db_session) -> None:
    pages = {"/faculty-of-engineering/": HOME, "/engineering-programs/": PROGRAMS_V1}
    hits: Counter = Counter()
    async with make_site(pages, hits) as client:
        report = await crawl(db_session, Fetcher(CFG, client=client), CFG)

    assert report.outcomes["new"] == 2
    assert report.outcomes["failed"] == 1  # the robots.txt-blocked link, recorded not fetched
    assert hits["meu.test/faculty-of-business/"] == 0  # out of scope: never requested
    assert hits["meu.test/engineering-private/x"] == 0  # blocked by robots.txt
    assert hits["other.test/engineering/"] == 0  # external host
    assert hits["meu.test/engineering-programs/"] == 1  # fragment duplicate fetched once

    sources = (
        await db_session.scalars(select(Source).where(Source.url.like("https://meu.test/%")))
    ).all()
    assert {s.review_status for s in sources} == {"pending"}
    programs = next(s for s in sources if s.url.endswith("/engineering-programs/"))
    assert programs.lang == "ar"
    assert programs.content_hash and programs.raw_html and programs.clean_text
    chunks = (await db_session.scalars(select(Chunk).where(Chunk.source_id == programs.id))).all()
    assert chunks and all(c.review_status == "pending" and c.lang == "ar" for c in chunks)
    assert "هندسة الأنظمة الذكية" in " ".join(c.text for c in chunks)


async def test_recrawl_detects_changes(db_session) -> None:
    pages = {"/faculty-of-engineering/": HOME, "/engineering-programs/": PROGRAMS_V1}
    hits: Counter = Counter()
    async with make_site(pages, hits) as client:
        await crawl(db_session, Fetcher(CFG, client=client), CFG)

        # A reviewer approves everything.
        await db_session.execute(
            update(Source)
            .where(Source.url.like("https://meu.test/%"))
            .values(review_status="approved")
        )
        await db_session.execute(
            update(Chunk)
            .values(review_status="approved")
            .where(
                Chunk.source_id.in_(select(Source.id).where(Source.url.like("https://meu.test/%")))
            )
        )
        await db_session.commit()

        # The university edits the programs page.
        pages["/engineering-programs/"] = PROGRAMS_V2
        report = await recrawl(db_session, Fetcher(CFG, client=client), CFG)

    by_url = dict(report.pages)
    assert by_url["https://meu.test/faculty-of-engineering/"] == "unchanged"
    assert by_url["https://meu.test/engineering-programs/"] == "changed"

    home = await db_session.scalar(
        select(Source).where(Source.url == "https://meu.test/faculty-of-engineering/")
    )
    programs = await db_session.scalar(
        select(Source).where(Source.url == "https://meu.test/engineering-programs/")
    )
    await db_session.refresh(home)
    await db_session.refresh(programs)
    assert home.review_status == "approved"  # unchanged page keeps its approval
    assert programs.review_status == "stale"  # changed page needs re-review
    statuses = Counter(
        (
            await db_session.scalars(
                select(Chunk.review_status).where(Chunk.source_id == programs.id)
            )
        ).all()
    )
    assert statuses["approved"] == 0  # old text is no longer usable by the robot
    assert statuses["stale"] >= 1 and statuses["pending"] >= 1


async def test_failed_fetch_marks_approved_source_stale(db_session) -> None:
    pages = {"/faculty-of-engineering/": HOME, "/engineering-programs/": PROGRAMS_V1}
    hits: Counter = Counter()
    async with make_site(pages, hits) as client:
        await crawl(db_session, Fetcher(CFG, client=client), CFG)
        await db_session.execute(
            update(Source)
            .where(Source.url.like("https://meu.test/%"))
            .values(review_status="approved")
        )
        await db_session.commit()
        del pages["/engineering-programs/"]  # page removed from the site
        report = await recrawl(db_session, Fetcher(CFG, client=client), CFG)

    assert dict(report.pages)["https://meu.test/engineering-programs/"] == "failed"
    programs = await db_session.scalar(
        select(Source).where(Source.url == "https://meu.test/engineering-programs/")
    )
    await db_session.refresh(programs)
    assert programs.review_status == "stale"
    assert programs.http_status == 404
