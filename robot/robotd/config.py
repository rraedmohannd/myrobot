"""robotd configuration: defaults, then an optional JSON file, then environment variables.

Secrets (ROBOT_TOKEN) come from the process environment or the config file given on the command
line, never from the repository's .env (CLAUDE.md rule 6).

*** The track width, the battery limits and the navigation distances are PLACEHOLDERS. Nothing
here was measured on the real Rover 5 yet. Measured values live in the CALIBRATION FILE
(robotd.calibration, sprint 5); `metres_per_tick` comes from that file only (no duplicate here),
and `track_width_m` / `battery_table` there override the placeholders below. ***
"""

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


@dataclass
class Config:
    # ---- ESP32 link ----
    serial_port: str = "/dev/esp32"  # udev name on the Pi; e.g. COM5 on Windows
    serial_baud: int = 460800
    sim: bool = False  # --sim: start the SIMULATED ESP32 in-process and connect over TCP
    sim_host: str = "127.0.0.1"
    sim_port: int = 8766
    sim_seed: int = 7
    drive_hz: float = 20.0  # DRIVE while moving
    heartbeat_hz: float = 10.0  # HEARTBEAT otherwise
    telemetry_timeout_ms: int = 500  # no TELEMETRY for this long -> FAULT, no more commands
    reconnect_s: float = 1.0

    # ---- calibration file (robotd.calibration); "" = the default path ----
    calibration_path: str = ""

    # ---- odometry (PLACEHOLDER, not measured; the calibration's track_width_m wins) ----
    track_width_m: float = 0.19
    slip_ratio: float = 0.25  # front/rear disagreement on one side (fraction of the motion)
    slip_min_ticks: int = 30  # ... checked only above this many ticks per window
    slip_window_s: float = 0.5

    # ---- navigation (proposed values, to be tuned on the floor) ----
    roam_speed_mps: float = 0.20
    turn_rate_rps: float = 1.0
    max_segment_m: float = 5.0
    min_segment_m: float = 1.5
    obstacle_stop_m: float = 0.45  # roaming: turn away below this (ESP32 hard stop: 0.25 m)
    obstacle_clear_m: float = 0.70  # a direction counts as free above this
    backoff_below_m: float = 0.22  # closer than this: back off first (only with rear sensing)
    backoff_distance_m: float = 0.15
    rear_clear_m: float = 0.40
    breadcrumb_spacing_m: float = 0.3
    breadcrumb_heading_rad: float = 0.5
    arrival_tolerance_m: float = 0.15  # proposed in ODOMETRY.md, to be approved by the supervisor
    crumb_tolerance_m: float = 0.12
    return_block_m: float = 0.40
    hard_stop_m: float = 0.28  # the ESP32 stops forward motion at 0.25 m: never push into that
    return_block_wait_s: float = 5.0  # breadcrumb mode: wait this long before giving up
    return_timeout_s: float = 180.0

    # ---- EXPERIMENTAL start-mark AprilTag (S7, docs/APRILTAG.md): OFF by default ----
    # A run that used it is marked (`apriltag_correction` events, odom_config) and is never
    # counted in any result unless Mohannd turns it on and says so in the report.
    apriltag_return: bool = False  # ROBOTD_APRILTAG_RETURN=1
    apriltag_id: int = 0  # ROBOTD_APRILTAG_ID; must match visiond's VISION_APRILTAG_ID
    apriltag_final_approach_m: float = 1.5  # used only this close to the odometry origin
    apriltag_max_age_ms: float = 400.0  # older sightings (visiond age + message age) are ignored
    apriltag_record_radius_m: float = 0.2  # the start sighting is recorded only this near (0,0)
    apriltag_min_m: float = 0.2  # plausible tag distances
    apriltag_max_m: float = 4.0
    apriltag_max_correction_m: float = 0.5  # a larger disagreement with odometry = inconsistent
    apriltag_blend: float = 0.3  # weight of a new sighting in the correction (smoothing)

    # ---- supervisor ----
    engage_timeout_s: float = 10.0  # ENGAGED without a conversation -> resume
    reengage_cooldown_s: float = 5.0  # after a conversation, ignore "engaged" for this long
    fault_clear_s: float = 2.0  # faults must be gone for this long before leaving FAULT
    resume_after_backend_loss: bool = True
    backend_lost_resume_s: float = 60.0  # ... but only if the backend was lost for <= this long
    engage_lookaway_s: float = 0.3  # "engaged" may drop for this long without counting
    battery_warning_v: float = 10.5  # 3S Li-ion (placeholder): event + status flag
    battery_low_v: float = 9.9  # below: no roaming, no new return (placeholder, 3.3 V per cell)
    battery_empty_v: float = 9.0
    battery_full_v: float = 12.6
    gyro_still_s: float = 2.0  # robot still this long -> robotd re-estimates the residual bias
    gyro_recal_idle_s: float = 120.0  # IDLE and still this long -> a new CALIBRATE_GYRO
    gyro_bias_alpha: float = 0.3  # weight of a new still-window estimate in the running bias
    param_push_retry_s: float = 2.0  # hash still wrong this long after a push -> push again
    servo_min_interval_s: float = 0.1
    servo_min_change_deg: float = 0.5
    vision_timeout_s: float = 1.5  # no vision.state for this long -> camera "absent"

    # ---- backend link ----
    # one address, or several separated by commas: tried in order, reconnect cycles through them
    backend_url: str = "ws://localhost:8000/ws/robot"
    backend_index: int = 0  # which of them is in use (set by the backend link)
    robot_token: str = ""
    robot_id: str = "robotd"
    backend_reconnect_s: float = 2.0
    backend_heartbeat_s: float = 5.0
    backend_lost_after_s: float = 15.0
    status_period_s: float = 1.0
    sample_period_ms: int = 100  # 10 Hz pose samples
    telemetry_batch: int = 10
    outbox_max: int = 2000  # bounded buffer of run messages while the backend is away

    # ---- black box, settings page ----
    log_dir: str = ""  # LOG_DIR; default /var/log/smart-campus-robot on Linux, else ./logs
    blackbox_s: float = 60.0
    blackbox_keep: int = 50
    settings_pin: str = ""  # ROBOTD_SETTINGS_PIN; empty = settings page disabled
    settings_file: str = ""  # local override file (backend addresses); default next to the cal.

    # ---- local UI server ----
    ui_host: str = "127.0.0.1"
    ui_port: int = 8765
    assets_dir: str = ""  # a local copy of backend/app/static (else: synced over HTTP)
    cache_dir: str = ""  # where synced assets are kept; default ~/.cache/robotd/assets
    page_backend: str = ""  # host:port the PAGE uses for /ws/robot; default: from backend_url

    @property
    def backend_urls(self) -> list[str]:
        urls = [u.strip() for u in self.backend_url.split(",") if u.strip()]
        return urls or ["ws://localhost:8000/ws/robot"]

    @property
    def current_backend_url(self) -> str:
        urls = self.backend_urls
        return urls[self.backend_index % len(urls)]

    @property
    def backend_http(self) -> str:
        """http(s)://host:port of the backend in use, derived from its WebSocket address."""
        url = self.current_backend_url
        scheme = "https" if url.startswith("wss://") else "http"
        rest = url.split("://", 1)[-1]
        return f"{scheme}://{rest.split('/', 1)[0]}"

    @property
    def page_backend_host(self) -> str:
        return self.page_backend or self.backend_http.split("://", 1)[1]

    @property
    def cache_path(self) -> Path:
        return Path(self.cache_dir) if self.cache_dir else Path.home() / ".cache/robotd/assets"

    def odom_config(
        self, is_simulated: bool, metres_per_tick: float = 0.0, track_width_m: float | None = None,
        calibration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        """Stored with every run (robot_runs.odom_config), so a run documents its own settings.
        `calibrated` is true only for a complete calibration that is not simulated."""
        cal = calibration or {"status": "none", "is_simulated": False}
        out: dict[str, Any] = {
            "simulated": is_simulated,
            "source": "robotd",
            "metres_per_tick": metres_per_tick,
            "track_width_m": self.track_width_m if track_width_m is None else track_width_m,
            "calibrated": cal.get("status") == "complete" and not cal.get("is_simulated"),
            "calibration": cal,
            "arrival_tolerance_m": self.arrival_tolerance_m,
            "sample_rate_hz": round(1000 / self.sample_period_ms, 1),
            "roam_speed_mps": self.roam_speed_mps,
        }
        if self.apriltag_return:
            out["apriltag_return"] = "EXPERIMENTAL - exclude this run from results"
        if is_simulated:
            out["note"] = "SIMULATED run (simulated ESP32) - not a physical result"
        return out


_ENV = {
    "BACKEND_URL": "backend_url",
    "ROBOT_TOKEN": "robot_token",
    "ROBOT_ID": "robot_id",
    "ROBOTD_SERIAL_PORT": "serial_port",
    "ROBOTD_UI_PORT": "ui_port",
    "ROBOTD_UI_HOST": "ui_host",
    "ROBOTD_SIM_PORT": "sim_port",
    "ROBOTD_ASSETS_DIR": "assets_dir",
    "ROBOTD_CACHE_DIR": "cache_dir",
    "ROBOTD_PAGE_BACKEND": "page_backend",
    "ROBOTD_CALIBRATION": "calibration_path",
    "LOG_DIR": "log_dir",
    "ROBOTD_SETTINGS_PIN": "settings_pin",
    "ROBOTD_SETTINGS_FILE": "settings_file",
    "ROBOTD_APRILTAG_RETURN": "apriltag_return",
    "ROBOTD_APRILTAG_ID": "apriltag_id",
}


def _coerce(current: Any, value: Any) -> Any:
    if isinstance(current, bool):
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
    return type(current)(value)


def load_config(
    path: str | None = None, env: dict[str, str] | None = None, **overrides: Any
) -> Config:  # fmt: skip
    """Defaults < JSON file < environment < explicit overrides (None values are ignored)."""
    cfg = Config()
    known = {f.name for f in fields(Config)}
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for key, value in data.items():
            if key not in known:
                raise ValueError(f"unknown config key: {key}")
            setattr(cfg, key, _coerce(getattr(cfg, key), value))
    source = os.environ if env is None else env
    for name, key in _ENV.items():
        if source.get(name):
            setattr(cfg, key, _coerce(getattr(cfg, key), source[name]))
    for key, value in overrides.items():
        if value is not None:
            setattr(cfg, key, _coerce(getattr(cfg, key), value))
    return cfg


def public_dict(cfg: Config) -> dict[str, Any]:
    """The configuration without the token (for logs)."""
    out = asdict(cfg)
    out["robot_token"] = "set" if cfg.robot_token else ""
    out["settings_pin"] = "set" if cfg.settings_pin else ""
    return out
