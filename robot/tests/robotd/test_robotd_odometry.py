"""Pi-side pose estimator (docs/ODOMETRY.md section 3) and the shared maths package."""

import math

import pytest
from scr_odometry import Pose, range_along, ray_box, update_pose, vector_to_origin, wrap_angle
from scr_serial import Flags, Telemetry

from robotd.odometry import OdometryEstimator

MPT, WIDTH = 0.0005, 0.19  # placeholders, as in the config


def frame(t_ms: int, enc: tuple[int, int, int, int], gyro: float = 0.0) -> Telemetry:
    return Telemetry(
        t_ms & 0xFFFFFFFF, enc, gyro, (0, 0, 1000), (0xFFFF,) * 4, 11800, 400, Flags.IMU_OK
    )


def make() -> OdometryEstimator:
    return OdometryEstimator(MPT, WIDTH)


def test_straight_line_uses_ticks_times_metres_per_tick() -> None:
    est = make()
    assert est.update(frame(0, (0, 0, 0, 0))) is None  # the first frame is only a reference
    step = None
    for i in range(1, 51):
        step = est.update(frame(i * 20, (i * 100,) * 4))
    assert est.pose.x == pytest.approx(50 * 100 * MPT)
    assert est.pose.y == pytest.approx(0.0)
    assert step is not None and step.v_mps == pytest.approx(100 * MPT / 0.02)
    assert est.travelled_m == pytest.approx(2.5)


def test_side_is_mean_of_front_and_rear() -> None:
    est = make()
    est.update(frame(0, (0, 0, 0, 0)))
    est.update(frame(20, (100, 120, 100, 120)))
    assert est.left_m == pytest.approx(110 * MPT)
    assert est.pose.x == pytest.approx(110 * MPT)


def test_heading_comes_from_gyro_and_encoder_heading_is_only_logged() -> None:
    est = make()
    est.update(frame(0, (0, 0, 0, 0)))
    # turning in place: the tracks slip, so the encoders claim more rotation than the gyro
    for i in range(1, 51):
        est.update(frame(i * 20, (-i * 5, -i * 5, i * 5, i * 5), gyro=1.0))
    assert est.pose.theta == pytest.approx(1.0)  # 1 rad/s for 1 s
    assert est.enc_theta == pytest.approx(2 * 250 * MPT / WIDTH)
    assert est.enc_theta != pytest.approx(est.pose.theta)
    assert math.hypot(est.pose.x, est.pose.y) < 1e-9


def test_arc_matches_shared_update_pose() -> None:
    est, ref = make(), Pose()
    est.update(frame(0, (0, 0, 0, 0)))
    for i in range(1, 101):
        est.update(frame(i * 20, (i * 40,) * 4, gyro=0.5))
        ref = update_pose(ref, 40 * MPT, 40 * MPT, 0.5, 0.02)
    assert (est.pose.x, est.pose.y, est.pose.theta) == pytest.approx((ref.x, ref.y, ref.theta))


def test_t_ms_wraps_at_u32() -> None:
    est = make()
    start = 0xFFFFFFFF - 50
    est.update(frame(start, (0, 0, 0, 0)))
    for i in range(1, 11):  # crosses 2**32 on the way
        step = est.update(frame(start + i * 20, (i * 100,) * 4, gyro=0.1))
        assert step is not None and step.dt_s == pytest.approx(0.02) and not step.restarted
    assert est.pose.theta == pytest.approx(0.1 * 0.2)
    assert est.pose.x > 0.49
    assert est.t_ms == start + 200  # unwrapped, keeps growing


def test_esp32_restart_does_not_move_the_pose() -> None:
    est = make()
    est.update(frame(100_000, (5000,) * 4))
    est.update(frame(100_020, (5100,) * 4))
    before = (est.pose.x, est.pose.y)
    step = est.update(frame(40, (0, 0, 0, 0)))  # clock and counters start again
    assert step is not None and step.restarted
    assert (est.pose.x, est.pose.y) == before
    est.update(frame(60, (100,) * 4))
    assert est.pose.x == pytest.approx(before[0] + 100 * MPT)


def test_slip_report_when_front_and_rear_disagree() -> None:
    est = make()
    est.update(frame(0, (0, 0, 0, 0)))
    reports = []
    for i in range(1, 60):
        step = est.update(frame(i * 20, (i * 50, i * 100, i * 100, i * 100)))
        if step and step.slip:
            reports.append(step.slip)
    assert reports and reports[0].side == "left"
    assert reports[0].front_ticks < reports[0].rear_ticks


def test_no_slip_report_for_small_differences() -> None:
    est = make()
    est.update(frame(0, (0, 0, 0, 0)))
    steps = [est.update(frame(i * 20, (i * 100, i * 102, i * 100, i * 99))) for i in range(1, 100)]
    assert not any(s and s.slip for s in steps)


def test_reset_makes_the_current_place_the_origin() -> None:
    est = make()
    est.update(frame(0, (0, 0, 0, 0)))
    est.update(frame(20, (1000,) * 4, gyro=1.0))
    est.reset()
    assert (est.pose.x, est.pose.theta, est.travelled_m) == (0.0, 0.0, 0.0)
    est.update(frame(40, (1100,) * 4))
    assert est.pose.x == pytest.approx(100 * MPT)


def test_shared_package_exports() -> None:
    assert abs(wrap_angle(3 * math.pi)) == pytest.approx(math.pi)
    distance, bearing = vector_to_origin(Pose(1.0, 1.0, 0.0))
    assert distance == pytest.approx(math.sqrt(2)) and bearing == pytest.approx(-3 * math.pi / 4)
    room = (-1.0, -1.0, 5.0, 1.0)
    assert ray_box(0, 0, 1, 0, room, inside=True) == pytest.approx(5.0)
    assert range_along(0, 0, 0.0, room, [(2.0, -0.5, 3.0, 0.5)]) == pytest.approx(2.0)
