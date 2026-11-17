"""Pan-servo target that keeps the selected face centred (pure, no OpenCV import).

Angles follow docs/SERIAL_PROTOCOL.md: 0 = straight ahead, positive = LEFT. The camera is
mounted on the servo, so the picture already shows the error relative to where the camera
points: the controller is incremental (error in the image -> change of the angle). The pan
angle is the camera's rotation relative to the robot body; it has nothing to do with the
base heading used by odometry.

SimulatedServo is SIMULATED hardware for the laptop test: no real servo moves.
"""

from __future__ import annotations

import math

from visiond.config import ServoConfig


def bearing_right_deg(centre_x: float, hfov_deg: float) -> float:
    """Angle of a point in the picture from the optical axis; positive = right of the centre.

    centre_x is a fraction of the frame width (0 = left edge, 1 = right edge), pinhole model.
    """
    half = math.radians(hfov_deg) / 2
    return math.degrees(math.atan((centre_x - 0.5) * 2 * math.tan(half)))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class PanController:
    """Dead band + smoothing + speed limit + angle limits.

    `update` is called once per analysed frame with the selected face's bearing (degrees to
    the RIGHT of the picture centre) or None when nobody is selected.
    """

    def __init__(self, config: ServoConfig | None = None) -> None:
        self.cfg = config or ServoConfig()
        self.target_deg = 0.0
        self._smoothed: float | None = None
        self._last_t: float | None = None
        self._lost_since: float | None = None
        self._actual_deg: float | None = None

    def set_actual(self, pan_deg: float) -> None:
        """The angle robotd actually commanded (`servo` message)."""
        self._actual_deg = pan_deg

    def update(self, error_right_deg: float | None, t_ms: float) -> float:
        cfg = self.cfg
        dt = 0.0 if self._last_t is None else _clamp((t_ms - self._last_t) / 1000.0, 0.0, 0.5)
        self._last_t = t_ms
        if error_right_deg is None:
            self._smoothed = None
            if self._lost_since is None:
                self._lost_since = t_ms
            if t_ms - self._lost_since >= cfg.recentre_after_ms:
                step = cfg.recentre_speed_dps * dt
                self.target_deg -= _clamp(self.target_deg, -step, step)
            return self._finish()
        self._lost_since = None
        if self._smoothed is None:
            self._smoothed = error_right_deg
        else:
            self._smoothed += cfg.smoothing * (error_right_deg - self._smoothed)
        if abs(self._smoothed) <= cfg.dead_band_deg:
            return self._finish()
        # Positive pan = left, so an error to the right lowers the angle. Only the part of
        # the error outside the dead band is corrected, so the target does not jump.
        outside = self._smoothed - math.copysign(cfg.dead_band_deg, self._smoothed)
        wanted = -outside * _clamp(cfg.gain * dt * 10.0, 0.0, 1.0)
        limit = cfg.max_speed_dps * dt
        self.target_deg += _clamp(wanted, -limit, limit)
        return self._finish()

    def _finish(self) -> float:
        cfg = self.cfg
        if self._actual_deg is not None:
            self.target_deg = _clamp(
                self.target_deg,
                self._actual_deg - cfg.max_lead_deg,
                self._actual_deg + cfg.max_lead_deg,
            )
        self.target_deg = _clamp(self.target_deg, cfg.min_deg, cfg.max_deg)
        return self.target_deg


class SimulatedServo:
    """SIMULATED pan servo: moves toward the target at a limited slew rate. Not hardware."""

    is_simulated = True

    def __init__(self, slew_dps: float = 120.0, min_deg: float = -80.0, max_deg: float = 80.0):
        self.slew_dps = slew_dps
        self.min_deg = min_deg
        self.max_deg = max_deg
        self.angle_deg = 0.0
        self._last_t: float | None = None

    def step(self, target_deg: float, t_ms: float) -> float:
        dt = 0.0 if self._last_t is None else _clamp((t_ms - self._last_t) / 1000.0, 0.0, 0.5)
        self._last_t = t_ms
        limit = self.slew_dps * dt
        goal = _clamp(target_deg, self.min_deg, self.max_deg)
        self.angle_deg += _clamp(goal - self.angle_deg, -limit, limit)
        return self.angle_deg


def error_for_fixed_camera(bearing_deg: float, simulated_pan_deg: float) -> float:
    """SIMULATION helper for the laptop test, where the phone does not turn.

    With a fixed camera the face stays off-centre however the imaginary servo moves, so the
    incremental controller would run to its limit. This returns the error a camera mounted on
    the simulated servo WOULD see: the face's bearing minus where the servo already points
    (pan is positive to the left, bearings are positive to the right).
    """
    return bearing_deg + simulated_pan_deg
