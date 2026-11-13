"""Live robots connected to /ws/robot: their status, and commands sent to them (sprint 3 S2).

Safety (CLAUDE.md rule 4): everything here is display and requests. A `robot.command` is a
REQUEST; the robot answers `robot.command.ack` and may refuse (conversation in progress, fault,
low battery, its own safety rules). If this process dies, the robot keeps deciding locally.

One hub per backend process. A connection counts as a robot once it has sent `robot.status`;
the browser pages `/robot` and `/voice` never do, so they never receive commands.
"""

import asyncio
import contextlib
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from scr_protocol import COMMANDS, PROTOCOL_VERSION, ProtocolError, messages, negotiate_version
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import Event, ReturnTrial, RobotRun

Send = Callable[[dict[str, Any]], Awaitable[None]]
TRAIL_POINTS = 600  # about 10 minutes of status poses for the small live plot
SIMULATED_LABEL = "SIMULATED — not a physical robot"


class RobotOffline(RuntimeError):
    """No robot is connected (or it stopped sending status): nothing was sent."""


@dataclass
class RobotLink:
    """One /ws/robot connection."""

    send: Send
    conn_id: int
    robot_id: str | None = None
    protocol_version: int = 1
    connected_at: float = field(default_factory=time.monotonic)
    status: dict[str, Any] | None = None
    status_at: float | None = None
    status_count: int = 0
    run_id: int | None = None  # the run opened on this connection with run.start
    trail: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=TRAIL_POINTS))

    def hello(self, msg: dict[str, Any]) -> None:
        self.robot_id = str(msg.get("robot_id") or "")[:64] or None
        self.protocol_version = negotiate_version(msg.get("protocol_version"))

    @property
    def is_simulated(self) -> bool | None:
        return self.status.get("is_simulated") if self.status else None


class RobotHub:
    def __init__(self) -> None:
        self.links: dict[int, RobotLink] = {}
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.recent: deque[dict[str, Any]] = deque(maxlen=30)  # newest first
        self._next = 0

    # ---------- connections ----------
    def register(self, send: Send) -> RobotLink:
        self._next += 1
        link = RobotLink(send=send, conn_id=self._next)
        self.links[link.conn_id] = link
        return link

    def unregister(self, link: RobotLink) -> None:
        self.links.pop(link.conn_id, None)

    def _fresh(self, link: RobotLink) -> bool:
        limit = get_settings().robot_offline_after_s
        return link.status_at is not None and time.monotonic() - link.status_at <= limit

    def current(self, robot_id: str | None = None) -> RobotLink | None:
        """The robot that commands go to: a protocol-2 connection whose status is fresh (the
        most recently heard one when several are connected)."""
        candidates = [
            link for link in self.links.values()
            if self._fresh(link) and link.protocol_version >= 2
            and (robot_id is None or link.robot_id == robot_id)
        ]  # fmt: skip
        return max(candidates, key=lambda link: link.status_at or 0.0, default=None)

    # ---------- robot -> backend ----------
    def on_status(self, link: RobotLink, msg: dict[str, Any]) -> None:
        try:
            status = messages.RobotStatus.model_validate(msg)
        except ValueError as e:
            raise ProtocolError(f"robot.status: {e.errors()[0]['msg']}") from e  # type: ignore[attr-defined]
        link.status = status.model_dump(exclude={"type"})
        link.status_at = time.monotonic()
        link.status_count += 1
        if status.pose is not None:
            point = (round(status.pose.x_m, 3), round(status.pose.y_m, 3))
            if not link.trail or link.trail[-1] != point:
                link.trail.append(point)

    def on_ack(self, link: RobotLink, msg: dict[str, Any]) -> None:
        try:
            ack = messages.RobotCommandAck.model_validate(msg)
        except ValueError as e:
            raise ProtocolError(f"robot.command.ack: {e.errors()[0]['msg']}") from e  # type: ignore[attr-defined]
        future = self.pending.get(ack.id)
        if future is not None and not future.done():
            future.set_result({"accepted": ack.accepted, "reason": ack.reason})
        # an ack for an unknown or expired command is ignored (the timeout was already recorded)

    # ---------- backend -> robot ----------
    async def send_command(
        self, sessionmaker: async_sessionmaker[AsyncSession], cmd: str,
        args: dict[str, Any] | None = None, requested_by: str = "admin",
        robot_id: str | None = None, timeout_s: float | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        """Send one request and wait for the robot's ack. Raises RobotOffline (and sends
        nothing) when no robot is connected. A missing ack is reported as `timed_out`: the
        backend then knows nothing about what the robot did."""
        if cmd not in COMMANDS:
            raise ValueError(f"unknown command {cmd!r}")
        link = self.current(robot_id)
        if link is None:
            raise RobotOffline("No robot is connected, so nothing was sent.")
        timeout_s = timeout_s if timeout_s is not None else get_settings().robot_command_timeout_s
        command_id = uuid.uuid4().hex[:12]
        record: dict[str, Any] = {
            "id": command_id, "cmd": cmd, "args": args or None, "robot_id": link.robot_id,
            "is_simulated": bool(link.is_simulated), "requested_by": requested_by,
            "sent_at": datetime.now(UTC).isoformat(timespec="seconds"), "run_id": link.run_id,
            "accepted": None, "reason": None, "timed_out": False, "return_trial_id": None,
        }  # fmt: skip
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[command_id] = future
        self.recent.appendleft(record)
        await self._event(sessionmaker, link, "command", {
            "id": command_id, "cmd": cmd, "args": args or None, "requested_by": requested_by,
            "robot_id": link.robot_id,
        })  # fmt: skip
        try:
            message: dict[str, Any] = {"type": "robot.command", "id": command_id, "cmd": cmd}
            if args:
                message["args"] = args
            try:
                await link.send(message)
            except Exception as e:  # noqa: BLE001 - the socket died between the check and the send
                record.update(timed_out=True, reason="connection lost")
                await self._event(sessionmaker, link, "command_ack", {"id": command_id, "lost": True})
                raise RobotOffline("The robot disconnected before the command could be sent.") from e
            try:
                ack = await asyncio.wait_for(future, timeout_s)
            except TimeoutError:
                record.update(timed_out=True, reason=f"no answer from the robot within {timeout_s:g} s")
                await self._event(sessionmaker, link, "command_ack", {"id": command_id, "timed_out": True})
                return record
            record.update(accepted=ack["accepted"], reason=ack["reason"])
            await self._event(sessionmaker, link, "command_ack", {"id": command_id, **ack})
            if ack["accepted"] and cmd == "return_to_start":
                record["return_trial_id"] = await self._pending_trial(sessionmaker, link, command_id)
            return record
        finally:
            self.pending.pop(command_id, None)

    async def _event(self, sessionmaker: async_sessionmaker[AsyncSession], link: RobotLink,
                     kind: str, data: dict[str, Any]) -> None:  # fmt: skip
        """Commands and acks are stored as events: on the open run when there is one."""
        async with sessionmaker() as db:
            run = await db.get(RobotRun, link.run_id) if link.run_id else None
            t_ms = 0
            if run is not None and run.started_at is not None:
                t_ms = max(0, int((datetime.now(UTC) - run.started_at).total_seconds() * 1000))
            db.add(Event(run_id=run.id if run else None, t_ms=t_ms, kind=kind, data=data))
            await db.commit()

    async def _pending_trial(self, sessionmaker: async_sessionmaker[AsyncSession],
                             link: RobotLink, command_id: str) -> int | None:  # fmt: skip
        """An accepted return-to-start on a REAL run opens a return trial in the `pending`
        state. Its error is typed in later by hand, after measuring on the floor with a tape.
        A simulated run never gets one (CLAUDE.md rule 5)."""
        if link.run_id is None or link.is_simulated is not False:
            return None
        async with sessionmaker() as db:
            run = await db.get(RobotRun, link.run_id)
            if run is None or run.is_simulated:
                return None
            trial = ReturnTrial(run_id=run.id, status="pending", command_id=command_id)
            db.add(trial)
            await db.commit()
            return trial.id

    # ---------- for the admin page ----------
    def snapshot(self) -> dict[str, Any]:
        link = self.current()
        robot = None
        if link is not None and link.status is not None:
            simulated = bool(link.status.get("is_simulated"))
            robot = {
                "robot_id": link.robot_id, "protocol_version": link.protocol_version,
                "connected_s": round(time.monotonic() - link.connected_at),
                "status_age_s": round(time.monotonic() - (link.status_at or 0.0), 1),
                "is_simulated": simulated,
                "label": SIMULATED_LABEL if simulated else "Physical robot",
                "run_id": link.run_id, "status": link.status, "trail": list(link.trail),
            }  # fmt: skip
        return {
            "online": robot is not None, "robot": robot, "commands": list(self.recent)[:10],
            "available_commands": list(COMMANDS), "protocol_version": PROTOCOL_VERSION,
            "connections": len(self.links),
            "offline_after_s": get_settings().robot_offline_after_s,
            "note": "Commands are requests. The robot decides locally and may refuse; safety "
                    "never depends on the backend.",
        }  # fmt: skip

    def reset(self) -> None:
        for future in self.pending.values():
            with contextlib.suppress(Exception):
                future.cancel()
        self.links.clear()
        self.pending.clear()
        self.recent.clear()


@lru_cache
def get_robot_hub() -> RobotHub:
    return RobotHub()
