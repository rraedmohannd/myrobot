"""Sprint 3 S9: the soak test summary (memory growth, latency drift, errors). The soak itself
is a long simulated session against a preview backend; here only its arithmetic is tested."""

from app.soak import COMMAND_CYCLE, QUESTIONS, format_summary, summarize
from app.telemetry.sim_robot import SimRobotReport


def _report() -> SimRobotReport:
    r = SimRobotReport(
        seconds=1800.0, status_sent=1800, telemetry_samples=9000, conversations=9, runs=[1, 2]
    )
    r.turns = [
        {"wall_ms": ms, "at_s": i * 20.0}
        for i, ms in enumerate([100, 110, 120, 200, 210, 220, 300, 310, 320])
    ]
    r.errors = ["internal: boom"]
    return r


def test_summary_reports_drift_memory_growth_and_errors() -> None:
    memory = [(i * 15.0, mb) for i, mb in enumerate([80, 150, 160, 160, 161, 161, 163, 163])]
    commands = [
        {"accepted": True},
        {"accepted": False, "reason": "busy"},
        {"timed_out": True, "accepted": None},
        {"cmd": "pause", "http_error": 503},
    ]
    s = summarize(_report(), memory, [5.0, 7.0, 30.0], commands, 30)
    assert s["simulated"] is True and "SIMULATED" in s["note"]
    assert s["conversations"] == 9 and s["turns_timed"] == 9 and s["robot_runs"] == 2
    a = s["answer_wall_ms"]
    assert (a["median_first_third"], a["median_last_third"], a["drift_ms"], a["max"]) == (
        110,
        310,
        200,
        320,
    )
    m = s["memory_mb"]
    # the jump from 80 MB at the start is warm-up, not a leak: growth compares the settled level
    assert (m["start"], m["end"], m["max"]) == (80, 163, 163)
    assert m["settled_median"] == 160 and m["final_median"] == 163 and m["growth_after_warmup"] == 3
    assert s["commands"] == {
        "sent": 4,
        "accepted": 1,
        "refused": 1,
        "timed_out": 1,
        "http_errors": 1,
    }
    assert s["health_ms"] == {"median": 7.0, "max": 30.0}
    assert s["error_count"] == 1 and s["errors"] == ["internal: boom"]
    text = format_summary(s)
    assert text.startswith("*** SIMULATED soak run") and "drift 200" in text and "growth 3" in text


def test_summary_of_an_empty_run_does_not_crash() -> None:
    s = summarize(SimRobotReport(), [], [], [], 0.5)
    assert (
        s["answer_wall_ms"]["median_all"] is None and s["memory_mb"]["growth_after_warmup"] is None
    )
    assert s["error_count"] == 0 and "SIMULATED" in format_summary(s)
    assert len(QUESTIONS) >= 5 and set(COMMAND_CYCLE) <= {
        "start_roaming",
        "pause",
        "resume",
        "return_to_start",
        "set_mode",
    }
