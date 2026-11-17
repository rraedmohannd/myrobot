"""EXPERIMENTAL start-mark AprilTag (sprint 5, S7). Off by default (`VISION_APRILTAG=1` /
`--apriltag`). See docs/APRILTAG.md.

An AprilTag 36h11 printed next to the start mark is found with OpenCV's aruco module
(`cv2.aruco.ArucoDetector`, OpenCV >= 4.7). Only numbers leave this module: the tag id, its centre
as a fraction of the frame width, a bearing and a distance. No picture is kept or sent.

Geometry (pinhole model, PLACEHOLDER field of view until the real webcam is measured):
- `bearing_deg` is relative to the ROBOT BODY, positive = LEFT (same sign as the pan servo and
  the odometry heading): bearing = pan angle of the camera - angle right of the picture centre.
- `distance_m` is the range from the camera to the tag centre, from the apparent height of the
  tag (mean of its two vertical edges, which a turn of the tag about the vertical axis does not
  shorten) and the printed size of the black square (`tag_size_m`).

Nothing here was measured with a real camera or a printed tag: every number is a model output.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

Corner = tuple[float, float]


@dataclass(frozen=True)
class TagConfig:
    enabled: bool = False  # EXPERIMENTAL, off by default
    tag_id: int = 0  # the id printed at the start mark
    tag_size_m: float = 0.15  # side of the BLACK square (not the paper), see docs/APRILTAG.md
    max_report_age_ms: float = 2000.0  # older sightings are no longer reported


@dataclass(frozen=True)
class TagSighting:
    """One detection of the start tag, already reduced to numbers."""

    id: int
    center_x: float  # 0 = left edge of the picture, 1 = right edge
    bearing_deg: float  # from the robot's forward axis, positive = left
    distance_m: float  # camera -> tag centre (pinhole model)
    seen_ms: float  # visiond clock when the frame was analysed


def _dist(a: Corner, b: Corner) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def sighting_from_corners(
    tag_id: int,
    corners: Sequence[Corner],
    frame_width: int,
    *,
    hfov_deg: float,
    tag_size_m: float,
    pan_deg: float,
    seen_ms: float,
) -> TagSighting:
    """Pinhole geometry from the four corners (aruco order: TL, TR, BR, BL), pixels."""
    tl, tr, br, bl = corners[:4]
    cx = (tl[0] + tr[0] + br[0] + bl[0]) / 4.0
    centre_x = cx / frame_width
    half = math.radians(hfov_deg) / 2.0
    focal_px = (frame_width / 2.0) / math.tan(half)
    right_rad = math.atan((cx - frame_width / 2.0) / focal_px)
    height_px = (_dist(tl, bl) + _dist(tr, br)) / 2.0
    depth_m = focal_px * tag_size_m / max(height_px, 1e-6)
    distance_m = depth_m / max(math.cos(right_rad), 1e-6)
    return TagSighting(
        id=int(tag_id),
        center_x=centre_x,
        bearing_deg=pan_deg - math.degrees(right_rad),
        distance_m=distance_m,
        seen_ms=seen_ms,
    )


def start_tag_field(sighting: TagSighting | None, now_ms: float, cfg: TagConfig) -> dict | None:
    """The optional `start_tag` field of `vision.state`, or None (field left out)."""
    if sighting is None:
        return None
    age = max(0.0, now_ms - sighting.seen_ms)
    if age > cfg.max_report_age_ms:
        return None
    return {
        "id": sighting.id,
        "center_x": round(max(0.0, min(1.0, sighting.center_x)), 4),
        "bearing_deg": round(sighting.bearing_deg, 2),
        "distance_m": round(sighting.distance_m, 3),
        "age_ms": int(age),
        "experimental": True,
    }


class TagDetector:
    """AprilTag 36h11 detector (OpenCV aruco). OpenCV is imported only when this is built."""

    def __init__(self, cfg: TagConfig, hfov_deg: float) -> None:
        import cv2

        aruco = cv2.aruco
        if not hasattr(aruco, "ArucoDetector"):
            raise RuntimeError("AprilTag needs OpenCV >= 4.7 (cv2.aruco.ArucoDetector)")
        dictionary = aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_36h11)
        params = aruco.DetectorParameters()
        params.cornerRefinementMethod = aruco.CORNER_REFINE_SUBPIX
        self._cv2 = cv2
        self._detector = aruco.ArucoDetector(dictionary, params)
        self.cfg = cfg
        self.hfov_deg = hfov_deg

    def detect(self, frame: Any, *, pan_deg: float, now_ms: float) -> TagSighting | None:
        """The configured tag in this frame, or None. Other ids are ignored."""
        gray = frame if frame.ndim == 2 else self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2GRAY)
        corners, ids, _rejected = self._detector.detectMarkers(gray)
        if ids is None:
            return None
        width = gray.shape[1]
        for tag_corners, tag_id in zip(corners, ids.flatten(), strict=False):
            if int(tag_id) != self.cfg.tag_id:
                continue
            pts = [(float(x), float(y)) for x, y in tag_corners.reshape(-1, 2)]
            return sighting_from_corners(
                int(tag_id),
                pts,
                width,
                hfov_deg=self.hfov_deg,
                tag_size_m=self.cfg.tag_size_m,
                pan_deg=pan_deg,
                seen_ms=now_ms,
            )
        return None
