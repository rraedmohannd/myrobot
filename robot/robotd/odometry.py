"""Pi-side pose estimator: consecutive TELEMETRY frames -> pose (docs/ODOMETRY.md section 3).

The maths is `scr_odometry` (one implementation, shared with the backend). This class only turns
raw frames into the inputs of that maths:
- 4 cumulative encoder counts -> ticks of this step per side = mean of the front and rear motor;
- dt from the ESP32 clock `t_ms` (u32, wraps after about 49.7 days);
- gyro_z for the heading (primary); the encoder-only heading is kept for comparison only;
- a `slip` report when the front and rear encoder of one side disagree strongly.

`metres_per_tick` and `track_width_m` are configuration PLACEHOLDERS until the calibration of
ODOMETRY.md section 5 is done on the real robot.
"""

from dataclasses import dataclass, field

from scr_odometry import Pose, encoder_heading_change, track_distances, update_pose, wrap_angle
from scr_serial import Telemetry

_U32 = 1 << 32
_MAX_STEP_MS = 1000  # a longer gap (or a clock jump) is a restart: no pose change for that step


@dataclass
class SlipReport:
    side: str  # "left" / "right"
    front_ticks: int
    rear_ticks: int


@dataclass
class OdometryStep:
    dt_s: float
    ds_m: float  # signed distance of this step
    v_mps: float
    gyro_z: float
    slip: SlipReport | None = None
    restarted: bool = False  # the ESP32 clock or counters jumped (reboot): step ignored


@dataclass
class OdometryEstimator:
    metres_per_tick: float
    track_width_m: float
    slip_ratio: float = 0.25
    slip_min_ticks: int = 30
    slip_window_s: float = 0.5
    pose: Pose = field(default_factory=Pose)
    enc_theta: float = 0.0  # encoder-only heading (comparison only, never used for control)
    left_m: float = 0.0  # cumulative track distances since reset
    right_m: float = 0.0
    travelled_m: float = 0.0  # sum of |ds|
    t_ms: int = 0  # unwrapped ESP32 time of the last frame
    gyro_bias: float = 0.0  # residual bias robotd estimated while still (subtracted), rad/s
    last_delta_ticks: int = 0  # sum of |encoder change| of the last step (0 = tracks still)
    _last: Telemetry | None = None
    _win: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    _win_s: float = 0.0

    def reset(self) -> None:
        """New run: the current place becomes the origin (the last frame is kept as reference)."""
        self.pose = Pose()
        self.enc_theta = self.left_m = self.right_m = self.travelled_m = 0.0
        self._win, self._win_s = [0, 0, 0, 0], 0.0

    def update(self, frame: Telemetry) -> OdometryStep | None:
        """Feed the next TELEMETRY frame. Returns None for the very first frame."""
        last, self._last = self._last, frame
        if last is None:
            self.t_ms = frame.t_ms
            return None
        dt_ms = (frame.t_ms - last.t_ms) % _U32  # correct across the u32 wrap
        d = [frame.encoders[i] - last.encoders[i] for i in range(4)]
        self.last_delta_ticks = sum(abs(x) for x in d)
        if dt_ms == 0 or dt_ms > _MAX_STEP_MS:
            # duplicate frame, or the ESP32 restarted (its clock and counters start again)
            self.t_ms += 0 if dt_ms == 0 else 1
            return OdometryStep(0.0, 0.0, 0.0, frame.gyro_z, restarted=dt_ms != 0)
        self.t_ms += dt_ms
        dt = dt_ms / 1000.0
        d_left, d_right = track_distances(
            (d[0] + d[1]) / 2.0, (d[2] + d[3]) / 2.0, self.metres_per_tick
        )
        gyro_z = frame.gyro_z - self.gyro_bias
        self.pose = update_pose(self.pose, d_left, d_right, gyro_z, dt)
        self.enc_theta = wrap_angle(
            self.enc_theta + encoder_heading_change(d_left, d_right, self.track_width_m)
        )
        ds = (d_left + d_right) / 2.0
        self.left_m += d_left
        self.right_m += d_right
        self.travelled_m += abs(ds)
        return OdometryStep(dt, ds, ds / dt, gyro_z, slip=self._slip_check(d, dt))

    def _slip_check(self, d: list[int], dt: float) -> SlipReport | None:
        for i in range(4):
            self._win[i] += d[i]
        self._win_s += dt
        if self._win_s < self.slip_window_s:
            return None
        win, self._win, self._win_s = self._win, [0, 0, 0, 0], 0.0
        for side, front, rear in (("left", win[0], win[1]), ("right", win[2], win[3])):
            biggest = max(abs(front), abs(rear))
            if biggest >= self.slip_min_ticks and abs(front - rear) > self.slip_ratio * biggest:
                return SlipReport(side, front, rear)
        return None
