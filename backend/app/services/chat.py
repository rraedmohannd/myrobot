"""Grounded question answering: retrieve approved passages -> LLM (JSON) -> checked answer.

Safety net: numbers in the answer that do not appear in the retrieved passages are reported
(`invented_numbers`); the evaluator counts them and the robot never presents unsourced figures
as facts (rule 1 in CLAUDE.md).
"""

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Session, Turn
from app.ingestion.lang import detect_lang
from app.services.providers import LLM, Embedder, ProviderError
from app.services.rag import Passage, embed_missing, retrieve

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


def build_input(question: str, passages: list[Passage], history: list[tuple[str, str]]) -> str:
    parts = ["CONTEXT:"]
    for i, p in enumerate(passages, start=1):
        label = f"{p.source_title or p.source_url}" + (f"; {p.category}" if p.category else "")
        parts.append(f"[{i}] ({label})\n{p.text}")
    if history:
        parts.append("\nCONVERSATION SO FAR:")
        for q, a in history:
            parts.append(f"Visitor: {q}\nRobot: {a}")
    parts.append(f"\nQUESTION: {question}\n\nReply with the JSON object only.")
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


async def answer_question(
    session: AsyncSession,
    embedder: Embedder,
    llm: LLM,
    question: str,
    session_id: int | None = None,
    log: bool = True,
    speaker_label: int | None = None,
) -> ChatResult:
    settings = get_settings()
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
    try:
        await embed_missing(session, embedder)  # keeps newly approved/edited chunks searchable
        retrieval = await retrieve(session, embedder, question)
        timings["embed_ms"] = retrieval.embed_ms
        timings["retrieval_ms"] = retrieval.embed_ms + retrieval.search_ms
        result.passages = retrieval.passages

        if not retrieval.passages:
            result.answer = NOT_FOUND[lang]
        else:
            history = (
                await _history(session, session_id, settings.chat_history_turns, speaker_label)
                if session_id is not None
                else []
            )
            llm_result = await llm.complete_json(
                INSTRUCTIONS, build_input(question, retrieval.passages, history)
            )
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
                                   url=p.source_url, category=p.category)  # fmt: skip
                    )
            result.invented_numbers = invented_numbers(
                result.answer, [p.text for p in retrieval.passages]
            )
    except ProviderError as e:
        result.error = str(e)
        result.answer = UNAVAILABLE[lang]
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
                total_ms=timings["total_ms"],
                error=result.error,
            )
        )
        chat_session.ended_at = datetime.now(UTC)
        await session.commit()
    return result
