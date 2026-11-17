"""Curated answers that come before retrieval (sprint 3 S3 and S4).

Three kinds of human-approved content can answer a visitor at once, with no language model:

- **FAQs** (`faqs`): question/answer pairs in Arabic and English. Used when the visitor's
  question is the same after normalisation, or nearly the same by meaning (embedding
  similarity at or above FAQ_MATCH_THRESHOLD, with guards so that a question about one program
  never gets the answer of another).
- **Pitches** (`pitches`): promotional talking points. `compare` answers "X ولا <a program of
  another faculty>؟" by talking only about X; `why` is attached to the retrieved passages for
  "why this program" questions. Never numbers outside the sources, never claims about other
  faculties or universities (CLAUDE.md rules 2 and 3).
- **Places and labs** (`places`, `labs`): directions typed in by the student. The robot never
  invents a location; with no approved match it says it does not know and refers to reception.

Everything is used only when its review status is `approved`. Drafts are made by one LLM call
per program from approved sources and stored as `pending` (`draft-faqs`).
"""

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Chunk, Faq, Lab, Media, Pitch, Place, Program, Source
from app.knowledge.store import usable_chunk
from app.db.types import to_pgvector
from app.services.chat_types import ChatSource
from app.services.explainers import (
    GENERAL_CATEGORIES,
    GUARANTEE_WORDS,
    PROGRAM_ALIASES,
    match_program,
    norm,
    program_context,
)
from app.services.numbers import numbers_in
from app.services.providers import LLM, Embedder, ProviderError

PITCH_KINDS = ("why", "compare")
PITCH_LABEL = {"ar": "نقطة ترويجية معتمدة", "en": "Approved promotional talking point"}
PLACES_LABEL = {"ar": "دليل الأماكن (مُدخل من الكلية)", "en": "Places list (entered by the faculty)"}
PLACE_UNKNOWN = {
    "ar": "ما بعرف وين بالضبط. اسأل موظف الاستقبال لو سمحت، وهو بدلّك.",
    "en": "I don't know where that is. Please ask at the reception desk.",
}


@dataclass
class Curated:
    """An approved answer that needs no language model."""

    kind: str  # faq / pitch / place
    text: str
    sources: list[ChatSource] = field(default_factory=list)
    faq_id: int | None = None
    pitch_ids: list[int] = field(default_factory=list)
    promotional: bool = False
    title: str | None = None  # card title (place name)
    lines: list[str] = field(default_factory=list)  # extra card lines (floor, contact numbers)
    media: dict[str, Any] | None = None
    similarity: float | None = None


# ====================================================================================
# FAQs
# ====================================================================================
def question_key(text_: str) -> str:
    """Normal form for exact matching: letters unified, punctuation and fillers removed."""
    words = [w for w in norm(text_).split() if w not in {"لو", "سمحت", "please", "يا", "روبوت"}]
    return " ".join(words)


async def _faq_source(session: AsyncSession, faq: Faq) -> list[ChatSource]:
    src = await session.get(Source, faq.source_id) if faq.source_id else None
    if src is None:
        return [ChatSource(n=1, chunk_id=0, title="FAQ (approved)", url=f"faq://{faq.id}", category="faq",
                           program_id=faq.program_id)]  # fmt: skip
    return [ChatSource(n=1, chunk_id=0, title=src.title, url=src.url, category="faq", program_id=faq.program_id)]


def _faq_answer(faq: Faq, lang: str) -> str | None:
    return (faq.answer_ar if lang == "ar" else faq.answer_en) or None


async def faq_exact(session: AsyncSession, question: str, lang: str) -> Curated | None:
    """An approved FAQ whose question is the same as the visitor's after normalisation."""
    key = question_key(question)
    if not key:
        return None
    rows = await session.scalars(
        select(Faq).where(Faq.review_status == "approved").order_by(Faq.priority.desc(), Faq.id)
    )
    asked_program = match_program(question)
    for faq in rows:
        if key in {question_key(faq.question_ar or ""), question_key(faq.question_en or "")} - {""}:
            program = await session.get(Program, faq.program_id) if faq.program_id else None
            if (program.slug if program else None) != asked_program:
                continue  # "how many hours is the plan?" does not say which program
            answer = _faq_answer(faq, lang)
            if answer:
                return Curated("faq", answer, await _faq_source(session, faq), faq_id=faq.id, similarity=1.0)
    return None


async def embed_missing_faqs(session: AsyncSession, embedder: Embedder) -> int:
    """Vectors for approved FAQ questions that have none (new, or edited since)."""
    rows = (
        await session.execute(
            text(
                "SELECT id, question_ar, question_en, embedding_ar IS NULL AS no_ar, "
                "embedding_en IS NULL AS no_en FROM faqs WHERE review_status = 'approved' AND "
                "((question_ar IS NOT NULL AND embedding_ar IS NULL) OR "
                "(question_en IS NOT NULL AND embedding_en IS NULL)) ORDER BY id"
            )
        )
    ).all()
    todo = [(r.id, col, q) for r in rows
            for col, q, missing in (("embedding_ar", r.question_ar, r.no_ar), ("embedding_en", r.question_en, r.no_en))
            if q and missing]  # fmt: skip
    if not todo:
        return 0
    vectors = await embedder.embed([q for _, _, q in todo])
    for (faq_id, column, _), vec in zip(todo, vectors, strict=True):
        await session.execute(
            text(f"UPDATE faqs SET {column} = CAST(:e AS vector), embedding_model = :m WHERE id = :id"),  # noqa: S608 - column is one of two constants
            {"e": to_pgvector(vec), "m": embedder.model, "id": faq_id},
        )
    await session.commit()
    return len(todo)


_FAQ_NEAR_SQL = text("""
    SELECT id, GREATEST(COALESCE(1 - (embedding_ar <=> CAST(:e AS vector)), -1),
                        COALESCE(1 - (embedding_en <=> CAST(:e AS vector)), -1)) AS sim
    FROM faqs
    WHERE review_status = 'approved' AND (embedding_ar IS NOT NULL OR embedding_en IS NOT NULL)
    ORDER BY sim DESC, priority DESC, id
    LIMIT 3
""")


async def faq_near(
    session: AsyncSession, question: str, lang: str, query_vec: list[float], threshold: float | None = None
) -> Curated | None:
    """An approved FAQ that asks nearly the same thing. Guards against confident mistakes:
    - a FAQ about one program only answers a question that names that program, and a general
      FAQ never answers a question that names a program;
    - the numbers in both questions must be the same."""
    threshold = get_settings().faq_match_threshold if threshold is None else threshold
    asked_program = match_program(question)
    asked_numbers = numbers_in(question)
    for row in (await session.execute(_FAQ_NEAR_SQL, {"e": to_pgvector(query_vec)})).all():
        if row.sim < threshold:
            break
        faq = await session.get(Faq, row.id)
        if faq is None:
            continue
        program = await session.get(Program, faq.program_id) if faq.program_id else None
        if (program.slug if program else None) != asked_program:
            continue
        if asked_numbers not in (numbers_in(faq.question_ar or ""), numbers_in(faq.question_en or "")):
            continue
        answer = _faq_answer(faq, lang)
        if answer:
            return Curated("faq", answer, await _faq_source(session, faq), faq_id=faq.id,
                           similarity=round(float(row.sim), 4))  # fmt: skip
    return None


# ====================================================================================
# Pitches: comparisons and "why this program"
# ====================================================================================
_COMPARE_WORDS = (
    "ولا", "الفرق بين", "شو الفرق", "فرق", "احسن", "افضل", "ايهما", "اي احسن", "مقارنه", "قارن",
    "vs", "versus", "difference between", "better", "compare", "comparison", "which is best",
)  # fmt: skip
_WHY_WORDS = (
    "ليش ادرس", "ليش اختار", "ليش بدي ادرس", "ليش لازم ادرس", "شو بميز", "شو مميزات", "مميزات", "ليش هذا التخصص",
    "why study", "why choose", "why should i study", "what is special", "advantages of", "what makes",
)  # fmt: skip
# Wording that judges somebody else or ranks the program (rule 3). The polite disclaimer "I cannot
# speak about other faculties" is allowed, so naming them is not flagged by itself: whether a pitch
# makes a claim about others is finally the reviewer's call.
OTHER_CLAIMS = (
    "افضل من", "احسن من", "اقوى من", "اسهل من", "اضعف من", "better than", "superior to", "easier than",
    "worse than", "unlike other", "بعكس", "الاولى في", "الافضل في", "the best in", "number one", "ranked",
)  # fmt: skip


def _has(text_: str, phrases: tuple[str, ...]) -> bool:
    padded = f" {norm(text_)} "
    return any(f" {norm(p)} " in padded for p in phrases)


def programs_in(text_: str) -> list[str]:
    """Every program of the faculty named in the text (slugs)."""
    normalized = norm(text_)
    found = []
    for slug, aliases in PROGRAM_ALIASES.items():
        if any(re.search(rf"(?:^| )(?:و|ب|ل|ف|لل|بال|وال|فال|ال)?{re.escape(a)}(?: |$)", normalized) for a in aliases):
            found.append(slug)
    return found


def is_comparison(question: str) -> bool:
    return _has(question, _COMPARE_WORDS) and bool(programs_in(question))


def is_why(question: str) -> bool:
    return _has(question, _WHY_WORDS) and bool(programs_in(question))


async def _approved_pitches(session: AsyncSession, slugs: list[str], kind: str) -> list[tuple[Pitch, Program]]:
    if not slugs:
        return []
    rows = await session.execute(
        select(Pitch, Program)
        .join(Program, Program.id == Pitch.program_id)
        .where(Pitch.topic == kind, Pitch.review_status == "approved", Program.slug.in_(slugs))
        .order_by(Pitch.id)
    )
    return [(p, prog) for p, prog in rows.all()]


def _pitch_text(pitch: Pitch, lang: str) -> str | None:
    return (pitch.text_ar if lang == "ar" else pitch.text_en) or None


async def comparison_answer(session: AsyncSession, question: str, lang: str) -> Curated | None:
    """"X ولا Y؟" where exactly one side is a program of this faculty: the approved `compare`
    pitch of X, which talks about X only. Two programs of the faculty are compared from the
    sources instead (retrieval), so this returns None for them."""
    if not is_comparison(question):
        return None
    slugs = programs_in(question)
    if len(slugs) != 1:
        return None
    for pitch, program in await _approved_pitches(session, slugs, "compare"):
        answer = _pitch_text(pitch, lang)
        if answer:
            return Curated(
                "pitch", answer,
                [ChatSource(n=1, chunk_id=0, title=PITCH_LABEL[lang], url=f"pitch://{pitch.id}",
                            category="pitch", program_id=program.id)],
                pitch_ids=[pitch.id], promotional=True,
            )  # fmt: skip
    return None


async def pitches_for_context(session: AsyncSession, question: str, lang: str) -> list[tuple[int, int, str]]:
    """Approved `why` pitches to add to the retrieved passages, for "why this program" questions
    and for comparisons between two programs of the faculty: (pitch id, program id, text)."""
    if not (is_why(question) or is_comparison(question)):
        return []
    out = []
    for pitch, program in await _approved_pitches(session, programs_in(question), "why"):
        body = _pitch_text(pitch, lang) or pitch.text_en or pitch.text_ar
        if body:
            out.append((pitch.id, program.id, body))
    return out


# ====================================================================================
# Places and labs
# ====================================================================================
_WHERE_WORDS = (
    "وين", "فين", "اين", "مكان", "كيف اوصل", "كيف بوصل", "كيف اروح", "كيف بروح", "دلني", "دلني على",
    "where", "wheres", "where s", "how do i get", "how can i get", "how do i find", "directions to", "location of",
)  # fmt: skip
_NOT_A_PLACE = (
    "بشتغل", "بشتغلوا", "بيشتغل", "بيشتغلوا", "يشتغل", "يشتغلوا", "اشتغل", "شغل", "وظيفه", "وظايف",
    "work", "works", "working", "job", "jobs", "employed", "career", "careers",
)  # fmt: skip


def is_location_question(question: str) -> bool:
    """"وين ...؟" about a place, not "وين بيشتغلوا الخريجين؟"."""
    words = set(norm(question).split())
    return _has(question, _WHERE_WORDS) and not (words & {norm(w) for w in _NOT_A_PLACE})


def _names(*values: str | None) -> list[str]:
    out = []
    for value in values:
        for part in re.split(r"[,،;؛\n]", value or ""):
            n = norm(part)
            if len(n) >= 3:
                out.append(n)
                if n.startswith("ال") and len(n) > 5:
                    out.append(n[2:])
    return out


def _best_name(question: str, names: list[str]) -> int:
    """Length of the longest name contained in the question as whole words (0 = no match)."""
    padded = f" {norm(question)} "
    stripped = " " + " ".join(w[2:] if w.startswith("ال") and len(w) > 4 else w for w in padded.split()) + " "
    return max((len(n) for n in names if f" {n} " in padded or f" {n} " in stripped), default=0)


async def _media(session: AsyncSession, media_id: int | None, lang: str) -> dict[str, Any] | None:
    media = await session.get(Media, media_id) if media_id else None
    if media is None or media.review_status != "approved":
        return None
    alt = (media.alt_ar if lang == "ar" else media.alt_en) or media.title or ""
    return {"kind": media.kind, "url": f"/media/{media.id}", "alt": alt}


def contact_lines(chunk_text: str, names: list[str]) -> list[str]:
    """The parts of an approved contacts passage that name this office and contain a number.
    They are copied as written; nothing is retyped or recomposed."""
    out = []
    for part in re.split(r"[؛;.\n]", chunk_text):
        part = part.strip(" :،,")
        if part and re.search(r"\d", part) and any(n in norm(part) for n in names):
            out.append(part)
    return out[:2]


async def place_answer(session: AsyncSession, question: str, lang: str) -> Curated | None:
    """Directions from an APPROVED place or lab whose name (or keyword) is in the question."""
    if not is_location_question(question):
        return None
    best: tuple[int, Any] | None = None
    for place in await session.scalars(select(Place).where(Place.review_status == "approved").order_by(Place.id)):
        score = _best_name(question, _names(place.name_ar, place.name_en, place.keywords))
        if score and (best is None or score > best[0]):
            best = (score, place)
    for lab in await session.scalars(select(Lab).where(Lab.review_status == "approved").order_by(Lab.id)):
        if not (lab.location_hint or "").strip():
            continue  # a lab without a typed location cannot answer "where"
        score = _best_name(question, _names(lab.name_ar, lab.name_en, lab.keywords))
        if score and (best is None or score > best[0]):
            best = (score, lab)
    if best is None:
        return None
    item = best[1]
    name = (item.name_ar if lang == "ar" else item.name_en) or item.name_ar or item.name_en
    sources = [ChatSource(n=1, chunk_id=0, title=PLACES_LABEL[lang], url=f"place://{type(item).__name__.lower()}/{item.id}",
                          category="place", program_id=getattr(item, "program_id", None))]  # fmt: skip
    lines: list[str] = []
    if isinstance(item, Place):
        directions = (item.directions_ar if lang == "ar" else item.directions_en) or item.directions_ar or item.directions_en
        where = [p for p in (item.building, (("الطابق " if lang == "ar" else "floor ") + item.floor) if item.floor else None) if p]
        if where:
            lines.append("، ".join(where) if lang == "ar" else ", ".join(where))
        body = directions or ("، ".join(where) if lang == "ar" else ", ".join(where))
        if not body:
            return None  # the name alone is not a direction
        chunk = await session.get(Chunk, item.contact_chunk_id) if item.contact_chunk_id else None
        src = await session.get(Source, chunk.source_id) if chunk is not None else None
        usable = chunk is not None and chunk.review_status == "approved"
        if usable and src is not None and src.review_status == "approved":
            found = contact_lines(chunk.text, _names(item.name_ar, item.name_en, item.keywords, item.related_office))
            if found:
                lines += found
                sources.append(ChatSource(n=2, chunk_id=chunk.id, title=src.title if src else None,
                                          url=src.url if src else "", category=chunk.category))  # fmt: skip
    else:
        body = item.location_hint.strip()
    answer = f"{name}: {body}" if not body.startswith(name) else body
    return Curated("place", answer, sources, title=name, lines=lines, media=await _media(session, item.media_id, lang))


# ====================================================================================
# Review checks
# ====================================================================================
async def known_numbers(session: AsyncSession, program_id: int | None) -> set[str]:
    """Numbers written in the approved content a curated text may rely on: the program's own
    passages plus the general (handbook) ones; for a general text, every general passage."""
    query = select(Chunk.text).where(usable_chunk())
    if program_id is not None:
        query = query.where((Chunk.program_id == program_id) | Chunk.program_id.is_(None))
    else:
        query = query.where(Chunk.program_id.is_(None))
    known: set[str] = set()
    for body in await session.scalars(query):
        known |= numbers_in(body)
    return known


def claim_issues(text_: str) -> list[str]:
    issues = [f"promises an outcome ('{w}')" for w in GUARANTEE_WORDS if _has(text_, (w,))]
    issues += [f"speaks about others or ranks itself ('{w}')" for w in OTHER_CLAIMS if _has(text_, (w,))]
    return issues


async def faq_issues(session: AsyncSession, faq: Faq) -> list[str]:
    issues = []
    if not (faq.question_ar or faq.question_en):
        issues.append("no question")
    if not (faq.answer_ar or faq.answer_en):
        issues.append("no answer")
    for lang in ("ar", "en"):
        if bool(getattr(faq, f"question_{lang}")) != bool(getattr(faq, f"answer_{lang}")):
            issues.append(f"the {lang} question and answer must both be filled, or both be empty")
    program = await session.get(Program, faq.program_id) if faq.program_id else None
    for lang in ("ar", "en"):
        question = getattr(faq, f"question_{lang}")
        if not question:
            continue
        named = match_program(question)
        if program is not None and named != program.slug:
            issues.append(
                f"the {lang} question does not name the program "
                f"({program.name_ar if lang == 'ar' else program.name_en}): "
                "the robot could not tell which program the visitor means"
            )
        if program is None and named is not None:
            issues.append(f"the {lang} question names a program, but the FAQ is marked general: choose the program")
    known = await known_numbers(session, faq.program_id)
    answers = f"{faq.answer_ar or ''} \n {faq.answer_en or ''}"
    issues += [f"{n} is not in the approved sources" for n in sorted(numbers_in(answers) - known)]
    issues += [f"the answer {i}" for i in claim_issues(answers) if i.startswith("promises")]
    return issues


async def pitch_issues(session: AsyncSession, pitch: Pitch) -> list[str]:
    issues = []
    if pitch.topic not in PITCH_KINDS:
        issues.append(f"unknown kind '{pitch.topic}'")
    if pitch.program_id is None:
        issues.append("no program is linked")
    body = f"{pitch.text_ar or ''} \n {pitch.text_en or ''}"
    if not body.strip():
        issues.append("no text")
    program = await session.get(Program, pitch.program_id) if pitch.program_id else None
    known: set[str] = set()
    if program is not None:
        for t in (await program_context(session, program)).texts:
            known |= numbers_in(t)
    issues += [f"{n} is not in the program's approved sources" for n in sorted(numbers_in(body) - known)]
    return issues + claim_issues(body)


# ====================================================================================
# Drafting (one LLM call per program, plus one for the handbook rules)
# ====================================================================================
DRAFT_FAQ_INSTRUCTIONS = """\
You prepare review drafts for the reception robot of the Faculty of Engineering & Design at \
Middle East University (MEU), Jordan. A human will check every line before the robot may use it.

FACTS (most important)
- Use ONLY what is written in the CONTEXT passages. No outside knowledge.
- Every number must be written in the CONTEXT, as digits, exactly as there. Do not calculate, \
estimate or round. If the CONTEXT does not answer a question, do not write that question.
- Never mention fees, admission averages, salaries, rankings or job guarantees unless the exact \
fact is in the CONTEXT. Never promise an outcome.

FAQS: the questions a prospective student or parent most often asks, each with a short spoken \
answer (1-2 sentences, at most 35 words, the direct answer first). Write every question and \
answer in BOTH languages: friendly Jordanian Arabic (عامية أردنية مهذبة) and simple English. \
The two versions must say the same thing. When the SUBJECT is a program, EVERY question must \
name that program (for example "... بهندسة العمارة؟" / "... in Architectural Engineering?"), \
because the robot matches questions without knowing the context. "passage" is the number of the CONTEXT passage that \
contains the answer.

PITCHES (only when asked for below): promotional talking points about THIS program only.
- "why": 2-3 sentences on why a student may like this program, built only from the CONTEXT.
- "compare": what the robot says when a visitor asks to compare this program with a program of \
ANOTHER faculty or university (for example Software Engineering). It must say politely that the \
robot can only speak about the Faculty of Engineering & Design, then give 1-2 sourced strengths \
of THIS program. It must NOT describe, judge or rank the other program, faculty or university, \
and must not use words like "better than".
- No numbers in pitches unless they are written in the CONTEXT.

OUTPUT: a JSON object exactly like
{"faqs": [{"question_ar": "...", "question_en": "...", "answer_ar": "...", "answer_en": "...", \
"passage": 1}], "pitches": {"why_ar": "...", "why_en": "...", "compare_ar": "...", "compare_en": "..."}}
Leave "pitches" as {} when no pitches are asked for.
"""


@dataclass
class DraftReport:
    target: str
    outcome: str  # drafted / no-sources / failed
    faqs_created: int = 0
    faqs_skipped: int = 0  # the same question already exists
    pitches_created: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    error: str | None = None


async def _general_passages(session: AsyncSession) -> list[tuple[int, str, str]]:
    """Handbook rules: approved passages that belong to no program and have a category (the old
    crawled faculty pages have none and are left out)."""
    rows = await session.execute(
        select(Chunk.id, Chunk.text, Chunk.category, Source.title, Chunk.source_id)
        .join(Source, Source.id == Chunk.source_id)
        .where(usable_chunk(), Chunk.program_id.is_(None), Chunk.category.is_not(None))
        .order_by(Chunk.source_id, Chunk.position, Chunk.id)
    )
    return [(r.source_id, f"{r.title}; {r.category}", r.text) for r in rows.all()]


async def _program_passages(session: AsyncSession, program: Program) -> list[tuple[int, str, str]]:
    rows = await session.execute(
        select(Chunk.text, Chunk.category, Source.title, Chunk.source_id)
        .join(Source, Source.id == Chunk.source_id)
        .where(
            usable_chunk(),
            (Chunk.program_id == program.id)
            | (Chunk.program_id.is_(None) & Chunk.category.in_(GENERAL_CATEGORIES)),
        )
        .order_by(Chunk.program_id.is_(None), Chunk.category == "courses", Chunk.source_id, Chunk.position, Chunk.id)
    )
    return [(r.source_id, f"{r.title}" + (f"; {r.category}" if r.category else ""), r.text) for r in rows.all()]


async def draft_curated(
    session: AsyncSession, llm: LLM, program: Program | None, n_faqs: int = 6, force: bool = False
) -> DraftReport:
    """One LLM call: FAQ drafts (and, for a program, `why` and `compare` pitch drafts), all stored
    as PENDING. `program=None` drafts FAQs about the university-wide handbook rules. Existing
    questions are not duplicated, and an existing pitch is kept unless `force`."""
    target = program.slug if program else "handbook"
    passages = await (_program_passages(session, program) if program else _general_passages(session))
    if not passages:
        return DraftReport(target, "no-sources")
    existing_pitches = set()
    if program is not None:
        existing_pitches = set(
            (await session.scalars(select(Pitch.topic).where(Pitch.program_id == program.id, Pitch.topic.in_(PITCH_KINDS)))).all()
        )
    want_pitches = program is not None and (force or existing_pitches != set(PITCH_KINDS))
    parts = [
        f"SUBJECT: {program.name_en} ({program.name_ar})" if program else
        "SUBJECT: university-wide bachelor rules and student services (Student Handbook) that apply to the faculty's students",
        f"WRITE: {n_faqs} FAQs" + (" and the two pitches (why, compare)" if want_pitches else "; no pitches"),
        "CONTEXT:",
    ]  # fmt: skip
    parts += [f"[{i}] ({label})\n{body}" for i, (_, label, body) in enumerate(passages, start=1)]
    parts.append("Reply with the JSON object only.")
    try:
        result = await llm.complete_json(DRAFT_FAQ_INSTRUCTIONS, "\n\n".join(parts))
    except ProviderError as e:
        return DraftReport(target, "failed", error=str(e))

    report = DraftReport(target, "drafted")
    known: set[str] = set()
    for _, _, body in passages:
        known |= numbers_in(body)
    have = {question_key(q) for row in (await session.execute(select(Faq.question_ar, Faq.question_en))).all() for q in row if q}
    for raw in (result.data.get("faqs") or [])[: n_faqs + 2]:
        if not isinstance(raw, dict):
            continue
        q_ar, q_en = _clean(raw.get("question_ar")), _clean(raw.get("question_en"))
        a_ar, a_en = _clean(raw.get("answer_ar")), _clean(raw.get("answer_en"))
        if not ((q_ar and a_ar) or (q_en and a_en)):
            continue
        if {question_key(q_ar), question_key(q_en)} & have:
            report.faqs_skipped += 1
            continue
        n = raw.get("passage")
        source_id = passages[n - 1][0] if isinstance(n, int) and 1 <= n <= len(passages) else None
        session.add(Faq(program_id=program.id if program else None, question_ar=q_ar or None, question_en=q_en or None,
                        answer_ar=a_ar or None, answer_en=a_en or None, source_id=source_id, review_status="pending"))  # fmt: skip
        have |= {question_key(q_ar), question_key(q_en)}
        report.faqs_created += 1
        for number in sorted(numbers_in(f"{a_ar} {a_en}") - known):
            report.issues.append(f"FAQ '{(q_en or q_ar)[:50]}': {number} is not in the sources")
    pitches = result.data.get("pitches") if isinstance(result.data.get("pitches"), dict) else {}
    if want_pitches and program is not None:
        for kind in PITCH_KINDS:
            t_ar, t_en = _clean(pitches.get(f"{kind}_ar")), _clean(pitches.get(f"{kind}_en"))
            if not (t_ar or t_en) or (kind in existing_pitches and not force):
                continue
            if kind in existing_pitches:  # force: the old row is replaced and reviewed again
                for old in await session.scalars(select(Pitch).where(Pitch.program_id == program.id, Pitch.topic == kind)):
                    await session.delete(old)
            session.add(Pitch(program_id=program.id, topic=kind, text_ar=t_ar or None, text_en=t_en or None,
                              is_promotional=True, review_status="pending"))  # fmt: skip
            report.pitches_created.append(kind)
            for number in sorted(numbers_in(f"{t_ar} {t_en}") - known):
                report.issues.append(f"pitch '{kind}': {number} is not in the sources")
            report.issues += [f"pitch '{kind}': {i}" for i in claim_issues(f"{t_ar} {t_en}")]
    await session.commit()
    return report


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
