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
    python -m app.ingestion.cli tts-samples           # one mp3 per voice (accent test)
    python -m app.ingestion.cli bench                 # LLM/STT model latency + correctness
    python -m app.ingestion.cli latency [--n 8]       # retrieval -> LLM -> first TTS piece timings
    python -m app.ingestion.cli seed-demo-media       # dev only: labelled placeholder images
    python -m app.ingestion.cli simulate-run [--seed 7]   # SIMULATED robot run (never a real result)
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
    from app.services.speech import OpenAISTT, OpenAITTS

    print(f"TTS_MODEL={settings.tts_model} TTS_VOICE={settings.tts_voice} STT_MODEL={settings.stt_model}")
    try:
        mp3 = await OpenAITTS().synthesize("أهلاً وسهلاً فيك بكلية الهندسة والتصميم")
        print(f"TTS OK — {len(mp3)} bytes of mp3")
    except ProviderError as e:
        print(f"TTS FAILED: {e}")
        return
    try:
        stt = await OpenAISTT().transcribe(mp3, filename="check.mp3")
        print(f"STT OK — {stt.ms} ms, heard: {stt.text!r}")
    except ProviderError as e:
        print(f"STT FAILED: {e}\nTry STT_MODEL=gpt-4o-transcribe in .env")


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


async def _tts_samples() -> None:
    """One mp3 per voice in data/voice_samples/ for the Jordanian-accent listening test."""
    from pathlib import Path

    from app.services.speech import VOICES, OpenAITTS

    out = Path(__file__).resolve().parents[2] / "data" / "voice_samples"
    out.mkdir(parents=True, exist_ok=True)
    text = (
        "أهلاً وسهلاً فيك! أنا روبوت كلية الهندسة والتصميم. تخصص هندسة الأنظمة الذكية بدّه "
        "مية واثنين وستين ساعة معتمدة، وفيه مواد عن الروبوتات. عندك سؤال ثاني؟"
    )
    tts = OpenAITTS()
    for voice in VOICES:
        try:
            (out / f"{voice}.mp3").write_bytes(await tts.synthesize(text, voice=voice))
            print(f"ok  {voice}")
        except Exception as e:  # noqa: BLE001
            print(f"ERR {voice}: {e}")
    print(f"Listen to backend/data/voice_samples/*.mp3 and set TTS_VOICE=<best> in .env")


# 10 golden questions answered from APPROVED sources. On 2026-11-13 "faculty-programs-ar" and
# "meu-founded-ar" were replaced: their sources were stale / pending review, so they could not
# pass whatever the model did. `latency` uses the first N (all answerable).
BENCH_IDS = (
    "ise-hours-ar", "arch-hours-ar", "ise-careers-ar", "ise-accredit-ar", "arch-years-ar",
    "interior-training-ar", "ise-careers-en", "ise-vs-se-ar", "ise-fee-ar", "ree-labs-ar",
)  # fmt: skip


async def _latency(n: int, stream: bool, effort: str | None) -> None:
    """Median/p90 per stage and time to the first answer audio, on the real pipeline."""
    from app.services.evaluate import load_questions
    from app.services.latency import format_report, measure, write_results
    from app.services.providers import OpenAILLM, get_embedder
    from app.services.speech import OpenAITTS

    by_id = {q["id"]: q for q in load_questions()}
    questions = [by_id[i] for i in BENCH_IDS if i in by_id][:n]
    llm = OpenAILLM()
    if effort:
        llm.reasoning_effort = effort
    tts = OpenAITTS()
    async with get_sessionmaker()() as session:
        rows = await measure(session, get_embedder(), llm, tts, questions, stream)
    label = (
        f"{llm.model}, effort={llm.reasoning_effort or 'default'}, "
        f"{'streaming' if stream else 'no streaming'}, TTS {tts.model}/{tts.voice}"
    )
    print(format_report(rows, label))
    path = write_results(rows, {
        "model": llm.model, "reasoning_effort": llm.reasoning_effort, "streaming": stream,
        "tts_model": tts.model, "tts_voice": tts.voice,
    })  # fmt: skip
    print(f"\nSaved: backend/{path.relative_to(path.parents[2])}")


async def _bench(models: list[str], effort: str | None, stt_models: list[str]) -> None:
    """Compare LLM models (latency + correctness on 10 golden questions) and STT models."""
    import statistics

    from app.services.chat import answer_question
    from app.services.evaluate import check, load_questions
    from app.services.providers import OpenAILLM, ProviderError, get_embedder

    questions = [q for q in load_questions() if q["id"] in BENCH_IDS]
    embedder = get_embedder()
    rows = []
    for model in models:
        llm = OpenAILLM()
        llm.model, llm.reasoning_effort = model, effort or None
        times, passed, words, errors = [], 0, [], 0
        async with get_sessionmaker()() as session:
            for q in questions:
                r = await answer_question(session, embedder, llm, q["question"], log=False)
                if r.error:
                    errors += 1
                    print(f"  {model}: ERROR {r.error[:120]}")
                    break
                times.append(r.timings.get("llm_ms", 0))
                words.append(len(r.answer.split()))
                passed += not check(q, r.answer, r.found, r.invented_numbers)
        if times:
            rows.append((model, statistics.median(times), max(times), passed, len(times),
                         statistics.mean(words)))  # fmt: skip
        print(f"done {model}")
    print("\nmodel                 median_ms  max_ms  passed  avg_words")
    for m, med, mx, ok, n, w in sorted(rows, key=lambda r: r[1]):
        print(f"{m:20s} {med:9.0f} {mx:7.0f}  {ok:>2}/{n:<3}  {w:9.1f}")

    if stt_models:
        from app.services.speech import OpenAISTT, OpenAITTS

        sample = "كم ساعة معتمدة بدها هندسة الأنظمة الذكية؟"
        try:
            mp3 = await OpenAITTS().synthesize(sample)
        except ProviderError as e:
            print(f"TTS failed, cannot test STT: {e}")
            return
        print(f"\nSTT test sentence: {sample}")
        for m in stt_models:
            stt = OpenAISTT()
            stt.model = m
            try:
                r = await stt.transcribe(mp3, filename="bench.mp3")
                print(f"{m:24s} {r.ms:6d} ms  heard: {r.text}")
            except ProviderError as e:
                print(f"{m:24s} ERROR {str(e)[:100]}")
    print("\nPut the fastest model that still passes ~10/10 in .env (LLM_MODEL=..., STT_MODEL=...).")


DEMO_MEDIA = (
    ("placeholder_ise.png", "intelligent-systems-engineering"),
    ("placeholder_ree.png", "renewable-energy-engineering"),
)
DEMO_MEDIA_NOTE = "generated placeholder"


async def _seed_demo_media() -> None:
    """Dev only: copy the generated, clearly labelled placeholder images into data/media and
    add them as approved media, so the robot screen can be shown before real MEU media exists."""
    from app.db.models import Media, Program
    from app.services.media import MEDIA_DIR, store, validate

    if get_settings().app_env == "production":
        print("seed-demo-media is a development command; it does nothing in production.")
        return
    assets = __import__("pathlib").Path(__file__).resolve().parents[1] / "demo_media"
    async with get_sessionmaker()() as session:
        for filename, slug in DEMO_MEDIA:
            program = await session.scalar(select(Program).where(Program.slug == slug))
            if program is None:
                print(f"skip {filename}: program '{slug}' is not in the database")
                continue
            existing = await session.scalar(
                select(Media).where(Media.program_id == program.id, Media.source_note == DEMO_MEDIA_NOTE)
            )
            if existing is not None:
                print(f"exists  media {existing.id} for {slug}")
                continue
            data = (assets / filename).read_bytes()
            kind, mime, ext = validate(filename, data)
            media = Media(
                kind=kind, path=store(data, ext, MEDIA_DIR), mime=mime, size_bytes=len(data),
                title=f"Sample media (placeholder) — {program.name_en}",
                alt_ar="صورة تجريبية — ليست صورة حقيقية من الجامعة",
                alt_en="Sample media — placeholder, not a real MEU photo",
                source_note=DEMO_MEDIA_NOTE, program_id=program.id, review_status="approved",
            )  # fmt: skip
            session.add(media)
            await session.commit()
            print(f"created media {media.id} for {slug} (approved, source_note='{DEMO_MEDIA_NOTE}')")
    print("Replace these with real MEU media in /admin → Media (upload, link to a program, approve).")


async def _simulate_run(seed: int, roam_seconds: float) -> None:
    """Store one SIMULATED Rover 5 run (roaming + return-to-origin) with is_simulated = true."""
    from app.telemetry.simulator import SimConfig, simulate_run
    from app.telemetry.store import MAX_SAMPLES_PER_MESSAGE, add_event, add_samples, end_run, start_run

    result = simulate_run(SimConfig(seed=seed, roam_seconds=roam_seconds))
    async with get_sessionmaker()() as session:
        run = await start_run(
            session, True, result.odom_config(),
            notes=f"SIMULATED run (seed {seed}) - not a physical result",
        )  # fmt: skip
        for i in range(0, len(result.samples), MAX_SAMPLES_PER_MESSAGE):
            await add_samples(session, run.id, result.samples[i : i + MAX_SAMPLES_PER_MESSAGE])
        for event in result.events:
            await add_event(session, run.id, event)
        counts = await end_run(session, run.id)
    last = result.events[-1]
    print("*** SIMULATED - not a physical result ***")
    print(f"run {run.id}: {counts['samples']} samples, {counts['events']} events, "
          f"{result.samples[-1]['t_ms'] / 1000:.0f} s, outcome {result.outcome} ({last['data'].get('method')})")  # fmt: skip
    print(f"odometry end distance {last['data']['odom_distance_m']} m; simulated true error "
          f"{last['data']['simulated_true_error_m']} m (a simulation value, not a measurement)")
    print("See it at http://localhost:8000/admin -> Robot runs")


async def _main(args: argparse.Namespace) -> None:
    cfg = CrawlConfig()
    simple = {
        "check-openai": _check_openai,
        "embed": _embed,
        "evaluate": _evaluate,
        "tts-samples": _tts_samples,
        "seed-demo-media": _seed_demo_media,
    }
    if args.cmd in simple:
        await simple[args.cmd]()
        await get_engine().dispose()
        return
    if args.cmd == "bench":
        await _bench(args.models, args.effort, args.stt)
        await get_engine().dispose()
        return
    if args.cmd == "simulate-run":
        await _simulate_run(args.seed, args.roam_seconds)
        await get_engine().dispose()
        return
    if args.cmd == "latency":
        await _latency(args.n, not args.no_stream, args.effort)
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
    sub.add_parser("tts-samples", help="write one mp3 per TTS voice for the accent listening test")
    b = sub.add_parser("bench", help="compare LLM (and STT) models: latency and correctness")
    b.add_argument("--models", nargs="+", default=["gpt-6-luna", "gpt-4.1-mini", "gpt-5.4-nano", "gpt-5.4-mini"])
    b.add_argument("--effort", default=None, help="reasoning effort for reasoning models, e.g. low / minimal")
    b.add_argument("--stt", nargs="*", default=["gpt-transcribe", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"])
    sub.add_parser("seed-demo-media", help="dev only: add labelled placeholder images as approved media")
    sim = sub.add_parser("simulate-run", help="store a SIMULATED robot run (trajectory + return-to-origin)")
    sim.add_argument("--seed", type=int, default=7, help="random seed (16 shows the breadcrumb fallback)")
    sim.add_argument("--roam-seconds", type=float, default=45.0)
    lat = sub.add_parser("latency", help="time retrieval, LLM and first TTS piece on golden questions")
    lat.add_argument("--n", type=int, default=8, help="number of golden questions (default 8)")
    lat.add_argument("--no-stream", action="store_true", help="wait for the whole LLM answer (old behaviour)")
    lat.add_argument("--effort", default=None, help="reasoning effort override, e.g. low")
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()
