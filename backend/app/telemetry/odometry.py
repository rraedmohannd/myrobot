"""Pose estimation for the Rover 5 (tracked skid-steer), exactly as in docs/ODOMETRY.md section 3.

The maths lives in `shared/scr_odometry` since sprint 4, so the backend simulator and the
Raspberry Pi runtime (`robot/robotd`) use one implementation. This module keeps the old import
path for the backend code and its tests.
"""

from scr_odometry.odometry import (
    Pose,
    encoder_heading_change,
    track_distances,
    update_pose,
    vector_to_origin,
    wrap_angle,
)

__all__ = [
    "Pose",
    "encoder_heading_change",
    "track_distances",
    "update_pose",
    "vector_to_origin",
    "wrap_angle",
]
