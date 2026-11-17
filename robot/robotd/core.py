"""The robot's brain: odometry + supervisor + navigation, glued together without any I/O.

`Brain` gets decoded serial messages, vision / page / backend facts and the time, and produces:
- track speed targets and one-shot messages on its `LinkCore` (to the ESP32);
- `outbox`: run messages for the backend (run.start, telemetry, event, run.end), bounded;
- `ui_out` / `vision_out`: messages for the robot page and for visiond.

The asyncio application (robotd.app) moves these over real sockets; the tests and
`robotd.sim_harness` drive the same object against the simulated ESP32 in simulated time.
"""

import logging
import math
import random
from typing import Any

from scr_serial import (
    US_ABSENT,
    US_NO_ECHO,
    Ack,
    AckStatus,
    DriveMode,
    Event,
    EventCode,
    Flags,
    Message,
    Mode,
    Servo,
    Stop,
    Telemetry,
)

from robotd.config import Config
from robotd.link import LinkCore
from robotd.navigation import Breadcrumbs, Distance, Returner, Roamer, Sense, track_speeds
from robotd.odometry import OdometryEstimator
from robotd.supervisor import Action, Facts, Supervisor

log = logging.getLogger("robotd.core")

_SIM_NOTE = "SIMULATED run (robotd with the simulated ESP32) - not a physical result"


def _distance(raw_mm: int) -> Distance:
    if raw_mm == US_ABSENT:
        return None
    return math.inf if raw_mm == US_NO_ECHO else raw_mm / 1000.0


class Brain:
    def __init__(self, cfg: Config, link: LinkCore | None = None) -> None:
        self.cfg = cfg
        self.link = link or LinkCore(cfg.drive_hz, cfg.heartbeat_hz, cfg.telemetry_timeout_ms)
        self.odom = OdometryEstimator(
            cfg.metres_per_tick, cfg.track_width_m, cfg.slip_ratio, cfg.slip_min_ticks,
            cfg.slip_window_s,
        )  # fmt: skip
        self.sup = Supervisor(
            cfg.engage_timeout_s, cfg.reengage_cooldown_s, cfg.fault_clear_s,
            cfg.resume_after_backend_loss,
        )  # fmt: skip
        self.rng = random.Random(cfg.sim_seed)
        self.roamer = Roamer(cfg, self.rng)
        self.returner = Returner(cfg)
        self.crumbs = Breadcrumbs(cfg.breadcrumb_spacing_m, cfg.breadcrumb_heading_rad)
        self.frame: Telemetry | None = None
        self.is_simulated = cfg.sim  # then taken from the SIMULATED flag of every frame
        self.backend_ok = False
        self.conversation_active = False
        self.vision: dict[str, Any] | None = None
        self.vision_connected = False
        self._vision_at = -1e9
        self.run_id: int | None = None  # set by the backend's run.started
        self.outbox: list[dict[str, Any]] = []
        self.outbox_dropped = 0
        self.ui_out: list[dict[str, Any]] = []
        self.vision_out: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []  # recent events (log, tests)
        self.rejected: list[Ack] = []  # commands the ESP32 refused (last few)
        self.command_v = 0.0
        self._samples: list[dict[str, Any]] = []
        self._run_t0_ms = 0
        self._last_sample_ms = -1
        self._last_tick_s: float | None = None
        self._last_mode_s = -1e9
        self._servo_deg: float | None = None
        self._servo_s = -1e9
        self._ui_state: dict[str, Any] = {}
        self._ui_state_s = -1e9
        self._sense = Sense(self.odom.pose, 0.0, (None, None, None), None, 0.0)

    # ================= inputs =================
    def on_serial(self, msg: Message, now_s: float) -> None:
        if isinstance(msg, Telemetry):
            self._on_telemetry(msg)
        elif isinstance(msg, Event):
            self._on_esp_event(msg)
        elif isinstance(msg, Ack) and msg.status != AckStatus.OK:
            self.rejected = (self.rejected + [msg])[-20:]

    def _on_telemetry(self, frame: Telemetry) -> None:
        self.frame = frame
        self.is_simulated = frame.is_simulated
        step = self.odom.update(frame)
        if step is None:
            return
        if step.slip is not None:
            s = step.slip
            self._event(
                "slip", {"side": s.side, "front_ticks": s.front_ticks, "rear_ticks": s.rear_ticks}
            )
        if self.sup.state == "ROAMING":
            self.crumbs.update(self.odom.pose)
        if self.sup.run_open and self.odom.t_ms - self._last_sample_ms >= self.cfg.sample_period_ms:
            self._last_sample_ms = self.odom.t_ms
            self._sample(step.v_mps, step.gyro_z)

    def _on_esp_event(self, ev: Event) -> None:
        if ev.code == EventCode.WATCHDOG_STOP:
            self._event("comm_loss", {"link": "esp32", "silent_ms": ev.arg})
        elif ev.code == EventCode.OBSTACLE_STOP:
            self._event("obstacle_stop", {"front_mm": ev.arg, "by": "esp32"})
        elif ev.code == EventCode.REAR_OBSTACLE_STOP:
            self._event("rear_obstacle_stop", {"rear_mm": ev.arg, "by": "esp32"})
        elif ev.code == EventCode.BOOT:
            self._event("esp32_boot", {})
        elif ev.code in (EventCode.IMU_FAULT, EventCode.ENCODER_FAULT, EventCode.LOW_BATTERY):
            self._event(ev.code.name.lower(), {"arg": ev.arg})
        else:
            self._event(f"esp32_{ev.code.name.lower()}", {"arg": ev.arg})

    def on_vision(self, msg: dict[str, Any], now_s: float) -> None:
        """A `vision.state` message from visiond (no image data, ever)."""
        if msg.get("type") != "vision.state":
            return
        self.vision, self._vision_at = msg, now_s
        selected = msg.get("active_speaker_id")
        if selected is None:
            selected = msg.get("selected_id")
        self.ui_out.append({
            "type": "local.vision", "face_track_id": selected, "overlap": bool(msg.get("overlap")),
            "engaged": bool(msg.get("engaged")), "faces": len(msg.get("faces") or []),
        })  # fmt: skip

    def on_ui(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "local.vad":
            self.vision_out.append({"type": "vad", "speaking": bool(msg.get("speaking"))})
        elif kind == "local.conversation":
            self.conversation_active = bool(msg.get("active"))

    def ui_disconnected(self) -> None:
        self.conversation_active = False  # no page, no conversation

    def set_backend(self, ok: bool) -> None:
        """The backend link came up or went away."""
        if ok == self.backend_ok:
            return
        self.backend_ok = ok
        if not ok:
            self.run_id = None  # the backend closes a run when its connection drops
            return
        has_start = bool(self.outbox) and self.outbox[0]["type"] == "run.start"
        if not has_start and (self.outbox or self.sup.run_open):
            # continue in a new backend run: buffered samples and events follow it
            self.outbox.insert(0, self._run_start(continued=True))

    def on_command(self, command_id: str, cmd: str, args: dict[str, Any] | None) -> dict[str, Any]:
        """A backend `robot.command` request -> the `robot.command.ack` to send. Always answers."""
        accepted, reason, actions = self.sup.command(cmd, args)
        self._do(actions)
        log.info(
            "command %s -> %s%s",
            cmd,
            "accepted" if accepted else "refused",
            f" ({reason})" if reason else "",
        )
        ack: dict[str, Any] = {"type": "robot.command.ack", "id": command_id, "accepted": accepted}
        if reason:
            ack["reason"] = reason
        return ack

    # ================= periodic step (about 50 Hz) =================
    def tick(self, now_s: float) -> None:
        cfg, frame = self.cfg, self.frame
        dt = 0.0 if self._last_tick_s is None else max(0.0, min(0.5, now_s - self._last_tick_s))
        self._last_tick_s = now_s
        lost = self.link.telemetry_lost(now_s)
        faults: list[str] = []
        battery_low = False
        front: tuple[Distance, Distance, Distance] = (None, None, None)
        rear: Distance = None
        if lost or frame is None:
            faults.append("telemetry_lost")
        else:
            if not frame.flags & Flags.IMU_OK:
                faults.append("imu_fault")
            if frame.flags & Flags.ENCODER_FAULT:
                faults.append("encoder_fault")
            us = frame.ultrasonic_mm
            front = (_distance(us[0]), _distance(us[1]), _distance(us[2]))
            rear = _distance(us[3])
            if all(d is None for d in front):
                faults.append("ultrasonic_fault")
            battery_low = bool(frame.flags & Flags.LOW_BATTERY) or (
                bool(frame.flags & Flags.POWER_SENSOR_OK)
                and frame.battery_mv / 1000.0 < cfg.battery_low_v
            )
        sense = Sense(self.odom.pose, self.odom.travelled_m, front, rear, dt)
        self._sense = sense
        vision_ok = self.vision is not None and now_s - self._vision_at <= cfg.vision_timeout_s
        facts = Facts(
            now_s=now_s, faults=faults, battery_low=battery_low,
            front_blocked=sense.front_min() < cfg.obstacle_stop_m,
            backend_ok=self.backend_ok,
            vision_engaged=bool(vision_ok and self.vision and self.vision.get("engaged")),
            conversation_active=self.conversation_active,
        )  # fmt: skip
        self._do(self.sup.update(facts))

        # ---- motion: only in ROAMING / RETURNING, and only with fresh telemetry ----
        v = w = 0.0
        if self.sup.state == "ROAMING":
            v, w = self.roamer.step(sense)
            self._drain(self.roamer.events)
        elif self.sup.state == "RETURNING":
            v, w = self.returner.step(sense)
            self._drain(self.returner.events)
            if self.returner.result is not None:
                kind, data = self.returner.result
                v = w = 0.0
                self._do(self.sup.return_finished(kind, data))
        if (v or w) and frame is not None and not frame.flags & Flags.MOTORS_ENABLED:
            # power-on state of the ESP32 is SAFE: ask for DRIVE mode, move when it is confirmed
            if now_s - self._last_mode_s >= 0.5:
                self._last_mode_s = now_s
                self.link.send(Mode(DriveMode.DRIVE))
            v = w = 0.0
        self.command_v = v
        self.link.set_drive(*track_speeds(v, w, cfg.track_width_m))

        # ---- camera pan servo: follow visiond's target, rate-limited ----
        if vision_ok and not lost and self.vision is not None:
            target = self.vision.get("servo_target_deg")
            if (
                isinstance(target, int | float)
                and now_s - self._servo_s >= cfg.servo_min_interval_s
            ):
                if (
                    self._servo_deg is None
                    or abs(target - self._servo_deg) >= cfg.servo_min_change_deg
                ):
                    self._servo_deg, self._servo_s = float(target), now_s
                    self.link.send(Servo(float(target)))
                    self.vision_out.append({"type": "servo", "pan_deg": float(target)})

        # ---- page: state badge ----
        ui_state = {
            "type": "local.state", "state": self.sup.state, "backend_ok": self.backend_ok,
            "is_simulated": self.is_simulated,
        }  # fmt: skip
        if ui_state != self._ui_state or now_s - self._ui_state_s >= 2.0:
            self._ui_state, self._ui_state_s = ui_state, now_s
            self.ui_out.append(dict(ui_state))

    # ================= outputs =================
    def status(self, now_s: float) -> dict[str, Any]:
        """The `robot.status` message (about 1 Hz)."""
        cfg, frame = self.cfg, self.frame
        lost = self.link.telemetry_lost(now_s) or frame is None
        sensors: dict[str, str] = {"esp32_link": "fault" if lost else "ok"}
        out: dict[str, Any] = {
            "type": "robot.status", "state": self.sup.state, "is_simulated": self.is_simulated,
            "faults": self.sup.faults, "mode": self.sup.mode,
        }  # fmt: skip
        if frame is not None and not lost:
            us = frame.ultrasonic_mm
            absent = sum(1 for v in us if v == US_ABSENT)
            front_absent = all(v == US_ABSENT for v in us[:3])
            faulty = front_absent or bool(frame.flags & Flags.ULTRASONIC_FAULT)
            sensors["encoders"] = "fault" if frame.flags & Flags.ENCODER_FAULT else "ok"
            sensors["imu"] = "ok" if frame.flags & Flags.IMU_OK else "fault"
            sensors["ultrasonic"] = "fault" if faulty else "degraded" if absent else "ok"
            if frame.flags & Flags.POWER_SENSOR_OK:
                volts = frame.battery_mv / 1000.0
                span = cfg.battery_full_v - cfg.battery_empty_v
                out["battery_v"] = round(volts, 2)
                out["battery_pct"] = round(
                    max(0.0, min(100.0, (volts - cfg.battery_empty_v) / span * 100)), 0
                )
        else:
            sensors.update(encoders="absent", imu="absent", ultrasonic="absent")
        vision_fresh = self.vision is not None and now_s - self._vision_at <= cfg.vision_timeout_s
        if vision_fresh and self.vision is not None:
            sensors["camera"] = "ok" if self.vision.get("camera_ok") else "fault"
        else:
            sensors["camera"] = "absent"
        out["sensors"] = sensors
        pose = self.odom.pose
        out["pose"] = {
            "x_m": round(pose.x, 3),
            "y_m": round(pose.y, 3),
            "theta_rad": round(pose.theta, 4),
        }
        if self.run_id is not None:
            out["run_id"] = self.run_id
        return out

    def take_outbox(self, limit: int = 50) -> list[dict[str, Any]]:
        """Run messages to send now (the caller only takes them while the backend is connected)."""
        out, self.outbox = self.outbox[:limit], self.outbox[limit:]
        return out

    # ================= internals =================
    def _do(self, actions: list[Action]) -> None:
        for action in actions:
            kind = action.kind
            if kind == "stop":
                self.link.set_drive(0.0, 0.0)
                self.link.send(Stop())
            elif kind == "engage":
                self.ui_out.append({"type": "local.engage"})
            elif kind == "open_run":
                self.odom.reset()
                self.crumbs.reset()
                self.roamer.segments.clear()
                self._run_t0_ms, self._last_sample_ms, self.run_id = self.odom.t_ms, -1, None
                self._push(self._run_start(continued=False))
                self._event("run_start", {})
            elif kind == "end_run":
                self._flush_samples()
                self._push({"type": "run.end"})
            elif kind == "start_return":
                pose = self.odom.pose
                if self.crumbs.points[-1] != (pose.x, pose.y):
                    self.crumbs.points.append((pose.x, pose.y))
                self.returner.start(self.crumbs.points, pose)
            elif kind == "recheck_path":
                self.roamer.reset(self._sense)
            elif kind == "event":
                self._event(action.data["kind"], action.data.get("data") or {})

    def _drain(self, events: list[tuple[str, dict[str, Any]]]) -> None:
        for kind, data in events:
            self._event(
                kind, {**data, "x_m": round(self.odom.pose.x, 3), "y_m": round(self.odom.pose.y, 3)}
            )
        events.clear()

    def _run_start(self, continued: bool) -> dict[str, Any]:
        notes = _SIM_NOTE if self.is_simulated else "robotd run"
        if continued:
            notes += " (continues an earlier run after a backend reconnect)"
        return {
            "type": "run.start", "is_simulated": self.is_simulated,
            "odom_config": self.cfg.odom_config(self.is_simulated), "notes": notes,
        }  # fmt: skip

    def _t_run_ms(self) -> int:
        return max(0, self.odom.t_ms - self._run_t0_ms)

    def _event(self, kind: str, data: dict[str, Any]) -> None:
        if self.is_simulated:
            data = {**data, "simulated": True}
        self.events = (self.events + [(kind, data)])[-200:]
        log.info("event %s %s%s", kind, data, " [SIMULATED]" if self.is_simulated else "")
        if self.sup.run_open or kind in ("return_done", "return_failed"):
            self._flush_samples()
            self._push({"type": "event", "t_ms": self._t_run_ms(), "kind": kind[:32], "data": data})

    def _sample(self, v_mps: float, gyro_z: float) -> None:
        pose, odom = self.odom.pose, self.odom
        sample: dict[str, Any] = {
            "t_ms": self._t_run_ms(),
            "x_m": round(pose.x, 4), "y_m": round(pose.y, 4), "theta_rad": round(pose.theta, 4),
            "v_mps": round(v_mps, 3), "omega_rps": round(gyro_z, 4),
            "enc": {"left_m": round(odom.left_m, 4), "right_m": round(odom.right_m, 4),
                    "enc_theta_rad": round(odom.enc_theta, 4)},
            "imu": {"gyro_z_rps": round(gyro_z, 4)},
            "state": self.sup.state.lower(),
        }  # fmt: skip
        self._samples.append(sample)
        if len(self._samples) >= self.cfg.telemetry_batch:
            self._flush_samples()

    def _flush_samples(self) -> None:
        if self._samples:
            self._push({"type": "telemetry", "samples": self._samples})
            self._samples = []

    def _push(self, msg: dict[str, Any]) -> None:
        self.outbox.append(msg)
        while len(self.outbox) > self.cfg.outbox_max:  # bounded: drop the oldest pose batch first
            for i, old in enumerate(self.outbox):
                if old["type"] == "telemetry":
                    del self.outbox[i]
                    break
            else:
                del self.outbox[0]
            self.outbox_dropped += 1
