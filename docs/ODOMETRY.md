# Odometry, Trajectory and Return-to-Origin

Platform: **Rover 5**, tracked skid-steer, 4 motors with Hall quadrature encoders, ICM-20948 IMU on the ESP32.

## 1. Frames and conventions
- World frame fixed at the start pose: `(x, y, θ) = (0, 0, 0)`; x forward, y left, θ counter-clockwise, radians internally.
- Units: metres, seconds, radians. Timestamps in ms since run start.
- Camera pan angle is separate from base heading and never used as base rotation.

## 2. Who does what
| Layer | Responsibility |
|---|---|
| ESP32 | Count encoders (PCNT), read IMU gyro, ultrasonics, drive motors, command timeout → stop, publish raw telemetry at ~50 Hz |
| Pi 5 (`robot/odometry`) | Pose estimation, trajectory buffer, breadcrumbs, return-to-origin controller |
| Backend | Store pose samples/events, plot trajectory, compute stats, store physically measured errors |

## 3. Pose update (every ~20 ms)
```
ΔL, ΔR = track distances from encoders  (ticks × metres_per_tick, per side = mean of front/rear motor)
Δs     = (ΔL + ΔR) / 2
Δθ     = gyro_z × Δt              (primary — tracks slip during turns)
θ_mid  = θ + Δθ / 2
x     += Δs · cos(θ_mid)
y     += Δs · sin(θ_mid)
θ     += Δθ   (wrap to [-π, π])
```
- Encoder-only heading `(ΔR − ΔL)/b` is logged for comparison but not trusted on tracks.
- Gyro bias calibrated at start (robot still for ~2 s); re-estimated whenever stopped.
- Slip check: large disagreement between front and rear encoder on one side → flag `slip` event.

## 4. Return-to-origin
1. Exit roaming; store current pose.
2. **Direct vector** `r = (−x, −y)`, distance `d = √(x²+y²)`, bearing `φ = atan2(−y, −x)`.
3. Rotate in place to `φ` (gyro-controlled), drive forward, recompute `r` continuously (closed loop), slow down near target.
4. If the direct path is blocked (ultrasonic) → fall back to **breadcrumbs**: waypoints recorded every ~0.3 m or on heading change during the outbound path; follow them in reverse order.
5. Stop when `d < arrival_tolerance` (proposed 0.15 m — to be approved by supervisor). Optional final heading alignment to θ=0 (tolerance TBD).
6. Report `return_done` with final odometry, or `return_failed{reason: blocked|timeout|sensor_fault}`.
Reversing recorded motor commands is not used.

## 5. Calibration procedures
- Distance: drive 3 m straight × 5 runs, tape-measure → `metres_per_tick`.
- Rotation: 360° in place × 5 runs → gyro scale check.
- Square test: 2 m × 2 m square, CW and CCW → combined error.

## 6. Validation (record real numbers only)
- Straight-line error, turning error, square-test closure error.
- Return trials (≥10): drive a random route, request return, measure **physical** distance from start mark with tape (independent of odometry), log in `return_trials`.
- Blocked-return test, sensor-unplugged test, comm-loss test.
Simulation may be used for development but must be labelled and excluded from results.
