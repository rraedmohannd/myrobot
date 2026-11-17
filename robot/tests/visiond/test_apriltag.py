"""EXPERIMENTAL start-mark AprilTag (S7) on SYNTHETIC pictures only.

The tag is rendered with cv2.aruco.generateImageMarker, given a white paper border and projected
with a pinhole camera (known distance, lateral offset and turn of the tag) onto a plain
background, with noise. Nothing here is a camera measurement.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from visiond.__main__ import build_parser, config_from_args  # noqa: E402
from visiond.app import VisionApp  # noqa: E402
from visiond.apriltag import (  # noqa: E402
    TagConfig,
    TagDetector,
    TagSighting,
    sighting_from_corners,
    start_tag_field,
)
from visiond.config import ServoConfig, VisionConfig, config_from_env  # noqa: E402
from visiond.messages import START_TAG_KEYS, STATE_KEYS  # noqa: E402

pytestmark = pytest.mark.skipif(
    not hasattr(cv2, "aruco") or not hasattr(cv2.aruco, "ArucoDetector"),
    reason="needs OpenCV >= 4.7 with aruco",
)

W, H, HFOV = 640, 480, 60.0
FOCAL = (W / 2) / math.tan(math.radians(HFOV) / 2)
SIZE = 0.15  # black square, metres
MARGIN = 0.25  # white paper border as a share of the black square


def render(
    z: float, x: float = 0.0, yaw_deg: float = 0.0, tag_id: int = 0, seed: int = 1
) -> tuple[Any, float]:
    """A grey frame with the tag at depth z, x metres to the RIGHT, turned yaw_deg about the
    vertical axis. Returns (frame, true centre_x)."""
    marker_px = 400
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    marker = cv2.aruco.generateImageMarker(dictionary, tag_id, marker_px, borderBits=1)
    pad = int(marker_px * MARGIN)
    paper = cv2.copyMakeBorder(marker, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    half = SIZE * (1 + 2 * MARGIN) / 2
    yaw = math.radians(yaw_deg)
    pts = []
    for u, v in ((-half, -half), (half, -half), (half, half), (-half, half)):  # TL TR BR BL
        px, pz = x + u * math.cos(yaw), z + u * math.sin(yaw)
        pts.append((W / 2 + FOCAL * px / pz, H / 2 + FOCAL * v / pz))
    side = paper.shape[0]
    src = np.float32([(0, 0), (side, 0), (side, side), (0, side)])
    matrix = cv2.getPerspectiveTransform(src, np.float32(pts))
    rng = np.random.default_rng(seed)
    frame = np.full((H, W), 110, np.uint8)
    warped = cv2.warpPerspective(paper, matrix, (W, H), flags=cv2.INTER_LINEAR, borderValue=0)
    mask = cv2.warpPerspective(np.full_like(paper, 255), matrix, (W, H), borderValue=0)
    frame[mask > 127] = warped[mask > 127]
    noisy = np.clip(frame.astype(np.int16) + rng.normal(0, 6, frame.shape), 0, 255)
    bgr = cv2.cvtColor(noisy.astype(np.uint8), cv2.COLOR_GRAY2BGR)
    return bgr, (W / 2 + FOCAL * x / z) / W


def detector(tag_id: int = 0) -> TagDetector:
    return TagDetector(TagConfig(enabled=True, tag_id=tag_id, tag_size_m=SIZE), HFOV)


@pytest.mark.parametrize(
    ("z", "x", "yaw"),
    [
        (0.6, 0.0, 0.0),
        (1.0, 0.2, 0.0),
        (1.0, -0.2, 30.0),
        (1.5, 0.3, -20.0),
        (2.0, 0.0, 0.0),
        (2.0, -0.4, 25.0),
    ],
)
def test_detects_id_centre_and_distance(z: float, x: float, yaw: float) -> None:
    frame, true_cx = render(z, x, yaw)
    found = detector().detect(frame, pan_deg=0.0, now_ms=1000.0)
    assert found is not None and found.id == 0
    assert found.center_x == pytest.approx(true_cx, abs=0.01)
    true_distance = math.hypot(x, z)
    assert found.distance_m == pytest.approx(true_distance, rel=0.06)
    true_bearing = -math.degrees(math.atan2(x, z))  # positive = left
    assert found.bearing_deg == pytest.approx(true_bearing, abs=1.5)


def test_pan_angle_is_added_to_the_bearing() -> None:
    frame, _ = render(1.0, 0.2)
    plain = detector().detect(frame, pan_deg=0.0, now_ms=0.0)
    panned = detector().detect(frame, pan_deg=25.0, now_ms=0.0)
    assert plain is not None and panned is not None
    assert panned.bearing_deg == pytest.approx(plain.bearing_deg + 25.0, abs=1e-6)


def test_other_ids_and_empty_frames_are_ignored() -> None:
    frame, _ = render(1.0, tag_id=5)
    assert detector(tag_id=0).detect(frame, pan_deg=0.0, now_ms=0.0) is None
    found = detector(tag_id=5).detect(frame, pan_deg=0.0, now_ms=0.0)
    assert found is not None and found.id == 5
    blank = np.full((H, W, 3), 110, np.uint8)
    assert detector().detect(blank, pan_deg=0.0, now_ms=0.0) is None


def test_pinhole_geometry_straight_ahead() -> None:
    # a 0.15 m square 1 m away, centred: f*S/Z pixels high
    h = FOCAL * SIZE / 1.0
    c = [(W / 2 - h / 2, H / 2 - h / 2), (W / 2 + h / 2, H / 2 - h / 2)]
    c += [(W / 2 + h / 2, H / 2 + h / 2), (W / 2 - h / 2, H / 2 + h / 2)]
    s = sighting_from_corners(3, c, W, hfov_deg=HFOV, tag_size_m=SIZE, pan_deg=0.0, seen_ms=0)
    assert s.center_x == pytest.approx(0.5)
    assert s.distance_m == pytest.approx(1.0)
    assert s.bearing_deg == pytest.approx(0.0, abs=1e-9)


def test_start_tag_field_age_and_expiry() -> None:
    cfg = TagConfig(enabled=True, max_report_age_ms=2000)
    s = TagSighting(id=0, center_x=0.4, bearing_deg=5.0, distance_m=1.2, seen_ms=1000.0)
    field = start_tag_field(s, 1300.0, cfg)
    assert field is not None and set(field) == START_TAG_KEYS
    assert field["age_ms"] == 300 and field["experimental"] is True
    assert start_tag_field(s, 3500.0, cfg) is None
    assert start_tag_field(None, 0.0, cfg) is None


def test_disabled_by_default() -> None:
    assert VisionConfig().apriltag.enabled is False
    assert config_from_env({}).apriltag.enabled is False
    assert config_from_args(build_parser().parse_args([]), VisionConfig()).apriltag.enabled is False


def test_enabled_by_env_or_flag() -> None:
    cfg = config_from_env({"VISION_APRILTAG": "1", "VISION_APRILTAG_ID": "7"})
    assert cfg.apriltag.enabled and cfg.apriltag.tag_id == 7
    args = build_parser().parse_args(["--apriltag", "--apriltag-size-m", "0.1"])
    cfg = config_from_args(args, VisionConfig())
    assert cfg.apriltag.enabled and cfg.apriltag.tag_size_m == 0.1


class OneFrameCamera:
    eof = False
    ok = True

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def latest(self) -> tuple[int, Any]:
        return 1, None


class NoFaces:
    mode = "none (test)"

    def analyze(self, frame: Any) -> list:
        return []

    def close(self) -> None: ...


def _app(enabled: bool) -> VisionApp:
    cfg = VisionConfig(
        servo=ServoConfig(hfov_deg=HFOV), apriltag=TagConfig(enabled=enabled, tag_size_m=SIZE)
    )
    app = VisionApp(cfg, camera=OneFrameCamera(), analyzer=NoFaces())
    app._make_models()
    return app


def test_vision_state_has_start_tag_only_when_enabled() -> None:
    frame, _ = render(1.2, 0.1)
    off = _app(False)
    off._detect_tag(frame)
    off._step([], True, 15.0, 1)
    assert set(off.message()) == STATE_KEYS  # unchanged message when the option is off

    on = _app(True)
    on._detect_tag(frame)
    on._step([], True, 15.0, 1)
    message = on.message()
    assert set(message) == STATE_KEYS | {"start_tag"}
    tag = message["start_tag"]
    assert set(tag) == START_TAG_KEYS and tag["id"] == 0 and tag["experimental"] is True
    assert tag["distance_m"] == pytest.approx(math.hypot(1.2, 0.1), rel=0.06)
    # only numbers: no picture can travel in this field
    assert all(isinstance(v, (int, float, bool)) for v in tag.values())
