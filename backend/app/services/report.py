"""Figures and tables for the graduation report (sprint 3, S8).

`export_report` reads what the system really recorded (the turns table, the latency and
evaluation result files, robot runs and physically measured return trials) and writes CSV
tables and PNG charts into one folder, plus a README.md that says where each file came from.

Rules that this module keeps:
- Nothing is invented. A section without data still gets its CSV (headers only) and a PNG that
  says "No data yet" and why.
- A simulated run is labelled on the chart ("SIMULATED — not a physical result") and in the
  file name, and simulated runs never enter the return-trial statistics.
"""

import csv
import json
import math
import statistics
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.api.rest.analytics import read_evaluation_runs  # noqa: E402
from app.db.models import PoseSample, ReturnTrial, RobotRun, Turn  # noqa: E402
from app.services.evaluate import GOLDEN_DIR  # noqa: E402
from app.services.latency import RESULTS_DIR as LATENCY_DIR  # noqa: E402
from app.services.latency import STAGES as RUN_STAGES  # noqa: E402
from app.services.latency import percentile  # noqa: E402

SIMULATED_LABEL = "SIMULATED — not a physical result"
NO_REAL_TRIALS = "No physical return trials recorded yet. Simulated runs are never counted."
MAX_TRAJECTORY_RUNS = 20
PASS_TARGET_PCT = 90.0

# Column of the turns table -> label on the chart.
TURN_STAGES: dict[str, str] = {
    "stt_ms": "STT",
    "retrieval_ms": "Retrieval",
    "llm_first_ms": "LLM\nfirst piece",
    "llm_ms": "LLM\nwhole answer",
    "ack_ms": "Acknow-\nledgement",
    "tts_ms": "Answer\naudio",
    "total_ms": "Total",
}

FIGSIZE = (8, 4.5)
DPI = 150
BLUE = "#2a78d6"
BLUE_LIGHT = "#9ec5f4"
ORANGE = "#eb6834"
INK = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e4e3df"
LABEL_BOX = {"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 1.0}


# ---------------- small helpers ----------------
def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in rows:
            writer.writerow(["" if v is None else v for v in row])
    return path


def _new_figure(title: str) -> tuple[Any, Any]:
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.set_title(title, color=INK, fontsize=12, loc="left")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.set_axisbelow(True)
    return fig, ax


def _save(fig: Any, path: Path) -> Path:
    try:
        fig.tight_layout()
        fig.savefig(path, dpi=DPI, facecolor="white")
    finally:
        plt.close(fig)
    return path


def _no_data_png(path: Path, title: str, reason: str) -> Path:
    fig, ax = _new_figure(title)
    ax.set_axis_off()
    ax.text(0.5, 0.58, "No data yet", ha="center", va="center", fontsize=20, color=INK,
            transform=ax.transAxes)  # fmt: skip
    ax.text(0.5, 0.40, reason, ha="center", va="center", fontsize=10, color=MUTED, wrap=True,
            transform=ax.transAxes)  # fmt: skip
    return _save(fig, path)


def _r(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(float(value), digits)


def _stamp(name: str) -> datetime | None:
    """`20261113-134651.json` -> datetime (None if the file is not named by a timestamp)."""
    try:
        return datetime.strptime(Path(name).stem, "%Y%m%d-%H%M%S")
    except ValueError:
        return None


# ---------------- 1. latency ----------------
async def _question_turns(session: AsyncSession, session_ids: list[int] | None) -> list[Turn]:
    stmt = select(Turn).where(Turn.intent == "question", Turn.error.is_(None))
    if session_ids is not None:
        stmt = stmt.where(Turn.session_id.in_(session_ids))
    return list((await session.scalars(stmt.order_by(Turn.created_at, Turn.id))).all())


def _latency_stages(turns: list[Turn], out_dir: Path) -> list[Path]:
    values = {col: [v for t in turns if (v := getattr(t, col)) is not None] for col in TURN_STAGES}
    rows = [
        [col, len(v), statistics.median(v) if v else None, percentile(v, 0.9),
         min(v) if v else None, max(v) if v else None]
        for col, v in values.items()
    ]  # fmt: skip
    header = ["stage", "n", "median_ms", "p90_ms", "min_ms", "max_ms"]
    files = [_write_csv(out_dir / "latency_stages.csv", header, rows)]

    title = "Latency per stage in recorded conversations"
    png = out_dir / "latency_stages.png"
    present = [(col, v) for col, v in values.items() if v]
    if not present:
        files.append(
            _no_data_png(png, title, "No question turns without error in the turns table.")
        )
        return files
    fig, ax = _new_figure(f"{title} ({len(turns)} question turns)")
    ax.boxplot(
        [v for _, v in present],
        patch_artist=True,
        widths=0.5,
        boxprops={"facecolor": BLUE_LIGHT, "edgecolor": BLUE},
        medianprops={"color": INK, "linewidth": 1.5},
        whiskerprops={"color": BLUE},
        capprops={"color": BLUE},
        flierprops={"marker": "o", "markersize": 3, "markerfacecolor": BLUE,
                    "markeredgecolor": BLUE, "alpha": 0.5},
    )  # fmt: skip
    ax.set_xticks(range(1, len(present) + 1))
    ax.set_xticklabels([f"{TURN_STAGES[col]}\n(n={len(v)})" for col, v in present], fontsize=8)
    ax.set_ylabel("milliseconds")
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    files.append(_save(fig, png))
    return files


def read_latency_runs(latency_dir: Path) -> list[dict[str, Any]]:
    """The latency measurement files, oldest first. Unreadable files are skipped."""
    runs = []
    for path in sorted(latency_dir.glob("*.json")) if latency_dir.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("summary"), dict):
            continue
        runs.append({"file": path.name, "stamp": _stamp(path.name), **data})
    return runs


def _latency_runs(latency_dir: Path, out_dir: Path) -> list[Path]:
    runs = read_latency_runs(latency_dir)
    header = ["file", "timestamp", "mode", "model", "streaming", "audio_stream", "stage", "n",
              "median_ms", "p90_ms"]  # fmt: skip
    rows = []
    for run in runs:
        stamp = run["stamp"].isoformat() if run["stamp"] else None
        stages = [s for s in RUN_STAGES if s in run["summary"]]
        stages += [s for s in run["summary"] if s not in stages]
        for stage in stages:
            s = run["summary"][stage] or {}
            rows.append([run["file"], stamp, run.get("mode") or "pipeline", run.get("model"),
                         run.get("streaming"), run.get("audio_stream"), stage, s.get("n"),
                         s.get("median"), s.get("p90")])  # fmt: skip
    files = [_write_csv(out_dir / "latency_runs.csv", header, rows)]

    title = "Median time to first answer audio per measurement run"
    png = out_dir / "latency_runs.png"
    points = [
        (run, m)
        for run in runs
        if (m := (run["summary"].get("first_audio_ms") or {}).get("median")) is not None
    ]
    if not points:
        files.append(
            _no_data_png(png, title, f"No latency measurement files in {latency_dir.name}/.")
        )
        return files
    fig, ax = _new_figure(title)
    xs = list(range(len(points)))
    ys = [float(m) for _, m in points]
    ax.plot(xs, ys, color=BLUE, linewidth=2, marker="o", markersize=7)
    labels = []
    for x, (run, m) in zip(xs, points, strict=True):
        ax.annotate(f"{m:.0f} ms", (x, float(m)), textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=9, color=INK, bbox=LABEL_BOX)  # fmt: skip
        stamp = (
            run["stamp"].strftime("%Y-%m-%d\n%H:%M:%S") if run["stamp"] else Path(run["file"]).stem
        )
        mode = run.get("mode") or "pipeline"
        flag = run.get("audio_stream") if mode == "dialogue" else run.get("streaming")
        what = "audio stream" if mode == "dialogue" else "LLM stream"
        labels.append(f"{stamp}\n{mode}\n{what}: {'on' if flag else 'off'}")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_xlim(-0.5, len(xs) - 0.5)
    ax.set_ylim(0, max(ys) * 1.2)
    ax.set_xlabel("measurement run (file timestamp)")
    ax.set_ylabel("median first answer audio (ms)")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    files.append(_save(fig, png))
    return files


# ---------------- 2. evaluation ----------------
def _evaluation(golden_dir: Path, out_dir: Path) -> list[Path]:
    results_dir = golden_dir / "results" if (golden_dir / "results").is_dir() else golden_dir
    runs = read_evaluation_runs(results_dir) if results_dir.is_dir() else []
    runs = [r for r in runs if r.get("total") and r.get("passed") is not None]
    runs.sort(key=lambda r: (str(r.get("started") or ""), r["file"]))
    header = ["file", "started", "model", "total", "passed", "pass_rate_pct", "accuracy",
              "invented_numbers_total", "latency_median_ms", "latency_p90_ms"]  # fmt: skip
    rows = [
        [r["file"], r.get("started"), r.get("model"), r["total"], r["passed"],
         round(100 * r["passed"] / r["total"], 1), r.get("accuracy"),
         r.get("invented_numbers_total"), r.get("latency_median_ms"), r.get("latency_p90_ms")]
        for r in runs
    ]  # fmt: skip
    files = [_write_csv(out_dir / "evaluation_runs.csv", header, rows)]

    title = "Golden-set evaluation pass rate per run"
    png = out_dir / "evaluation_pass_rate.png"
    if not runs:
        files.append(
            _no_data_png(png, title, f"No evaluation result files in {results_dir.name}/.")
        )
        return files
    fig, ax = _new_figure(title)
    xs = list(range(len(runs)))
    ys = [100 * r["passed"] / r["total"] for r in runs]
    ax.axhline(PASS_TARGET_PCT, color=MUTED, linestyle="--", linewidth=1.2,
               label=f"target {PASS_TARGET_PCT:.0f} %")  # fmt: skip
    ax.plot(xs, ys, color=BLUE, linewidth=2, marker="o", markersize=7, label="pass rate")
    for x, y, r in zip(xs, ys, runs, strict=True):
        ax.annotate(f"{r['passed']}/{r['total']}", (x, y), textcoords="offset points",
                    xytext=(0, -16), ha="center", fontsize=9, color=INK,
                    bbox=LABEL_BOX)  # fmt: skip
    ax.set_xticks(xs)
    ax.set_xticklabels([str(r.get("started") or r["file"])[:16].replace("T", "\n") for r in runs],
                       fontsize=8)  # fmt: skip
    ax.set_xlim(-0.5, len(xs) - 0.5)
    ax.set_ylim(0, 100)
    for line in ax.lines:  # a point at exactly 100 % must not be cut by the axis limit
        line.set_clip_on(False)
    ax.set_xlabel("evaluation run (start time)")
    ax.set_ylabel("pass rate (%)")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.legend(loc="lower right", frameon=False, fontsize=9)
    files.append(_save(fig, png))
    return files


# ---------------- 3. found / not found ----------------
def _found_rate(turns: list[Turn], out_dir: Path) -> list[Path]:
    found = sum(1 for t in turns if t.found is True)
    not_found = sum(1 for t in turns if t.found is False)
    unknown = len(turns) - found - not_found
    known = found + not_found
    rate = round(found / known, 3) if known else None
    files = [_write_csv(out_dir / "found_rate.csv", ["found", "not_found", "unknown", "found_rate"],
                        [[found, not_found, unknown, rate]])]  # fmt: skip

    title = "Questions answered from the knowledge base"
    png = out_dir / "found_rate.png"
    if not turns:
        files.append(
            _no_data_png(png, title, "No question turns without error in the turns table.")
        )
        return files
    subtitle = (
        f"found rate {100 * rate:.1f} % of {known} known"
        if rate is not None
        else "found flag not recorded"
    )
    fig, ax = _new_figure(f"{title} ({subtitle})")
    names = ["Found", "Not found", "Unknown\n(before the flag existed)"]
    counts = [found, not_found, unknown]
    bars = ax.bar(names, counts, color=BLUE, width=0.5)
    ax.bar_label(bars, labels=[str(c) for c in counts], padding=3, fontsize=10, color=INK)
    ax.set_ylabel("question turns (count)")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylim(0, max(counts) * 1.15 + 1)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    files.append(_save(fig, png))
    return files


# ---------------- 4. speakers ----------------
async def _speakers(
    session: AsyncSession, session_ids: list[int] | None, out_dir: Path
) -> list[Path]:
    stmt = select(Turn.speaker_source, Turn.speaker_confidence).where(
        Turn.speaker_label.is_not(None)
    )
    if session_ids is not None:
        stmt = stmt.where(Turn.session_id.in_(session_ids))
    by_source: dict[str, list[float | None]] = {}
    for source, confidence in (await session.execute(stmt)).all():
        by_source.setdefault(source or "(none)", []).append(confidence)
    rows = []
    for source in sorted(by_source):
        conf = [c for c in by_source[source] if c is not None]
        rows.append([source, len(by_source[source]), len(conf),
                     _r(statistics.median(conf)) if conf else None,
                     _r(min(conf)) if conf else None, _r(max(conf)) if conf else None])  # fmt: skip
    header = ["speaker_source", "n", "n_with_confidence", "median_confidence", "min_confidence",
              "max_confidence"]  # fmt: skip
    files = [_write_csv(out_dir / "speakers.csv", header, rows)]

    title = "Speaker assignment confidence in recorded conversations"
    note = "Not a labelled validation: see docs/SPEAKERS.md"
    png = out_dir / "speaker_confidence.png"
    confidences = [c for values in by_source.values() for c in values if c is not None]
    if not confidences:
        files.append(_no_data_png(png, title, f"No turns with a speaker confidence. {note}"))
        return files
    fig, ax = _new_figure(f"{title} (n={len(confidences)})")
    # The stored value is a similarity score: normally 0..1, but it can be slightly negative.
    # The axis grows to show such values instead of silently dropping them.
    low = min(0.0, math.floor(min(confidences) * 10) / 10)
    high = max(1.0, math.ceil(max(confidences) * 10) / 10)
    ax.hist(confidences, bins=round((high - low) * 20), range=(low, high), color=BLUE,
            edgecolor="white", linewidth=1)  # fmt: skip
    ax.set_xlim(low, high)
    ax.set_xlabel("speaker confidence (similarity score, nominal range 0 to 1)")
    ax.set_ylabel("turns (count)")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    fig.text(0.01, 0.01, note, fontsize=8, color=MUTED, ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    try:
        fig.savefig(png, dpi=DPI, facecolor="white")
    finally:
        plt.close(fig)
    files.append(png)
    return files


# ---------------- 5. trajectories ----------------
def trajectory_file_name(run_id: int, is_simulated: bool) -> str:
    return f"trajectory_run_{run_id}{'_SIMULATED' if is_simulated else ''}.png"


def _trajectory_png(
    path: Path, run: RobotRun, samples: list[tuple[int, float, float, str | None]]
) -> Path:
    kind = SIMULATED_LABEL if run.is_simulated else "Physical run"
    title = f"{kind} — run {run.id} trajectory (odometry estimate)"
    if not samples:
        return _no_data_png(path, title, "This run has no pose samples.")
    fig, ax = _new_figure(title)
    # Split into consecutive outbound / return pieces; each piece starts at the previous point
    # so the line is continuous.
    pieces: list[tuple[bool, list[tuple[float, float]]]] = []
    for i, (_, x, y, state) in enumerate(samples):
        is_return = bool(state) and state.startswith("return")
        if not pieces or pieces[-1][0] != is_return:
            start = [(samples[i - 1][1], samples[i - 1][2])] if i else []
            pieces.append((is_return, start))
        pieces[-1][1].append((x, y))
    seen: set[bool] = set()
    for is_return, pts in pieces:
        label = None
        if is_return not in seen:
            seen.add(is_return)
            label = "return" if is_return else "outbound"
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=ORANGE if is_return else BLUE,
                linewidth=2, label=label)  # fmt: skip
    ax.scatter([0.0], [0.0], marker="o", s=70, color="white", edgecolor=INK, linewidth=1.5,
               zorder=5, label="start (0, 0)")  # fmt: skip
    ax.scatter([samples[-1][1]], [samples[-1][2]], marker="X", s=80, color=INK, edgecolor="white",
               linewidth=1, zorder=6, label="end")  # fmt: skip
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.grid(color=GRID, linewidth=0.8)
    ax.legend(loc="best", frameon=False, fontsize=9)
    if run.is_simulated:
        ax.text(0.5, 0.5, "SIMULATED", transform=ax.transAxes, ha="center", va="center",
                rotation=30, fontsize=54, color=MUTED, alpha=0.13, zorder=0)  # fmt: skip
    return _save(fig, path)


async def _trajectories(
    session: AsyncSession, run_ids: list[int] | None, out_dir: Path
) -> list[Path]:
    stmt = select(RobotRun).order_by(RobotRun.started_at.desc(), RobotRun.id.desc())
    if run_ids is not None:
        stmt = stmt.where(RobotRun.id.in_(run_ids))
    runs = list((await session.scalars(stmt.limit(MAX_TRAJECTORY_RUNS))).all())
    runs.sort(key=lambda r: r.id)

    rows, pngs = [], []
    for run in runs:
        samples = [
            (t, x, y, state)
            for t, x, y, state in (
                await session.execute(
                    select(PoseSample.t_ms, PoseSample.x_m, PoseSample.y_m, PoseSample.state)
                    .where(PoseSample.run_id == run.id)
                    .order_by(PoseSample.t_ms, PoseSample.id)
                )
            ).all()
        ]
        length = sum(
            math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(samples, samples[1:], strict=False)
        )
        rows.append([
            run.id, run.is_simulated, len(samples),
            _r((samples[-1][0] - samples[0][0]) / 1000) if samples else None,
            _r(length) if samples else None,
            _r(math.hypot(samples[-1][1], samples[-1][2])) if samples else None,
        ])  # fmt: skip
        pngs.append(
            _trajectory_png(out_dir / trajectory_file_name(run.id, run.is_simulated), run, samples)
        )
    header = ["run_id", "is_simulated", "samples", "duration_s", "path_length_m",
              "end_distance_from_start_m"]  # fmt: skip
    return [_write_csv(out_dir / "trajectories.csv", header, rows), *pngs]


# ---------------- 6. return trials (physical runs only) ----------------
async def _return_trials(
    session: AsyncSession, run_ids: list[int] | None, out_dir: Path
) -> list[Path]:
    stmt = (
        select(ReturnTrial)
        .join(RobotRun, RobotRun.id == ReturnTrial.run_id)
        .where(RobotRun.is_simulated.is_(False), ReturnTrial.status == "measured")
        .order_by(ReturnTrial.created_at, ReturnTrial.id)
    )
    if run_ids is not None:
        stmt = stmt.where(ReturnTrial.run_id.in_(run_ids))
    trials = list((await session.scalars(stmt)).all())
    files = [
        _write_csv(
            out_dir / "return_trials.csv",
            [
                "trial_id",
                "run_id",
                "measured_err_m",
                "measured_heading_err_deg",
                "success",
                "created_at",
            ],
            [
                [
                    t.id,
                    t.run_id,
                    t.measured_err_m,
                    t.measured_heading_err_deg,
                    t.success,
                    t.created_at.isoformat() if t.created_at else None,
                ]
                for t in trials
            ],
        )  # fmt: skip
    ]
    errors = [t.measured_err_m for t in trials if t.measured_err_m is not None]
    n = len(trials)
    summary = [
        n,
        _r(sum(1 for t in trials if t.success) / n) if n else None,
        _r(statistics.mean(errors)) if errors else None,
        _r(statistics.median(errors)) if errors else None,
        _r(max(errors)) if errors else None,
    ]
    files.append(_write_csv(out_dir / "return_trials_summary.csv",
                            ["n", "success_rate", "mean_err_m", "median_err_m", "max_err_m"],
                            [summary]))  # fmt: skip

    title = "Return-to-start error, physical runs only"
    png = out_dir / "return_trials.png"
    if not errors:
        files.append(_no_data_png(png, title, NO_REAL_TRIALS))
        return files
    fig, ax = _new_figure(f"{title} (n={len(errors)}, tape-measured)")
    ax.hist(errors, bins=min(12, max(4, len(errors))), range=(0.0, max(max(errors) * 1.1, 0.1)),
            color=BLUE, edgecolor="white", linewidth=1)  # fmt: skip
    ax.axvline(statistics.median(errors), color=INK, linestyle="--", linewidth=1.2,
               label=f"median {statistics.median(errors):.2f} m")  # fmt: skip
    ax.set_xlim(left=0)
    ax.set_xlabel("distance from the start point after the return (m)")
    ax.set_ylabel("trials (count)")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    files.append(_save(fig, png))
    return files


# ---------------- README ----------------
_DESCRIPTIONS: dict[str, str] = {
    "latency_stages.csv": "Per-stage latency (n, median, p90 nearest-rank, min, max, in ms). "
    "Source: turns table, question turns without error.",
    "latency_stages.png": "Box plot of the same per-stage latency distribution. Source: turns table.",
    "latency_runs.csv": "One row per latency measurement run and stage (median, p90, n). "
    "Source: backend/data/latency/*.json.",
    "latency_runs.png": "Median time from question text to first answer audio per measurement run. "
    "Source: backend/data/latency/*.json.",
    "evaluation_runs.csv": "One row per golden-set evaluation run (passed, total, pass rate, "
    "invented numbers, latency). Source: backend/data/golden/results/*.json.",
    "evaluation_pass_rate.png": "Pass rate per evaluation run in time order with the 90 % target. "
    "Source: backend/data/golden/results/*.json.",
    "found_rate.csv": "Counts of question turns answered from the knowledge base, not answered, and "
    "unknown (flag not recorded), with the found rate. Source: turns.found.",
    "found_rate.png": "Bar chart of the same counts. Source: turns.found.",
    "speakers.csv": "Speaker assignment confidence per speaker_source (n, median, min, max). "
    "Source: turns table. Not a labelled validation, see docs/SPEAKERS.md.",
    "speaker_confidence.png": "Histogram of speaker assignment confidence. Source: turns table. "
    "Not a labelled validation, see docs/SPEAKERS.md.",
    "trajectories.csv": f"One row per robot run (most recent {MAX_TRAJECTORY_RUNS}): samples, duration, "
    "path length and end distance from (0, 0), all from odometry. Source: robot_runs, pose_samples. "
    "Rows with is_simulated = True are simulations, not physical results.",
    "return_trials.csv": "One row per tape-measured return trial of a physical run. "
    "Source: return_trials joined to robot_runs (is_simulated = false, status = measured).",
    "return_trials_summary.csv": "n, success rate, mean, median and max error (m) of those trials. "
    "Simulated runs and pending trials are never counted.",
    "return_trials.png": "Histogram of the measured return error (m), physical runs only. "
    "Source: return_trials.",
}


def _describe(name: str) -> str:
    if name.startswith("trajectory_run_"):
        if name.endswith("_SIMULATED.png"):
            return ("SIMULATED run, not a physical result: x/y path from the simulator's odometry. "
                    "Source: pose_samples.")  # fmt: skip
        return "Physical run: x/y path estimated by odometry (outbound and return). Source: pose_samples."
    return _DESCRIPTIONS.get(name, "")


def _write_readme(out_dir: Path, files: list[Path]) -> Path:
    lines = [
        "# Report export",
        "",
        f"Generated {datetime.now().astimezone().isoformat(timespec='seconds')} by "
        "`app.services.report.export_report`.",
        "",
        "Every value comes from recorded data; nothing is estimated or filled in. A chart that says",
        '"No data yet" means the system has not recorded that kind of data. Files with `SIMULATED`',
        "in the name come from the simulator and are not physical test results.",
        "",
        "| File | What it is and where the data came from |",
        "|---|---|",
    ]
    lines += [f"| `{p.name}` | {_describe(p.name)} |" for p in files]
    path = out_dir / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------- public entry point ----------------
async def export_report(
    session: AsyncSession,
    out_dir: Path,
    golden_dir: Path = GOLDEN_DIR,
    latency_dir: Path = LATENCY_DIR,
    *,
    session_ids: list[int] | None = None,
    run_ids: list[int] | None = None,
) -> list[Path]:
    """Write the report tables (CSV) and charts (PNG) into `out_dir`; return the files written.

    `golden_dir` is the golden-set folder (its `results/` sub-folder is read; a folder that holds
    the result files directly also works). `latency_dir` holds the latency measurement files.
    `session_ids` limits the conversation sections to those sessions and `run_ids` limits the
    trajectory and return-trial sections to those runs (used by tests; None = everything).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    turns = await _question_turns(session, session_ids)

    files: list[Path] = []
    files += _latency_stages(turns, out_dir)
    files += _latency_runs(Path(latency_dir), out_dir)
    files += _evaluation(Path(golden_dir), out_dir)
    files += _found_rate(turns, out_dir)
    files += await _speakers(session, session_ids, out_dir)
    files += await _trajectories(session, run_ids, out_dir)
    files += await _return_trials(session, run_ids, out_dir)
    files.append(_write_readme(out_dir, files))
    return files
