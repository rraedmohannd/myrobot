"""Pure maths on face landmarks (no OpenCV, no MediaPipe import).

Two landmark sources feed the same quantities:

* MediaPipe Face Landmarker (478 points, x/y as fractions of the analysed image, z = relative
  depth on the scale of x, smaller = closer to the camera). Mouth ratio and the facing rule are
  the SAME as in backend/app/static/speaker-camera.js.
* YuNet's 5 points (right eye, left eye, nose tip, right mouth corner, left mouth corner).
  This is the WEAK fallback: there is no lip point, so the real mouth opening cannot be seen.

Angle convention (image based): yaw > 0 = the face is turned toward the right side of the
image, pitch > 0 = the face looks up. These signs come from the geometry below and have not
been checked on a real face yet.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from visiond.config import DEFAULT_LANDMARKS

FOREHEAD = 10  # MediaPipe: top of the face oval
CHIN = 152  # MediaPipe: bottom of the face oval
# Nose-tip depth in front of the eye plane divided by the distance between the eyes: a rough
# value for an adult face, used only by the 5-point fallback.
NOSE_DEPTH_PER_EYE_SPAN = 0.5
# Where the nose tip sits between the eye line (0) and the mouth line (1) on a frontal face.
NOSE_HEIGHT_FRONTAL = 0.55
FALLBACK_PITCH_GAIN_DEG = 90.0  # degrees of pitch per unit of nose-height change (rough)


class Point(NamedTuple):
    x: float
    y: float
    z: float = 0.0


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def mouth_ratio(landmarks: Sequence[Point], lm: Mapping[str, int] = DEFAULT_LANDMARKS) -> float:
    """Inner-lip opening / mouth width: about 0 closed, 0.1-0.6 while talking (as the JS)."""
    width = _dist(landmarks[lm["mouthLeft"]], landmarks[lm["mouthRight"]])
    if width <= 1e-6:
        return 0.0
    return _dist(landmarks[lm["lipTop"]], landmarks[lm["lipBottom"]]) / width


def nose_position(left_x: float, right_x: float, nose_x: float) -> float | None:
    """Where the nose sits between two side references (0 = left, 1 = right, 0.5 = middle)."""
    span = right_x - left_x
    if abs(span) < 1e-6:
        return None
    return (nose_x - left_x) / span


def nose_position_mesh(
    landmarks: Sequence[Point], lm: Mapping[str, int] = DEFAULT_LANDMARKS
) -> float | None:
    """The prototype's `lookingAtCamera` measure: nose tip between the two cheeks."""
    return nose_position(
        landmarks[lm["cheekLeft"]].x, landmarks[lm["cheekRight"]].x, landmarks[lm["nose"]].x
    )


def is_facing(
    nose_pos: float | None,
    yaw_deg: float,
    pitch_deg: float,
    *,
    pos_min: float,
    pos_max: float,
    yaw_max_deg: float,
    pitch_max_deg: float,
) -> bool:
    """Facing the camera = the prototype's nose rule AND the head pose inside the limits."""
    if nose_pos is None or not (pos_min < nose_pos < pos_max):
        return False
    return abs(yaw_deg) <= yaw_max_deg and abs(pitch_deg) <= pitch_max_deg


def head_pose_mesh(
    landmarks: Sequence[Point],
    width_px: float,
    height_px: float,
    lm: Mapping[str, int] = DEFAULT_LANDMARKS,
) -> tuple[float, float]:
    """(yaw_deg, pitch_deg) from the 3-D direction cheek-to-cheek and forehead-to-chin.

    MediaPipe's z is an estimate, so expect errors of several degrees; good enough for a
    "facing / not facing" decision, not for measuring angles.
    """
    left, right = landmarks[lm["cheekLeft"]], landmarks[lm["cheekRight"]]
    top, bottom = landmarks[FOREHEAD], landmarks[CHIN]
    dx = (right.x - left.x) * width_px
    dz = (right.z - left.z) * width_px
    yaw = math.degrees(math.atan2(dz, dx)) if abs(dx) + abs(dz) > 1e-9 else 0.0
    dy = (bottom.y - top.y) * height_px
    dz_v = (top.z - bottom.z) * width_px
    pitch = math.degrees(math.atan2(dz_v, dy)) if abs(dy) + abs(dz_v) > 1e-9 else 0.0
    return yaw, pitch


class FivePoints(NamedTuple):
    """YuNet landmarks in image order: 'right'/'left' are the person's own right/left, so on
    an unmirrored image the right eye has the smaller x."""

    right_eye: Point
    left_eye: Point
    nose: Point
    mouth_right: Point
    mouth_left: Point


def head_pose_five(points: FivePoints) -> tuple[float, float]:
    """Rough (yaw_deg, pitch_deg) from 5 points.

    Yaw: on a turned head the nose tip moves sideways from the middle of the eyes by
    depth * sin(yaw) while the eye span shrinks by cos(yaw), so
    offset / span = NOSE_DEPTH_PER_EYE_SPAN * tan(yaw).
    Pitch: how far down the nose sits between the eye line and the mouth line, compared with
    a frontal face. This depends on the person's face shape; treat it as a hint only.
    """
    eye_span = points.left_eye.x - points.right_eye.x
    eye_mid_x = (points.left_eye.x + points.right_eye.x) / 2
    eye_mid_y = (points.left_eye.y + points.right_eye.y) / 2
    mouth_mid_y = (points.mouth_left.y + points.mouth_right.y) / 2
    yaw = 0.0
    if abs(eye_span) > 1e-6:
        offset = (points.nose.x - eye_mid_x) / abs(eye_span)
        yaw = math.degrees(math.atan(offset / NOSE_DEPTH_PER_EYE_SPAN))
    pitch = 0.0
    face_h = mouth_mid_y - eye_mid_y
    if abs(face_h) > 1e-6:
        height = (points.nose.y - eye_mid_y) / face_h
        pitch = max(-60.0, min(60.0, (NOSE_HEIGHT_FRONTAL - height) * FALLBACK_PITCH_GAIN_DEG))
    return yaw, pitch


def mouth_proxy_five(points: FivePoints) -> float:
    """WEAK stand-in for the mouth ratio: mouth width / eye distance.

    YuNet gives only the two mouth corners, so the opening itself is invisible. The width
    changes a little while a person talks (and also when they smile or turn the head), so the
    "activity" of this value is only a hint. It is not on the scale of `mouth_ratio`.
    """
    eye_span = _dist(points.left_eye, points.right_eye)
    if eye_span <= 1e-6:
        return 0.0
    return _dist(points.mouth_left, points.mouth_right) / eye_span
