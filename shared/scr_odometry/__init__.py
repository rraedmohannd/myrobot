"""Pose maths shared by the backend (simulator, plots) and the Raspberry Pi runtime (robotd).

One implementation of docs/ODOMETRY.md section 3, plus the small room geometry the simulators use.
Standard library only.
"""

from scr_odometry.geometry import Box, range_along, range_min, ray_box
from scr_odometry.odometry import (
    Pose,
    encoder_heading_change,
    track_distances,
    update_pose,
    vector_to_origin,
    wrap_angle,
)

__all__ = [
    "Box", "Pose", "encoder_heading_change", "range_along", "range_min", "ray_box",
    "track_distances", "update_pose", "vector_to_origin", "wrap_angle",
]  # fmt: skip
