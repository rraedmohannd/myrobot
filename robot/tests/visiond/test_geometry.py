"""Landmark maths with synthetic points (no model, no picture)."""

from __future__ import annotations

import math

import pytest

from visiond.config import DEFAULT_LANDMARKS as LM
from visiond.geometry import (
    CHIN,
    FOREHEAD,
    FivePoints,
    Point,
    head_pose_five,
    head_pose_mesh,
    is_facing,
    mouth_proxy_five,
    mouth_ratio,
    nose_position,
    nose_position_mesh,
)

LIMITS = {"pos_min": 0.3, "pos_max": 0.7, "yaw_max_deg": 30.0, "pitch_max_deg": 25.0}


def mesh(ratio: float = 0.0, nose_pos: float = 0.5, yaw_deg: float = 0.0, pitch_deg: float = 0.0):
    """478 MediaPipe-style points of a flat face in a square picture, turned by yaw/pitch.

    Coordinates are fractions of the picture; z is on the scale of x, smaller = closer.
    """
    points = [Point(0.5, 0.5, 0.0)] * 478
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)

    def place(u: float, v: float) -> Point:
        # (u, v) on the face plane, u to the image right, v down; half-width 0.2.
        # Positive yaw turns the face toward the image right: its right side moves away.
        # Positive pitch looks up: the chin comes closer, the forehead moves away.
        x = u * math.cos(yaw)
        z = u * math.sin(yaw) - v * math.sin(pitch)
        y = v * math.cos(pitch)
        return Point(0.5 + x, 0.5 + y, z)

    points[LM["cheekLeft"]] = place(-0.2, 0.0)
    points[LM["cheekRight"]] = place(0.2, 0.0)
    points[FOREHEAD] = place(0.0, -0.25)
    points[CHIN] = place(0.0, 0.25)
    left, right = points[LM["cheekLeft"]].x, points[LM["cheekRight"]].x
    points[LM["nose"]] = Point(left + nose_pos * (right - left), 0.5, -0.05)
    width = 0.1
    points[LM["mouthLeft"]] = Point(0.5 - width / 2, 0.62)
    points[LM["mouthRight"]] = Point(0.5 + width / 2, 0.62)
    points[LM["lipTop"]] = Point(0.5, 0.62 - ratio * width / 2)
    points[LM["lipBottom"]] = Point(0.5, 0.62 + ratio * width / 2)
    return points


def test_mouth_ratio_is_opening_over_width() -> None:
    assert mouth_ratio(mesh(0.4)) == pytest.approx(0.4)
    assert mouth_ratio(mesh(0.0)) == 0.0
    flat = mesh(0.4)
    flat[LM["mouthRight"]] = flat[LM["mouthLeft"]]
    assert mouth_ratio(flat) == 0.0  # zero width does not divide


def test_nose_rule_is_the_prototype_rule() -> None:
    assert nose_position_mesh(mesh(nose_pos=0.5)) == pytest.approx(0.5)
    assert is_facing(nose_position_mesh(mesh(nose_pos=0.5)), 0, 0, **LIMITS)
    assert not is_facing(nose_position_mesh(mesh(nose_pos=0.1)), 0, 0, **LIMITS)
    assert not is_facing(0.3, 0, 0, **LIMITS)  # the limits themselves are outside, as in the JS
    assert is_facing(0.31, 0, 0, **LIMITS)
    assert nose_position(0.4, 0.4, 0.4) is None
    assert not is_facing(None, 0, 0, **LIMITS)


def test_facing_needs_yaw_and_pitch_inside_the_limits() -> None:
    assert is_facing(0.5, 29, -24, **LIMITS)
    assert not is_facing(0.5, 31, 0, **LIMITS)
    assert not is_facing(0.5, -31, 0, **LIMITS)
    assert not is_facing(0.5, 0, 26, **LIMITS)
    assert not is_facing(0.5, 0, -26, **LIMITS)


@pytest.mark.parametrize("yaw", [-40.0, -15.0, 0.0, 20.0, 45.0])
def test_mesh_yaw_is_recovered_with_its_sign(yaw: float) -> None:
    got_yaw, got_pitch = head_pose_mesh(mesh(yaw_deg=yaw), 300, 300)
    assert got_yaw == pytest.approx(yaw, abs=0.01)
    assert got_pitch == pytest.approx(0.0, abs=0.01)


@pytest.mark.parametrize("pitch", [-30.0, 0.0, 25.0])
def test_mesh_pitch_is_recovered_with_its_sign(pitch: float) -> None:
    got_yaw, got_pitch = head_pose_mesh(mesh(pitch_deg=pitch), 300, 300)
    assert got_pitch == pytest.approx(pitch, abs=0.01)
    assert got_yaw == pytest.approx(0.0, abs=0.01)


def test_mesh_pose_uses_the_crop_size_for_non_square_crops() -> None:
    # The same face in a crop twice as wide as high: x fractions shrink, z follows x.
    square = mesh(yaw_deg=20.0)
    wide = [Point(p.x / 2, p.y, p.z / 2) for p in square]
    yaw, _ = head_pose_mesh(wide, 600, 300)
    assert yaw == pytest.approx(20.0, abs=0.01)


def five(yaw_deg: float = 0.0, nose_height: float = 0.55, mouth_width: float = 50.0) -> FivePoints:
    """YuNet-style points (pixels) of a head turned by yaw: eye span 60 px, nose depth 30 px."""
    yaw = math.radians(yaw_deg)
    half_eye, depth, cx = 30.0, 30.0, 200.0
    eye_y, mouth_y = 100.0, 170.0
    return FivePoints(
        right_eye=Point(cx - half_eye * math.cos(yaw), eye_y),
        left_eye=Point(cx + half_eye * math.cos(yaw), eye_y),
        nose=Point(cx + depth * math.sin(yaw), eye_y + nose_height * (mouth_y - eye_y)),
        mouth_right=Point(cx - mouth_width / 2, mouth_y),
        mouth_left=Point(cx + mouth_width / 2, mouth_y),
    )


@pytest.mark.parametrize("yaw", [-35.0, -10.0, 0.0, 10.0, 35.0])
def test_five_point_yaw_follows_the_geometric_model(yaw: float) -> None:
    got_yaw, got_pitch = head_pose_five(five(yaw))
    assert got_yaw == pytest.approx(yaw, abs=0.01)
    assert got_pitch == pytest.approx(0.0, abs=0.01)


def test_five_point_pitch_is_only_a_direction_hint() -> None:
    up, _down = head_pose_five(five(nose_height=0.40))[1], None
    down = head_pose_five(five(nose_height=0.70))[1]
    assert up > 5 and down < -5


def test_five_point_degenerate_input_gives_zero() -> None:
    p = Point(10.0, 10.0)
    assert head_pose_five(FivePoints(p, p, p, p, p)) == (0.0, 0.0)
    assert mouth_proxy_five(FivePoints(p, p, p, p, p)) == 0.0


def test_mouth_proxy_is_width_over_eye_distance() -> None:
    assert mouth_proxy_five(five(mouth_width=48.0)) == pytest.approx(0.8)
    assert mouth_proxy_five(five(mouth_width=54.0)) > mouth_proxy_five(five(mouth_width=48.0))
