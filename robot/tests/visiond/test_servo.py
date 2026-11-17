"""Pan controller: dead band, smoothing, speed limit, angle limits, SIMULATED servo."""

from __future__ import annotations

import pytest

from visiond.config import ServoConfig
from visiond.servo import (
    PanController,
    SimulatedServo,
    bearing_right_deg,
    error_for_fixed_camera,
)

DT_MS = 1000.0 / 15.0
CFG = ServoConfig()


def drive(controller: PanController, errors: list[float | None], t0: float = 0.0) -> list[float]:
    return [controller.update(e, t0 + i * DT_MS) for i, e in enumerate(errors)]


def test_bearing_from_the_picture_position() -> None:
    assert bearing_right_deg(0.5, 60) == pytest.approx(0.0)
    assert bearing_right_deg(1.0, 60) == pytest.approx(30.0)
    assert bearing_right_deg(0.0, 60) == pytest.approx(-30.0)
    assert 0 < bearing_right_deg(0.75, 60) < 15.0 + 1.5  # pinhole: a bit more than half


def test_dead_band_holds_still() -> None:
    controller = PanController(CFG)
    assert drive(controller, [CFG.dead_band_deg - 0.5] * 60) == [0.0] * 60
    assert drive(controller, [-(CFG.dead_band_deg - 0.5)] * 60, t0=5000) == [0.0] * 60


def test_face_to_the_right_turns_right_which_is_negative_pan() -> None:
    right = drive(PanController(CFG), [10.0] * 10)
    left = drive(PanController(CFG), [-10.0] * 10)
    assert right[-1] < 0 < left[-1]  # positive pan = left (docs/SERIAL_PROTOCOL.md)
    assert right[-1] == pytest.approx(-left[-1])


def test_speed_limit_per_step() -> None:
    targets = [0.0, *drive(PanController(CFG), [40.0] * 30)]
    steps = [abs(b - a) for a, b in zip(targets, targets[1:], strict=False)]
    assert max(steps) <= CFG.max_speed_dps * DT_MS / 1000 + 1e-9
    assert max(steps) > 0


def test_angle_limits() -> None:
    cfg = ServoConfig(min_deg=-20, max_deg=25, max_speed_dps=600)
    assert min(drive(PanController(cfg), [29.0] * 200)) == pytest.approx(-20.0)
    assert max(drive(PanController(cfg), [-29.0] * 200)) == pytest.approx(25.0)


def test_smoothing_damps_a_single_frame_jump() -> None:
    spike = drive(PanController(CFG), [0.0] * 5 + [25.0] + [0.0] * 10)
    steady = drive(PanController(CFG), [0.0] * 5 + [25.0] * 11)
    assert abs(spike[5]) < abs(steady[-1])
    first_raw_step = 25.0 - CFG.dead_band_deg
    assert abs(spike[5]) < first_raw_step * 0.5  # one noisy frame moves the target only a bit
    assert abs(spike[-1] - spike[8]) < 0.5  # and it settles instead of continuing


def test_closed_loop_centres_the_face_without_oscillating() -> None:
    """Camera on the (SIMULATED) servo: the error shrinks as the servo turns."""
    controller, servo = PanController(CFG), SimulatedServo()
    face_bearing = 22.0  # degrees right of straight ahead, in the room
    angles = []
    for i in range(150):  # 10 s
        t = i * DT_MS
        controller.set_actual(servo.angle_deg)
        error = error_for_fixed_camera(face_bearing, servo.angle_deg)
        angles.append(servo.step(controller.update(error, t), t))
    assert abs(angles[-1] + face_bearing) <= CFG.dead_band_deg + 0.5  # pan = -bearing (right)
    assert min(angles) >= -face_bearing - 0.5  # no overshoot past the face
    assert all(b <= a + 1e-9 for a, b in zip(angles, angles[1:], strict=False))  # monotonic


def test_closed_loop_follows_a_face_that_moves() -> None:
    controller, servo = PanController(CFG), SimulatedServo()
    for i in range(300):
        t = i * DT_MS
        bearing = -20.0 if i < 150 else 15.0  # the person walks from the left to the right
        controller.set_actual(servo.angle_deg)
        servo.step(controller.update(error_for_fixed_camera(bearing, servo.angle_deg), t), t)
        if i == 149:
            assert servo.angle_deg == pytest.approx(20.0, abs=CFG.dead_band_deg + 0.5)
    assert servo.angle_deg == pytest.approx(-15.0, abs=CFG.dead_band_deg + 0.5)


def test_target_does_not_run_ahead_of_a_slow_servo() -> None:
    controller = PanController(CFG)
    controller.set_actual(0.0)  # robotd reports that the servo has not moved
    targets = drive(controller, [-29.0] * 100)
    assert max(targets) == pytest.approx(CFG.max_lead_deg)


def test_no_face_holds_then_recentres_slowly() -> None:
    controller = PanController(CFG)
    drive(controller, [-20.0] * 40)
    start = controller.target_deg
    assert start > 5
    t0 = 40 * DT_MS
    held = [controller.update(None, t0 + i * DT_MS) for i in range(30)]  # 2 s
    assert held == [start] * 30
    later = [controller.update(None, t0 + 3000 + i * DT_MS) for i in range(150)]
    steps = [abs(b - a) for a, b in zip(later, later[1:], strict=False)]
    assert max(steps) <= CFG.recentre_speed_dps * DT_MS / 1000 + 1e-9
    assert later[-1] == pytest.approx(0.0)


def test_simulated_servo_is_labelled_and_slew_limited() -> None:
    servo = SimulatedServo(slew_dps=90.0, min_deg=-30, max_deg=30)
    assert servo.is_simulated is True
    servo.step(60.0, 0.0)
    assert servo.step(60.0, 100.0) == pytest.approx(9.0)  # 90 deg/s for 0.1 s
    for i in range(2, 100):
        servo.step(60.0, i * 100.0)
    assert servo.angle_deg == pytest.approx(30.0)  # clamped to its limit
