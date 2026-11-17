"""Thresholds (shared with the browser prototype) and run-time settings of visiond.

No OpenCV import here: this module is used by the pure-logic tests.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from visiond.apriltag import TagConfig

ROBOT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = ROBOT_DIR.parent
DEFAULT_THRESHOLDS_PATH = REPO_DIR / "shared" / "vision_thresholds.json"
DEFAULT_MODELS_DIR = ROBOT_DIR / "models"
YUNET_FILE = "face_detection_yunet_2023mar.onnx"
LANDMARKER_FILE = "face_landmarker.task"


@dataclass(frozen=True)
class Thresholds:
    """Decision thresholds. The first block mirrors speaker-camera.js (kept in sync by a test).

    All of them are first guesses: none has been calibrated on real faces yet.
    """

    # --- same as the browser prototype (shared/vision_thresholds.json -> "prototype") ---
    iou_min: float = 0.25  # a detection continues a track when the boxes overlap this much
    max_age_ms: float = 1000.0  # a face unseen for longer gets a new id when it returns
    window_ms: float = 500.0  # mouth activity = std of the mouth ratio over this window
    activity_min: float = 0.025  # a face is "talking" in a frame at or above this activity
    overlap_share: float = 0.3  # 2+ talking faces in more than this share of voiced frames
    engage_ms: float = 1500.0  # facing the camera this long = engaged
    rearm_no_face_ms: float = 2000.0  # (prototype's greeting re-arm; reported, used by robotd)
    looking_pos_min: float = 0.3  # nose position between the cheeks / box edges
    looking_pos_max: float = 0.7
    max_faces: int = 4
    # --- robot only (shared/vision_thresholds.json -> "visiond") ---
    yaw_max_deg: float = 30.0  # |yaw| limit of "facing"
    pitch_max_deg: float = 25.0  # |pitch| limit of "facing"
    switch_hold_ms: float = 800.0  # another face must out-talk the selected one this long
    min_dwell_ms: float = 2000.0  # no second switch sooner than this after a switch
    fallback_activity_min: float = 0.02  # activity threshold of the weak YuNet mouth proxy
    assumed_vad_hangover_ms: float = 700.0  # --assume-speaking: silence that ends an utterance


def load_thresholds(path: Path | str | None = None) -> Thresholds:
    """Read shared/vision_thresholds.json. A missing file gives the built-in defaults."""
    file = Path(path) if path else DEFAULT_THRESHOLDS_PATH
    if not file.is_file():
        return Thresholds()
    data = json.loads(file.read_text(encoding="utf-8"))
    merged: dict[str, object] = {}
    merged.update(data.get("prototype", {}))
    merged.update(data.get("visiond", {}))
    known = {f.name for f in fields(Thresholds)}
    return Thresholds(**{k: v for k, v in merged.items() if k in known})  # type: ignore[arg-type]


def load_landmark_indices(path: Path | str | None = None) -> dict[str, int]:
    """MediaPipe landmark indices used for the mouth ratio and the facing rule."""
    file = Path(path) if path else DEFAULT_THRESHOLDS_PATH
    indices = dict(DEFAULT_LANDMARKS)
    if file.is_file():
        data = json.loads(file.read_text(encoding="utf-8"))
        indices.update(data.get("prototype", {}).get("landmarks", {}))
    return indices


DEFAULT_LANDMARKS: dict[str, int] = {
    "lipTop": 13,
    "lipBottom": 14,
    "mouthLeft": 78,
    "mouthRight": 308,
    "nose": 1,
    "cheekLeft": 234,
    "cheekRight": 454,
}


@dataclass(frozen=True)
class ServoConfig:
    """Pan controller settings. Angles follow docs/SERIAL_PROTOCOL.md: 0 = straight ahead,
    positive = left. The field of view and the limits are PLACEHOLDERS until the real webcam
    and the servo mount are known."""

    hfov_deg: float = 60.0  # horizontal field of view of the camera (placeholder)
    dead_band_deg: float = 3.0  # no movement while the face is this close to the centre
    smoothing: float = 0.4  # 0..1 weight of the newest error (exponential smoothing)
    gain: float = 0.6  # share of the smoothed error corrected per 0.1 s
    max_speed_dps: float = 60.0  # speed limit of the target
    min_deg: float = -80.0
    max_deg: float = 80.0
    max_lead_deg: float = 15.0  # the target never runs further ahead of the commanded angle
    recentre_after_ms: float = 3000.0  # nobody selected this long -> go back to 0 slowly
    recentre_speed_dps: float = 30.0


@dataclass(frozen=True)
class VisionConfig:
    """Run-time settings (environment variables, overridden by CLI flags)."""

    camera_source: str = "0"  # device index, http(s)/rtsp URL or a video file path
    robotd_url: str = "ws://127.0.0.1:8765/ws/vision"
    models_dir: Path = DEFAULT_MODELS_DIR
    landmarks: str = "auto"  # auto | mediapipe | yunet
    process_width: int = 640  # frames are scaled down to this width before detection
    max_fps: float = 15.0  # analysis rate limit (the prototype runs at about 15 fps)
    publish_hz: float = 10.0
    mirror: bool = False  # flip the frame horizontally (some phone apps mirror the front camera)
    detect_score_min: float = 0.7
    console: bool = False
    assume_speaking: bool = False
    sim_servo: bool = False  # SIMULATED servo: the camera is fixed, the servo is imaginary
    debug_window: bool = False
    loop_file: bool = False
    exit_at_eof: bool = False
    max_seconds: float = 0.0  # 0 = run until stopped
    reconnect_s: float = 2.0
    servo: ServoConfig = field(default_factory=ServoConfig)
    apriltag: TagConfig = field(default_factory=TagConfig)  # EXPERIMENTAL (S7), off by default


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def config_from_env(env: Mapping[str, str] | None = None) -> VisionConfig:
    """Build the settings from environment variables (all optional)."""
    e = os.environ if env is None else env
    base = VisionConfig()
    servo = ServoConfig(
        hfov_deg=float(e.get("CAMERA_HFOV_DEG", base.servo.hfov_deg)),
        dead_band_deg=float(e.get("SERVO_DEAD_BAND_DEG", base.servo.dead_band_deg)),
        max_speed_dps=float(e.get("SERVO_MAX_SPEED_DPS", base.servo.max_speed_dps)),
        min_deg=float(e.get("SERVO_MIN_DEG", base.servo.min_deg)),
        max_deg=float(e.get("SERVO_MAX_DEG", base.servo.max_deg)),
    )
    return replace(
        base,
        camera_source=e.get("CAMERA_SOURCE", base.camera_source),
        robotd_url=e.get("ROBOTD_VISION_URL", base.robotd_url),
        models_dir=Path(e.get("VISION_MODELS_DIR", str(base.models_dir))),
        landmarks=e.get("VISION_LANDMARKS", base.landmarks),
        process_width=int(e.get("VISION_PROCESS_WIDTH", base.process_width)),
        max_fps=float(e.get("VISION_MAX_FPS", base.max_fps)),
        mirror=_env_bool(e, "CAMERA_MIRROR", base.mirror),
        servo=servo,
        apriltag=TagConfig(
            enabled=_env_bool(e, "VISION_APRILTAG", base.apriltag.enabled),
            tag_id=int(e.get("VISION_APRILTAG_ID", base.apriltag.tag_id)),
            tag_size_m=float(e.get("VISION_APRILTAG_SIZE_M", base.apriltag.tag_size_m)),
        ),
    )


def parse_camera_source(source: str) -> int | str:
    """'0' -> 0 (device index); anything else stays a URL or a file path."""
    text = source.strip()
    return int(text) if text.isdigit() else text


def source_kind(source: int | str) -> str:
    """'device', 'url' or 'file'."""
    if isinstance(source, int):
        return "device"
    return "url" if "://" in source else "file"
