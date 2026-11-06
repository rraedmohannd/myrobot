"""Curated knowledge files: data checks (no DB) and import behaviour (rolled-back DB session)."""

import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select, update

from app.db.models import Chunk, Faculty, Program, Source
from app.knowledge.importer import DEFAULT_DIR, course_chunks, import_dir, load_file

ISE = DEFAULT_DIR / "ise_study_plan_2025_2026.json"


# ---------- the real data files ----------
def test_all_knowledge_files_are_valid() -> None:
    files = sorted(DEFAULT_DIR.glob("*.json"))
    assert files
    for f in files:
        load_file(f)


def test_study_plan_hours_match_the_official_pdfs() -> None:
    plans = [load_file(f) for f in sorted(DEFAULT_DIR.glob("*.json"))]
    plans = [d for d in plans if d.get("courses")]
    assert len(plans) >= 5
    for doc in plans:
        per_group: dict[str, int] = defaultdict(int)
        required: dict[str, int] = {}
        rules: dict[str, str] = {}
        for c in doc["courses"]:
            per_group[c["group"]] += c["credit_hours"] or 0
            required[c["group"]] = c["group_credit_hours"]
            rules[c["group"]] = c["group_rule"]
            assert c["title_ar"] and c["title_en"]
            # a missing value is only allowed when the plan itself leaves it blank (explained in a note)
            assert c["credit_hours"] is not None or c.get("note"), c["code"]
        for group, rule in rules.items():
            if rule == "compulsory":
                assert per_group[group] == required[group], (doc["program"]["slug"], group)
        counted = sum(v for k, v in required.items() if k != "remedial_courses")
        assert counted == doc["program"]["credit_hours"], doc["program"]["slug"]
        codes = [c["code"] for c in doc["courses"]]
        assert len(codes) == len(set(codes))


def test_course_chunks_resolve_prerequisite_names() -> None:
    doc = load_file(ISE)
    text = "\n".join(s.text for s in course_chunks(doc["courses"], doc["program"]))
    assert "1924704 Robot Programming (برمجة الروبوت)" in text
    assert "prerequisites: 1923803 Embedded Systems" in text
    assert "1924806 Sensors and Actuators (concurrent)" in text


# ---------- import behaviour ----------
def _write(tmp: Path, name: str, doc: dict) -> None:
    (tmp / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _fixture_docs() -> tuple[dict, dict]:
    plan = {
        "source": {"url": "test-doc://plan", "title": "Test plan", "lang": "en", "academic_year": "2099/2100"},
        "faculty": {"slug": "test-faculty", "name_en": "Test Faculty", "name_ar": "كلية اختبار"},
        "program": {"slug": "test-program", "name_en": "Test Program", "name_ar": "برنامج اختبار",
                    "credit_hours": 10, "academic_year": "2099/2100"},
        "chunks": [{"category": "study_plan", "lang": "en", "text": "Test Program needs 10 credit hours."}],
        "courses": [
            {"group": "core", "group_title": "Core", "group_credit_hours": 6, "group_rule": "compulsory",
             "code": "T1", "title_en": "Intro", "title_ar": "مقدمة", "credit_hours": 3, "theory_hours": 3,
             "practical_hours": 0, "prerequisites": [], "learning": "face-to-face", "jnqf_level": 6},
            {"group": "core", "group_title": "Core", "group_credit_hours": 6, "group_rule": "compulsory",
             "code": "T2", "title_en": "Advanced", "title_ar": "متقدم", "credit_hours": 3, "theory_hours": 3,
             "practical_hours": 0, "prerequisites": ["T1"], "learning": "blended", "jnqf_level": 7},
        ],
    }  # fmt: skip
    page = {
        "source": {"url": "test-web://page", "title": "Test page", "lang": "en"},
        "program": {"slug": "test-program", "summary_en": "A test program."},
        "chunks": [{"category": "careers", "lang": "en", "text": "Graduates work in testing."}],
    }
    return plan, page


async def test_import_creates_pending_records(db_session, tmp_path) -> None:
    plan, page = _fixture_docs()
    _write(tmp_path, "a_page.json", page)  # sorts first, but must import after the plan
    _write(tmp_path, "b_plan.json", plan)
    report = await import_dir(db_session, tmp_path)
    assert dict(report.files) == {"a_page.json": "new", "b_plan.json": "new"}

    fac = await db_session.scalar(select(Faculty).where(Faculty.slug == "test-faculty"))
    prog = await db_session.scalar(select(Program).where(Program.slug == "test-program"))
    assert fac is not None and prog is not None
    assert prog.credit_hours == 10 and prog.summary_en == "A test program."
    assert prog.review_status == "pending"

    src = await db_session.scalar(select(Source).where(Source.url == "test-doc://plan"))
    chunks = (await db_session.scalars(select(Chunk).where(Chunk.source_id == src.id).order_by(Chunk.position))).all()
    assert [c.review_status for c in chunks] == ["pending", "pending"]
    assert all(c.program_id == prog.id for c in chunks)
    assert "T2 Advanced (متقدم)" in chunks[1].text and "prerequisites: T1 Intro" in chunks[1].text
    assert chunks[1].academic_year == "2099/2100"


async def test_reimport_unchanged_then_changed(db_session, tmp_path) -> None:
    plan, page = _fixture_docs()
    _write(tmp_path, "b_plan.json", plan)
    _write(tmp_path, "a_page.json", page)
    await import_dir(db_session, tmp_path)

    report = await import_dir(db_session, tmp_path)
    assert set(dict(report.files).values()) == {"unchanged"}

    # Reviewer approves the plan, then the university changes it.
    src = await db_session.scalar(select(Source).where(Source.url == "test-doc://plan"))
    src.review_status = "approved"
    await db_session.execute(update(Chunk).where(Chunk.source_id == src.id).values(review_status="approved"))
    await db_session.commit()
    plan["chunks"][0]["text"] = "Test Program needs 12 credit hours."
    plan["program"]["credit_hours"] = 12
    _write(tmp_path, "b_plan.json", plan)

    report = await import_dir(db_session, tmp_path)
    assert dict(report.files)["b_plan.json"] == "changed"
    await db_session.refresh(src)
    assert src.review_status == "stale"
    statuses = (await db_session.scalars(select(Chunk.review_status).where(Chunk.source_id == src.id))).all()
    assert "approved" not in statuses and "pending" in statuses and "stale" in statuses
    prog = await db_session.scalar(select(Program).where(Program.slug == "test-program"))
    await db_session.refresh(prog)
    assert prog.credit_hours == 12


async def test_real_files_import(db_session) -> None:
    report = await import_dir(db_session)
    assert {name for name, _ in report.files} >= {
        "ise_study_plan_2025_2026.json", "ise_department_page.json",
        "ree_study_plan_2025_2026.json", "ree_department_page.json",
        "arch_study_plan_2025_2026.json", "arch_department_page.json",
        "interior_design_study_plan_2025_2026.json", "interior_design_department_page.json",
        "graphic_design_study_plan_2025_2026.json", "graphic_design_department_page.json",
        "faculty_overview.json",
    }
    expected = {"intelligent-systems-engineering": 162, "renewable-energy-engineering": 162,
                "architectural-engineering": 165, "interior-design": 141, "graphic-design": 135}
    for slug, hours in expected.items():
        prog = await db_session.scalar(select(Program).where(Program.slug == slug))
        assert prog is not None and prog.credit_hours == hours
