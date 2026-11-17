# AprilTag at the start mark (EXPERIMENTAL, off by default)

Sprint 5, item S7. Status: **code and synthetic tests only. Never run with a real camera or a
printed tag.**

> **This correction is never counted in any result (return accuracy, odometry error, demo
> numbers) unless Mohannd turns it on himself and says so in the report. A run that used it must
> be reported separately or excluded.**

## What it is
An AprilTag is a printed black-and-white square code (family **36h11**, like a small QR code)
that a camera can find and measure. One tag is placed next to the start mark on the floor.

- `visiond` looks for it in the frames it already analyses (OpenCV aruco,
  `cv2.aruco.DICT_APRILTAG_36h11`, `ArucoDetector`, OpenCV ≥ 4.7) and adds an optional
  `start_tag` field to `vision.state`:
  `{id, center_x (0..1), bearing_deg (from the robot's forward axis, + = left, includes the pan
  servo angle), distance_m, age_ms, experimental: true}`. Only these numbers leave `visiond`;
  no picture is stored or sent (same privacy rule as faces).
- `robotd` records where the tag is (in the odometry frame) at the start of a run, if it is seen
  within 0.2 m of the start. While **RETURNING**, and only in the **final approach** (within
  1.5 m of the odometry origin), a fresh sighting tells how far the odometry has drifted; the
  return target is shifted by that drift (translation only, smoothed, at most 0.5 m).
- Distance comes from the pinhole model: the apparent height of the tag and its printed size.
  The camera's field of view is still a **placeholder** (60°, `CAMERA_HFOV_DEG`), so the
  distance is only as good as that number.

**Wiring status (2026-11-17):** the logic lives in `robotd/navigation.py`
(`tag_from_vision`, `Sense.tag`, `Returner.observe_start` / `clear_start_tag`); the three calls
in `robotd/core.py` (pass the tag into `Sense`, clear it at `run_start`, call `observe_start`
while ROAMING) are a separate small change. Until they are in, the flag has no effect.

When the tag is not seen, the sighting is older than 400 ms, the id is wrong, the distance is
implausible (outside 0.2-4 m) or the implied drift is larger than 0.5 m, the return behaves
**exactly as without the feature**. It is never used while roaming.

## Printing
- Tag family **36h11**, id **0** (or the id you configure). Generate it, for example, with
  `python -c "import cv2; d=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11); cv2.imwrite('tag0.png', cv2.aruco.generateImageMarker(d, 0, 800))"`
  (run this outside `robot/visiond`; visiond itself never writes images).
- Print on **A4**, the **black square 15 cm** wide (measure it after printing: printers scale),
  with a **white border of at least 2 cm** all around. Matte paper; no lamination glare.
- If the printed black square is not exactly 15 cm, set its real size
  (`VISION_APRILTAG_SIZE_M`, e.g. `0.148`).
- Glue it on stiff card so it stays flat.

## Placement
- Next to the start mark, **upright**, with its centre at **camera height**, facing the direction
  the robot comes back from (the robot normally faces away from it at the start; the pan servo
  may turn the camera toward it).
- 0.5-1.5 m from the start mark works best (about 40-170 px tall at 640 px width with a 60° lens).
- Nothing else with an AprilTag nearby; other ids are ignored anyway.
- Do not move it during a run: its position at the start is the reference.

## How to enable (both sides)
| Side | Environment | Flag |
|---|---|---|
| visiond | `VISION_APRILTAG=1`, `VISION_APRILTAG_ID=0`, `VISION_APRILTAG_SIZE_M=0.15` | `--apriltag --apriltag-id 0 --apriltag-size-m 0.15` |
| robotd | `ROBOTD_APRILTAG_RETURN=1`, `ROBOTD_APRILTAG_ID=0` | config file key `"apriltag_return": true` |

Other robotd keys (config file): `apriltag_final_approach_m` (1.5), `apriltag_max_age_ms` (400),
`apriltag_record_radius_m` (0.2), `apriltag_min_m` / `apriltag_max_m` (0.2 / 4.0),
`apriltag_max_correction_m` (0.5), `apriltag_blend` (0.3).

visiond prints `AprilTag EXPERIMENTAL ... (not counted in any result)` at start-up and, with
`--console`, the sighting on each status line.

## How a run that used it is recognised
- events `apriltag_start_recorded {..., experimental: true}` and
  `apriltag_correction {tag_id, tag_distance_m, tag_bearing_deg, tag_age_ms, offset_x_m,
  offset_y_m, odom_x, odom_y, experimental: true}` (at most one per second);
- `return_done` / `return_failed` carry `apriltag_used: true` and `apriltag_offset_m`;
- the run's `odom_config` has `apriltag_return: "EXPERIMENTAL - exclude this run from results"`
  whenever robotd ran with the flag on.

## Tests (synthetic only)
- `robot/tests/visiond/test_apriltag.py`: the tag is rendered with
  `cv2.aruco.generateImageMarker`, projected with a pinhole camera onto a grey background at
  0.6-2 m, up to 0.4 m to the side and turned up to 30°, with noise; id, centre (±0.01), distance
  (±6 %) and bearing (±1.5°) are checked. Off by default; the message is unchanged when off.
- `robot/tests/robotd/test_apriltag_return.py`: simulated kinematics with a constant odometry
  error: off = identical commands to the plain return; on = correction only in the final approach,
  marked `experimental`; stale, wrong id, inconsistent or unseen = identical to the plain return;
  never during roaming.

## Not verified
Real camera, real print, lighting, motion blur, the real field of view, the pan-angle sign on the
real servo, and whether the correction helps at all on the floor. All of that needs Mohannd and
the hardware.
