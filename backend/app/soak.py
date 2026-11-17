"""30-minute soak test: a SIMULATED mixed session against the backend (sprint 3 S9).

*** Simulation. The robot is the software stand-in (app/telemetry/sim_robot.py), and the backend
must run with FAKE AI providers (tests/preview_app.py). The numbers say how the backend process
behaves under a long mixed session: memory, latency drift, errors. They say nothing about the
real robot, the real providers or the network at the demo site. ***

What runs, all at once, for `--minutes` (default 30, the project's demo target):
- a simulated robot: `robot.status` every second, telemetry while it roams, answers to commands;
- conversations: a typed question every 20 s (every fourth one asks for a program explainer);
- an "operator": Start roaming / Pause / Resume / Return to start requests through the admin API;
- a monitor: the backend's memory and the response time of /health every 15 s.

Start a preview backend on a scratch database, then run this (see docs/HOW_TO_START.md):
    python -m app.ingestion.cli soak --minutes 30 --url ws://HOST:8000/ws/robot --admin-url http://HOST:8000
`--minutes 0.5` is enough for a quick check.
"""

import asyncio
import contextlib
import json
import os
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.telemetry.sim_robot import SimRobotReport, SimulatedRobot, run_sim_robot

RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "soak"
QUESTIONS = [
    "كم ساعة معتمدة بدها هندسة الأنظمة الذكية؟",
    "وين بيشتغلوا خريجين هندسة الأنظمة الذكية؟",
    "هندسة الطاقة المتجددة كم ساعة؟",
    "احكيلي عن تخصص هندسة العمارة",
    "How many credit hours is Interior Design?",
    "شو الحد الأعلى للساعات بالفصل؟",
    "في تدريب ميداني بالتصميم الجرافيكي؟",
    "وين مكتب العميد؟",
]
COMMAND_CYCLE = ("start_roaming", "pause", "resume", "return_to_start", "pause", "set_mode")


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def summarize(report: SimRobotReport, memory: list[tuple[float, float]], health_ms: list[float],
              commands: list[dict[str, Any]], minutes: float) -> dict[str, Any]:  # fmt: skip
    """Memory growth, latency drift and errors of one soak run."""
    turns = [t for t in report.turns if t.get("wall_ms") is not None]
    third = max(1, len(turns) // 3)
    first, last = [t["wall_ms"] for t in turns[:third]], [t["wall_ms"] for t in turns[-third:]]
    mem = [m for _, m in memory]
    # growth: the median of the last quarter against the median of the second quarter, so the
    # warm-up at the start (imports, caches, connection pool) is not counted as a leak
    quarter = max(1, len(mem) // 4)
    settled, final = mem[quarter : 2 * quarter] or mem[:1], mem[-quarter:]
    accepted = sum(1 for c in commands if c.get("accepted") is True)
    refused = sum(1 for c in commands if c.get("accepted") is False)
    timed_out = sum(1 for c in commands if c.get("timed_out"))
    return {
        "simulated": True,
        "note": (
            "SIMULATED session with fake AI providers: backend behaviour only, not a physical or "
            "provider result"
        ),
        "planned_minutes": minutes,
        "seconds": report.seconds,
        "conversations": report.conversations,
        "turns_timed": len(turns),
        "status_messages": report.status_sent,
        "telemetry_samples": report.telemetry_samples,
        "robot_runs": len(report.runs),
        "commands": {
            "sent": len(commands),
            "accepted": accepted,
            "refused": refused,
            "timed_out": timed_out,
            "http_errors": sum(1 for c in commands if c.get("http_error")),
        },
        "answer_wall_ms": {
            "median_all": _median([t["wall_ms"] for t in turns]),
            "median_first_third": _median(first),
            "median_last_third": _median(last),
            "drift_ms": round(statistics.median(last) - statistics.median(first), 1)
            if first and last
            else None,
            "max": max((t["wall_ms"] for t in turns), default=None),
        },
        "health_ms": {
            "median": _median(health_ms),
            "max": round(max(health_ms), 1) if health_ms else None,
        },
        "memory_mb": {
            "start": mem[0] if mem else None,
            "end": mem[-1] if mem else None,
            "max": max(mem) if mem else None,
            "settled_median": _median(settled),
            "final_median": _median(final),
            "growth_after_warmup": round(statistics.median(final) - statistics.median(settled), 1)
            if mem
            else None,
            "samples": len(mem),
        },
        "errors": report.errors[:50],
        "error_count": len(report.errors),
    }


def format_summary(s: dict[str, Any]) -> str:
    m, a, c = s["memory_mb"], s["answer_wall_ms"], s["commands"]
    return "\n".join(
        [
            "*** SIMULATED soak run (simulated robot, fake AI providers): not a physical result "
            "***",
            f"duration            {s['seconds'] / 60:.1f} min (planned {s['planned_minutes']:g})",
            f"conversations       {s['conversations']} ({s['turns_timed']} timed turns)",
            f"robot               {s['status_messages']} status messages, {s['telemetry_samples']} "
            f"pose samples, {s['robot_runs']} simulated run(s)",
            f"commands            {c['sent']} sent: {c['accepted']} accepted, {c['refused']} "
            "refused, "
            f"{c['timed_out']} without answer, {c['http_errors']} HTTP errors",
            f"answer time (ms)    median {a['median_all']}, first third {a['median_first_third']}, "
            f"last third {a['median_last_third']}, drift {a['drift_ms']}, max {a['max']}",
            f"/health (ms)        median {s['health_ms']['median']}, max {s['health_ms']['max']}",
            f"backend memory (MB) start {m['start']}, end {m['end']}, max {m['max']}; after "
            "warm-up "
            f"{m['settled_median']} -> {m['final_median']} (growth {m['growth_after_warmup']})",
            f"errors              {s['error_count']}"
            + (": " + "; ".join(s["errors"][:5]) if s["errors"] else ""),
        ]
    )


async def main(minutes: float, url: str, admin_url: str, out: str | None = None,
               allow_real_providers: bool = False) -> dict[str, Any] | None:  # fmt: skip
    token = os.environ.get("SOAK_ADMIN_TOKEN") or os.environ.get("ADMIN_TOKEN") or ""
    headers = {"X-Admin-Token": token}
    seconds = minutes * 60
    async with httpx.AsyncClient(base_url=admin_url, headers=headers, timeout=15) as http:
        try:
            info = await http.get("/preview-info")
        except httpx.HTTPError as e:
            print(f"Cannot reach {admin_url}: {e}")
            return None
        if info.status_code != 200 and not allow_real_providers:
            print(
                "This backend is not the preview app (fake providers). The soak test asks hundreds "
                "of\n"
                "questions: against the real backend it would cost money and fill the real "
                "database.\n"
                'Start the preview backend first (docs/HOW_TO_START.md, "Soak test").'
            )
            return None
        if (await http.get("/api/admin/system")).status_code != 200:
            print(
                "The admin token is not accepted (set SOAK_ADMIN_TOKEN to the preview backend's "
                "ADMIN_TOKEN)."
            )
            return None
        print(f"*** SIMULATED soak test: {minutes:g} min against {admin_url} (fake providers) ***")
        robot = SimulatedRobot(seed=7)
        memory: list[tuple[float, float]] = []
        health_ms: list[float] = []
        commands: list[dict[str, Any]] = []
        started = time.monotonic()

        async def monitor() -> None:
            while True:
                t0 = time.perf_counter()
                with contextlib.suppress(httpx.HTTPError):
                    await http.get("/health")
                    health_ms.append((time.perf_counter() - t0) * 1000)
                with contextlib.suppress(httpx.HTTPError, KeyError, TypeError):
                    mb = (await http.get("/api/admin/system")).json()["memory_mb"]
                    if mb is not None:
                        memory.append((round(time.monotonic() - started, 1), float(mb)))
                await asyncio.sleep(15)

        async def operator() -> None:
            n = 0
            await asyncio.sleep(5)
            while True:
                cmd = COMMAND_CYCLE[n % len(COMMAND_CYCLE)]
                body: dict[str, Any] = {"cmd": cmd}
                if cmd == "set_mode":
                    body["args"] = {"mode": f"demo-{n}"}
                try:
                    r = await http.post("/api/admin/robot/command", json=body)
                    commands.append(
                        r.json()
                        if r.status_code == 200
                        else {"cmd": cmd, "http_error": r.status_code}
                    )
                except httpx.HTTPError as e:
                    commands.append({"cmd": cmd, "http_error": str(e)})
                n += 1
                await asyncio.sleep(25)

        extra = [asyncio.create_task(monitor()), asyncio.create_task(operator())]
        try:
            report = await run_sim_robot(
                url, seconds, robot=robot, questions=QUESTIONS, converse_every_s=20
            )
        finally:
            for task in extra:
                task.cancel()
            for task in extra:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
    summary = summarize(report, memory, health_ms, commands, minutes)
    print(format_summary(summary))
    out_dir = Path(out) if out else RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(
        json.dumps(
            {**summary, "memory_series": memory, "turns": report.turns},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"Saved: {path}")
    return summary
