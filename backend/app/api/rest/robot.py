"""Live robot status and command requests for the admin "Robot" tab (sprint 3 S2).

Commands are REQUESTS (CLAUDE.md rule 4): the robot answers with an ack and may refuse. When no
robot is connected nothing is sent and the API answers 503.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from scr_protocol.messages import CommandName
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import SessionDep, require_admin
from app.db.models import Event, ReturnTrial, RobotRun
from app.db.session import get_sessionmaker
from app.services.robot_hub import RobotHub, RobotOffline, get_robot_hub
from app.telemetry.store import ReturnTrialIn

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

HubDep = Annotated[RobotHub, Depends(get_robot_hub)]
MakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]


class CommandRequest(BaseModel):
    cmd: CommandName
    args: dict[str, Any] | None = None
    robot_id: str | None = Field(default=None, max_length=64)


@router.get("/robot")
async def robot_state(hub: HubDep, session: SessionDep) -> dict[str, Any]:
    """What the admin page polls about once per second."""
    snapshot = hub.snapshot()
    pending = (
        await session.execute(
            select(ReturnTrial, RobotRun)
            .join(RobotRun, RobotRun.id == ReturnTrial.run_id)
            .where(ReturnTrial.status == "pending")
            .order_by(ReturnTrial.id.desc())
            .limit(20)
        )
    ).all()
    snapshot["pending_return_trials"] = [
        {"id": t.id, "run_id": t.run_id, "command_id": t.command_id, "created_at": t.created_at}
        for t, _ in pending
    ]
    return snapshot


@router.post("/robot/command")
async def robot_command(
    body: CommandRequest, hub: HubDep, sessionmaker: MakerDep
) -> dict[str, Any]:
    """Ask the robot to do something and wait for its ack (a few seconds at most). The answer
    says whether the robot accepted, refused (with its reason) or did not answer in time."""
    if body.cmd == "set_mode" and not (body.args and isinstance(body.args.get("mode"), str)):
        raise HTTPException(422, 'set_mode needs args: {"mode": "..."}')
    try:
        return await hub.send_command(sessionmaker, body.cmd, body.args, robot_id=body.robot_id)
    except RobotOffline as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e


@router.get("/robot/commands")
async def command_history(session: SessionDep, limit: int = 50) -> dict[str, Any]:
    """Stored command and ack events, newest first (they survive a backend restart)."""
    rows = (
        await session.scalars(
            select(Event)
            .where(Event.kind.in_(("command", "command_ack")))
            .order_by(Event.id.desc())
            .limit(max(1, min(limit, 200)))
        )
    ).all()
    return {
        "items": [
            {
                "id": e.id,
                "run_id": e.run_id,
                "kind": e.kind,
                "data": e.data,
                "created_at": e.created_at,
            }
            for e in rows
        ]
    }


@router.put("/return-trials/{trial_id}")
async def measure_return_trial(
    trial_id: int, body: ReturnTrialIn, session: SessionDep
) -> dict[str, Any]:
    """Type in the tape measurement of a pending return trial (real runs only)."""
    trial = await session.get(ReturnTrial, trial_id)
    if trial is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Return trial not found")
    run = await session.get(RobotRun, trial.run_id)
    if run is None or run.is_simulated:  # cannot happen through the hub; kept as a hard rule
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Return trials are physical measurements of real runs."
        )
    for key, value in body.model_dump().items():
        setattr(trial, key, value)
    trial.status = "measured"
    await session.commit()
    return {
        "id": trial.id, "run_id": trial.run_id, "status": trial.status,
        "measured_err_m": trial.measured_err_m, "success": trial.success,
    }  # fmt: skip


@router.delete("/return-trials/{trial_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_pending_trial(trial_id: int, session: SessionDep) -> None:
    """Remove a pending trial that was never measured (for example an aborted return)."""
    trial = await session.get(ReturnTrial, trial_id)
    if trial is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Return trial not found")
    if trial.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a pending trial can be discarded.")
    await session.delete(trial)
    await session.commit()
