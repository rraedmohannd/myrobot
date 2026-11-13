"""Pose estimation for the Rover 5 (tracked skid-steer), exactly as in docs/ODOMETRY.md section 3.

    ds     = (dL + dR) / 2            track distances from the encoders
    dtheta = gyro_z * dt              primary heading source (tracks slip during turns)
    x     += ds * cos(theta + dtheta/2)
    y     += ds * sin(theta + dtheta/2)
    theta += dtheta                   wrapped to [-pi, pi]

The encoder-only heading (dR - dL) / b is computed for comparison but never trusted.
This module is pure maths: the same code will run on the Raspberry Pi (robot/odometry).
"""

import math
from dataclasses import dataclass


def wrap_angle(theta: float) -> float:
    """Angle in [-pi, pi]."""
    return math.atan2(math.sin(theta), math.cos(theta))


@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0  # radians, counter-clockwise, 0 = the start heading


def track_distances(ticks_left: float, ticks_right: float, metres_per_tick: float) -> tuple[float, float]:
    """Encoder ticks of one step -> metres per side (each side = mean of its front/rear motor)."""
    return ticks_left * metres_per_tick, ticks_right * metres_per_tick


def update_pose(pose: Pose, d_left: float, d_right: float, gyro_z: float, dt: float) -> Pose:
    """One odometry step: encoders give the distance, the gyro gives the heading change."""
    ds = (d_left + d_right) / 2.0
    dtheta = gyro_z * dt
    mid = pose.theta + dtheta / 2.0
    return Pose(
        x=pose.x + ds * math.cos(mid),
        y=pose.y + ds * math.sin(mid),
        theta=wrap_angle(pose.theta + dtheta),
    )


def encoder_heading_change(d_left: float, d_right: float, track_width_m: float) -> float:
    """Heading change from the encoders alone: logged for comparison, not used on tracks."""
    return (d_right - d_left) / track_width_m


def vector_to_origin(pose: Pose) -> tuple[float, float]:
    """(distance, bearing) of the start point seen from `pose` (ODOMETRY.md section 4, step 2)."""
    return math.hypot(pose.x, pose.y), math.atan2(-pose.y, -pose.x)
