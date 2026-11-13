"""Interest matching (sprint 2 S3): "what do you like?" -> one or two programs of the faculty.

The interest -> program mapping is curated, promotional data. It lives in the `pitches` table
(topic = "interest:<key>", one row per interest and program) and follows the pitch rules: a row
is used only when a human approved it, it never contains made-up numbers, and it never promises
an outcome. Rows are seeded as `pending` from the approved explainers and edited in the admin.
"""

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Explainer, ExplainerSegment, Pitch, Program
from app.services.numbers import numbers_in
from app.services.explainers import GUARANTEE_WORDS, norm, program_context

TOPIC_PREFIX = "interest:"
MAX_SUGGESTIONS = 2

# The areas the robot asks about (labels are UI text, not university facts).
INTERESTS: dict[str, dict[str, Any]] = {
    "computers_ai": {
        "ar": "الكمبيوتر والذكاء الاصطناعي", "en": "Computers and AI",
        "keywords": ("كمبيوتر", "كمبيوترات", "حاسوب", "برمجه", "ذكاء", "روبوت", "روبوتات", "تكنولوجيا",
                     "computer", "computers", "ai", "programming", "coding", "robots", "robotics",
                     "robot", "technology", "software"),
    },
    "energy_environment": {
        "ar": "الطاقة والبيئة", "en": "Energy and environment",
        "keywords": ("طاقه", "بيئه", "شمس", "شمسيه", "كهربا", "كهرباء", "رياح", "energy",
                     "environment", "solar", "wind", "electricity"),
    },
    "buildings_drawing": {
        "ar": "المباني والرسم", "en": "Buildings and drawing",
        "keywords": ("مباني", "مبنى", "بنايات", "عماره", "عمارات", "بناء", "رسم", "buildings",
                     "building", "drawing", "architecture", "construction"),
    },
    "art_design": {
        "ar": "الفن والتصميم", "en": "Art and design",
        "keywords": ("فن", "فنون", "تصميم", "الوان", "ابداع", "ديكور", "art", "arts", "design",
                     "creative", "colors", "colours"),
    },
    "medicine_biology": {
        "ar": "الطب والأحياء", "en": "Medicine and biology",
        "keywords": ("طب", "احياء", "صحه", "مستشفى", "مستشفيات", "طبيه", "medicine", "biology",
                     "medical", "health", "hospital"),
    },
    "maths_physics": {
        "ar": "الرياضيات والفيزياء", "en": "Maths and physics",
        "keywords": ("رياضيات", "فيزياء", "حساب", "ارقام", "math", "maths", "mathematics",
                     "physics", "numbers"),
    },
}  # fmt: skip

# First draft of the mapping (a curated, promotional judgement). It is only ever stored as
# PENDING rows; the student edits and approves them in the admin.
DEFAULT_MAPPING: dict[str, tuple[str, ...]] = {
    "computers_ai": ("intelligent-systems-engineering",),
    "energy_environment": ("renewable-energy-engineering",),
    "buildings_drawing": ("architectural-engineering", "interior-design"),
    "art_design": ("graphic-design", "interior-design"),
    "medicine_biology": ("biomedical-engineering",),
    "maths_physics": ("intelligent-systems-engineering", "renewable-energy-engineering"),
}

_REQUESTS = (
    "مش عارف شو ادرس", "مش عارفه شو ادرس", "مش عارف شو بدي ادرس", "ما بعرف شو ادرس",
    "مش عارف شو اختار", "مش عارف اي تخصص", "ساعدني اختار", "ساعديني اختار", "ساعدني اختار تخصص",
    "محتار", "محتاره", "شو بتنصحني ادرس", "شو تنصحني ادرس", "انصحني شو ادرس", "اي تخصص بناسبني",
    "شو التخصص اللي بناسبني", "شو ادرس",
    "help me choose", "help me pick", "not sure what to study", "don t know what to study",
    "do not know what to study", "what should i study", "which program suits me",
    "which major suits me", "i can t decide",
)  # fmt: skip
_PREFIX = r"(?:و|ب|ل|ف|لل|بال|وال|فال|ال)?"
YES = {
    "اه", "آه", "ايوا", "ايوه", "اي", "نعم", "اكيد", "طبعا", "ياريت", "يا ريت", "تمام", "ماشي",
    "كمل", "كملي", "اكمل", "كمل لو سمحت", "اه كمل", "اه اكيد", "تفضل", "احكيلي", "يلا",
    "yes", "yeah", "yep", "sure", "ok", "okay", "continue", "go on", "go ahead", "yes please",
    "please", "please continue",
}  # fmt: skip


_YES_FIRST = {"اه", "نعم", "ايوا", "ايوه", "yes", "yeah", "كمل", "اكمل", "اكيد", "sure", "continue"}


def is_yes(text: str) -> bool:
    n = norm(text)
    words = n.split()
    return n in {norm(y) for y in YES} or (0 < len(words) <= 3 and words[0] in _YES_FIRST)


def is_interest_request(text: str) -> bool:
    padded = f" {norm(text)} "
    return any(f" {norm(r)} " in padded for r in _REQUESTS)


def match_interest(text: str) -> str | None:
    """The interest named in `text` (the one mentioned first), or None."""
    n = norm(text)
    best: tuple[int, str] | None = None
    for key, info in INTERESTS.items():
        for word in info["keywords"]:
            m = re.search(rf"(?:^| ){_PREFIX}{re.escape(word)}(?: |$)", n)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), key)
    return best[1] if best else None


def options(lang: str) -> list[dict[str, str]]:
    return [{"key": k, "label": v[lang if lang in ("ar", "en") else "ar"]} for k, v in INTERESTS.items()]


# ---------------- approved mapping ----------------
@dataclass
class Suggestion:
    slug: str
    name_ar: str
    name_en: str
    line_ar: str | None
    line_en: str | None
    pitch_id: int

    def name(self, lang: str) -> str:
        return self.name_ar if lang == "ar" else self.name_en

    def line(self, lang: str) -> str | None:
        return self.line_ar if lang == "ar" else self.line_en


async def has_approved_mapping(session: AsyncSession) -> bool:
    return (
        await session.scalar(
            select(Pitch.id)
            .where(Pitch.topic.like(f"{TOPIC_PREFIX}%"), Pitch.review_status == "approved",
                   Pitch.program_id.is_not(None))
            .limit(1)
        )  # fmt: skip
    ) is not None


async def suggest(session: AsyncSession, picked: list[str]) -> list[Suggestion]:
    """At most two programs for the picked interests, from APPROVED mapping rows only. A program
    matching more of the interests comes first, then the order in which they were picked."""
    topics = [f"{TOPIC_PREFIX}{k}" for k in picked]
    if not topics:
        return []
    rows = (
        await session.execute(
            select(Pitch, Program)
            .join(Program, Program.id == Pitch.program_id)
            .where(Pitch.topic.in_(topics), Pitch.review_status == "approved")
            .order_by(Pitch.id)
        )
    ).all()
    score: dict[str, list[int]] = {}
    first: dict[str, Suggestion] = {}
    for pitch, program in sorted(rows, key=lambda r: (topics.index(r[0].topic), r[0].id)):
        score.setdefault(program.slug, []).append(topics.index(pitch.topic))
        first.setdefault(program.slug, Suggestion(
            program.slug, program.name_ar, program.name_en, pitch.text_ar, pitch.text_en, pitch.id
        ))  # fmt: skip
    ordered = sorted(first.values(), key=lambda s: (-len(set(score[s.slug])), min(score[s.slug]), s.pitch_id))
    return ordered[:MAX_SUGGESTIONS]


# ---------------- seeding and review ----------------
async def seed_from_explainers(session: AsyncSession) -> dict[str, list[str]]:
    """Create PENDING mapping rows from DEFAULT_MAPPING for programs that have an APPROVED
    explainer; the line shown with the suggestion is the tagline of that explainer's title
    scene (already reviewed text). Existing rows are left alone. Never approves anything."""
    report: dict[str, list[str]] = {"created": [], "exists": [], "no_approved_explainer": []}
    for key, slugs in DEFAULT_MAPPING.items():
        for slug in slugs:
            program = await session.scalar(select(Program).where(Program.slug == slug))
            if program is None:
                continue
            label = f"{key} -> {slug}"
            existing = await session.scalar(
                select(Pitch.id).where(Pitch.topic == f"{TOPIC_PREFIX}{key}", Pitch.program_id == program.id)
            )
            if existing is not None:
                report["exists"].append(label)
                continue
            lines: dict[str, str | None] = {"ar": None, "en": None}
            for lang in lines:
                lines[lang] = await session.scalar(
                    select(ExplainerSegment.scene["tagline"].astext)
                    .join(Explainer, Explainer.id == ExplainerSegment.explainer_id)
                    .where(
                        Explainer.program_id == program.id, Explainer.lang == lang,
                        Explainer.status == "approved",
                        ExplainerSegment.scene["type"].astext == "title",
                    )
                    .order_by(ExplainerSegment.position)
                    .limit(1)
                )  # fmt: skip
            approved = await session.scalar(
                select(Explainer.id).where(Explainer.program_id == program.id, Explainer.status == "approved").limit(1)
            )
            if approved is None:
                report["no_approved_explainer"].append(label)
                continue
            session.add(Pitch(
                program_id=program.id, topic=f"{TOPIC_PREFIX}{key}", text_ar=lines["ar"],
                text_en=lines["en"], is_promotional=True, review_status="pending",
            ))  # fmt: skip
            report["created"].append(label)
    await session.commit()
    return report


async def mapping_issues(session: AsyncSession, pitch: Pitch) -> list[str]:
    """Why a mapping row may not be approved: unknown interest, no program, a number that is
    not in the program's approved sources, or a promise."""
    issues: list[str] = []
    key = pitch.topic.removeprefix(TOPIC_PREFIX)
    if key not in INTERESTS:
        issues.append(f"unknown interest '{key}'")
    program = await session.get(Program, pitch.program_id) if pitch.program_id else None
    if program is None:
        return [*issues, "no program is linked"]
    text = " \n ".join(t for t in (pitch.text_ar, pitch.text_en) if t)
    known: set[str] = set()
    for t in (await program_context(session, program)).texts:
        known |= numbers_in(t)
    issues += [f"{n} is not in the program's approved sources" for n in sorted(numbers_in(text) - known)]
    low = f" {norm(text)} "
    issues += [f"promises an outcome ('{w}')" for w in GUARANTEE_WORDS if f" {norm(w)} " in low]
    return issues
