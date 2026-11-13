"""Grounded question answering: retrieve approved passages -> LLM (JSON) -> checked answer.

Safety net: numbers in the answer that do not appear in the retrieved passages are reported
(`invented_numbers`); the evaluator counts them and the robot never presents unsourced figures
as facts (rule 1 in CLAUDE.md).

The answer can be streamed (`answer_events`): speech pieces are released while the model is
still writing, but each piece is checked for invented numbers first. A piece that fails is
never spoken; the safe "ask Admission & Registration" line replaces it.
"""

import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Session, Turn
from app.ingestion.lang import detect_lang
from app.services.providers import LLM, Embedder, LLMResult, ProviderError, stream_json
from app.services.rag import Passage, embed_missing, retrieve
from app.services.streaming import AnswerExtractor, SpeechPieces

INSTRUCTIONS = """\
You are the reception robot of the Faculty of Engineering & Design (كلية الهندسة والتصميم) at \
Middle East University (MEU) in Amman, Jordan. You talk with prospective students, parents and \
visitors.

LANGUAGE
- Reply in the language of the QUESTION. For Arabic, use friendly, polite Jordanian Arabic \
(عامية أردنية مهذبة), not formal Modern Standard Arabic. For English, use simple friendly English.

STYLE (your answer is spoken aloud by text-to-speech and shown as subtitles)
- Start with the direct answer in the first few words. Then at most 1–2 more short sentences.
- Keep the whole answer under about 40 words; if the visitor wants more, they will ask.
- No markdown, no bullet lists, no emojis, no links, no greetings like "أهلين" at the start.
- Warm and proud of the faculty, but do not add praise or claims that are not in the CONTEXT.

GROUNDING RULES (most important)
- Use ONLY facts written in the CONTEXT passages. Do not use any outside knowledge about MEU or \
other universities.
- Never invent, estimate or round numbers, fees, prices, dates, admission averages, scholarships, \
rankings or names. Only say a number if it is written in the CONTEXT.
- Do not add up, subtract or otherwise calculate numbers. Only state a total if that total is \
written in the CONTEXT (for example "requires 162 credit hours in total"); never present a part \
(such as major requirements or electives) as the total.
- The CONTEXT is often in English even when the question is Arabic: use it and answer in Arabic.
- If the CONTEXT answers the question, even partly, answer it and set "found" to true.
- If the CONTEXT does not contain the answer, say briefly that you do not have this information \
and suggest the Admission and Registration Department (دائرة القبول والتسجيل), and set "found" to false.
- Speak naturally: never mention "context", "passages", "documents" or "sources".
- You only cover the Faculty of Engineering & Design. For other faculties or universities, say so \
politely and set "found" to false.
- Claims about quality or accreditation must come from the CONTEXT.

OUTPUT: a JSON object exactly like
{"answer": "<what the robot says>", "found": true or false, "used": [numbers of the passages you relied on]}
"""

NOT_FOUND = {
    "ar": "للأسف ما عندي هاي المعلومة هلأ. بنصحك تراجع دائرة القبول والتسجيل بالجامعة، وهم بفيدوك أكثر.",
    "en": "Sorry, I don't have that information right now. Please check with the Admission and "
    "Registration Department, they will be happy to help.",
}
UNAVAILABLE = {
    "ar": "عذراً، الخدمة مش متاحة هلأ. جرّب كمان شوي.",
    "en": "Sorry, the service is not available right now. Please try again shortly.",
}

# Spoken instead of a sentence whose numbers are not in the retrieved passages.
NUMBER_SAFE = {
    "ar": "ما عندي الرقم الدقيق لهاي المعلومة، بنصحك تسأل دائرة القبول والتسجيل.",
    "en": "I don't have that exact information; please ask the Admission and Registration "
    "Department.",
}

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUMBER = re.compile(r"\d+(?:[.,/]\d+)*")


def numbers_in(text_: str) -> set[str]:
    return set(_NUMBER.findall(text_.translate(_ARABIC_DIGITS)))


def invented_numbers(answer: str, passages: list[str]) -> list[str]:
    """Numbers said in the answer that appear in none of the passages (digit-normalised)."""
    known: set[str] = set()
    for p in passages:
        known |= numbers_in(p)
    return sorted(n for n in numbers_in(answer) if n not in known)


@dataclass
class ChatSource:
    n: int
    chunk_id: int
    title: str | None
    url: str
    category: str | None
    program_id: int | None = None


@dataclass
class ChatResult:
    session_id: int | None
    answer: str
    lang: str
    found: bool
    sources: list[ChatSource]
    model: str | None
    timings: dict[str, int]
    invented_numbers: list[str] = field(default_factory=list)
    error: str | None = None
    passages: list[Passage] = field(default_factory=list)
    # What the robot actually says: the answer without the pieces that failed the number check
    # (replaced once by NUMBER_SAFE). Equal to `answer` when nothing was dropped.
    spoken: str = ""
    dropped: list[str] = field(default_factory=list)


@dataclass
class AnswerEvent:
    """`piece`: text that passed the number check and may be spoken now (`replacement` = the
    safe line standing in for a dropped piece). `result`: the final ChatResult (always last)."""

    kind: str
    text: str = ""
    replacement: bool = False
    result: ChatResult | None = None


def build_input(question: str, passages: list[Passage], history: list[tuple[str, str]]) -> str:
    parts = ["CONTEXT:"]
    for i, p in enumerate(passages, start=1):
        label = f"{p.source_title or p.source_url}" + (f"; {p.category}" if p.category else "")
        parts.append(f"[{i}] ({label})\n{p.text}")
    if history:
        parts.append("\nCONVERSATION SO FAR:")
        for q, a in history:
            parts.append(f"Visitor: {q}\nRobot: {a}")
    language = "English" if detect_lang(question) == "en" else "Arabic (Jordanian dialect)"
    parts.append(
        f"\nQUESTION: {question}\n\nANSWER LANGUAGE: {language}\n\n"
        "Reply with the JSON object only."
    )
    return "\n\n".join(parts)


async def _history(
    session: AsyncSession, session_id: int, n: int, speaker_label: int | None = None
) -> list[tuple[str, str]]:
    """Last n turns of the session; with several visitors, only this speaker's own turns."""
    query = select(Turn.user_text, Turn.answer_text).where(
        Turn.session_id == session_id, Turn.error.is_(None), Turn.intent == "question"
    )
    if speaker_label is not None:
        query = query.where(Turn.speaker_label == speaker_label)
    rows = (await session.execute(query.order_by(Turn.idx.desc()).limit(n))).all()
    return [(q or "", a or "") for q, a in reversed(rows)]


class _SpokenFilter:
    """Lets a speech piece through only if all its numbers are in the retrieved passages."""

    def __init__(self, passages: list[Passage], lang: str) -> None:
        self.texts = [p.text for p in passages]
        self.lang = lang
        self.spoken: list[str] = []
        self.dropped: list[str] = []
        self.approved = 0

    def check(self, piece: str) -> AnswerEvent | None:
        if not invented_numbers(piece, self.texts):
            self.spoken.append(piece)
            self.approved += 1
            return AnswerEvent("piece", piece)
        self.dropped.append(piece)
        if len(self.dropped) > 1:
            return None  # the safe line is said once
        self.spoken.append(NUMBER_SAFE[self.lang])
        return AnswerEvent("piece", NUMBER_SAFE[self.lang], replacement=True)


async def answer_events(
    session: AsyncSession,
    embedder: Embedder,
    llm: LLM,
    question: str,
    session_id: int | None = None,
    log: bool = True,
    speaker_label: int | None = None,
    stream: bool | None = None,
) -> AsyncIterator[AnswerEvent]:
    """Answer `question`; yields checked speech pieces as soon as they exist, then the result.

    `stream=None` follows the LLM_STREAMING setting. Without streaming (or with an LLM adapter
    that cannot stream) the pieces are simply yielded after the model has finished.
    """
    settings = get_settings()
    if stream is None:
        stream = settings.llm_streaming
    start = time.perf_counter()
    lang = detect_lang(question)
    timings: dict[str, int] = {}

    chat_session: Session | None = None
    if log:
        if session_id is not None:
            chat_session = await session.get(Session, session_id)
        if chat_session is None:
            chat_session = Session(lang_detected=lang)
            session.add(chat_session)
            await session.flush()

    result = ChatResult(
        session_id=chat_session.id if chat_session else session_id, answer="", lang=lang, found=False,
        sources=[], model=None, timings=timings,
    )  # fmt: skip
    spoken_filter = _SpokenFilter([], lang)
    try:
        await embed_missing(session, embedder)  # keeps newly approved/edited chunks searchable
        retrieval = await retrieve(session, embedder, question)
        timings["embed_ms"] = retrieval.embed_ms
        timings["retrieval_ms"] = retrieval.embed_ms + retrieval.search_ms
        result.passages = retrieval.passages
        spoken_filter = _SpokenFilter(retrieval.passages, lang)

        if not retrieval.passages:
            result.answer = NOT_FOUND[lang]
        else:
            history = (
                await _history(session, session_id, settings.chat_history_turns, speaker_label)
                if session_id is not None
                else []
            )
            extractor, pieces = AnswerExtractor(), SpeechPieces()
            llm_start = time.perf_counter()
            llm_result: LLMResult | None = None
            async for item in stream_json(
                llm, INSTRUCTIONS, build_input(question, retrieval.passages, history), stream
            ):
                if isinstance(item, LLMResult):
                    llm_result = item
                    continue
                ready = pieces.feed(extractor.feed(item))
                if extractor.done:  # the answer string is complete: no need to wait for the rest
                    ready += pieces.flush()
                for piece in ready:
                    event = spoken_filter.check(piece)
                    if event is not None:
                        if "llm_first_ms" not in timings:  # first speakable text
                            timings["llm_first_ms"] = int((time.perf_counter() - llm_start) * 1000)
                        yield event
            if llm_result is None:
                raise ProviderError("LLM stream ended without a result")
            timings["llm_ms"] = llm_result.ms
            result.model = llm_result.model
            data = llm_result.data
            result.answer = str(data.get("answer") or "").strip() or NOT_FOUND[lang]
            result.found = bool(data.get("found")) and bool(data.get("answer"))
            used = [u for u in data.get("used") or [] if isinstance(u, int)]
            for n in sorted(set(used)):
                if 1 <= n <= len(retrieval.passages):
                    p = retrieval.passages[n - 1]
                    result.sources.append(
                        ChatSource(n=n, chunk_id=p.chunk_id, title=p.source_title,
                                   url=p.source_url, category=p.category,
                                   program_id=p.program_id)  # fmt: skip
                    )
            result.invented_numbers = invented_numbers(
                result.answer, [p.text for p in retrieval.passages]
            )
            # Whatever was not spoken while streaming (everything, when not streaming).
            streamed = extractor.text.strip()
            if result.answer.startswith(streamed):
                rest = pieces.feed(result.answer[len(streamed) :]) + pieces.flush()
            else:  # the stream and the final JSON disagree: keep only what was already checked
                rest = pieces.flush() if pieces.emitted else []
            for piece in rest:
                event = spoken_filter.check(piece)
                if event is not None:
                    timings.setdefault("llm_first_ms", llm_result.ms)
                    yield event
    except ProviderError as e:
        result.error = str(e)
        result.answer = UNAVAILABLE[lang]
    if result.error is None and not spoken_filter.spoken:
        # nothing retrieved, or an answer the extractor could not follow: checked as a whole
        fallback = SpeechPieces()
        for piece in fallback.feed(result.answer) + fallback.flush():
            event = spoken_filter.check(piece)
            if event is not None:
                yield event
    result.spoken = " ".join(spoken_filter.spoken) if result.error is None else result.answer
    result.dropped = spoken_filter.dropped
    if spoken_filter.dropped and spoken_filter.approved == 0:
        result.found = False  # only the safe line is left
    timings["total_ms"] = int((time.perf_counter() - start) * 1000)

    if log and chat_session is not None:
        idx = await session.scalar(
            select(func.count()).select_from(Turn).where(Turn.session_id == chat_session.id)
        )
        session.add(
            Turn(
                session_id=chat_session.id,
                idx=idx or 0,
                user_text=question,
                user_lang=lang,
                intent="question",
                answer_text=result.answer,
                used_chunk_ids=[s.chunk_id for s in result.sources] or None,
                retrieval_ms=timings.get("retrieval_ms"),
                llm_ms=timings.get("llm_ms"),
                llm_first_ms=timings.get("llm_first_ms"),
                found=result.found if result.error is None else None,
                total_ms=timings["total_ms"],
                error=result.error,
            )
        )
        chat_session.ended_at = datetime.now(UTC)
        await session.commit()
    yield AnswerEvent("result", result=result)


async def answer_question(
    session: AsyncSession,
    embedder: Embedder,
    llm: LLM,
    question: str,
    session_id: int | None = None,
    log: bool = True,
    speaker_label: int | None = None,
    stream: bool | None = None,
) -> ChatResult:
    """The whole answer at once (text chat, evaluation): same pipeline as `answer_events`."""
    result: ChatResult | None = None
    async for event in answer_events(
        session, embedder, llm, question, session_id, log, speaker_label, stream
    ):
        if event.kind == "result":
            result = event.result
    assert result is not None  # answer_events always ends with the result
    return result
