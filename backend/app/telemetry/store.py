"""Storage of robot runs: pose samples, events and physically measured return trials.

Used by the /ws/robot telemetry messages (docs/PROTOCOL.md) and by the `simulate-run` command.
Rule 5: a simulated run can never receive a return trial, because return trials are tape
measurements on the floor.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Event, PoseSample, ReturnTrial, RobotRun

MAX_SAMPLES_PER_MESSAGE = 1000


class TelemetryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PowerIn(BaseModel):
    """Battery reading of a sample (sprint 5): volts, amps (positive = discharging), Wh used."""

    v: float | None = Field(default=None, ge=0, le=60)
    a: float | None = Field(default=None, ge=-100, le=100)
    wh: float | None = None


class SampleIn(BaseModel):
    t_ms: int = Field(ge=0)
    x_m: float
    y_m: float
    theta_rad: float
    v_mps: float | None = None
    omega_rps: float | None = None
    enc: dict[str, Any] | None = None
    imu: dict[str, Any] | None = None
    state: str | None = Field(default=None, max_length=32)
    power: PowerIn | None = None


class EventIn(BaseModel):
    t_ms: int = Field(ge=0)
    kind: str = Field(min_length=1, max_length=32)
    data: dict[str, Any] | None = None


class ReturnTrialIn(BaseModel):
    """Physical measurement with a tape, independent of the odometry (ODOMETRY.md section 6)."""

    measured_err_m: float = Field(ge=0, le=50)
    measured_heading_err_deg: float | None = Field(default=None, ge=-180, le=180)
    success: bool
    odom_end_x: float | None = None
    odom_end_y: float | None = None
    notes: str | None = Field(default=None, max_length=500)


async def start_run(
    session: AsyncSession, is_simulated: bool, odom_config: dict[str, Any] | None = None,
    notes: str | None = None,
) -> RobotRun:  # fmt: skip
    run = RobotRun(is_simulated=is_simulated, odom_config=odom_config, notes=notes)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def add_samples(session: AsyncSession, run_id: int, samples: list[dict[str, Any]]) -> int:
    if len(samples) > MAX_SAMPLES_PER_MESSAGE:
        raise TelemetryError(
            "bad_message", f"at most {MAX_SAMPLES_PER_MESSAGE} samples per message"
        )
    try:
        parsed = [SampleIn.model_validate(s) for s in samples]
    except ValidationError as e:
        raise TelemetryError("bad_message", f"invalid sample: {e.errors()[0]['msg']}") from e
    session.add_all([
        PoseSample(run_id=run_id, **s.model_dump(exclude={"power"}),
                   power=s.power.model_dump(exclude_none=True) or None if s.power else None)
        for s in parsed
    ])  # fmt: skip
    await session.commit()
    return len(parsed)


# Power-design evidence (sprint 5): these warnings are stored even when no run is open.
WARNING_EVENTS = ("under_voltage", "battery_warning")


async def add_event(session: AsyncSession, run_id: int | None, event: dict[str, Any]) -> Event:
    try:
        parsed = EventIn.model_validate(event)
    except ValidationError as e:
        raise TelemetryError("bad_message", f"invalid event: {e.errors()[0]['msg']}") from e
    row = Event(run_id=run_id, **parsed.model_dump())
    session.add(row)
    await session.commit()
    return row


async def end_run(session: AsyncSession, run_id: int) -> dict[str, int]:
    run = await session.get(RobotRun, run_id)
    if run is not None and run.ended_at is None:
        run.ended_at = datetime.now(UTC)
        await session.commit()
    samples = await session.scalar(
        select(func.count()).select_from(PoseSample).where(PoseSample.run_id == run_id)
    )
    events = await session.scalar(
        select(func.count()).select_from(Event).where(Event.run_id == run_id)
    )
    return {"samples": samples or 0, "events": events or 0}


async def add_return_trial(session: AsyncSession, run_id: int, trial: ReturnTrialIn) -> ReturnTrial:
    run = await session.get(RobotRun, run_id)
    if run is None:
        raise TelemetryError("not_found", "Run not found")
    if run.is_simulated:
        raise TelemetryError(
            "simulated",
            "This run is SIMULATED. Return trials are physical tape measurements and can only "
            "be entered for real runs.",
        )
    row = ReturnTrial(run_id=run_id, status="measured", **trial.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


class TelemetryRecorder:
    """Handles the telemetry messages of one /ws/robot connection (one run at a time)."""

    TYPES = ("run.start", "telemetry", "event", "run.end")

    def __init__(self, send: Any, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        from app.api.ws.contract import checked

        self.send = checked(send)
        self.sessionmaker = sessionmaker
        self.run_id: int | None = None

    async def handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        try:
            async with self.sessionmaker() as db:
                if kind == "run.start":
                    if not isinstance(msg.get("is_simulated"), bool):
                        raise TelemetryError(
                            "bad_message", "run.start needs is_simulated: true or false"
                        )
                    if self.run_id is not None:
                        await end_run(db, self.run_id)
                    config = msg.get("odom_config")
                    run = await start_run(
                        db, msg["is_simulated"], config if isinstance(config, dict) else None,
                        str(msg["notes"])[:500] if msg.get("notes") else None,
                    )  # fmt: skip
                    self.run_id = run.id
                    await self.send({"type": "run.started", "run_id": run.id,
                                     "is_simulated": run.is_simulated})  # fmt: skip
                    return
                if self.run_id is None and kind == "event" and msg.get("kind") in WARNING_EVENTS:
                    await add_event(db, None, {k: msg.get(k) for k in ("t_ms", "kind", "data")})
                    return
                if self.run_id is None:
                    raise TelemetryError("no_run", f"{kind} needs run.start first")
                if kind == "telemetry":
                    samples = msg.get("samples")
                    if not isinstance(samples, list):
                        raise TelemetryError("bad_message", "telemetry needs samples: [...]")
                    await add_samples(db, self.run_id, samples)
                elif kind == "event":
                    await add_event(
                        db, self.run_id, {k: msg.get(k) for k in ("t_ms", "kind", "data")}
                    )
                elif kind == "run.end":
                    counts = await end_run(db, self.run_id)
                    await self.send({"type": "run.ended", "run_id": self.run_id, **counts})
                    self.run_id = None
        except TelemetryError as e:
            await self.send({"type": "error", "code": e.code, "message": str(e)})

    async def disconnected(self) -> None:
        if self.run_id is not None:
            async with self.sessionmaker() as db:
                await end_run(db, self.run_id)
            self.run_id = None
