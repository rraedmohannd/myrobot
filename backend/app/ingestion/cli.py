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
python -m app.ingestion.cli latency --dialogue    # the same through the conversation manager
python -m app.ingestion.cli bench --stt-only      # STT models on the same spoken sentences
python -m app.ingestion.cli prepare-phrases       # fixed phrases with the current voice/pace
python -m app.ingestion.cli draft-explainers [--program slug] [--lang ar|en]
                                        # PENDING drafts (1 LLM call each)
python -m app.ingestion.cli prepare-explainers    # speech of the APPROVED explainers -> cache
python -m app.ingestion.cli seed-interests        # PENDING interest -> program rows
python -m app.ingestion.cli draft-faqs [--program slug | --general]
                                        # PENDING FAQs and pitches (1 LLM call each)
python -m app.ingestion.cli seed-demo-media       # dev only: labelled placeholder images
python -m app.ingestion.cli simulate-run [--seed 7]   # SIMULATED robot run (never a real result)
python -m app.ingestion.cli simulate-robot [--seconds 120]
                                        # SIMULATED robot client (status, commands)
python -m app.ingestion.cli check-curated         # READ ONLY: FAQs and pitches vs the review rules
python -m app.ingestion.cli create-admin NAME     # admin login (asks for the password)
python -m app.ingestion.cli purge-conversations [--days 30] [--dry-run]
                                        # privacy: delete old conversations
python -m app.ingestion.cli export-report [--out /reports]
                                        # CSVs and charts for the graduation report
python -m app.ingestion.cli soak [--minutes 30]
                                        # SIMULATED mixed session (see docs/HOW_TO_START.md)
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
            'Reply with the JSON object {"ok": true, "answer": "<one short Jordanian Arabic '
            'greeting>"}',
            "Say hello. Reply in JSON.",
        )
        print(f"LLM OK — {r.ms} ms, reply: {r.data}")
    except ProviderError as e:
        print(f"LLM FAILED: {e}\nPick a chat model from the list above and set LLM_MODEL in .env")
    from app.services.speech import OpenAISTT, OpenAITTS

    print(
        f"TTS_MODEL={settings.tts_model} TTS_VOICE={settings.tts_voice} "
        f"STT_MODEL={settings.stt_model}"
    )
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


async def _latency_dialogue(questions: list, llm, stream_audio: bool) -> None:  # noqa: ANN001
    """`latency --dialogue`: typed questions through the real conversation manager."""
    from sqlalchemy import delete

    from app.db.models import Session
    from app.services.latency import format_report, measure_dialogue, write_results
    from app.services.providers import get_embedder
    from app.services.speech import get_stt, get_tts

    tts = get_tts()
    rows, session_ids = await measure_dialogue(
        get_sessionmaker(), get_embedder(), llm, get_stt(), tts, questions, stream_audio
    )
    async with get_sessionmaker()() as session:  # measurements are not visitors: remove them
        await session.execute(delete(Session).where(Session.id.in_(session_ids)))
        await session.commit()
    label = (
        f"conversation manager, {llm.model}, effort={llm.reasoning_effort or 'default'}, "
        f"answer audio {'streamed as PCM chunks' if stream_audio else 'as whole mp3 pieces'}, "
        f"TTS {tts.model}/{tts.voice}"
    )
    print(format_report(rows, label))
    print("\n'audio' = question text -> first audio of the answer sent to the client "
          "(the cached filler is not counted; speech-to-text is not included).")  # fmt: skip
    path = write_results(rows, {
        "mode": "dialogue", "model": llm.model, "reasoning_effort": llm.reasoning_effort,
        "audio_stream": stream_audio, "tts_model": tts.model, "tts_voice": tts.voice,
    })  # fmt: skip
    print(f"Saved: backend/{path.relative_to(path.parents[2])}")


async def _latency(n: int, stream: bool, effort: str | None, dialogue: bool = False,
                   stream_audio: bool = True) -> None:  # fmt: skip
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
    if dialogue:
        await _latency_dialogue(questions, llm, stream_audio)
        return
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


# Sentences for the STT comparison. They are spoken by the text-to-speech voice (there are no
# human recordings in the repository), so the result says little about real Jordanian speakers.
STT_SAMPLES = (
    "كم ساعة معتمدة بدها هندسة الأنظمة الذكية؟",
    "احكيلي عن تخصص هندسة الطاقة المتجددة.",
    "وين بيشتغلوا خريجين التصميم الجرافيكي؟",
    "مش عارف شو أدرس، ساعدني أختار.",
    "شو الفرق بين هندسة العمارة والتصميم الداخلي؟",
    "قديش الحد الأعلى للساعات اللي بقدر أسجلها بالفصل؟",
    "Tell me about Biomedical Engineering.",
    "How many credit hours is Interior Design?",
)


def _stt_words(text: str) -> list[str]:
    from app.services.dialogue import _norm

    return _norm(text).split()


async def _bench_stt(stt_models: list[str], repeats: int = 2) -> None:
    """The same recordings through each STT model: median time and words heard correctly."""
    import statistics

    from app.services.providers import ProviderError
    from app.services.speech import OpenAISTT, OpenAITTS

    try:
        tts = OpenAITTS()
        recordings = [(s, await tts.synthesize(s)) for s in STT_SAMPLES]
    except ProviderError as e:
        print(f"TTS failed, cannot test STT: {e}")
        return
    print(
        f"\nSTT comparison: {len(recordings)} sentences spoken by the TTS voice (synthetic speech, "
        f"not human recordings), {repeats} pass(es) per model, the same audio for every model."
    )
    rows = []
    for m in stt_models:
        stt = OpenAISTT()
        stt.model = m
        times, exact, word_hits, word_total, errors = [], 0, 0, 0, 0
        for _ in range(repeats):
            for sentence, mp3 in recordings:
                try:
                    r = await stt.transcribe(mp3, filename="bench.mp3")
                except ProviderError as e:
                    errors += 1
                    print(f"  {m}: ERROR {str(e)[:100]}")
                    continue
                times.append(r.ms)
                want, got = _stt_words(sentence), _stt_words(r.text)
                exact += want == got
                word_hits += sum(1 for w in want if w in got)
                word_total += len(want)
                if want != got:
                    print(f"  {m}: heard {r.text!r} for {sentence!r}")
        if times:
            rows.append(
                (
                    m,
                    statistics.median(times),
                    max(times),
                    exact,
                    len(times),
                    word_hits,
                    word_total,
                    errors,
                )
            )
    print("\nSTT model                median_ms  max_ms  exact  words_heard  errors")
    for m, med, mx, exact, n, hits, total, errors in rows:
        print(f"{m:24s} {med:9.0f} {mx:7.0f}  {exact:>2}/{n:<3} {hits:>5}/{total:<5} {errors:>6}")


async def _bench(models: list[str], effort: str | None, stt_models: list[str],
                 stt_only: bool = False) -> None:  # fmt: skip
    """Compare LLM models (latency + correctness on 10 golden questions) and STT models."""
    import statistics

    from app.services.chat import answer_question
    from app.services.evaluate import check, load_questions
    from app.services.providers import OpenAILLM, ProviderError, get_embedder

    if stt_only:
        await _bench_stt(stt_models)
        return
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
    print(
        "\nPut the fastest model that still passes ~10/10 in .env (LLM_MODEL=..., STT_MODEL=...)."
    )


async def _prepare_phrases() -> None:
    """Generate the fixed phrases (greeting, fillers, follow-up...) with the current voice, pace
    and instructions. The cache key includes all of them, so run this after changing any."""
    from app.services.dialogue import fixed_phrase_pieces
    from app.services.providers import ProviderError
    from app.services.speech import get_tts

    tts = get_tts()
    made = ready = failed = 0
    for piece in fixed_phrase_pieces():
        if tts.is_cached(piece):
            ready += 1
            continue
        try:
            await tts.synthesize(piece, cache=True)
            made += 1
        except ProviderError as e:
            failed += 1
            print(f"ERR {piece[:40]}: {e}")
    print(
        f"fixed phrases: {made} generated, {ready} already cached, {failed} failed "
        f"(model {tts.model}, voice {tts.voice}, speed {getattr(tts.inner, 'speed', 1.0)})"
    )


async def _draft_explainers(slug: str | None, lang: str | None, force: bool) -> None:
    """One LLM call per program and language -> PENDING explainer drafts (never approved here)."""
    from app.db.models import Program
    from app.services.explainers import draft_explainer
    from app.services.providers import OpenAILLM

    llm = OpenAILLM()
    llm.max_tokens = max(llm.max_tokens, 4000)  # 5-7 segments of JSON, plus reasoning tokens
    langs = [lang] if lang else ["ar", "en"]
    calls = 0
    async with get_sessionmaker()() as session:
        query = select(Program).order_by(Program.id)
        if slug:
            query = query.where(Program.slug == slug)
        programs = (await session.scalars(query)).all()
        if not programs:
            print(f"no program with slug {slug!r}")
            return
        for program in programs:
            for code in langs:
                r = await draft_explainer(session, llm, program, code, force=force)
                calls += r.outcome in ("created", "replaced", "failed")
                line = f"{r.outcome:16s} {program.slug} [{code}]"
                if r.segments:
                    line += (
                        f": {r.segments} segments, {len(r.issues)} issue(s) to fix before approval"
                    )
                if r.error:
                    line += f": {r.error[:160]}"
                print(line)
                for issue in r.issues:
                    print(f"      segment {issue.segment}: {issue.kind}: {issue.detail}")
    print(f"\n{calls} LLM call(s). Drafts are PENDING: review, edit and approve them at "
          "http://localhost:8000/admin -> Explainers")  # fmt: skip


async def _draft_faqs(slug: str | None, general: bool, n: int, force: bool) -> None:
    """One LLM call per program (FAQs + the `why` and `compare` pitches) and one for the handbook
    rules. Everything is stored PENDING; nothing is approved here."""
    from app.db.models import Program
    from app.services.curated import draft_curated
    from app.services.providers import OpenAILLM

    llm = OpenAILLM()
    llm.max_tokens = max(llm.max_tokens, 6000)
    calls = 0
    async with get_sessionmaker()() as session:
        targets: list = []
        if not general:
            query = select(Program).order_by(Program.id)
            if slug:
                query = query.where(Program.slug == slug)
            targets += (await session.scalars(query)).all()
            if slug and not targets:
                print(f"no program with slug {slug!r}")
                return
        if general or not slug:
            targets.append(None)  # the handbook rules
        for program in targets:
            r = await draft_curated(
                session, llm, program, n_faqs=n if program else n + 2, force=force
            )
            calls += r.outcome in ("drafted", "failed")
            line = f"{r.outcome:11s} {r.target}: {r.faqs_created} FAQ(s)"
            if r.faqs_skipped:
                line += f", {r.faqs_skipped} already existed"
            if r.pitches_created:
                line += f", pitches: {', '.join(r.pitches_created)}"
            if r.error:
                line += f": {r.error[:160]}"
            print(line)
            for issue in r.issues:
                print(f"      to fix before approval: {issue}")
    print(f"\n{calls} LLM call(s). Drafts are PENDING: review, edit and approve them at "
          "http://localhost:8000/admin -> FAQs & pitches")  # fmt: skip


async def _prepare_explainers() -> None:
    """Generate and cache the speech of every APPROVED explainer (instant playback)."""
    from app.db.models import Explainer, Program
    from app.services.explainers import prepare_audio
    from app.services.providers import ProviderError
    from app.services.speech import get_tts

    tts = get_tts()
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(Explainer, Program).join(Program, Program.id == Explainer.program_id)
                .where(Explainer.status == "approved").order_by(Program.id, Explainer.lang)
            )
        ).all()  # fmt: skip
        if not rows:
            print(
                "No approved explainers yet. Approve drafts at http://localhost:8000/admin -> "
                "Explainers"
            )
        for explainer, program in rows:
            try:
                made, ready = await prepare_audio(session, tts, explainer)
                print(
                    f"ok   {program.slug} [{explainer.lang}]: {made} generated, {ready} already "
                    "cached"
                )
            except ProviderError as e:
                print(f"ERR  {program.slug} [{explainer.lang}]: {e}")


async def _seed_interests() -> None:
    from app.services.interests import seed_from_explainers

    async with get_sessionmaker()() as session:
        report = await seed_from_explainers(session)
    for outcome, rows in report.items():
        for row in rows:
            print(f"{outcome:22s} {row}")
    print(
        "New rows are PENDING: review them at http://localhost:8000/admin -> Explainers -> "
        "Interests"
    )


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
                select(Media).where(
                    Media.program_id == program.id, Media.source_note == DEMO_MEDIA_NOTE
                )
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
            print(
                f"created media {media.id} for {slug} (approved, source_note='{DEMO_MEDIA_NOTE}')"
            )
    print(
        "Replace these with real MEU media in /admin → Media (upload, link to a program, approve)."
    )


async def _simulate_run(seed: int, roam_seconds: float) -> None:
    """Store one SIMULATED Rover 5 run (roaming + return-to-origin) with is_simulated = true."""
    from app.telemetry.simulator import SimConfig, simulate_run
    from app.telemetry.store import (
        MAX_SAMPLES_PER_MESSAGE,
        add_event,
        add_samples,
        end_run,
        start_run,
    )

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
    print(
        f"run {run.id}: {counts['samples']} samples, {counts['events']} events, "
        f"{result.samples[-1]['t_ms'] / 1000:.0f} s, outcome {result.outcome} "
        f"({last['data'].get('method')})"
    )
    print(
        f"odometry end distance {last['data']['odom_distance_m']} m; simulated true error "
        f"{last['data']['simulated_true_error_m']} m (a simulation value, not a measurement)"
    )
    print("See it at http://localhost:8000/admin -> Robot runs")


async def _create_admin(username: str, password_stdin: bool) -> None:
    """Create an admin user (or set a new password). The password is typed, never an argument,
    so it does not end up in the shell history."""
    import getpass
    import sys

    from app.services.auth import AuthError, create_admin

    if password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    elif sys.stdin.isatty():
        password = getpass.getpass("Password (at least 10 characters): ")
        if getpass.getpass("Again: ") != password:
            print("The two passwords are different. Nothing was changed.")
            return
    else:
        print("No terminal: run this without -T (docker compose exec backend ...), or pipe the "
              "password with --password-stdin.")  # fmt: skip
        return
    async with get_sessionmaker()() as session:
        try:
            user, created = await create_admin(session, username, password)
        except AuthError as e:
            print(f"Not created: {e}")
            return
    print(f"{'created' if created else 'password changed for'} admin user '{user.username}'. "
          "Log in at http://localhost:8000/admin")  # fmt: skip


async def _check_curated() -> None:
    """READ ONLY: lists every FAQ and pitch that would be refused by the review rules today
    (wrong program, numbers not in the program's sources, ...). Changes nothing."""
    import json
    from collections import Counter

    from app.services.curated import check_curated

    async with get_sessionmaker()() as session:
        report = await check_curated(session)
        await session.rollback()  # nothing was written; make that explicit
    for name in ("faqs", "pitches"):
        rows = report[name]
        print(f"{name}: {len(rows)} checked, by status {dict(Counter(r['status'] for r in rows))}")
        bad = [r for r in rows if r["issues"]]
        wrong = sum(1 for r in bad if r.get("wrong_program"))
        print(f"  with issues: {len(bad)}; wrong program: {wrong}")
        for r in bad:
            print("  " + json.dumps(r, ensure_ascii=False))


async def _purge_conversations(days: int | None, dry_run: bool) -> None:
    from app.services.auth import purge_conversations

    async with get_sessionmaker()() as session:
        r = await purge_conversations(session, days, dry_run)
    verb = "would delete" if dry_run else "deleted"
    print(
        f"{verb} {r['sessions']} conversation(s) with {r['turns']} turn(s) older than {r['days']} "
        "days"
    )


async def _export_report(out: str | None) -> None:
    """CSV files and PNG charts for the graduation report (reports/ is git-ignored)."""
    from pathlib import Path

    from app.services.report import export_report

    out_dir = (
        Path(out) if out else (Path("/reports") if Path("/reports").is_dir() else Path("reports"))
    )
    async with get_sessionmaker()() as session:
        files = await export_report(session, out_dir)
    for path in files:
        print(f"  {path.name}")
    print(f"{len(files)} file(s) written to {out_dir} (on the laptop: the reports folder of the "
          "project). "
          "Simulated runs are titled SIMULATED; they are not results.")  # fmt: skip


async def _simulate_robot(url: str, seconds: float, seed: int, token: str | None) -> None:
    """Connect a SIMULATED robot (protocol 2) to the backend: live status, telemetry when it
    roams, and answers to the command buttons of the admin "Robot" tab."""
    from app.telemetry.sim_robot import run_sim_robot

    print(
        "*** SIMULATED robot - not a physical robot. Its runs are stored with is_simulated = true. "
        "***"
    )
    print(f"connecting to {url} for {seconds:g} s; open http://localhost:8000/admin -> Robot")
    try:
        report = await run_sim_robot(url, seconds, seed=seed, token=token)
    except OSError as e:
        print(f"could not connect: {e}")
        return
    print(f"done: {report.status_sent} status messages, {report.telemetry_samples} pose samples, "
          f"runs {report.runs or 'none'}, commands {report.commands or 'none'}")  # fmt: skip
    if report.errors:
        print("errors:", report.errors)


async def _usage_today() -> None:
    """One JSON line: today's real AI provider calls against DAILY_PAID_CALL_LIMIT
    (deploy/server/status.sh reads it). "limit" is null when the cap is off (development)."""
    import json

    from app.services.cost_guard import usage_today

    usage = await usage_today()
    print(json.dumps({k: usage[k] for k in ("date", "paid_calls", "limit")}))


async def _main(args: argparse.Namespace) -> None:
    cfg = CrawlConfig()
    simple = {
        "usage-today": _usage_today,
        "check-openai": _check_openai,
        "embed": _embed,
        "evaluate": _evaluate,
        "tts-samples": _tts_samples,
        "seed-demo-media": _seed_demo_media,
        "prepare-phrases": _prepare_phrases,
        "prepare-explainers": _prepare_explainers,
        "seed-interests": _seed_interests,
    }
    if args.cmd in simple:
        await simple[args.cmd]()
        await get_engine().dispose()
        return
    if args.cmd == "draft-faqs":
        await _draft_faqs(args.program, args.general, args.n, args.force)
        await get_engine().dispose()
        return
    if args.cmd == "draft-explainers":
        await _draft_explainers(args.program, args.lang, args.force)
        await get_engine().dispose()
        return
    if args.cmd == "bench":
        await _bench(args.models, args.effort, args.stt, args.stt_only)
        await get_engine().dispose()
        return
    if args.cmd == "simulate-run":
        await _simulate_run(args.seed, args.roam_seconds)
        await get_engine().dispose()
        return
    if args.cmd == "create-admin":
        await _create_admin(args.username, args.password_stdin)
        await get_engine().dispose()
        return
    if args.cmd == "check-curated":
        await _check_curated()
        await get_engine().dispose()
        return
    if args.cmd == "purge-conversations":
        await _purge_conversations(args.days, args.dry_run)
        await get_engine().dispose()
        return
    if args.cmd == "export-report":
        await _export_report(args.out)
        await get_engine().dispose()
        return
    if args.cmd == "soak":
        from app.soak import main as soak_main

        await soak_main(args.minutes, args.url, args.admin_url, args.out)
        return
    if args.cmd == "simulate-robot":
        await _simulate_robot(args.url, args.seconds, args.seed, args.token)
        return
    if args.cmd == "latency":
        await _latency(
            args.n, not args.no_stream, args.effort, args.dialogue, not args.no_stream_audio
        )
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
    from app.core.config import ProductionConfigError, check_production

    setup_logging(get_settings().log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:  # the same startup check as the server (APP_ENV=production only)
        check_production(get_settings())
    except ProductionConfigError as e:
        raise SystemExit(str(e)) from None
    p = argparse.ArgumentParser(prog="python -m app.ingestion.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crawl", help="crawl MEU Faculty of Engineering pages from the seeds")
    c.add_argument("--max-pages", type=int, default=CrawlConfig.max_pages)
    c.add_argument("--max-depth", type=int, default=CrawlConfig.max_depth)
    c.add_argument(
        "--url",
        action="append",
        help="crawl from this URL instead of the default seeds (repeatable)",
    )
    sub.add_parser("recrawl", help="re-fetch known pages and flag changes as stale/pending")
    sub.add_parser("stats", help="count sources and chunks by review status")
    sub.add_parser(
        "usage-today", help='one JSON line {"date", "paid_calls", "limit"} (daily paid-call cap)'
    )
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
    b.add_argument(
        "--models",
        nargs="+",
        default=["gpt-6-luna", "gpt-4.1-mini", "gpt-5.4-nano", "gpt-5.4-mini"],
    )
    b.add_argument(
        "--effort", default=None, help="reasoning effort for reasoning models, e.g. low / minimal"
    )
    b.add_argument(
        "--stt",
        nargs="*",
        default=["gpt-transcribe", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"],
    )
    b.add_argument(
        "--stt-only",
        action="store_true",
        help="only compare the STT models, on several sentences spoken by the TTS voice",
    )
    sub.add_parser(
        "seed-demo-media", help="dev only: add labelled placeholder images as approved media"
    )
    sub.add_parser(
        "prepare-phrases", help="generate the fixed phrases with the current voice and pace"
    )
    dx = sub.add_parser(
        "draft-explainers", help="draft PENDING program explainers from approved sources"
    )
    dx.add_argument("--program", help="program slug (default: every program)")
    dx.add_argument("--lang", choices=["ar", "en"], help="one language (default: both)")
    dx.add_argument(
        "--force",
        action="store_true",
        help="also replace an APPROVED explainer (it becomes pending)",
    )
    df = sub.add_parser("draft-faqs", help="draft PENDING FAQs and pitches from approved sources")
    df.add_argument("--program", help="program slug (default: every program, then the handbook)")
    df.add_argument(
        "--general", action="store_true", help="only the university-wide handbook rules"
    )
    df.add_argument("--n", type=int, default=6, help="FAQs per program (default 6)")
    df.add_argument(
        "--force", action="store_true", help="also replace existing why/compare pitches"
    )
    sub.add_parser(
        "prepare-explainers", help="generate and cache the speech of approved explainers"
    )
    sub.add_parser(
        "seed-interests", help="pending interest -> program rows from approved explainers"
    )
    sim = sub.add_parser(
        "simulate-run", help="store a SIMULATED robot run (trajectory + return-to-origin)"
    )
    sim.add_argument(
        "--seed", type=int, default=7, help="random seed (16 shows the breadcrumb fallback)"
    )
    sim.add_argument("--roam-seconds", type=float, default=45.0)
    ca = sub.add_parser("create-admin", help="create an admin login (or change its password)")
    ca.add_argument("username")
    ca.add_argument(
        "--password-stdin", action="store_true", help="read the password from standard input"
    )
    sub.add_parser(
        "check-curated", help="READ ONLY: list FAQs and pitches the review rules would refuse"
    )
    pc = sub.add_parser(
        "purge-conversations", help="delete conversations older than the retention period"
    )
    pc.add_argument(
        "--days", type=int, default=None, help="default: CONVERSATION_RETENTION_DAYS (30)"
    )
    pc.add_argument("--dry-run", action="store_true", help="only count, delete nothing")
    er = sub.add_parser("export-report", help="write CSVs and PNG charts for the graduation report")
    er.add_argument(
        "--out", default=None, help="output folder (default: /reports = reports/ on the laptop)"
    )
    so = sub.add_parser(
        "soak", help="SIMULATED mixed session against a preview backend (fake providers)"
    )
    so.add_argument("--minutes", type=float, default=30.0)
    so.add_argument("--url", default="ws://localhost:8000/ws/robot")
    so.add_argument("--admin-url", default="http://localhost:8000")
    so.add_argument("--out", default=None, help="folder for the JSON result (default: data/soak)")
    bot = sub.add_parser(
        "simulate-robot", help="connect a SIMULATED robot client (status + commands)"
    )
    bot.add_argument("--url", default="ws://localhost:8000/ws/robot")
    bot.add_argument("--seconds", type=float, default=120.0)
    bot.add_argument("--seed", type=int, default=7)
    bot.add_argument(
        "--token", default=None, help="ROBOT_TOKEN when the backend is on another machine"
    )
    lat = sub.add_parser(
        "latency", help="time retrieval, LLM and first TTS piece on golden questions"
    )
    lat.add_argument("--n", type=int, default=8, help="number of golden questions (default 8)")
    lat.add_argument(
        "--no-stream", action="store_true", help="wait for the whole LLM answer (old behaviour)"
    )
    lat.add_argument("--effort", default=None, help="reasoning effort override, e.g. low")
    lat.add_argument(
        "--dialogue",
        action="store_true",
        help="run the questions through the conversation manager (real audio path)",
    )
    lat.add_argument(
        "--no-stream-audio",
        action="store_true",
        help="with --dialogue: whole mp3 pieces instead of PCM chunks",
    )
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()
