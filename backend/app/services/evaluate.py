"""Golden-set evaluation of the grounded chat (B3 acceptance: >= 90% correct, 0 invented numbers).

Run on the laptop (needs the OpenAI key):
    docker compose exec backend python -m app.ingestion.cli evaluate
Writes data/golden/results/<timestamp>.json and .md (for the report).
"""

import json
import re
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.chat import answer_question
from app.services.providers import LLM, Embedder

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "data" / "golden"
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ـ": None})
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalize(s: str) -> str:
    """Arabic diacritics (shadda, fatha...) are dropped: "المضمّنة" and "المضمنة" are the same
    word, and the model sometimes writes them."""
    s = re.sub(r"[ً-ْٰ]", "", s)
    return re.sub(r"\s+", " ", s.translate(_ALEF).translate(_DIGITS).lower())


@dataclass
class QuestionResult:
    id: str
    lang: str
    kind: str
    question: str
    answer: str
    found: bool
    passed: bool
    reasons: list[str]
    invented_numbers: list[str]
    sources: list[str]
    total_ms: int | None
    error: str | None = None


@dataclass
class Report:
    model: str | None
    started: str
    total: int
    passed: int
    accuracy: float
    invented_numbers_total: int
    latency_median_ms: float | None
    latency_p90_ms: float | None
    results: list[QuestionResult] = field(default_factory=list)


def check(item: dict[str, Any], answer: str, found: bool, invented: list[str]) -> list[str]:
    """Reasons for failure (empty list = pass)."""
    reasons: list[str] = []
    text_ = normalize(answer)
    if item["kind"] == "answerable" and not found:
        reasons.append("expected an answer but found=false")
    if item["kind"] == "unanswerable" and found:
        reasons.append("expected 'I don't know' (found=false)")
    for group in item.get("must_include", []):
        if not any(normalize(alt) in text_ for alt in group):
            reasons.append(f"missing one of {group}")
    for bad in item.get("must_not_include", []):
        if normalize(bad) in text_:
            reasons.append(f"contains forbidden '{bad}'")
    if invented:
        reasons.append(f"numbers not in the sources: {invented}")
    return reasons


def load_questions(path: Path = GOLDEN_DIR / "questions.json") -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


def _p90(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))])


async def evaluate(
    session: AsyncSession, embedder: Embedder, llm: LLM, questions: list[dict[str, Any]]
) -> Report:
    results: list[QuestionResult] = []
    model = None
    for item in questions:
        r = await answer_question(session, embedder, llm, item["question"], log=False)
        model = model or r.model
        reasons = check(item, r.answer, r.found, r.invented_numbers)
        if r.error:
            reasons.insert(0, f"error: {r.error}")
        results.append(
            QuestionResult(
                id=item["id"],
                lang=item["lang"],
                kind=item["kind"],
                question=item["question"],
                answer=r.answer,
                found=r.found,
                passed=not reasons,
                reasons=reasons,
                invented_numbers=r.invented_numbers,
                sources=[f"[{s.n}] {s.title}" for s in r.sources],
                total_ms=r.timings.get("total_ms"),
                error=r.error,
            )  # fmt: skip
        )
    latencies = [x.total_ms for x in results if x.total_ms is not None and not x.error]
    passed = sum(x.passed for x in results)
    return Report(
        model=model,
        started=datetime.now().isoformat(timespec="seconds"),
        total=len(results),
        passed=passed,
        accuracy=round(passed / len(results), 3) if results else 0.0,
        invented_numbers_total=sum(len(x.invented_numbers) for x in results),
        latency_median_ms=statistics.median(latencies) if latencies else None,
        latency_p90_ms=_p90(latencies),
        results=results,
    )


def write_report(report: Report, out_dir: Path = GOLDEN_DIR / "results") -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"{stamp}.json"
    json_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Golden-set evaluation — {report.started}",
        "",
        f"- Model: `{report.model}`",
        f"- Passed: **{report.passed}/{report.total}** ({report.accuracy:.0%}); target ≥ 90%",
        f"- Numbers not found in sources: **{report.invented_numbers_total}** (target 0)",
        f"- Latency (text, retrieval + LLM): median {report.latency_median_ms} ms, "
        f"p90 {report.latency_p90_ms} ms",
        "",
        "| # | id | lang | kind | pass | answer | reasons |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(report.results, start=1):
        answer = r.answer.replace("|", "/").replace("\n", " ")
        lines.append(
            f"| {i} | {r.id} | {r.lang} | {r.kind} | {'✅' if r.passed else '❌'} | {answer} | "
            f"{'; '.join(r.reasons).replace('|', '/')} |"
        )
    md_path = out_dir / f"{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
