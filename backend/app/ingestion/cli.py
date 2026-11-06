"""Command line for ingestion.

    python -m app.ingestion.cli crawl [--max-pages 60] [--max-depth 2] [--url URL ...]
    python -m app.ingestion.cli recrawl
    python -m app.ingestion.cli stats
    python -m app.ingestion.cli import-knowledge [--dir data/knowledge]
    python -m app.ingestion.cli debug-page URL
    python -m app.ingestion.cli check-openai          # key + model check (B3)
    python -m app.ingestion.cli embed                 # embeddings for approved chunks
    python -m app.ingestion.cli ask "question"        # one grounded answer with sources
    python -m app.ingestion.cli evaluate              # golden set -> data/golden/results/
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


async def _check_openai() -> None:
    from app.services.providers import OpenAIEmbedder, OpenAILLM, ProviderError, _openai_client

    settings = get_settings()
    print(f"LLM_MODEL={settings.llm_model}  EMBEDDING_MODEL={settings.embedding_model}")
    try:
        client = _openai_client()
        ids = sorted(m.id for m in (await client.models.list()).data)
        chat_like = [i for i in ids if i.startswith(("gpt-", "o")) and "embedding" not in i]
        print(f"key OK — {len(ids)} models visible. Chat models:\n  " + ", ".join(chat_like))
        print("Embedding models: " + ", ".join(i for i in ids if "embedding" in i))
    except Exception as e:  # noqa: BLE001
        print(f"FAILED to list models: {e}")
        return
    try:
        vec = (await OpenAIEmbedder().embed(["اختبار test"]))[0]
        print(f"embedding OK — {len(vec)} dimensions")
    except ProviderError as e:
        print(f"embedding FAILED: {e}")
    try:
        r = await OpenAILLM().complete_json(
            'Reply with the JSON object {"ok": true, "answer": "<one short Jordanian Arabic greeting>"}',
            "Say hello. Reply in JSON.",
        )
        print(f"LLM OK — {r.ms} ms, reply: {r.data}")
    except ProviderError as e:
        print(f"LLM FAILED: {e}\nPick a chat model from the list above and set LLM_MODEL in .env")


async def _ask(question: str) -> None:
    from app.services.chat import answer_question
    from app.services.providers import get_embedder, get_llm

    async with get_sessionmaker()() as session:
        r = await answer_question(session, get_embedder(), get_llm(), question, log=False)
    print(f"\n{r.answer}\n")
    print(f"found={r.found} lang={r.lang} model={r.model} timings={r.timings}")
    for s in r.sources:
        print(f"  [{s.n}] {s.title}  ({s.category})")
    if r.invented_numbers:
        print(f"  WARNING numbers not in sources: {r.invented_numbers}")
    if r.error:
        print(f"  ERROR: {r.error}")
    print("\nPassages given to the model:")
    for i, p in enumerate(r.passages, start=1):
        print(f"  [{i}] score={p.score} fts={p.in_fts} vec={p.in_vector}  {p.source_title}")


async def _embed() -> None:
    from app.services.providers import get_embedder
    from app.services.rag import embed_missing

    async with get_sessionmaker()() as session:
        n = await embed_missing(session, get_embedder())
    print(f"embedded {n} approved chunk(s)")


async def _evaluate() -> None:
    from app.services.evaluate import evaluate, load_questions, write_report
    from app.services.providers import get_embedder, get_llm

    questions = load_questions()
    async with get_sessionmaker()() as session:
        report = await evaluate(session, get_embedder(), get_llm(), questions)
    json_path, md_path = write_report(report)
    for r in report.results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"{mark}  {r.id}: {r.answer[:110]}")
        for reason in r.reasons:
            print(f"        - {reason}")
    print(
        f"\nPassed {report.passed}/{report.total} ({report.accuracy:.0%}), "
        f"numbers not in sources: {report.invented_numbers_total}, "
        f"median {report.latency_median_ms} ms, p90 {report.latency_p90_ms} ms"
    )
    print(f"Report: backend/{md_path.relative_to(md_path.parents[3])}")


async def _main(args: argparse.Namespace) -> None:
    cfg = CrawlConfig()
    simple = {
        "check-openai": _check_openai,
        "embed": _embed,
        "evaluate": _evaluate,
    }
    if args.cmd in simple:
        await simple[args.cmd]()
        await get_engine().dispose()
        return
    if args.cmd == "ask":
        await _ask(" ".join(args.question))
        await get_engine().dispose()
        return
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
        print(f"search hints updated on {report.hints_updated} chunk(s)")
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
    sub.add_parser("check-openai", help="check the OpenAI key, list models, test embedding + LLM")
    sub.add_parser("embed", help="compute embeddings for approved chunks that have none")
    a = sub.add_parser("ask", help="ask one question (text) and show answer, sources, timings")
    a.add_argument("question", nargs="+")
    sub.add_parser("evaluate", help="run the golden question set and write a report")
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()
