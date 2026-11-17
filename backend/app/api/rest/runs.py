"""Robot runs for the admin (demo sprint M6): trajectory, events, return trials and their stats.

Simulated runs are always marked. Return trials are physical tape measurements: they can only
be entered for real runs, and the statistics only count real runs (CLAUDE.md rule 5).
"""

import statistics
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import SessionDep, require_admin
from app.db.models import Event, PoseSample, ReturnTrial, RobotRun
from app.telemetry.store import ReturnTrialIn, TelemetryError, add_return_trial

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

SIMULATED_LABEL = "SIMULATED — not a physical result"
RETURN_STATES = ("return",)  # pose_samples.state starts with this on the way back


def _run(run: RobotRun, samples: int = 0, events: int = 0, trials: int = 0) -> dict[str, Any]:
    return {
        "id": run.id, "started_at": run.started_at, "ended_at": run.ended_at,
        "is_simulated": run.is_simulated,
        "label": SIMULATED_LABEL if run.is_simulated else "Physical run",
        "notes": run.notes, "samples": samples, "events": events, "return_trials": trials,
    }  # fmt: skip


def _trial(t: ReturnTrial) -> dict[str, Any]:
    return {
        "id": t.id, "run_id": t.run_id, "measured_err_m": t.measured_err_m,
        "measured_heading_err_deg": t.measured_heading_err_deg, "success": t.success,
        "odom_end_x": t.odom_end_x, "odom_end_y": t.odom_end_y, "notes": t.notes,
        "status": t.status, "command_id": t.command_id,
    }  # fmt: skip


async def _counts(session, model, run_ids: list[int]) -> dict[int, int]:
    if not run_ids:
        return {}
    rows = await session.execute(
        select(model.run_id, func.count()).where(model.run_id.in_(run_ids)).group_by(model.run_id)
    )
    return dict(rows.all())


@router.get("/runs")
async def list_runs(
    session: SessionDep, limit: int = Query(default=50, ge=1, le=200)
) -> dict[str, Any]:
    runs = (await session.scalars(select(RobotRun).order_by(RobotRun.id.desc()).limit(limit))).all()
    ids = [r.id for r in runs]
    samples = await _counts(session, PoseSample, ids)
    events = await _counts(session, Event, ids)
    trials = await _counts(session, ReturnTrial, ids)
    return {
        "items": [
            _run(r, samples.get(r.id, 0), events.get(r.id, 0), trials.get(r.id, 0)) for r in runs
        ]
    }


@router.get("/runs/{run_id}")
async def get_run(
    run_id: int, session: SessionDep, max_points: int = Query(default=1500, ge=10, le=5000)
) -> dict[str, Any]:
    run = await session.get(RobotRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    samples = (
        await session.scalars(
            select(PoseSample)
            .where(PoseSample.run_id == run_id)
            .order_by(PoseSample.t_ms, PoseSample.id)
        )
    ).all()
    events = (
        await session.scalars(
            select(Event).where(Event.run_id == run_id).order_by(Event.t_ms, Event.id)
        )
    ).all()
    trials = (
        await session.scalars(
            select(ReturnTrial).where(ReturnTrial.run_id == run_id).order_by(ReturnTrial.id)
        )
    ).all()
    stride = max(
        1, -(-len(samples) // max_points)
    )  # thin long runs for the plot; keep the last point
    kept = samples[::stride]
    if samples and kept[-1] is not samples[-1]:
        kept.append(samples[-1])
    points = []
    for s in kept:
        point: dict[str, Any] = {
            "t_ms": s.t_ms,
            "x": s.x_m,
            "y": s.y_m,
            "theta": s.theta_rad,
            "state": s.state,
            "phase": "return" if (s.state or "").startswith(RETURN_STATES) else "outbound",
        }
        true_pose = (s.imu or {}).get("sim_true_pose") if run.is_simulated else None
        if true_pose:
            point["sim_true"] = true_pose[:2]
        points.append(point)
    return {
        **_run(run, len(samples), len(events), len(trials)),
        "odom_config": run.odom_config,
        "duration_s": round(samples[-1].t_ms / 1000, 1) if samples else None,
        "points": points,
        "events": [{"t_ms": e.t_ms, "kind": e.kind, "data": e.data} for e in events],
        "trials": [_trial(t) for t in trials],
        "can_add_return_trial": not run.is_simulated,
    }


@router.post("/runs/{run_id}/return-trials", status_code=status.HTTP_201_CREATED)
async def create_return_trial(
    run_id: int, body: ReturnTrialIn, session: SessionDep
) -> dict[str, Any]:
    try:
        return _trial(await add_return_trial(session, run_id, body))
    except TelemetryError as e:
        code = status.HTTP_404_NOT_FOUND if e.code == "not_found" else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(e)) from e


@router.get("/return-trials/stats")
async def return_trial_stats(session: SessionDep) -> dict[str, Any]:
    """Statistics of physical return trials (real runs only; simulated runs cannot have any)."""
    rows = (
        await session.scalars(
            select(ReturnTrial)
            .join(RobotRun, RobotRun.id == ReturnTrial.run_id)
            .where(RobotRun.is_simulated.is_(False), ReturnTrial.status == "measured")
            .order_by(ReturnTrial.id)
        )
    ).all()
    errors = [t.measured_err_m for t in rows if t.measured_err_m is not None]
    simulated_runs = await session.scalar(
        select(func.count()).select_from(RobotRun).where(RobotRun.is_simulated.is_(True))
    )
    real_runs = await session.scalar(
        select(func.count()).select_from(RobotRun).where(RobotRun.is_simulated.is_(False))
    )
    return {
        "trials": len(rows),
        "successes": sum(1 for t in rows if t.success),
        "success_rate": round(sum(1 for t in rows if t.success) / len(rows), 3) if rows else None,
        "mean_err_m": round(statistics.mean(errors), 3) if errors else None,
        "median_err_m": round(statistics.median(errors), 3) if errors else None,
        "max_err_m": round(max(errors), 3) if errors else None,
        "real_runs": real_runs or 0,
        "simulated_runs": simulated_runs or 0,
        "note": "Physical tape measurements on real runs only. Simulated runs are never counted."
        if rows
        else "No physical return trials have been recorded yet. Simulated runs are never counted.",
    }
