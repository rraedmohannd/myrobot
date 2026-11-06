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
    # University-wide rules and services from the Student Handbook (apply to the faculty's students)
    "about_meu": "عن جامعة الشرق الأوسط، إمتى تأسست الجامعة، سنة التأسيس، وين موقع الجامعة، "
    "الاعتماد والجودة، التصنيفات، لغة التدريس | about Middle East University, founded, location, "
    "quality accreditation, rankings, teaching language",
    "study_system": "نظام الدراسة، الساعات المعتمدة، الفصل الدراسي، الفصل الصيفي، كم سنة الدراسة، "
    "مدة الدراسة، المستوى الدراسي، سنة أولى، العبء الدراسي، كم ساعة بسجل بالفصل، أكثر وأقل عدد "
    "ساعات | study system, credit hours, semester, summer term, study duration, years, study load, "
    "maximum and minimum hours per semester",
    "university_requirements": "متطلبات الجامعة، مواد الجامعة الإجبارية والاختيارية، علوم عسكرية، "
    "تربية وطنية، امتحان المستوى، امتحان مستوى انجليزي وعربي وحاسوب، مواد استدراكية | university "
    "requirements, compulsory and elective university courses, placement test, remedial courses",
    "attendance_exams": "الغياب، نسبة الغياب، الحرمان، العذر الطبي، الامتحان النهائي، غير مكتمل، "
    "امتحان تكميلي، امتحان تعويضي، مراجعة العلامة | attendance, absence limit, barred from the "
    "exam, medical excuse, final exam, incomplete, make-up exam, grade review",
    "grading": "العلامات، الرموز، النقاط، المعدل التراكمي، المعدل الفصلي، التقدير، ممتاز، جيد جدًا، "
    "علامة النجاح | grades, letter grades, grade points, GPA, cumulative average, classification, "
    "passing grade",
    "academic_standing": "الإنذار الأكاديمي، الفصل من التخصص، معدلي أقل من 2، إعادة المادة، رفع "
    "المعدل، امتحان الإكمال، متوقع تخرجه | academic warning, probation, dismissal, repeat a course, "
    "raise GPA, completion exam",
    "registration_changes": "السحب والإضافة، الانسحاب من مادة، تأجيل الدراسة، تأجيل الفصل، فقدان "
    "المقعد | add and drop, withdraw from a course, postpone or defer studies, losing the seat",
    "transfer": "الانتقال من جامعة ثانية، التحويل، معادلة المواد، تغيير التخصص، التجسير، دبلوم كلية "
    "مجتمع | transfer from another university, course equivalence, change of major, bridging, "
    "community college diploma",
    "graduation": "متطلبات التخرج، شروط التخرج، التدريب الميداني، تدريب عملي، مشروع التخرج، براءة "
    "الذمة | graduation requirements, field training, internship, graduation project, clearance",
    "scholarships": "منح، منحة، خصم، الطلبة المتفوقين، منحة الدكتور يعقوب ناصر الدين، منح رياضية، "
    "تفوق رياضي، الأنشطة | scholarships, grants, discounts, outstanding students, sports "
    "scholarship, extracurricular activities",
    "student_support": "صندوق دعم الطالب، مساعدة مالية، ظروف مادية، تشغيل الطلبة، شغل بالجامعة، "
    "عمل جزئي | student support fund, financial aid, hardship, student jobs on campus, part-time work",
    "student_services": "خدمات الطلبة، سكن الطلبة، سكن داخلي، عيادة، طبيب، تأمين صحي، لوحة الشرف | "
    "student services, housing, dormitory, clinic, doctor, health insurance, honor board",
    "contacts": "أرقام التواصل، رقم الجامعة، رقم التلفون، كيف أتواصل، رقم القبول والتسجيل، رقم "
    "كلية الهندسة، الرقم الفرعي | contact numbers, phone, telephone, admission and registration "
    "phone, faculty extension",
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
