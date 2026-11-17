"""Admin "Bring-up" tab (sprint 5 S6.3): live diagnostics, calibration records, the
docs/BRINGUP.md checklist with ticks stored in the database, and the power chart data.

Display and record keeping only: nothing here commands the robot (CLAUDE.md rule 4). Simulated
calibration records and simulated runs are always labelled (rule 5).
"""

from datetime import UTC, datetime
from pathlib import Path
from secrets import compare_digest
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import SessionDep, require_admin
from app.core.config import get_settings
from app.db.models import BringupStep, PoseSample, RobotRun
from app.services import auth
from app.services.bringup import (
    SIMULATED_CALIBRATION_LABEL,
    calibration_records,
    latest_per_key,
    read_checklist,
)
from app.services.robot_hub import RobotHub, get_robot_hub

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

HubDep = Annotated[RobotHub, Depends(get_robot_hub)]
SIMULATED_RUN_LABEL = "SIMULATED — not a physical result"


async def admin_actor(
    request: Request,
    session: SessionDep,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> str:
    """Who ticks a step: the logged-in admin user's name, else "token" (the shared ADMIN_TOKEN).
    `require_admin` has already checked the access."""
    user = await auth.session_user(session, request.cookies.get(auth.COOKIE))
    if user is not None:
        return user.username
    expected = get_settings().admin_token
    token = expected.get_secret_value() if expected is not None else ""
    if x_admin_token and token and compare_digest(x_admin_token, token):
        return "token"
    return "unknown"


def checklist_path() -> Path:
    return Path(get_settings().bringup_doc_path)


class StepTick(BaseModel):
    done: bool
    note: str | None = Field(default=None, max_length=1000)


def _step(row: BringupStep | None) -> dict[str, Any]:
    if row is None:
        return {"done": False, "done_by": None, "done_at": None, "note": None}
    return {"done": row.done, "done_by": row.done_by, "done_at": row.done_at, "note": row.note}


async def _checklist(session: SessionDep) -> dict[str, Any]:
    path = checklist_path()
    checklist = read_checklist(path)
    if checklist is None:
        return {"exists": False, "path": str(path), "sections": [], "problems": [], "done": 0,
                "total": 0}  # fmt: skip
    rows = {
        r.step_id: r
        for r in (
            await session.scalars(
                select(BringupStep).where(BringupStep.step_id.in_(checklist.step_ids))
            )
        ).all()
    }
    sections = [
        {
            "number": sec.number,
            "title": sec.title,
            "steps": [{"id": s.id, "text": s.text, **_step(rows.get(s.id))} for s in sec.steps],
        }
        for sec in checklist.sections
    ]
    return {
        "exists": True, "path": str(path), "sections": sections, "problems": checklist.problems,
        "done": sum(1 for r in rows.values() if r.done), "total": len(checklist.step_ids),
    }  # fmt: skip


async def _power_runs(session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(RobotRun, func.count(PoseSample.id))
            .join(PoseSample, PoseSample.run_id == RobotRun.id)
            .where(PoseSample.power.is_not(None))
            .group_by(RobotRun.id)
            .order_by(RobotRun.id.desc())
            .limit(50)
        )
    ).all()
    return [
        {
            "id": run.id, "started_at": run.started_at, "is_simulated": run.is_simulated,
            "label": SIMULATED_RUN_LABEL if run.is_simulated else "Physical run",
            "power_samples": n,
        }
        for run, n in rows
    ]  # fmt: skip


@router.get("/bringup")
async def bringup_state(session: SessionDep, hub: HubDep) -> dict[str, Any]:
    """Everything the Bring-up tab shows (polled while the tab is open)."""
    robot = hub.snapshot()["robot"]
    live = None
    if robot is not None:
        s = robot["status"]
        live = {
            "robot_id": robot["robot_id"], "is_simulated": robot["is_simulated"],
            "label": robot["label"], "status_age_s": robot["status_age_s"],
            "state": s.get("state"), "mode": s.get("mode"), "faults": s.get("faults") or [],
            "sensors": s.get("sensors") or {}, "firmware": s.get("firmware"),
            "health": s.get("health"), "calibration": s.get("calibration"),
            "battery": {k: s.get(k) for k in ("battery_pct", "battery_v", "battery_a",
                                              "energy_wh", "battery_warning")},
        }  # fmt: skip
    real, simulated = await calibration_records(session)
    return {
        "live": live,
        "checklist": await _checklist(session),
        "calibration": {
            "real": latest_per_key(real),
            "simulated": latest_per_key(simulated),
            "simulated_label": SIMULATED_CALIBRATION_LABEL,
            "note": "Values reported by the robot's calibration file (calibration.report). A new "
            "row is stored only when a value changes; earlier values are the history.",
        },
        "power_runs": await _power_runs(session),
    }


@router.put("/bringup/steps/{step_id}")
async def tick_step(
    step_id: str,
    body: StepTick,
    session: SessionDep,
    actor: Annotated[str, Depends(admin_actor)],
) -> dict[str, Any]:
    """Tick or untick one checklist step (who and when are stored)."""
    checklist = read_checklist(checklist_path())
    if checklist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{checklist_path()} does not exist")
    if step_id not in checklist.step_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No step '{step_id}' in the checklist")
    row = await session.get(BringupStep, step_id)
    if row is None:
        row = BringupStep(step_id=step_id)
        session.add(row)
    row.done = body.done
    row.note = body.note
    row.done_by = actor if body.done else None
    row.done_at = datetime.now(UTC) if body.done else None
    await session.commit()
    return {"id": step_id, **_step(row)}


@router.get("/runs/{run_id}/power")
async def run_power(
    run_id: int, session: SessionDep, max_points: int = Query(default=1500, ge=10, le=5000)
) -> dict[str, Any]:
    """Battery V / A / Wh over the run time (samples that carry `power`)."""
    run = await session.get(RobotRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    rows = (
        await session.execute(
            select(PoseSample.t_ms, PoseSample.power)
            .where(PoseSample.run_id == run_id, PoseSample.power.is_not(None))
            .order_by(PoseSample.t_ms, PoseSample.id)
        )
    ).all()
    stride = max(1, -(-len(rows) // max_points))
    kept = rows[::stride]
    if rows and kept[-1] is not rows[-1]:
        kept.append(rows[-1])
    return {
        "id": run.id, "is_simulated": run.is_simulated,
        "label": SIMULATED_RUN_LABEL if run.is_simulated else "Physical run",
        "samples": len(rows),
        "points": [
            {"t_s": round(t / 1000, 2), "v": p.get("v"), "a": p.get("a"), "wh": p.get("wh")}
            for t, p in kept
        ],
    }  # fmt: skip
