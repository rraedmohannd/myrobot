"""A SIMULATED robot that speaks protocol version 2 (sprint 3 S2, S9).

*** Simulation only. Every status it sends has `is_simulated: true`, every run it opens is
stored with `is_simulated = true`, and nothing it produces is a physical result (rule 5). ***

It stands in for the Raspberry Pi so the admin "Robot" tab, the command flow and the soak test
can be developed before the hardware exists. It replays a trajectory from the simulator
(app/telemetry/simulator.py), answers command requests the way the real robot is expected to
(including refusals), and can hold typed conversations.

    python -m app.ingestion.cli simulate-robot [--seconds 120] [--url ws://localhost:8000/ws/robot]

`SimulatedRobot` is the pure state machine (no network); `run_sim_robot` connects it to a
backend over a WebSocket.
"""

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.telemetry.simulator import SimConfig, simulate_run

SAMPLES_PER_TICK = 10  # the simulator stores 10 Hz; one tick = 1 s


@dataclass
class SimulatedRobot:
    seed: int = 7
    robot_id: str = "simulator"
    state: str = "IDLE"
    mode: str = "demo"
    battery_v: float = 8.2  # simulated value, falls slowly while moving
    faults: list[str] = field(default_factory=list)
    _before: str = "IDLE"  # state to go back to after PAUSED / CONVERSATION
    _i: int = 0
    _run_open: bool = False
    _t0_ms: int = 0

    def __post_init__(self) -> None:
        result = simulate_run(SimConfig(seed=self.seed))
        self._odom_config = result.odom_config()
        self._roam = [
            s for s in result.samples if not str(s.get("state") or "").startswith("return")
        ]
        self._back = [s for s in result.samples if str(s.get("state") or "").startswith("return")]
        self._events = sorted(result.events, key=lambda e: e["t_ms"])
        self._path: list[dict[str, Any]] = []
        self._pose = {"x_m": 0.0, "y_m": 0.0, "theta_rad": 0.0}

    # ---------- what the robot reports ----------
    def status(self) -> dict[str, Any]:
        return {
            "type": "robot.status",
            "state": self.state,
            "is_simulated": True,
            "battery_v": round(self.battery_v, 2),
            "battery_pct": round(
                max(0.0, min(100.0, (self.battery_v - 6.4) / (8.4 - 6.4) * 100)), 0
            ),
            "faults": list(self.faults),
            "sensors": {
                "encoders": "ok",
                "imu": "ok",
                "ultrasonic": "ok",
                "camera": "absent",
                "microphone": "absent",
            },
            "pose": dict(self._pose),
            "mode": self.mode,
        }

    # ---------- command requests: the robot decides ----------
    def command(
        self, cmd: str, args: dict[str, Any] | None = None
    ) -> tuple[bool, str | None, list[dict[str, Any]]]:
        """(accepted, reason, messages to send). Refusals are part of normal behaviour."""
        if self.faults and cmd in ("start_roaming", "resume"):
            return False, f"fault active: {self.faults[0]}", []
        if self.state == "CONVERSATION" and cmd in ("start_roaming", "resume", "return_to_start"):
            return False, "a conversation is in progress", []
        if cmd == "set_mode":
            mode = (args or {}).get("mode")
            if not isinstance(mode, str) or not mode:
                return False, "set_mode needs args.mode", []
            self.mode = mode[:32]
            return True, None, []
        if cmd == "start_roaming":
            if self.state != "IDLE":
                return False, f"not idle (state {self.state})", []
            self.state, self._i, self._path = "ROAMING", 0, self._roam
            return True, None, self._open_run()
        if cmd == "pause":
            if self.state not in ("ROAMING", "RETURNING"):
                return False, f"nothing to pause (state {self.state})", []
            self._before, self.state = self.state, "PAUSED"
            return True, None, []
        if cmd == "resume":
            if self.state != "PAUSED":
                return False, "not paused", []
            self.state = self._before
            return True, None, []
        if cmd == "return_to_start":
            if self.state not in ("ROAMING", "PAUSED"):
                return False, f"not roaming (state {self.state})", []
            return True, None, self._start_return()
        return False, f"unknown command {cmd}", []

    def _open_run(self) -> list[dict[str, Any]]:
        self._run_open, self._t0_ms = True, 0
        return [{
            "type": "run.start", "is_simulated": True, "odom_config": self._odom_config,
            "notes": f"SIMULATED robot client (seed {self.seed}) - not a physical result",
        }]  # fmt: skip

    def _start_return(self) -> list[dict[str, Any]]:
        self.state, self._i, self._path = "RETURNING", 0, self._back
        return [
            {
                "type": "event",
                "t_ms": self._t0_ms,
                "kind": "return_start",
                "data": {"simulated": True},
            }
        ]

    def begin_conversation(self) -> None:
        if self.state != "CONVERSATION":
            self._before, self.state = self.state, "CONVERSATION"

    def end_conversation(self) -> None:
        if self.state == "CONVERSATION":
            self.state = self._before

    # ---------- one second of simulated time ----------
    def tick(self) -> list[dict[str, Any]]:
        if self.state not in ("ROAMING", "RETURNING"):
            return []
        batch = self._path[self._i : self._i + SAMPLES_PER_TICK]
        self._i += len(batch)
        out: list[dict[str, Any]] = []
        if batch:
            last = batch[-1]
            self._pose = {"x_m": last["x_m"], "y_m": last["y_m"], "theta_rad": last["theta_rad"]}
            self._t0_ms = last["t_ms"]
            self.battery_v = max(6.6, self.battery_v - 0.0004 * len(batch))
            out.append({"type": "telemetry", "samples": batch})
        if self._i >= len(self._path):
            if self.state == "ROAMING":  # the roaming time is over: come back by itself
                out += self._start_return()
            else:
                out.append({"type": "event", "t_ms": self._t0_ms, "kind": "return_done",
                            "data": {"simulated": True}})  # fmt: skip
                out.append({"type": "run.end"})
                self.state, self._run_open = "IDLE", False
        return out


@dataclass
class SimRobotReport:
    """What a simulated session saw (used by the soak test). All of it is simulation."""

    seconds: float = 0.0
    status_sent: int = 0
    telemetry_samples: int = 0
    commands: list[dict[str, Any]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)  # turn.timings + wall-clock ms
    errors: list[str] = field(default_factory=list)
    runs: list[int] = field(default_factory=list)
    conversations: int = 0


async def run_sim_robot(
    url: str, seconds: float, seed: int = 7, token: str | None = None,
    questions: list[str] | None = None, converse_every_s: float = 0.0,
    robot: SimulatedRobot | None = None, tick_s: float = 1.0,
) -> SimRobotReport:  # fmt: skip
    """Connect a SimulatedRobot to a backend for `seconds`. With `questions`, it also asks one
    typed question every `converse_every_s` seconds and ends the conversation after the answer."""
    import websockets

    robot = robot or SimulatedRobot(seed=seed)
    report = SimRobotReport()
    full_url = f"{url}{'&' if '?' in url else '?'}token={token}" if token else url
    started = time.monotonic()
    answered = asyncio.Event()
    asked_at = 0.0

    async with websockets.connect(full_url, max_size=None) as ws:

        async def send(msg: dict[str, Any]) -> None:
            await ws.send(json.dumps(msg))

        await send({"type": "hello", "robot_id": robot.robot_id, "fw_version": "sim-robot-1",
                    "protocol_version": 2})  # fmt: skip

        async def reader() -> None:
            async for raw in ws:
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "robot.command":
                    accepted, reason, effects = robot.command(msg.get("cmd"), msg.get("args"))
                    ack = {"type": "robot.command.ack", "id": msg.get("id"), "accepted": accepted}
                    if reason:
                        ack["reason"] = reason
                    await send(ack)
                    report.commands.append(
                        {"cmd": msg.get("cmd"), "accepted": accepted, "reason": reason}
                    )
                    for effect in effects:
                        await send(effect)
                elif kind == "run.started":
                    report.runs.append(msg["run_id"])
                elif kind == "turn.timings":
                    report.turns.append({
                        **{k: v for k, v in msg.items() if k.endswith("_ms")},
                        "wall_ms": int((time.monotonic() - asked_at) * 1000),
                        "at_s": round(time.monotonic() - started, 1),
                    })  # fmt: skip
                elif kind == "dialogue.state" and msg.get("state") == "FOLLOW_UP":
                    answered.set()
                elif kind == "error":
                    report.errors.append(f"{msg.get('code')}: {msg.get('message')}")

        async def ticker() -> None:
            while True:
                await send(robot.status())
                report.status_sent += 1
                for out in robot.tick():
                    if out["type"] == "telemetry":
                        report.telemetry_samples += len(out["samples"])
                    await send(out)
                await asyncio.sleep(tick_s)

        async def talker() -> None:
            nonlocal asked_at
            if not questions or converse_every_s <= 0:
                return
            n = 0
            while True:
                await asyncio.sleep(converse_every_s)
                robot.begin_conversation()
                answered.clear()
                asked_at = time.monotonic()
                await send({"type": "text.input", "text": questions[n % len(questions)]})
                n += 1
                try:
                    await asyncio.wait_for(answered.wait(), 60)
                except TimeoutError:
                    report.errors.append("no answer within 60 s")
                await send({"type": "session.end", "reason": "user_done"})
                report.conversations += 1
                robot.end_conversation()

        tasks = [asyncio.create_task(c()) for c in (reader, ticker, talker)]
        try:
            await asyncio.sleep(seconds)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
    report.seconds = round(time.monotonic() - started, 1)
    return report
