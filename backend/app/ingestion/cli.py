"""Command line for ingestion.

    python -m app.ingestion.cli crawl [--max-pages 60] [--max-depth 2] [--url URL ...]
    python -m app.ingestion.cli recrawl
    python -m app.ingestion.cli stats
    python -m app.ingestion.cli import-knowledge [--dir data/knowledge]
    python -m app.ingestion.cli debug-page URL
"""

import argparse
import asyncio
import dataclasses
import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.models import Chunk, Source
from app.db.session import get_engine, get_sessionmaker
from app.ingestion.config import CrawlConfig
from app.ingestion.fetcher import Fetcher
from app.ingestion.pipeline import CrawlReport, crawl, recrawl


def _print_report(report: CrawlReport) -> None:
    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in sorted(report.outcomes.items())))
    print("New and changed pages are 'pending'/'stale': review them at http://localhost:8000/admin")


async def _stats() -> None:
    async with get_sessionmaker()() as s:
        for model in (Source, Chunk):
            rows = await s.execute(
                select(model.review_status, func.count()).group_by(model.review_status)
            )
            counts = {status: n for status, n in rows}
            print(f"{model.__tablename__:8s}", counts or "{}")


async def _debug_page(url: str) -> None:
    """Fetch one page, save its raw HTML to debug_page.html and print what the cleaner sees."""
    from pathlib import Path

    from app.ingestion.cleaner import clean_html

    cfg = CrawlConfig()
    async with Fetcher(cfg) as fetcher:
        result = await fetcher.fetch(url)
    print(f"status={result.status} content_type={result.content_type!r} error={result.error}")
    if result.html is None:
        return
    out = Path("debug_page.html")
    out.write_text(result.html, encoding="utf-8")
    page = clean_html(result.html)
    print(f"html_chars={len(result.html)} title={page.title!r} lang={page.html_lang}")
    print(f"links={len(page.links)} clean_text_chars={len(page.text)}")
    print("first 500 chars of clean text:\n", page.text[:500])
    print(f"\nRaw HTML saved to backend/{out}")


async def _main(args: argparse.Namespace) -> None:
    cfg = CrawlConfig()
    if args.cmd == "stats":
        await _stats()
        return
    if args.cmd == "debug-page":
        await _debug_page(args.url)
        return
    if args.cmd == "import-knowledge":
        from pathlib import Path

        from app.knowledge.importer import DEFAULT_DIR, import_dir

        directory = Path(args.dir) if args.dir else DEFAULT_DIR
        async with get_sessionmaker()() as session:
            report = await import_dir(session, directory)
        for name, outcome in report.files:
            print(f"{outcome:9s} {name}")
        print("New/changed knowledge is 'pending': review it at http://localhost:8000/admin")
        await get_engine().dispose()
        return
    if args.cmd == "crawl":
        changes: dict = {"max_pages": args.max_pages, "max_depth": args.max_depth}
        if args.url:
            changes["seeds"] = tuple(args.url)
        cfg = dataclasses.replace(cfg, **changes)
    async with Fetcher(cfg) as fetcher, get_sessionmaker()() as session:
        if args.cmd == "crawl":
            report = await crawl(session, fetcher, cfg)
        else:
            report = await recrawl(session, fetcher, cfg)
    _print_report(report)
    await get_engine().dispose()


def main() -> None:
    setup_logging(get_settings().log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="python -m app.ingestion.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crawl", help="crawl MEU Faculty of Engineering pages from the seeds")
    c.add_argument("--max-pages", type=int, default=CrawlConfig.max_pages)
    c.add_argument("--max-depth", type=int, default=CrawlConfig.max_depth)
    c.add_argument("--url", action="append", help="crawl from this URL instead of the default seeds (repeatable)")
    sub.add_parser("recrawl", help="re-fetch known pages and flag changes as stale/pending")
    sub.add_parser("stats", help="count sources and chunks by review status")
    d = sub.add_parser("debug-page", help="fetch one URL, save raw HTML, show cleaned text")
    d.add_argument("url")
    k = sub.add_parser("import-knowledge", help="import curated files from data/knowledge/*.json")
    k.add_argument("--dir", help="folder with knowledge JSON files (default: data/knowledge)")
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()
