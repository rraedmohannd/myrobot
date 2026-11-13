"""Report export (sprint 3, S8): CSV tables and PNG charts from recorded data only, simulated
runs labelled in the file name, and return-trial statistics from physical measured trials only."""

import csv
import json
from pathlib import Path

from app.db.models import PoseSample, ReturnTrial, RobotRun, Session, Turn
from app.services.report import NO_REAL_TRIALS, export_report, trajectory_file_name

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FIXED_FILES = {
    "latency_stages.csv", "latency_stages.png", "latency_runs.csv", "latency_runs.png",
    "evaluation_runs.csv", "evaluation_pass_rate.png", "found_rate.csv", "found_rate.png",
    "speakers.csv", "speaker_confidence.png", "trajectories.csv", "return_trials.csv",
    "return_trials_summary.csv", "return_trials.png", "README.md",
}  # fmt: skip


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


async def _seed(db_session) -> tuple[int, int, int]:
    """One conversation, one simulated run with samples, one physical run with two trials."""
    conversation = Session()
    db_session.add(conversation)
    await db_session.flush()
    timings = [(100, 200, 1000, 2000, 300, 3000, 5000), (200, 220, 1200, 2400, 320, 3400, 6000),
               (300, 260, 1400, 2800, 340, 3800, 7000)]  # fmt: skip
    found = [True, True, False]
    speakers = [(1, 0.9, "voice"), (2, 0.5, "voice"), (1, 0.7, "camera")]
    for i, (stt, retrieval, llm_first, llm, ack, tts, total) in enumerate(timings):
        label, confidence, source = speakers[i]
        db_session.add(Turn(
            session_id=conversation.id, idx=i, intent="question", user_text=f"q{i}",
            stt_ms=stt, retrieval_ms=retrieval, llm_first_ms=llm_first, llm_ms=llm, ack_ms=ack,
            tts_ms=tts, total_ms=total, found=found[i], speaker_label=label,
            speaker_confidence=confidence, speaker_source=source,
        ))  # fmt: skip
    # not counted: a failed question, a greeting, and a question stored without the found flag
    db_session.add(Turn(session_id=conversation.id, idx=3, intent="question", stt_ms=99999,
                        found=True, error="provider down"))  # fmt: skip
    db_session.add(Turn(session_id=conversation.id, idx=4, intent="greeting", stt_ms=88888))
    db_session.add(Turn(session_id=conversation.id, idx=5, intent="question", found=None))

    simulated = RobotRun(is_simulated=True, odom_config={"simulated": True})
    real = RobotRun(is_simulated=False)
    db_session.add_all([simulated, real])
    await db_session.flush()
    path = [(0, 0.0, 0.0, "start"), (1000, 1.0, 0.0, "roam"), (2000, 1.0, 1.0, "roam"),
            (3000, 0.5, 1.0, "return"), (4000, 0.0, 0.5, "return_arrived")]  # fmt: skip
    for t_ms, x, y, state in path:
        db_session.add(PoseSample(run_id=simulated.id, t_ms=t_ms, x_m=x, y_m=y, theta_rad=0.0,
                                  state=state))  # fmt: skip
    db_session.add(
        PoseSample(run_id=real.id, t_ms=0, x_m=0.0, y_m=0.0, theta_rad=0.0, state="start")
    )
    db_session.add(
        PoseSample(run_id=real.id, t_ms=500, x_m=3.0, y_m=4.0, theta_rad=0.0, state="roam")
    )
    db_session.add(
        ReturnTrial(run_id=real.id, status="measured", measured_err_m=0.25, success=True)
    )
    db_session.add(ReturnTrial(run_id=real.id, status="pending"))
    # a trial row on a simulated run must never be counted, even if one got into the table
    db_session.add(
        ReturnTrial(run_id=simulated.id, status="measured", measured_err_m=9.0, success=False)
    )
    await db_session.commit()
    return conversation.id, simulated.id, real.id


def _data_dirs(tmp_path: Path) -> tuple[Path, Path]:
    golden, latency = tmp_path / "golden", tmp_path / "latency"
    (golden / "results").mkdir(parents=True)
    latency.mkdir()
    for name, passed in (("20260101-100000", 40), ("20260102-100000", 49)):
        (golden / "results" / f"{name}.json").write_text(json.dumps({
            "model": "fake", "started": f"2026-01-0{name[7]}T10:00:00", "total": 49,
            "passed": passed, "accuracy": round(passed / 49, 3), "invented_numbers_total": 0,
            "latency_median_ms": 2000, "latency_p90_ms": 3000.0, "results": [],
        }), encoding="utf-8")  # fmt: skip
    (latency / "20260101-120000.json").write_text(json.dumps({
        "model": "fake", "streaming": True,
        "summary": {"retrieval_ms": {"median": 220.0, "p90": 250.0, "n": 8},
                    "first_audio_ms": {"median": 3100.0, "p90": 3700.0, "n": 8}},
    }), encoding="utf-8")  # fmt: skip
    (latency / "20260102-120000.json").write_text(json.dumps({
        "mode": "dialogue", "model": "fake", "audio_stream": True,
        "summary": {"first_audio_ms": {"median": 2900.0, "p90": 3300.0, "n": 8},
                    "tts_first_ms": {"median": None, "p90": None, "n": 0}},
    }), encoding="utf-8")  # fmt: skip
    (latency / "broken.json").write_text("{not json", encoding="utf-8")
    return golden, latency


async def test_export_writes_all_files_with_the_seeded_numbers(db_session, tmp_path) -> None:
    session_id, sim_id, real_id = await _seed(db_session)
    golden, latency = _data_dirs(tmp_path)
    out = tmp_path / "out"
    files = await export_report(db_session, out, golden, latency,
                                session_ids=[session_id], run_ids=[sim_id, real_id])  # fmt: skip

    names = {p.name for p in files}
    sim_png, real_png = f"trajectory_run_{sim_id}_SIMULATED.png", f"trajectory_run_{real_id}.png"
    assert names == FIXED_FILES | {sim_png, real_png}
    assert len(files) == len(names) and all(p.parent == out and p.is_file() for p in files)
    assert {p.name for p in out.iterdir()} == names  # nothing written that is not reported
    for p in files:
        if p.suffix == ".png":
            data = p.read_bytes()
            assert data.startswith(PNG_SIGNATURE) and len(data) > 5000, p.name

    stages = {r["stage"]: r for r in _rows(out / "latency_stages.csv")}
    assert list(stages) == [
        "stt_ms",
        "retrieval_ms",
        "llm_first_ms",
        "llm_ms",
        "ack_ms",
        "tts_ms",
        "total_ms",
    ]
    stt = stages["stt_ms"]  # the failed turn (99999) and the greeting (88888) are not in it
    assert (int(stt["n"]), float(stt["median_ms"]), float(stt["p90_ms"])) == (3, 200, 300)
    assert (float(stt["min_ms"]), float(stt["max_ms"])) == (100, 300)
    assert float(stages["retrieval_ms"]["median_ms"]) == 220
    assert float(stages["llm_first_ms"]["median_ms"]) == 1200
    assert float(stages["tts_ms"]["median_ms"]) == 3400
    assert (
        float(stages["total_ms"]["median_ms"]) == 6000
        and float(stages["total_ms"]["p90_ms"]) == 7000
    )

    assert _rows(out / "found_rate.csv") == [
        {"found": "2", "not_found": "1", "unknown": "1", "found_rate": "0.667"}
    ]

    speakers = {r["speaker_source"]: r for r in _rows(out / "speakers.csv")}
    assert set(speakers) == {"voice", "camera"}
    voice = speakers["voice"]
    assert (int(voice["n"]), float(voice["median_confidence"])) == (2, 0.7)
    assert (float(voice["min_confidence"]), float(voice["max_confidence"])) == (0.5, 0.9)

    runs = _rows(out / "latency_runs.csv")
    assert {r["file"] for r in runs} == {"20260101-120000.json", "20260102-120000.json"}
    first = next(
        r for r in runs if r["file"].startswith("20260101") and r["stage"] == "first_audio_ms"
    )
    assert (float(first["median_ms"]), float(first["p90_ms"]), first["mode"]) == (
        3100,
        3700,
        "pipeline",
    )
    empty = next(r for r in runs if r["stage"] == "tts_first_ms")
    assert empty["median_ms"] == "" and empty["mode"] == "dialogue"  # missing stays empty, not 0

    evaluation = _rows(out / "evaluation_runs.csv")
    assert [(r["passed"], r["total"], r["pass_rate_pct"]) for r in evaluation] == [
        ("40", "49", "81.6"), ("49", "49", "100.0")
    ]  # fmt: skip

    trajectories = {int(r["run_id"]): r for r in _rows(out / "trajectories.csv")}
    assert set(trajectories) == {sim_id, real_id}
    sim = trajectories[sim_id]
    assert (
        sim["is_simulated"] == "True"
        and int(sim["samples"]) == 5
        and float(sim["duration_s"]) == 4.0
    )
    assert float(sim["path_length_m"]) == 3.207 and float(sim["end_distance_from_start_m"]) == 0.5
    real = trajectories[real_id]
    assert real["is_simulated"] == "False" and float(real["path_length_m"]) == 5.0

    trials = _rows(out / "return_trials.csv")  # the pending one and the simulated one are left out
    assert [(int(r["run_id"]), float(r["measured_err_m"]), r["success"]) for r in trials] == [
        (real_id, 0.25, "True")
    ]
    assert _rows(out / "return_trials_summary.csv") == [
        {
            "n": "1",
            "success_rate": "1.0",
            "mean_err_m": "0.25",
            "median_err_m": "0.25",
            "max_err_m": "0.25",
        }
    ]

    readme = (out / "README.md").read_text(encoding="utf-8")
    assert all(f"`{name}`" in readme for name in names - {"README.md"})
    assert "SIMULATED run, not a physical result" in readme


async def test_simulated_runs_are_marked_in_the_file_name(db_session, tmp_path) -> None:
    _, sim_id, real_id = await _seed(db_session)
    files = await export_report(db_session, tmp_path / "out", tmp_path / "none", tmp_path / "none",
                                session_ids=[], run_ids=[sim_id, real_id])  # fmt: skip
    by_run = {p.name: p for p in files if p.name.startswith("trajectory_run_")}
    assert set(by_run) == {
        f"trajectory_run_{sim_id}_SIMULATED.png",
        f"trajectory_run_{real_id}.png",
    }
    assert "SIMULATED" not in f"trajectory_run_{real_id}.png"
    assert trajectory_file_name(7, True) == "trajectory_run_7_SIMULATED.png"
    assert trajectory_file_name(7, False) == "trajectory_run_7.png"


async def test_export_without_any_data_writes_the_no_data_outputs(db_session, tmp_path) -> None:
    golden, latency = tmp_path / "golden", tmp_path / "latency"
    golden.mkdir()
    latency.mkdir()
    out = tmp_path / "out"
    files = await export_report(db_session, out, golden, latency, session_ids=[], run_ids=[])

    assert {p.name for p in files} == FIXED_FILES  # no trajectory pictures without runs
    for p in files:
        if p.suffix == ".png":
            assert p.read_bytes().startswith(PNG_SIGNATURE) and p.stat().st_size > 2000, p.name
    for name in ("latency_runs.csv", "evaluation_runs.csv", "speakers.csv", "trajectories.csv",
                 "return_trials.csv"):  # fmt: skip
        lines = (out / name).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1 and "," in lines[0], name  # the header only
    stages = _rows(out / "latency_stages.csv")
    assert len(stages) == 7 and all(r["n"] == "0" and r["median_ms"] == "" for r in stages)
    assert _rows(out / "found_rate.csv") == [
        {"found": "0", "not_found": "0", "unknown": "0", "found_rate": ""}
    ]
    assert _rows(out / "return_trials_summary.csv") == [
        {"n": "0", "success_rate": "", "mean_err_m": "", "median_err_m": "", "max_err_m": ""}
    ]
    assert "Simulated runs are never counted" in NO_REAL_TRIALS
