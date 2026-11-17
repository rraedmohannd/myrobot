"""The robot's brain: odometry + supervisor + navigation, glued together without any I/O.

`Brain` gets decoded serial messages, vision / page / backend facts and the time, and produces:
- track speed targets and one-shot messages on its `LinkCore` (to the ESP32);
- `outbox`: run messages for the backend (run.start, telemetry, event, run.end), bounded;
- `ui_out` / `vision_out`: messages for the robot page and for visiond.

Sprint 5 additions:
- calibration (robotd.calibration): every firmware parameter is pushed at each (re)connection
  and after an ESP32 reboot, the parameter hash in the telemetry is compared with the expected
  one (fault `param_hash_mismatch` while they differ; pushed again every 2 s); `metres_per_tick`
  comes from the calibration file only; no METRES_PER_TICK -> moving commands are refused with
  a clear reason (the firmware refuses MODE DRIVE as well);
- gyro bias: CALIBRATE_GYRO at start while still, again when IDLE and still for 2 minutes;
  between those, a running estimate of the residual bias from the telemetry while the robot
  stands still (targets 0, encoders still for >= 2 s), subtracted by the odometry;
- battery: filtered voltage, percentage from a placeholder 3S table (or `battery_table` of the
  calibration), warning at 10.5 V, no new motion below 9.9 V, energy (Wh) from the current;
- bring-up mode for robotctl (`/ws/ctl`), the black box, the look-away tolerance.

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
    CalibrateGyro,
    DiagReport,
    DiagRequest,
    DriveMode,
    Event,
    EventCode,
    Flags,
    Message,
    Mode,
    MsgType,
    ParamSet,
    Servo,
    Stop,
    Telemetry,
    load_table,
)

from robotd import calibration as calib
from robotd.blackbox import BlackBox
from robotd.calibration import Calibration
from robotd.config import Config
from robotd.link import LinkCore
from robotd.navigation import Breadcrumbs, Distance, Returner, Roamer, Sense, track_speeds
from robotd.odometry import OdometryEstimator
from robotd.supervisor import Action, Facts, Supervisor

log = logging.getLogger("robotd.core")

_SIM_NOTE = "SIMULATED run (robotd with the simulated ESP32) - not a physical result"
MOVE_COMMANDS = ("start_roaming", "resume", "return_to_start")

# *** PLACEHOLDER voltage -> percent table of a 3S Li-ion pack at rest (typical 18650 curve,
# NOT measured on this pack). Replace with the calibration key `battery_table`. ***
BATTERY_TABLE_3S: list[tuple[float, float]] = [
    (9.0, 0.0), (9.9, 5.0), (10.5, 15.0), (10.8, 25.0), (11.1, 40.0), (11.4, 55.0),
    (11.7, 70.0), (12.0, 85.0), (12.3, 95.0), (12.6, 100.0),
]  # fmt: skip
BATTERY_FILTER = 0.05  # low-pass of the voltage at 50 Hz (about 0.4 s)
BATTERY_HYSTERESIS_V = 0.2
PARAM_GRACE_S = 1.0  # after a push, the hash may differ this long before it is a fault
BLACKBOX_TRIGGERS = ("fault", "obstacle_stop", "rear_obstacle_stop", "comm_loss")
_RESET_NAMES = {0: "unknown", 1: "power-on", 2: "brownout", 3: "task-watchdog", 4: "panic",
                5: "software", 6: "external", 7: "other-watchdog", 8: "deep-sleep"}  # fmt: skip


def _distance(raw_mm: int) -> Distance:
    if raw_mm == US_ABSENT:
        return None
    return math.inf if raw_mm == US_NO_ECHO else raw_mm / 1000.0


def battery_percent(volts: float, table: list[tuple[float, float]]) -> float:
    """Linear interpolation in a (volts, percent) table, clamped to 0..100."""
    if volts <= table[0][0]:
        return max(0.0, min(100.0, table[0][1]))
    for (v0, p0), (v1, p1) in zip(table, table[1:], strict=False):
        if volts <= v1:
            return max(0.0, min(100.0, p0 + (p1 - p0) * (volts - v0) / (v1 - v0)))
    return max(0.0, min(100.0, table[-1][1]))


class Brain:
    def __init__(
        self, cfg: Config, link: LinkCore | None = None, calibration: Calibration | None = None,
        calibration_problem: str = "",
    ) -> None:  # fmt: skip
        self.cfg = cfg
        self.table = load_table()
        self.link = link or LinkCore(cfg.drive_hz, cfg.heartbeat_hz, cfg.telemetry_timeout_ms)
        self.odom = OdometryEstimator(
            0.0, cfg.track_width_m, cfg.slip_ratio, cfg.slip_min_ticks, cfg.slip_window_s,
        )  # fmt: skip
        self.sup = Supervisor(
            cfg.engage_timeout_s, cfg.reengage_cooldown_s, cfg.fault_clear_s,
            cfg.resume_after_backend_loss, cfg.backend_lost_resume_s,
        )  # fmt: skip
        self.rng = random.Random(cfg.sim_seed)
        self.roamer = Roamer(cfg, self.rng)
        self.returner = Returner(cfg)
        self.crumbs = Breadcrumbs(cfg.breadcrumb_spacing_m, cfg.breadcrumb_heading_rad)
        self.frame: Telemetry | None = None
        self._seen_telemetry = False
        self.is_simulated = cfg.sim  # then taken from the SIMULATED flag of every frame
        self.backend_ok = False
        self.conversation_active = False
        self.vision: dict[str, Any] | None = None
        self.vision_connected = False
        self._vision_at = -1e9
        self._engaged_at = -1e9  # last time visiond said "engaged"
        self.run_id: int | None = None  # set by the backend's run.started
        self.outbox: list[dict[str, Any]] = []
        self.outbox_dropped = 0
        self.ui_out: list[dict[str, Any]] = []
        self.vision_out: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []  # recent events (log, tests)
        self.rejected: list[Ack] = []  # commands the ESP32 refused (last few)
        self.command_v = 0.0
        self.now_s = 0.0
        self.blackbox = BlackBox(None, cfg.blackbox_s, cfg.blackbox_keep)  # app sets the folder
        # ---- calibration / parameters ----
        self.cal = Calibration()
        self.cal_problem = ""
        self.cal_dirty = False  # a calibration.report is due
        self.metres_per_tick = 0.0
        self.track_width_m = cfg.track_width_m
        self.expected_hash = 0
        self.params_ok = False
        self.param_pushes = 0
        self._need_push = True
        self._push_at: float | None = None
        self._grace_until = -1e9
        self.diag: DiagReport | None = None
        self.health: dict[str, Any] = {}  # Pi health (robotd.health), set by the app
        self.bringup = False
        # ---- gyro bias ----
        self.gyro_bias_residual = 0.0
        self.gyro_cal_due = True  # CALIBRATE_GYRO at start
        self._gyro_cal_sent_s = -1e9
        self._gyro_cal_done_s = -1e9
        self._gyro_cal_source = "start"
        self._last_motion_s = -1e9
        self._still_since: float | None = None
        self._still_sum, self._still_n = 0.0, 0
        self._reported_bias = 0.0
        # ---- battery ----
        self.battery_v: float | None = None
        self.battery_a: float | None = None
        self.energy_wh = 0.0
        self.battery_warning = False
        self.battery_low = False
        self._last_power_t_ms: int | None = None
        self.set_calibration(
            calibration if calibration is not None
            else (calib.simulated() if cfg.sim else Calibration()),
            calibration_problem,
        )  # fmt: skip
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

    # ================= calibration =================
    def set_calibration(self, cal: Calibration, problem: str = "") -> None:
        """Use this calibration from now on; its firmware parameters are pushed at the next
        tick and the backend gets a `calibration.report`."""
        self.cal, self.cal_problem = cal, problem
        self.metres_per_tick = float(cal.value("METRES_PER_TICK", 0.0) or 0.0)
        width = cal.value("track_width_m")
        self.track_width_m = float(width) if width else self.cfg.track_width_m
        self.odom.metres_per_tick = self.metres_per_tick
        self.odom.track_width_m = self.track_width_m
        self.expected_hash = calib.expected_hash(cal, self.table)
        self.params_ok = False
        self._need_push = True
        self.cal_dirty = True
        state = calib.status(cal, self.table)
        log.info(
            "calibration: %s%s, %d values, expected parameter hash %08x%s",
            state, " (SIMULATED)" if cal.is_simulated else "", len(cal.values),
            self.expected_hash, f"; problem: {problem}" if problem else "",
        )  # fmt: skip

    def calibration_status(self) -> dict[str, Any]:
        return calib.status_dict(self.cal, self.table)

    def _push_params(self, now_s: float, grace: bool = True) -> None:
        for pid, value in calib.push_values(self.cal, self.table).items():
            self.link.send(ParamSet(pid, value))
        self.link.send(DiagRequest())
        self._need_push, self._push_at = False, now_s
        if grace:  # a first push: its answer is on the way; a retry gets no grace
            self._grace_until = now_s + PARAM_GRACE_S
        self.param_pushes += 1
        log.info("parameters pushed to the ESP32 (push %d)", self.param_pushes)

    def set_health(self, health: dict[str, Any]) -> None:
        """A new Pi health reading; an under-voltage becomes a warning event (power evidence)."""
        if health.get("under_voltage_now") and not self.health.get("under_voltage_now"):
            self._event("under_voltage", {
                "cpu_temp_c": health.get("cpu_temp_c"), "throttled": health.get("throttled_now"),
            }, always=True)  # fmt: skip
        self.health = health

    def on_link(self, connected: bool, now_s: float) -> None:
        """The serial port was (re)opened or lost."""
        if connected:
            self._need_push = True
            self.params_ok = False
            self.gyro_cal_due = True

    def set_bringup(self, on: bool, now_s: float) -> tuple[bool, str | None]:
        ok, reason = self.sup.bringup(on)
        if not ok:
            return ok, reason
        if on != self.bringup:
            self.bringup = on
            self.link.set_drive(0.0, 0.0)
            self.link.send(Stop())
            self._event("bringup", {"on": on})
            if not on:  # whatever robotctl changed: the calibration file is the truth again
                self.link.send(Mode(DriveMode.SAFE))
                self._need_push = True
        return True, None

    # ================= inputs =================
    def on_serial(self, msg: Message, now_s: float) -> None:
        if isinstance(msg, Telemetry):
            self._on_telemetry(msg, now_s)
        elif isinstance(msg, Event):
            self._on_esp_event(msg, now_s)
        elif isinstance(msg, DiagReport):
            self.diag = msg
        elif isinstance(msg, Ack) and msg.status != AckStatus.OK:
            self.rejected = (self.rejected + [msg])[-20:]
            if msg.acked_type == MsgType.MODE and msg.status == AckStatus.REJECTED_MODE:
                reason = (
                    "not calibrated: METRES_PER_TICK is 0" if self.metres_per_tick <= 0
                    else "refused by the ESP32"
                )  # fmt: skip
                self._event("mode_refused", {"reason": reason})
            elif msg.acked_type == MsgType.CALIBRATE_GYRO:
                self.gyro_cal_due = True  # BUSY: try again later

    def _on_telemetry(self, frame: Telemetry, now_s: float) -> None:
        self.frame = frame
        self._seen_telemetry = True
        self.is_simulated = frame.is_simulated
        self.blackbox.telemetry(now_s, frame)
        self._power(frame)
        step = self.odom.update(frame)
        if step is None:
            return
        self._gyro_still(frame, now_s)
        if self.sup.state == "ROAMING":
            self.crumbs.update(self.odom.pose)
        if self.sup.run_open and self.odom.t_ms - self._last_sample_ms >= self.cfg.sample_period_ms:
            self._last_sample_ms = self.odom.t_ms
            self._sample(step.v_mps, step.gyro_z)

    def _power(self, frame: Telemetry) -> None:
        if not frame.flags & Flags.POWER_SENSOR_OK:
            self.battery_v = self.battery_a = None
            self._last_power_t_ms = None
            return
        volts, amps = frame.battery_mv / 1000.0, frame.battery_ma / 1000.0
        if self.battery_v is None:
            self.battery_v = volts
        else:
            self.battery_v += BATTERY_FILTER * (volts - self.battery_v)
        self.battery_a = amps
        if self._last_power_t_ms is not None:
            dt_ms = (frame.t_ms - self._last_power_t_ms) % (1 << 32)
            if 0 < dt_ms <= 1000:
                self.energy_wh += volts * amps * dt_ms / 3.6e6
        self._last_power_t_ms = frame.t_ms

    def _gyro_still(self, frame: Telemetry, now_s: float) -> None:
        """Running estimate of the bias left in the telemetry while the robot stands still."""
        still = (
            self.link.target == (0.0, 0.0) and self.odom.last_delta_ticks == 0
            and not frame.flags & Flags.GYRO_CALIBRATING
        )  # fmt: skip
        if self.odom.last_delta_ticks:
            self._last_motion_s = now_s
        if not still:
            self._still_since, self._still_sum, self._still_n = None, 0.0, 0
            return
        if self._still_since is None:
            self._still_since = now_s
        self._still_sum += frame.gyro_z
        self._still_n += 1
        if now_s - self._still_since < self.cfg.gyro_still_s or self._still_n < 10:
            return
        estimate = self._still_sum / self._still_n
        a = self.cfg.gyro_bias_alpha
        self.gyro_bias_residual = (1 - a) * self.gyro_bias_residual + a * estimate
        self.odom.gyro_bias = self.gyro_bias_residual
        self._still_since, self._still_sum, self._still_n = now_s, 0.0, 0
        if abs(self.gyro_bias_residual - self._reported_bias) > 0.001:
            self._reported_bias = self.gyro_bias_residual
            self._event("gyro_bias", {
                "bias_rad_s": round(self.gyro_bias_residual, 5), "source": "still",
            })  # fmt: skip

    def _on_esp_event(self, ev: Event, now_s: float) -> None:
        code = ev.code
        if code == EventCode.WATCHDOG_STOP:
            self._event("comm_loss", {"link": "esp32", "silent_ms": ev.arg})
        elif code == EventCode.OBSTACLE_STOP:
            self._event("obstacle_stop", {"front_mm": ev.arg, "by": "esp32"})
        elif code == EventCode.REAR_OBSTACLE_STOP:
            self._event("rear_obstacle_stop", {"rear_mm": ev.arg, "by": "esp32"})
        elif code == EventCode.BOOT:
            # a (re)started ESP32 lost every unsaved parameter and its gyro bias
            self._need_push, self.params_ok, self.gyro_cal_due = True, False, True
            self._event("esp32_boot", {})
        elif code == EventCode.SLIP:
            self._event("slip", {"side": "left" if ev.arg == 0 else "right", "by": "esp32"})
        elif code == EventCode.GYRO_CAL_DONE:
            self._gyro_cal_done_s = now_s
            self.gyro_bias_residual = self.odom.gyro_bias = self._reported_bias = 0.0
            self._event("gyro_bias", {"bias_rad_s": ev.arg / 10000.0,
                                      "source": self._gyro_cal_source})  # fmt: skip
        elif code == EventCode.GYRO_CAL_FAILED:
            self.gyro_cal_due = True
            self._event("gyro_cal_failed", {})
        elif code == EventCode.PARAM_CHANGED:
            log.debug("ESP32 parameter %d changed", ev.arg)
        elif code in (EventCode.IMU_FAULT, EventCode.ENCODER_FAULT, EventCode.LOW_BATTERY):
            self._event(code.name.lower(), {"arg": ev.arg})
        else:
            self._event(f"esp32_{code.name.lower()}", {"arg": ev.arg})

    def on_vision(self, msg: dict[str, Any], now_s: float) -> None:
        """A `vision.state` message from visiond (no image data, ever)."""
        if msg.get("type") != "vision.state":
            return
        self.vision, self._vision_at = msg, now_s
        if msg.get("engaged"):
            self._engaged_at = now_s
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
        self.cal_dirty = True  # the backend gets the calibration at every connect
        has_start = bool(self.outbox) and self.outbox[0]["type"] == "run.start"
        if not has_start and self.sup.run_open:
            # continue in a new backend run: buffered samples and events follow it
            self.outbox.insert(0, self._run_start(continued=True))

    def on_command(self, command_id: str, cmd: str, args: dict[str, Any] | None) -> dict[str, Any]:
        """A backend `robot.command` request -> the `robot.command.ack` to send. Always answers."""
        refusal = self._move_refusal(cmd)
        if refusal:
            accepted, reason = False, refusal
        else:
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
            ack["reason"] = reason[:200]
        return ack

    def _move_refusal(self, cmd: str) -> str | None:
        """robotd's own reasons not to move, before the supervisor's (calibration, bring-up)."""
        if cmd not in MOVE_COMMANDS or self.bringup:
            return None
        if self.metres_per_tick <= 0:
            what = self.cal_problem or f"calibration {calib.status(self.cal, self.table)}"
            return f"not calibrated: METRES_PER_TICK missing ({what}); run robotctl distance-cal"
        if not self.params_ok:
            return "ESP32 parameters not confirmed yet (parameter hash check)"
        return None

    # ================= periodic step (about 50 Hz) =================
    def tick(self, now_s: float) -> None:
        cfg, frame = self.cfg, self.frame
        self.now_s = now_s
        dt = 0.0 if self._last_tick_s is None else max(0.0, min(0.5, now_s - self._last_tick_s))
        self._last_tick_s = now_s
        lost = self.link.telemetry_lost(now_s)
        faults: list[str] = []
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
            if not self.bringup:
                faults += self._check_params(frame, now_s)
                self._gyro_calibration(frame, now_s)
        self._battery_rules(frame, lost)
        sense = Sense(self.odom.pose, self.odom.travelled_m, front, rear, dt)
        self._sense = sense
        vision_ok = self.vision is not None and now_s - self._vision_at <= cfg.vision_timeout_s
        engaged = vision_ok and now_s - self._engaged_at <= cfg.engage_lookaway_s + 1e-6
        facts = Facts(
            now_s=now_s, faults=faults, battery_low=self.battery_low,
            front_blocked=sense.front_min() < cfg.obstacle_stop_m,
            backend_ok=self.backend_ok,
            vision_engaged=engaged,
            conversation_active=self.conversation_active,
        )  # fmt: skip
        state_before = self.sup.state
        if self.bringup:
            self.sup._facts = facts  # robotd commands nothing while robotctl works
        else:
            self._do(self.sup.update(facts))
        if self.sup.state == "FAULT" and state_before != "FAULT" and self._seen_telemetry:
            self._blackbox("fault")  # (not for the start-up wait for the first telemetry)

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
        if (v or w) and not self.params_ok:
            v = w = 0.0  # never drive with parameters the ESP32 has not confirmed
        if (v or w) and frame is not None and frame.flags & Flags.GYRO_CALIBRATING:
            v = w = 0.0  # the ESP32 refuses DRIVE (BUSY) for the 2 s of gyro calibration
        if (v or w) and frame is not None and not frame.flags & Flags.MOTORS_ENABLED:
            # power-on state of the ESP32 is SAFE: ask for DRIVE mode, move when it is confirmed
            if now_s - self._last_mode_s >= 0.5:
                self._last_mode_s = now_s
                self.link.send(Mode(DriveMode.DRIVE))
            v = w = 0.0
        self.command_v = v
        if not self.bringup:
            self.link.set_drive(*track_speeds(v, w, self.track_width_m))

        # ---- camera pan servo: follow visiond's target, rate-limited ----
        if vision_ok and not lost and self.vision is not None and not self.bringup:
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

        # ---- page: state badge (+ battery) ----
        ui_state: dict[str, Any] = {
            "type": "local.state", "state": self.sup.state, "backend_ok": self.backend_ok,
            "is_simulated": self.is_simulated,
        }  # fmt: skip
        battery = self.battery_status()
        if "battery_pct" in battery:
            ui_state["battery_pct"] = battery["battery_pct"]
            ui_state["battery_warning"] = self.battery_warning
            ui_state["battery_low"] = self.battery_low
        if ui_state != self._ui_state or now_s - self._ui_state_s >= 2.0:
            self._ui_state, self._ui_state_s = ui_state, now_s
            self.ui_out.append(dict(ui_state))

    def _check_params(self, frame: Telemetry, now_s: float) -> list[str]:
        if self._need_push:
            self._push_params(now_s)
        if frame.param_hash == self.expected_hash:
            if not self.params_ok:
                log.info("ESP32 parameter hash %08x matches the calibration", frame.param_hash)
            self.params_ok = True
            return []
        if self.params_ok:
            log.warning(
                "ESP32 parameter hash %08x != expected %08x", frame.param_hash, self.expected_hash
            )
            self._event("param_hash_mismatch", {
                "esp32": f"{frame.param_hash:08x}", "expected": f"{self.expected_hash:08x}",
            })  # fmt: skip
        self.params_ok = False
        if now_s < self._grace_until:
            return []  # just pushed: the answer is on its way (motion is held meanwhile)
        if self._push_at is None or now_s - self._push_at >= self.cfg.param_push_retry_s:
            self._push_params(now_s, grace=False)
        return ["param_hash_mismatch"]

    def _gyro_calibration(self, frame: Telemetry, now_s: float) -> None:
        """CALIBRATE_GYRO at start (and after an ESP32 reboot) while still, and again when IDLE
        and still for `gyro_recal_idle_s`."""
        if frame.flags & Flags.GYRO_CALIBRATING or now_s - self._gyro_cal_sent_s < 3.0:
            return
        if self.link.target != (0.0, 0.0) or now_s - self._last_motion_s < 0.5:
            return
        if self.gyro_cal_due and self.sup.state not in ("ROAMING", "RETURNING"):
            self._gyro_cal_source = "start"
        elif (
            self.sup.state == "IDLE"
            and now_s - max(self._last_motion_s, self._gyro_cal_done_s)
            >= self.cfg.gyro_recal_idle_s
        ):
            self._gyro_cal_source = "still"
        else:
            return
        self.gyro_cal_due = False
        self._gyro_cal_sent_s = now_s
        self.link.send(CalibrateGyro())

    def _battery_rules(self, frame: Telemetry | None, lost: bool) -> None:
        cfg, volts = self.cfg, self.battery_v
        if volts is None or lost or frame is None:
            low = bool(frame is not None and not lost and frame.flags & Flags.LOW_BATTERY)
            self.battery_low = low
            return
        if not self.battery_warning and volts < cfg.battery_warning_v:
            self.battery_warning = True
            self._event("battery_warning", {"battery_v": round(volts, 2)}, always=True)
        elif self.battery_warning and volts > cfg.battery_warning_v + BATTERY_HYSTERESIS_V:
            self.battery_warning = False
        if self.battery_low:
            self.battery_low = volts < cfg.battery_low_v + BATTERY_HYSTERESIS_V
        else:
            self.battery_low = volts < cfg.battery_low_v or bool(frame.flags & Flags.LOW_BATTERY)

    # ================= outputs =================
    def battery_status(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.battery_v is None:
            return out
        table = calib.battery_table(self.cal) or BATTERY_TABLE_3S
        out["battery_v"] = round(self.battery_v, 2)
        out["battery_pct"] = round(battery_percent(self.battery_v, table), 0)
        if self.battery_a is not None:
            out["battery_a"] = round(max(-100.0, min(100.0, self.battery_a)), 3)
        out["energy_wh"] = round(self.energy_wh, 4)
        out["battery_warning"] = self.battery_warning
        return out

    def firmware_status(self) -> dict[str, Any] | None:
        d = self.diag
        if d is None:
            return None
        env = d.build_env.name.lower().replace("_", "-")
        return {
            "version": d.version_text, "git": d.git_text, "param_hash": f"{d.param_hash:08x}",
            "pins_confirmed": bool(d.diag_flags & 1), "build_env": env[:32],
            "reset_reason": _RESET_NAMES.get(int(d.reset_reason), "unknown"),
        }  # fmt: skip

    def status(self, now_s: float) -> dict[str, Any]:
        """The `robot.status` message (about 1 Hz)."""
        cfg, frame = self.cfg, self.frame
        lost = self.link.telemetry_lost(now_s) or frame is None
        sensors: dict[str, str] = {"esp32_link": "fault" if lost else "ok"}
        faults = list(self.sup.faults)
        if self.battery_low and "low_battery" not in faults:
            faults.append("low_battery")
        out: dict[str, Any] = {
            "type": "robot.status", "state": self.sup.state, "is_simulated": self.is_simulated,
            "faults": faults, "mode": self.sup.mode,
        }  # fmt: skip
        if frame is not None and not lost:
            us = frame.ultrasonic_mm
            absent = sum(1 for v in us if v == US_ABSENT)
            front_absent = all(v == US_ABSENT for v in us[:3])
            faulty = front_absent or bool(frame.flags & Flags.ULTRASONIC_FAULT)
            sensors["encoders"] = "fault" if frame.flags & Flags.ENCODER_FAULT else "ok"
            sensors["imu"] = "ok" if frame.flags & Flags.IMU_OK else "fault"
            sensors["ultrasonic"] = "fault" if faulty else "degraded" if absent else "ok"
            out.update(self.battery_status())
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
        firmware = self.firmware_status()
        if firmware is not None:
            out["firmware"] = firmware
        out["calibration"] = self.calibration_status()
        if self.health:
            out["health"] = dict(self.health)
        return out

    def take_outbox(self, limit: int = 50) -> list[dict[str, Any]]:
        """Run messages to send now (the caller only takes them while the backend is connected)."""
        out, self.outbox = self.outbox[:limit], self.outbox[limit:]
        return out

    def take_calibration_report(self) -> dict[str, Any] | None:
        """`calibration.report` when one is due (backend connect, file change)."""
        if not self.cal_dirty:
            return None
        self.cal_dirty = False
        return self.cal.report()

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
        odom = self.cfg.odom_config(
            self.is_simulated, self.metres_per_tick, self.track_width_m,
            self.calibration_status(),
        )  # fmt: skip
        return {
            "type": "run.start", "is_simulated": self.is_simulated,
            "odom_config": odom, "notes": notes,
        }  # fmt: skip

    def _t_run_ms(self) -> int:
        return max(0, self.odom.t_ms - self._run_t0_ms)

    def _blackbox(self, reason: str) -> None:
        path = self.blackbox.dump(self.now_s, reason, self.is_simulated)
        if path is not None:
            self._event("blackbox", {"file": path.name, "reason": reason}, always=True)

    def _event(self, kind: str, data: dict[str, Any], always: bool = False) -> None:
        """`always`: also sent to the backend when no run is open (power warnings, black box)."""
        if self.is_simulated:
            data = {**data, "simulated": True}
        self.events = (self.events + [(kind, data)])[-200:]
        self.blackbox.event(self.now_s, kind, data)
        log.info("event %s %s%s", kind, data, " [SIMULATED]" if self.is_simulated else "")
        if self.sup.run_open or always or kind in ("return_done", "return_failed"):
            self._flush_samples()
            self._push({"type": "event", "t_ms": self._t_run_ms(), "kind": kind[:32], "data": data})
        if kind in BLACKBOX_TRIGGERS and kind != "fault":  # FAULT: on entering the state
            self._blackbox(kind)

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
        if self.battery_v is not None:
            power = {"v": round(self.battery_v, 2), "wh": round(self.energy_wh, 4)}
            if self.battery_a is not None:
                power["a"] = round(max(-100.0, min(100.0, self.battery_a)), 3)
            sample["power"] = power
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
