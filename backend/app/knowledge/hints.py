"""Search hints: bilingual keywords stored next to each chunk (never shown, never facts).

Most curated content is in English while visitors ask in Jordanian Arabic. A hint adds the
program names in both languages and Arabic/English words people use for that kind of
question, so full-text and vector search can match an Arabic question to an English passage
without changing the reviewed text itself (so no re-approval is needed).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CATEGORY_HINTS: dict[str, str] = {
    "study_plan": "الخطة الدراسية، مجموع الساعات المعتمدة، كم ساعة بدها، كم ساعة التخصص، "
    "متطلبات التخرج | study plan, total credit hours, how many hours, degree requirements",
    "courses": "المواد والمساقات، المتطلب السابق، مواد التخصص | courses, subjects, prerequisites",
    "program_overview": "نبذة عن التخصص والقسم، شو بندرس، إمتى تأسس القسم، تاريخ القسم، "
    "كم سنة الدراسة، مدة البرنامج | about the program and department, history, founded, years",
    "careers": "مجالات العمل، وظائف، وين بيشتغل الخريج، شغل الخريجين، فرص العمل بعد التخرج | "
    "careers, jobs, where graduates work, employment",
    "why_meu": "ليش أدرس هذا التخصص بالجامعة، مميزات التخصص، الاعتماد، معتمد، نقابة المهندسين "
    "الأردنيين، اتحاد المهندسين العرب | why study at MEU, advantages, accreditation, accredited",
    "labs": "المختبرات، كم مختبر، أعضاء الهيئة التدريسية، الدكاترة | laboratories, labs, staff",
    "outcomes": "مخرجات التعلم، أهداف البرنامج | student outcomes, program educational objectives",
    "faculty_overview": "تخصصات الكلية، الأقسام، البرامج، شو التخصصات الموجودة، عميد الكلية | "
    "faculty programs, departments, majors, dean",
}


def build_hint(category: str | None, program_en: str | None, program_ar: str | None) -> str | None:
    parts = []
    if program_en or program_ar:
        parts.append(" / ".join(p for p in (program_en, program_ar) if p))
    if category and category in CATEGORY_HINTS:
        parts.append(CATEGORY_HINTS[category])
    return " — ".join(parts) or None


async def refresh_hints(session: AsyncSession) -> int:
    """Recompute every chunk's hint; a changed hint clears the embedding (re-embedded on use)."""
    rows = (
        await session.execute(
            text(
                "SELECT c.id, c.category, c.search_hint, p.name_en, p.name_ar "
                "FROM chunks c LEFT JOIN programs p ON p.id = c.program_id"
            )
        )
    ).all()
    changed = 0
    for r in rows:
        hint = build_hint(r.category, r.name_en, r.name_ar)
        if hint != r.search_hint:
            await session.execute(
                text(
                    "UPDATE chunks SET search_hint = :h, embedding = NULL, embedding_model = NULL "
                    "WHERE id = :id"
                ),
                {"h": hint, "id": r.id},
            )
            changed += 1
    if changed:
        await session.commit()
    return changed
