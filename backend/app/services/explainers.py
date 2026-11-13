"""Program explainers (sprint 2): "the robot presents the program".

A 60-90 second presentation per program and language, spoken in segments; each segment has a
scene on the robot screen (docs/PROTOCOL.md `explainer.segment`). The rules are the rules of
pitches (CLAUDE.md rule 2): promotional in tone, factual in content.

- Drafts are written by the LLM from the program's APPROVED chunks only and stored as `pending`.
- Every number in the spoken text and in the scenes must appear in those approved sources
  (the same invented-numbers check as the answers); course names shown on screen must appear
  there too. A draft with such an issue cannot be approved.
- Only a human approves (admin page). The robot only ever plays approved explainers.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Explainer, ExplainerSegment, Program, Source
from app.services.chat import numbers_in
from app.services.media import approved_media_for_program
from app.services.providers import LLM, ProviderError
from app.services.speech import CachedTTS

SCENE_TYPES = ("title", "keypoints", "stat", "timeline", "courses", "diagram", "media", "qr")
MIN_SEGMENTS, MAX_SEGMENTS = 3, 8
PAUSE_AFTER = 3  # "بدك أكمل ولا عندك سؤال؟" after this many segments
# University-wide handbook passages that also describe every program of the faculty.
GENERAL_CATEGORIES = ("study_system", "graduation")
# The robot never promises an outcome (sprint 2 S3; also applied to explainers).
GUARANTEE_WORDS = ("guarantee", "guaranteed", "بضمن", "بنضمن", "مضمون", "مضمونة", "ضمان", "أكيد بتشتغل")

_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي", "ـ": None})


def norm(text: str) -> str:
    """Lower-case, Arabic letter forms unified, punctuation removed (for matching only)."""
    text = re.sub(r"[ً-ْٰ]", "", text or "")
    text = text.translate(_ALEF).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------- which program is the visitor talking about? ----------------
# Spoken names per program slug (normalised). The official names come from the database; these
# are only the ways people say them.
PROGRAM_ALIASES: dict[str, tuple[str, ...]] = {
    "intelligent-systems-engineering": (
        "الانظمه الذكيه", "انظمه ذكيه", "الانظمه الذكيه", "انظمه الذكيه", "intelligent systems",
        "smart systems", "intelligent system",
    ),
    "renewable-energy-engineering": (
        "الطاقه المتجدده", "طاقه متجدده", "طاقه المتجدده", "renewable energy", "renewable",
    ),
    "architectural-engineering": (
        "هندسه العماره", "العماره", "عماره", "المعماريه", "معماري", "architectural engineering",
        "architecture", "architectural",
    ),
    "interior-design": ("التصميم الداخلي", "تصميم داخلي", "تصميم الداخلي", "الديكور", "ديكور", "interior design", "interior"),
    "graphic-design": (
        "التصميم الجرافيكي", "تصميم جرافيكي", "الجرافيكي", "جرافيك", "الغرافيكي", "غرافيك",
        "جرافيكس", "graphic design", "graphics", "graphic",
    ),
    "biomedical-engineering": (
        "الطبيه الحيويه", "طبيه حيويه", "الهندسه الطبيه", "هندسه طبيه", "biomedical engineering",
        "biomedical", "bio medical",
    ),
}  # fmt: skip

# "Tell me about X": the request is for a presentation, not for one fact.
_TRIGGERS = (
    "احكيلي عن", "احكي لي عن", "احكيلنا عن", "احكي عن", "خبرني عن", "خبريني عن", "عرفني على",
    "عرفني عن", "عرفني ب", "اشرحلي عن", "اشرحلي", "اشرح لي", "حدثني عن", "قلي عن", "قولي عن",
    "بدي اعرف عن", "بدي اتعرف على", "حابب اعرف عن", "حابه اعرف عن", "شو هو", "شو هي", "شو يعني",
    "ما هو", "ما هي", "ليش ادرس", "ليش اختار", "ليش بدي ادرس", "ليش لازم ادرس", "عرض عن", "نبذه عن",
    "tell me about", "tell me more about", "tell us about", "what is", "what s", "whats",
    "why study", "why should i study", "why choose", "explain", "present", "introduce",
    "i want to know about", "talk about", "describe",
)  # fmt: skip
# Words that carry no question of their own once the trigger and the program name are removed.
_FILLER_WORDS = {
    "تخصص", "التخصص", "هندسه", "الهندسه", "برنامج", "البرنامج", "قسم", "القسم", "عن", "على", "في",
    "ب", "بالجامعه", "الجامعه", "عندكم", "عندكو", "لو", "سمحت", "ممكن", "شوي", "اكثر", "كمان", "يعني",
    "بالضبط", "هاد", "هذا", "هاي", "هذه", "و", "يا", "روبوت", "مرحبا", "بكالوريوس", "دراسه", "ادرس",
    "the", "a", "an", "program", "programme", "major", "engineering", "department", "degree",
    "please", "me", "more", "about", "of", "in", "at", "meu", "your", "to", "study", "bachelor",
}  # fmt: skip


_PREFIX = r"(?:و|ب|ل|ف|لل|بال|وال|فال|ال)?"


def match_program(text: str) -> str | None:
    """The program slug named in `text` (longest alias wins), or None."""
    normalized = norm(text)
    best: tuple[int, str] | None = None
    for slug, aliases in PROGRAM_ALIASES.items():
        for alias in aliases:
            # Arabic attaches short prefixes to the word: "بالعمارة", "للعمارة", "والعمارة"
            if re.search(rf"(?:^| ){_PREFIX}{re.escape(alias)}(?: |$)", normalized):
                if best is None or len(alias) > best[0]:
                    best = (len(alias), slug)
    return best[1] if best else None


def explainer_request(text: str) -> str | None:
    """Slug of the program when `text` asks for a presentation of it ("احكيلي عن تخصص X",
    "شو هو X", "ليش أدرس X", "tell me about X"), else None. A specific question that only
    mentions a program ("شو هي شروط القبول بهندسة العمارة") is NOT a request: after removing
    the trigger phrase and the program name, almost nothing may be left."""
    normalized = norm(text)
    slug = match_program(normalized)
    if slug is None:
        return None
    rest = f" {normalized} "
    trigger = next((t for t in sorted(_TRIGGERS, key=len, reverse=True) if f" {t} " in rest or rest.startswith(f" {t}")), None)
    if trigger is None:
        return None
    rest = rest.replace(f" {trigger}", " ", 1)
    for alias in sorted(PROGRAM_ALIASES[slug], key=len, reverse=True):
        rest = rest.replace(f" {alias} ", " ")
    left = [w for w in rest.split() if w not in _FILLER_WORDS and w.lstrip("وبال") not in _FILLER_WORDS]
    return slug if len(left) <= 1 else None


# ---------------- scenes ----------------
def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _texts(value: Any, limit: int, most: int) -> list[str]:
    items = value if isinstance(value, list) else []
    return [t for t in (_text(v, limit) for v in items) if t][:most]


def clean_scene(raw: Any) -> dict[str, Any]:
    """Keep only the known fields of a scene, with bounded sizes. Unknown types become `title`.
    URLs and media are never taken from here: the backend resolves them at playback time."""
    raw = raw if isinstance(raw, dict) else {}
    kind = raw.get("type") if raw.get("type") in SCENE_TYPES else "title"
    scene: dict[str, Any] = {"type": kind}
    if kind == "title":
        scene["tagline"] = _text(raw.get("tagline"), 90)
    elif kind == "keypoints":
        scene["title"] = _text(raw.get("title"), 60)
        scene["points"] = _texts(raw.get("points"), 70, 4)
    elif kind == "stat":
        scene["value"] = _text(raw.get("value"), 12)
        scene["unit"] = _text(raw.get("unit"), 40)
        scene["caption"] = _text(raw.get("caption"), 90)
    elif kind == "timeline":
        scene["title"] = _text(raw.get("title"), 60)
        steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
        scene["steps"] = [
            {"label": _text(s.get("label"), 34), "items": _texts(s.get("items"), 44, 3)}
            for s in steps[:5]
            if isinstance(s, dict) and _text(s.get("label"), 34)
        ]
    elif kind == "courses":
        scene["title"] = _text(raw.get("title"), 60)
        scene["courses"] = _texts(raw.get("courses"), 56, 8)
    elif kind == "diagram":
        scene["title"] = _text(raw.get("title"), 60)
        scene["nodes"] = _texts(raw.get("nodes"), 28, 4)
    else:  # media, qr
        scene["caption"] = _text(raw.get("caption"), 90)
    return {k: v for k, v in scene.items() if v not in ("", [], None)}


def scene_strings(scene: dict[str, Any]) -> list[str]:
    """Every text a scene shows (for the number check)."""
    out: list[str] = []
    for key, value in scene.items():
        if key == "type":
            continue
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    out.append(str(item.get("label") or ""))
                    out.extend(str(i) for i in item.get("items") or [])
    return out


def scene_course_names(scene: dict[str, Any]) -> list[str]:
    """Texts that claim to be real courses of the plan: course chips and timeline items."""
    if scene.get("type") == "courses":
        return list(scene.get("courses") or [])
    if scene.get("type") == "timeline":
        return [i for s in scene.get("steps") or [] for i in s.get("items") or []]
    return []


# ---------------- sources and checks ----------------
@dataclass
class ProgramContext:
    program: Program
    passages: list[tuple[str, str]] = field(default_factory=list)  # (label, text)

    @property
    def texts(self) -> list[str]:
        return [t for _, t in self.passages]


async def program_context(session: AsyncSession, program: Program) -> ProgramContext:
    """The approved passages an explainer of this program may use: the program's own chunks
    (department page, study plan, courses) and the general study rules of the handbook."""
    rows = await session.execute(
        select(Chunk.text, Chunk.category, Source.title)
        .join(Source, Source.id == Chunk.source_id)
        .where(
            Chunk.review_status == "approved",
            (Chunk.program_id == program.id)
            | (Chunk.program_id.is_(None) & Chunk.category.in_(GENERAL_CATEGORIES)),
        )
        .order_by(Chunk.program_id.is_(None), Chunk.category == "courses", Chunk.source_id, Chunk.position, Chunk.id)
    )
    ctx = ProgramContext(program)
    for text_, category, title in rows:
        ctx.passages.append((f"{title or 'source'}" + (f"; {category}" if category else ""), text_))
    return ctx


@dataclass
class Issue:
    segment: int  # 1-based position; 0 = the explainer as a whole
    kind: str  # number / course / claim / structure
    detail: str


def find_issues(segments: list[tuple[str, dict[str, Any]]], source_texts: list[str]) -> list[Issue]:
    """Why an explainer may not be approved. `segments` = (spoken text, scene) in order."""
    issues: list[Issue] = []
    if not MIN_SEGMENTS <= len(segments) <= MAX_SEGMENTS:
        issues.append(Issue(0, "structure", f"{len(segments)} segments (expected {MIN_SEGMENTS}-{MAX_SEGMENTS})"))
    known: set[str] = set()
    for t in source_texts:
        known |= numbers_in(t)
    haystack = norm("\n".join(source_texts))
    for i, (spoken, scene) in enumerate(segments, start=1):
        if not spoken.strip():
            issues.append(Issue(i, "structure", "empty spoken text"))
        shown = " \n ".join([spoken, *scene_strings(scene)])
        for n in sorted(numbers_in(shown) - known):
            issues.append(Issue(i, "number", f"{n} is not in the program's approved sources"))
        for course in scene_course_names(scene):
            if norm(course) and norm(course) not in haystack:
                issues.append(Issue(i, "course", f"'{course}' is not written in the approved sources"))
        low = f" {norm(shown)} "
        for word in GUARANTEE_WORDS:
            if f" {norm(word)} " in low:
                issues.append(Issue(i, "claim", f"promises an outcome ('{word}')"))
    return issues


async def load_segments(session: AsyncSession, explainer_id: int) -> list[ExplainerSegment]:
    rows = await session.scalars(
        select(ExplainerSegment)
        .where(ExplainerSegment.explainer_id == explainer_id)
        .order_by(ExplainerSegment.position, ExplainerSegment.id)
    )
    return list(rows.all())


async def explainer_issues(session: AsyncSession, explainer: Explainer) -> list[Issue]:
    program = await session.get(Program, explainer.program_id)
    assert program is not None
    ctx = await program_context(session, program)
    segments = await load_segments(session, explainer.id)
    return find_issues([(s.text, s.scene) for s in segments], ctx.texts)


# ---------------- drafting ----------------
DRAFT_INSTRUCTIONS = """\
You write a short spoken presentation ("explainer") of ONE bachelor program of the Faculty of \
Engineering & Design at Middle East University (MEU), Jordan. A friendly reception robot will \
say it to prospective students and parents while its screen shows one animated scene per segment.

FACTS (most important)
- Use ONLY what is written in the CONTEXT passages. No outside knowledge.
- Every number you write (in the spoken text or in a scene) must be written in the CONTEXT, as \
digits, exactly as there. Do not calculate, estimate or round. If you are not sure, leave the \
number out.
- Course names must be copied exactly from the CONTEXT (the Arabic title for Arabic, the English \
title for English). Never invent a course.
- Mention laboratories, projects, field training, accreditation or where graduates work ONLY if \
the CONTEXT says so, and only what it says.
- Do NOT invent or mention salaries, rankings, job guarantees, or comparisons with other \
universities or other faculties. Never promise an outcome.
- The tone may be warm and proud (this is a recruitment robot), but every claim must be in the \
CONTEXT.

LANGUAGE
- LANGUAGE "ar": friendly, polite Jordanian Arabic (عامية أردنية مهذبة), not formal Modern \
Standard Arabic. Address the visitor in a neutral way.
- LANGUAGE "en": simple, friendly English.
- The text is spoken aloud: short sentences, no lists, no markdown, no emojis, no URLs.

STRUCTURE: 5 to 7 segments, about 20-35 words each (60-90 seconds in total).
1. What the program is (scene "title").
2. The size of the degree: total credit hours as written in the study plan (scene "stat").
3. What you study: real courses from the plan (scene "courses", or "timeline" if the CONTEXT \
gives an order such as requirement groups; use years ONLY if the CONTEXT lists courses per year).
   The robot pauses after segment 3 and asks whether to continue, so segments 1-3 must stand on \
their own.
4. Labs, projects, graduation project or field training, only if the CONTEXT mentions them \
(scene "keypoints" or "diagram").
5. Skills you gain, or where graduates work, only if the CONTEXT says (scene "keypoints").
6. Optional: one more strong, sourced point (scene "keypoints" or "media").
Last segment: how to learn more: scan the code on the screen for the official study plan, ask \
the robot questions, or visit the Admission and Registration Department (scene "qr").

SCENES (the "scene" object of each segment; all texts short, in the presentation language)
- {"type": "title", "tagline": "<one short line, at most 8 words>"}
- {"type": "stat", "value": "<digits from the CONTEXT>", "unit": "<e.g. ساعة معتمدة / credit hours>", \
"caption": "<short line>"}
- {"type": "courses", "title": "<short heading>", "courses": ["<4 to 7 exact course names>"]}
- {"type": "timeline", "title": "<short heading>", "steps": [{"label": "<short>", "items": \
["<up to 2 exact course names>"]}]}  (3 to 5 steps)
- {"type": "keypoints", "title": "<short heading>", "points": ["<2 to 4 phrases of 2-6 words>"]}
- {"type": "diagram", "title": "<short heading>", "nodes": ["<3 or 4 labels of 1-3 words, a \
chain such as sensors -> processing -> action, only from the CONTEXT>"]}
- {"type": "media", "caption": "<short line>"}  (a photo of the program if one exists)
- {"type": "qr", "caption": "<short line inviting to scan the code>"}

OUTPUT: a JSON object exactly like
{"segments": [{"text": "<what the robot says>", "scene": {...}}, ...]}
"""


def build_draft_input(ctx: ProgramContext, lang: str) -> str:
    p = ctx.program
    parts = [
        f"PROGRAM: {p.name_en} ({p.name_ar})",
        f"LANGUAGE: {lang}",
        "CONTEXT:",
    ]
    parts += [f"[{i}] ({label})\n{text_}" for i, (label, text_) in enumerate(ctx.passages, start=1)]
    parts.append("Write the explainer now. Reply with the JSON object only.")
    return "\n\n".join(parts)


@dataclass
class DraftResult:
    explainer: Explainer | None
    outcome: str  # created / replaced / skipped-approved / no-sources / failed
    issues: list[Issue] = field(default_factory=list)
    segments: int = 0
    error: str | None = None


async def draft_explainer(
    session: AsyncSession, llm: LLM, program: Program, lang: str, force: bool = False
) -> DraftResult:
    """One LLM call -> a PENDING explainer for (program, lang). An approved explainer is left
    alone unless `force`; a replaced one always goes back to pending. Never approves."""
    existing = await session.scalar(
        select(Explainer).where(Explainer.program_id == program.id, Explainer.lang == lang)
    )
    if existing is not None and existing.status == "approved" and not force:
        return DraftResult(existing, "skipped-approved")
    ctx = await program_context(session, program)
    if not await _has_program_chunks(session, program.id):
        return DraftResult(None, "no-sources")
    try:
        result = await llm.complete_json(DRAFT_INSTRUCTIONS, build_draft_input(ctx, lang))
    except ProviderError as e:
        return DraftResult(existing, "failed", error=str(e))
    raw_segments = result.data.get("segments")
    cleaned: list[tuple[str, dict[str, Any]]] = []
    for raw in raw_segments if isinstance(raw_segments, list) else []:
        if isinstance(raw, dict) and str(raw.get("text") or "").strip():
            cleaned.append((re.sub(r"\s+", " ", str(raw["text"])).strip(), clean_scene(raw.get("scene"))))
    cleaned = cleaned[:MAX_SEGMENTS]
    if not cleaned:
        return DraftResult(existing, "failed", error="the model returned no segments")

    now = datetime.now(UTC)
    if existing is None:
        explainer, outcome = Explainer(program_id=program.id, lang=lang, status="pending"), "created"
        session.add(explainer)
        await session.flush()
    else:
        explainer, outcome = existing, "replaced"
        await session.execute(delete(ExplainerSegment).where(ExplainerSegment.explainer_id == explainer.id))
        explainer.status = "pending"  # new text is never approved by itself
    explainer.updated_at = now
    for position, (spoken, scene) in enumerate(cleaned, start=1):
        session.add(ExplainerSegment(explainer_id=explainer.id, position=position, text=spoken, scene=scene))
    await session.commit()
    return DraftResult(explainer, outcome, find_issues(cleaned, ctx.texts), len(cleaned))


async def _has_program_chunks(session: AsyncSession, program_id: int) -> bool:
    return (
        await session.scalar(
            select(Chunk.id).where(Chunk.program_id == program_id, Chunk.review_status == "approved").limit(1)
        )
    ) is not None


# ---------------- audio ----------------
async def prepare_audio(session: AsyncSession, tts: CachedTTS, explainer: Explainer) -> tuple[int, int]:
    """Generate and cache the speech of every segment of an APPROVED explainer, so playback
    starts instantly. Returns (generated, already cached). Raises ProviderError if TTS fails."""
    if explainer.status != "approved":
        return 0, 0
    made = ready = 0
    for seg in await load_segments(session, explainer.id):
        cached = tts.is_cached(seg.text) if hasattr(tts, "is_cached") else False
        if not cached:
            await tts.synthesize(seg.text, cache=True)
        made, ready = made + (not cached), ready + cached
        seg.audio_cache_key = tts.cache_key(seg.text) if hasattr(tts, "cache_key") else None
    await session.commit()
    return made, ready


# ---------------- playback ----------------
@dataclass
class PlayableSegment:
    index: int  # 1-based
    text: str
    scene: dict[str, Any]


@dataclass
class Playable:
    explainer_id: int
    program_id: int
    slug: str
    name: str
    lang: str
    segments: list[PlayableSegment]


async def study_plan_url(session: AsyncSession, program: Program) -> str | None:
    """The official study-plan address for the QR code, taken from the records: the program's
    `study_plan_url`, else the URL of an approved study-plan source of the program. Only real
    web addresses count (`meu-document://...` identifiers cannot be scanned)."""
    if (program.study_plan_url or "").startswith(("https://", "http://")):
        return program.study_plan_url
    rows = await session.scalars(
        select(Source.url)
        .join(Chunk, Chunk.source_id == Source.id)
        .where(
            Chunk.program_id == program.id, Chunk.category == "study_plan",
            Chunk.review_status == "approved", Source.review_status == "approved",
        )
        .distinct()
    )  # fmt: skip
    return next((u for u in rows if u.startswith(("https://", "http://"))), None)


async def resolve_scene(
    session: AsyncSession, program: Program, lang: str, scene: dict[str, Any], turn: int = 0
) -> dict[str, Any]:
    """The scene as sent to the screen: program name and drawing added; the QR address and the
    media come from the database records, never from the draft."""
    out = {**scene, "program": {
        "slug": program.slug, "name": program.name_ar if lang == "ar" else program.name_en,
    }}  # fmt: skip
    if scene.get("type") == "qr":
        out["url"] = await study_plan_url(session, program)
    elif scene.get("type") == "media":
        out["media"] = await approved_media_for_program(session, program.id, lang, turn)
    return out


async def load_playable(session: AsyncSession, slug: str, lang: str) -> Playable | None:
    """The APPROVED explainer of a program in `lang`, ready to play, or None."""
    program = await session.scalar(select(Program).where(Program.slug == slug))
    if program is None:
        return None
    explainer = await session.scalar(
        select(Explainer).where(
            Explainer.program_id == program.id, Explainer.lang == lang, Explainer.status == "approved"
        )
    )
    if explainer is None:
        return None
    segments = await load_segments(session, explainer.id)
    if not segments:
        return None
    # Approved once is not enough: if a source was changed or withdrawn since, a number of the
    # explainer may no longer be backed by approved content. Then it is not played (the admin
    # shows what to fix) and the visitor gets the normal grounded answer.
    ctx = await program_context(session, program)
    if find_issues([(s.text, s.scene) for s in segments], ctx.texts):
        return None
    playable = [
        PlayableSegment(i, s.text, await resolve_scene(session, program, lang, s.scene, i))
        for i, s in enumerate(segments, start=1)
    ]
    name = program.name_ar if lang == "ar" else program.name_en
    return Playable(explainer.id, program.id, program.slug, name, lang, playable)


async def screen_content(session: AsyncSession) -> dict[str, Any]:
    """What the idle robot screen may show: the programs that have an approved explainer (touch
    buttons) and their approved `title` and `stat` scenes (attract mode). Arabic first."""
    rows = await session.execute(
        select(Explainer, Program)
        .join(Program, Program.id == Explainer.program_id)
        .where(Explainer.status == "approved")
        .order_by(Program.id, Explainer.lang)
    )
    programs: dict[str, dict[str, Any]] = {}
    attract: list[dict[str, Any]] = []
    for explainer, program in rows:
        if await load_playable(session, program.slug, explainer.lang) is None:
            continue  # approved, but no longer backed by its sources: not offered
        entry = programs.setdefault(
            program.slug,
            {"slug": program.slug, "name_ar": program.name_ar, "name_en": program.name_en, "langs": []},
        )
        entry["langs"].append(explainer.lang)
        if explainer.lang != "ar" and "ar" in entry["langs"]:
            continue  # the idle screen is Arabic first: English scenes only when there is no Arabic
        for seg in await load_segments(session, explainer.id):
            if seg.scene.get("type") in ("title", "stat"):
                attract.append(await resolve_scene(session, program, explainer.lang, seg.scene))
    return {"programs": list(programs.values()), "attract": attract}
