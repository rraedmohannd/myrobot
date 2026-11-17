"""Supervisor state machine: IDLE, ROAMING, ENGAGED, CONVERSATION, RETURNING, PAUSED, FAULT.

Pure and unit-testable: inputs are facts (derived from telemetry, vision, the page, the backend
link) and command requests; outputs are actions for the caller to carry out. No I/O, no clock
(the time is a field of `Facts`).

Motion is allowed only in ROAMING and RETURNING. Every backend command is a request: it is
accepted or refused with a reason, like the sprint-3 simulated robot client
(backend/app/telemetry/sim_robot.py). Stopping never depends on the backend (CLAUDE.md rule 4).
"""

from dataclasses import dataclass, field
from typing import Any

MOVING_STATES = ("ROAMING", "RETURNING")


@dataclass
class Facts:
    now_s: float
    faults: list[str] = field(default_factory=list)  # e.g. telemetry_lost, imu_fault
    battery_low: bool = False
    front_blocked: bool = False  # no front sensor sees a free way
    backend_ok: bool = True
    vision_engaged: bool = False  # a person has faced the robot for N seconds (visiond)
    conversation_active: bool = False  # reported by the robot page


@dataclass
class Action:
    kind: str  # stop, engage, open_run, end_run, start_return, recheck_path, event
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Supervisor:
    engage_timeout_s: float = 10.0
    reengage_cooldown_s: float = 5.0
    fault_clear_s: float = 2.0
    resume_after_backend_loss: bool = True
    state: str = "IDLE"
    mode: str = "demo"
    run_open: bool = False
    pause_reason: str = ""  # "command" / "backend_lost" / "fault"
    _resume: str = "IDLE"  # state to go back to after ENGAGED / CONVERSATION
    _paused_from: str = "ROAMING"
    _engaged_at: float = 0.0
    _no_engage_until: float = 0.0
    _fault_clear_since: float | None = None
    _facts: Facts = field(default_factory=lambda: Facts(now_s=0.0))

    @property
    def may_move(self) -> bool:
        return self.state in MOVING_STATES

    @property
    def faults(self) -> list[str]:
        out = list(self._facts.faults)
        if self._facts.battery_low and "low_battery" not in out:
            out.append("low_battery")
        return out

    # ---------------- command requests: the robot decides ----------------
    def command(
        self, cmd: str, args: dict[str, Any] | None = None
    ) -> tuple[bool, str | None, list[Action]]:
        """(accepted, reason, actions). A refusal is a normal answer."""
        facts = self._facts
        moves = cmd in ("start_roaming", "resume", "return_to_start")
        if moves and facts.battery_low:
            return False, "battery low", []
        if moves and self.faults:
            return False, f"fault active: {self.faults[0]}", []
        if moves and self.state in ("CONVERSATION", "ENGAGED"):
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
            if facts.front_blocked:
                return False, "blocked: no free way in front", []
            self.state, self.run_open = "ROAMING", True
            return True, None, [Action("open_run"), Action("recheck_path")]
        if cmd == "pause":
            if self.state not in MOVING_STATES:
                return False, f"nothing to pause (state {self.state})", []
            self._pause("command")
            return True, None, [Action("stop")]
        if cmd == "resume":
            if self.state != "PAUSED":
                return False, "not paused", []
            if not facts.backend_ok:  # cannot happen over the backend link; kept for local callers
                return False, "backend unavailable", []
            if self._paused_from == "ROAMING" and facts.front_blocked:
                return False, "blocked: no free way in front", []
            return True, None, self._unpause()
        if cmd == "return_to_start":
            if self.state == "PAUSED" and self._paused_from == "RETURNING":
                return True, None, self._unpause()
            if self.state not in ("ROAMING", "PAUSED"):
                return False, f"not roaming (state {self.state})", []
            self.state, self.pause_reason = "RETURNING", ""
            return True, None, [Action("start_return")]
        return False, f"unknown command {cmd}", []

    def _pause(self, reason: str) -> None:
        self._paused_from, self.state, self.pause_reason = self.state, "PAUSED", reason

    def _unpause(self) -> list[Action]:
        self.state, self.pause_reason = self._paused_from, ""
        return [Action("recheck_path")] if self.state == "ROAMING" else []

    # ---------------- navigation feedback ----------------
    def return_finished(self, kind: str, data: dict[str, Any]) -> list[Action]:
        """The return controller ended: `return_done` or `return_failed{reason}`."""
        if self.state != "RETURNING":
            return []
        self.state, self.run_open = "IDLE", False
        return [Action("stop"), Action("event", {"kind": kind, "data": data}), Action("end_run")]

    # ---------------- periodic update ----------------
    def update(self, facts: Facts) -> list[Action]:
        self._facts = facts
        now = facts.now_s
        actions: list[Action] = []
        faults = self.faults

        # 1. faults win over everything
        if faults:
            self._fault_clear_since = None
            if self.state != "FAULT":
                was = self.state if self.state not in ("ENGAGED", "CONVERSATION") else self._resume
                if was == "PAUSED":
                    was = self._paused_from
                elif was == "IDLE":
                    was = ""
                self._paused_from = was or self._paused_from
                self._fault_from_moving = bool(was)
                self.state = "FAULT"
                actions += [
                    Action("stop"),
                    Action("event", {"kind": "fault", "data": {"faults": faults}}),
                ]
            return actions
        if self.state == "FAULT":
            if self._fault_clear_since is None:
                self._fault_clear_since = now
            if now - self._fault_clear_since < self.fault_clear_s:
                return actions
            # never start moving again by itself after a fault: PAUSED waits for `resume`
            if self._fault_from_moving:
                self.state, self.pause_reason = "PAUSED", "fault"
            else:
                self.state = "IDLE"
            actions.append(Action("event", {"kind": "fault_cleared", "data": {}}))

        # 2. the page says a conversation is running (engagement, or the start button)
        if facts.conversation_active:
            if self.state != "CONVERSATION":
                if self.state != "ENGAGED":
                    self._resume = self.state
                self.state = "CONVERSATION"
                actions.append(Action("stop"))
            return actions
        if self.state == "CONVERSATION":
            self._no_engage_until = now + self.reengage_cooldown_s
            actions += self._resume_after_visitor()

        # 3. backend lost: stop roaming, wait
        if not facts.backend_ok:
            if self.state == "ROAMING":
                self._pause("backend_lost")
                actions += [
                    Action("stop"),
                    Action("event", {"kind": "comm_loss", "data": {"link": "backend"}}),
                ]
            elif self.state == "ENGAGED":  # nobody can answer the visitor
                actions += self._resume_after_visitor()
            return actions
        if self.state == "PAUSED" and self.pause_reason == "backend_lost":
            if self.resume_after_backend_loss:
                actions += self._unpause()
            else:
                self.pause_reason = "command"

        # 4. engagement
        if self.state == "ENGAGED":
            if now - self._engaged_at >= self.engage_timeout_s:
                if facts.vision_engaged:  # still there: ask the page again
                    self._engaged_at = now
                    actions.append(Action("engage"))
                else:
                    actions += self._resume_after_visitor()
        elif (
            facts.vision_engaged
            and self.state in ("IDLE", "ROAMING")
            and now >= self._no_engage_until
        ):
            self._resume, self.state, self._engaged_at = self.state, "ENGAGED", now
            actions += [Action("stop"), Action("engage")]
        return actions

    _fault_from_moving: bool = False

    def _resume_after_visitor(self) -> list[Action]:
        """Back to what the robot was doing; a moving state rechecks the path first."""
        self.state = self._resume
        self._resume = "IDLE"
        return [Action("recheck_path")] if self.state == "ROAMING" else []
