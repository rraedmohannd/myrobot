"""Latency measurement of the answer pipeline: retrieval -> LLM -> first TTS piece (demo sprint M2).

Run on the laptop (uses the real providers, so it costs a little):
    docker compose exec backend python -m app.ingestion.cli latency [--n 8] [--no-stream]

It measures what a visitor waits for after the speech-to-text step: the time until the first
audio of the ANSWER exists. Speech-to-text and the cached filler are not part of it (the /voice
page and the turns table report those per real conversation).
"""

import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.chat import answer_events
from app.services.providers import LLM, Embedder, ProviderError

RESULTS_DIR = Path(__file__).resolve().parents[2] / "data" / "latency"
STAGES = (
    "retrieval_ms", "llm_first_ms", "llm_ms", "tts_first_ms", "first_audio_ms",
    "tts_stream_mp3_ms", "tts_stream_pcm_ms",
)  # fmt: skip
STAGE_LABELS = {
    "retrieval_ms": "retrieval (embedding + search)",
    "llm_first_ms": "LLM until first speakable piece",
    "llm_ms": "LLM whole answer",
    "tts_first_ms": "TTS of the first piece",
    "first_audio_ms": "question text -> first answer audio",
    "tts_stream_mp3_ms": "TTS first piece, streamed mp3: first chunk",
    "tts_stream_pcm_ms": "TTS first piece, streamed pcm: first chunk",
}


class Synthesizer(Protocol):
    async def synthesize(self, text: str) -> bytes: ...


@dataclass
class LatencyRow:
    id: str
    question: str
    first_piece: str = ""
    pieces: int = 0
    found: bool = False
    retrieval_ms: int | None = None
    llm_first_ms: int | None = None
    llm_ms: int | None = None
    tts_first_ms: int | None = None
    first_audio_ms: int | None = None
    # Streaming trial (sprint 2): the same first piece requested with chunked transfer, in
    # parallel with the normal request; ms from the TTS request to its first audio chunk.
    tts_stream_mp3_ms: int | None = None
    tts_stream_pcm_ms: int | None = None
    error: str | None = None


def percentile(values: list[int], q: float) -> float | None:
    """Nearest-rank percentile (same rule as the golden-set report)."""
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))])


def summarize(rows: list[LatencyRow]) -> dict[str, dict[str, float | int | None]]:
    """Median and p90 of each stage over the questions that produced audio."""
    ok = [r for r in rows if r.error is None]
    out: dict[str, dict[str, float | int | None]] = {}
    for stage in STAGES:
        values = [v for r in ok if (v := getattr(r, stage)) is not None]
        out[stage] = {
            "median": statistics.median(values) if values else None,
            "p90": percentile(values, 0.9),
            "n": len(values),
        }
    return out


async def measure_one(
    session: AsyncSession, embedder: Embedder, llm: LLM, tts: Synthesizer,
    item: dict[str, Any], stream: bool,
) -> LatencyRow:  # fmt: skip
    row = LatencyRow(id=item["id"], question=item["question"])
    start = time.perf_counter()
    first_audio: asyncio.Task[tuple[int, int]] | None = None

    async def synthesize_first(piece: str) -> tuple[int, int]:
        t0 = time.perf_counter()
        await tts.synthesize(piece)
        done = time.perf_counter()
        return int((done - t0) * 1000), int((done - start) * 1000)

    async def first_chunk(piece: str, response_format: str) -> int | None:
        t0 = time.perf_counter()
        try:
            async for _ in tts.stream(piece, response_format=response_format):  # type: ignore[attr-defined]
                return int((time.perf_counter() - t0) * 1000)
        except ProviderError:
            return None
        return None

    streams: list[asyncio.Task[int | None]] = []
    async for event in answer_events(session, embedder, llm, item["question"], log=False, stream=stream):
        if event.kind == "piece":
            row.pieces += 1
            if first_audio is None:
                row.first_piece = event.text
                # as a task: the LLM stream is not held back while the audio is being made
                first_audio = asyncio.create_task(synthesize_first(event.text))
                if hasattr(tts, "stream"):
                    streams = [asyncio.create_task(first_chunk(event.text, f)) for f in ("mp3", "pcm")]
        elif event.kind == "result" and event.result is not None:
            r = event.result
            row.found, row.error = r.found, r.error
            row.retrieval_ms = r.timings.get("retrieval_ms")
            row.llm_first_ms = r.timings.get("llm_first_ms")
            row.llm_ms = r.timings.get("llm_ms")
    if first_audio is not None:
        try:
            row.tts_first_ms, row.first_audio_ms = await first_audio
        except ProviderError as e:
            row.error = row.error or str(e)
    if streams:
        row.tts_stream_mp3_ms, row.tts_stream_pcm_ms = [await t for t in streams]
    return row


async def measure(
    session: AsyncSession, embedder: Embedder, llm: LLM, tts: Synthesizer,
    questions: list[dict[str, Any]], stream: bool,
) -> list[LatencyRow]:  # fmt: skip
    return [await measure_one(session, embedder, llm, tts, q, stream) for q in questions]


async def measure_dialogue(
    sessionmaker: Any, embedder: Embedder, llm: LLM, stt: Any, tts: Any,
    questions: list[dict[str, Any]], audio_stream: bool = True,
) -> tuple[list[LatencyRow], list[int]]:  # fmt: skip
    """The same questions through the real conversation manager, as typed questions from a
    client that accepts streamed audio: `first_audio_ms` is the time from the question text to
    the first audio of the ANSWER being sent (a PCM chunk when streaming, else the first mp3
    piece). The cached filler is not counted. Returns the rows and the ids of the sessions it
    created, so the caller can delete them (they are measurements, not visitors)."""
    from app.services.dialogue import DialogueManager

    rows: list[LatencyRow] = []
    session_ids: list[int] = []
    for item in questions:
        sent: list[dict[str, Any]] = []

        async def send(msg: dict[str, Any], sent: list[dict[str, Any]] = sent) -> None:
            sent.append(msg)

        dm = DialogueManager(send, sessionmaker, embedder, llm, stt, tts)
        await dm.handle({"type": "hello", "audio_stream": ["pcm16"] if audio_stream else []})
        await dm.handle({"type": "text.input", "text": item["question"]})
        if dm.session_id is not None:
            session_ids.append(dm.session_id)
        row = LatencyRow(id=item["id"], question=item["question"])
        timings = next((m for m in sent if m["type"] == "turn.timings"), {})
        answer = next((m for m in sent if m["type"] == "answer.text"), {})
        spoken = str(answer.get("text") or "")
        pieces = [  # the answer's own pieces, streamed or whole (not the filler or the follow-up)
            m for m in sent
            if m["type"] in ("answer.audio.start", "answer.audio") and m.get("text") and m["text"] in spoken
        ]
        row.pieces = len(pieces)
        row.first_piece = pieces[0]["text"] if pieces else ""
        row.found = bool(answer.get("found"))
        row.retrieval_ms, row.llm_ms = timings.get("retrieval_ms"), timings.get("llm_ms")
        row.llm_first_ms, row.first_audio_ms = timings.get("llm_first_ms"), timings.get("tts_ms")
        errors = [m["message"] for m in sent if m["type"] == "error"]
        row.error = errors[0] if errors else None
        rows.append(row)
        await dm.handle({"type": "session.end", "reason": "user_done"})
    return rows, session_ids


def format_report(rows: list[LatencyRow], label: str) -> str:
    summary = summarize(rows)
    lines = [f"Latency — {label} — {len(rows)} question(s)", ""]
    lines.append(f"{'id':24s} {'retr':>6} {'llm1':>6} {'llm':>6} {'tts1':>6} {'audio':>6} {'s.mp3':>6} {'s.pcm':>6}  first piece")
    for r in rows:
        cells = " ".join(f"{(getattr(r, s) if getattr(r, s) is not None else '-'):>6}" for s in STAGES)
        lines.append(f"{r.id:24s} {cells}  {r.error or r.first_piece[:50]}")
    lines.append("")
    lines.append(f"{'stage':44s} {'median':>8} {'p90':>8}   (ms)")
    for stage in STAGES:
        s = summary[stage]
        if s["median"] is None:
            lines.append(f"{STAGE_LABELS[stage]:44s} {'-':>8} {'-':>8}")
        else:
            lines.append(f"{STAGE_LABELS[stage]:44s} {s['median']:8.0f} {s['p90']:8.0f}")
    return "\n".join(lines)


def write_results(rows: list[LatencyRow], meta: dict[str, Any], out_dir: Path = RESULTS_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    data = {**meta, "summary": summarize(rows), "rows": [asdict(r) for r in rows]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
