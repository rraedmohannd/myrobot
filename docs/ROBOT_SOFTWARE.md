# Robot software architecture (sprint 4, 2026-11-17; sprint 5 additions 2026-11-17)

Written **before the hardware exists**. Everything here was developed against a simulated ESP32 and (for vision) a laptop; nothing has run on the Raspberry Pi, the ESP32 or the Rover 5 yet. Values marked "placeholder" are not measurements.

## 1. Who decides what
| Layer | Decides | Never needs |
|---|---|---|
| **ESP32** (`firmware/`) | Motor safety: 300 ms command watchdog, local obstacle stop, reverse only with rear sensing, power-on SAFE mode, speed PID and ramps | the Pi or the backend to stop |
| **Raspberry Pi 5** (`robot/`) | Behaviour: roaming, engagement, pose (odometry), return-to-start, what to show locally | the backend to stop or to stay stopped |
| **Backend** (laptop) | Conversation, knowledge, logs, admin requests | — it only **asks** (`robot.command`); the robot may refuse |

Rule 4 in one line: cut any cable or kill any process above the ESP32, and the tracks stop within 300 ms.

## 2. Processes on the Pi
```
                         laptop (backend, port 8000)
                           ▲ /ws/robot (conversation)        ▲ /ws/robot (status, telemetry, commands)
                           │                                 │
┌──────────────────────────┼─────────────── Raspberry Pi 5 ──┼──────────────────────────────┐
│  Chromium kiosk (7" screen, mic, speaker)                  │                              │
│   robot page from http://localhost:8765 ──── /ws/ui ────►  robotd  (asyncio, one process) │
│                                                            │  serial link ─ odometry ─    │
│  visiond (separate process, CPU heavy)                     │  navigation ─ supervisor ─   │
│   camera → faces → engagement / active     ── /ws/vision ► │  backend link ─ local UI     │
│   speaker → servo target                                   │  server                      │
└────────────────────────────────────────────────────────────┼──────────────────────────────┘
                                                             │ USB serial, 460800 baud, COBS + CRC16
                                                     ESP32 (motors, encoders, IMU, ultrasonics,
                                                     servo, power; watchdog + local obstacle stop)
```
- **`robotd`** (`python -m robotd`): the serial link (docs/SERIAL_PROTOCOL.md), odometry (docs/ODOMETRY.md), navigation, the supervisor state machine, the backend link (docs/PROTOCOL.md, version 2) and a small local web server.
- **`visiond`** (`python -m visiond`): camera → face detection and landmarks → tracking, engagement, active speaker, overlap → pan-servo target. A separate process because it is CPU heavy and may crash or be restarted without touching the motors. **Frames never leave it**; it sends ids, boxes and flags to `robotd` over a local WebSocket.
- **UI = Chromium in kiosk mode** opening the robot page served by `robotd` on `http://localhost:8765`. `localhost` is a secure context, so the microphone works without HTTPS. The page is the same `/robot` page the backend serves (copied at startup, cached for offline starts). It connects:
  - to the **backend** `/ws/robot` on the laptop for the conversation (speech, answers, screen content);
  - to **`robotd`** `/ws/ui` for local events: "a person engaged → start the conversation", the camera's `face_track_id` / `overlap`, and the robot state.

Why two connections to the backend (page and `robotd`): the conversation needs the browser's audio, while status, telemetry and command requests belong to the process that controls the robot. Either can drop without stopping the other.

### Local messages (JSON over WebSocket on the Pi only)
| Path | Direction | Message |
|---|---|---|
| `/ws/vision` | visiond → robotd, ~10 Hz | `vision.state {t_ms, camera_ok, faces[] {id, box, facing, yaw_deg, pitch_deg, mouth, speaking}, selected_id, engaged, active_speaker_id, overlap, servo_target_deg}` |
| `/ws/vision` | robotd → visiond | `vad {speaking}` (from the page's voice detector), `servo {pan_deg}` (the angle actually commanded) |
| `/ws/ui` | robotd → page | `local.engage`, `local.vision {face_track_id, overlap, engaged, faces (a count)}`, `local.state {state, backend_ok, is_simulated}` |
| `/ws/ui` | page → robotd | `local.vad {speaking}`, `local.conversation {active}` |
| `/ws/ui` | robotd → page | sprint 5: `local.state` also carries `battery_pct`, `battery_warning`, `battery_low` (battery badge) |
| `/ws/ctl` | robotctl ↔ robotd | sprint 5, localhost only: `ctl.hello`, `ctl.bringup {on}`, `ctl.serial {frame_type, fields}`, `ctl.reload_calibration`, `ctl.telemetry_rate {hz}` → `ctl.ack`, `ctl.serial_rx`, `ctl.status` (2 Hz, with `vision` = the last `vision.state` + `age_ms`, or null). See `robot/robotd/ctl.py` |
| `/local/settings…` | page ↔ robotd | sprint 5 settings page behind a PIN (section 7) |

## 3. States
```
              start_roaming                    person faces the robot N s
     IDLE ───────────────────► ROAMING ─────────────────────────────────► ENGAGED
      ▲  ▲                      │  ▲  ▲                                      │ camera on the person,
      │  │        return_to_start│  │  │ path rechecked, free                │ page sends session.start
      │  │                      ▼  │  └──────────────────────────────┐       ▼
      │  └──── return_done ── RETURNING                              └── CONVERSATION
      │        return_failed     │  ▲                                  (also from IDLE: back to IDLE)
      │                    pause │  │ resume
      │                          ▼  │
      │                         PAUSED          any state ── fault ──► FAULT ── fault cleared ──► PAUSED / IDLE
      └──────────────────────────┘ (return_done / operator)
```
| State | Tracks | Leaves when |
|---|---|---|
| IDLE | stopped | `start_roaming` accepted (after `stop_roaming` it continues the same run); `return_to_start` while a run is still open; a person engages → ENGAGED; `ctl.bringup` → PAUSED (mode `bringup`) |
| ROAMING | slow reactive roaming | a person engages; `pause`; `stop_roaming` → IDLE where it stands (run stays open); `return_to_start`; backend lost → PAUSED; battery under 9.9 V → PAUSED (`battery_low`); fault |
| ENGAGED | stopped; the camera turns to the person | the page reports the conversation started → CONVERSATION; the person leaves or nothing starts within the timeout → previous state |
| CONVERSATION | stopped | the page reports the conversation ended → recheck the path, then the previous state (ROAMING continues only if a direction is free) |
| RETURNING | return-to-start controller | `return_done` / `return_failed` → IDLE; `pause` |
| PAUSED | stopped | `resume` → the state it paused; `return_to_start`; `stop_roaming` → IDLE; paused by a lost backend: resumes by itself only if the backend was lost for ≤ 60 s, otherwise it waits for `resume`; bring-up: only `ctl.bringup off` ends it (→ IDLE) |
| FAULT | stopped | the fault is gone → PAUSED if it was moving (it waits for `resume`; the robot never starts moving by itself after a fault), else IDLE |

**Engagement.** The robot stops when a person has faced it for N seconds (1.5 s, the browser prototype's value). The camera then turns to that person (pan servo, not the base), the page greets and the conversation runs. Afterwards the robot rechecks the path with the ultrasonic sensors before it moves again.

**Roaming.** Speed is low and configurable (placeholder 0.20 m/s). One straight segment is at most 5 m. When the front is blocked the robot turns in place toward the freer side; if no direction is free it **stays stopped**. It reverses only when the rear sensor works and shows free space — and the ESP32 enforces the same rule itself.

**Return-to-start.** docs/ODOMETRY.md §4: the direct vector first, breadcrumbs in reverse when the direct path is blocked, arrival tolerance 0.15 m (proposed), `return_done` / `return_failed {reason}`. Recorded motor commands are never replayed backwards.

**Engagement look-away.** "engaged" from visiond may drop for up to 0.3 s (`engage_lookaway_s`) without counting: a blink or a short glance away does not end an engagement.

**Commands are requests.** `start_roaming`, `pause`, `resume`, `return_to_start`, `stop_roaming`, `set_mode` from the admin page are accepted or refused with a reason (conversation in progress, fault active, battery low, not calibrated, parameters not confirmed, bring-up in progress, wrong state, front blocked), always answered with `robot.command.ack`.

**Faults.** FAULT comes from: no telemetry for 500 ms, IMU or encoder fault flags, all front sensors absent, and (sprint 5) `param_hash_mismatch`. Low battery is no longer a fault (see "Battery" below). A watchdog stop on the ESP32 is recorded as `comm_loss {link: esp32}`, not as a fault.

**Backend lost.** `robotd` stops roaming (ROAMING → PAUSED, reason `backend_lost`; it resumes by itself only if the backend came back within 60 s, `backend_lost_resume_s`; after a longer outage it stays PAUSED until `resume` and logs `backend_back_stay_paused`), a return-to-start in progress continues because it needs nothing from the backend, no new engagement starts, the page shows "الخدمة غير متوفرة حالياً / service unavailable", telemetry is buffered (bounded) and both reconnect on their own. Stopping never waited for the backend in the first place.

### Sprint 5 rules (numbers are placeholders, all in `robot/robotd/config.py`)
**Calibration (S3.1).** `robotd` reads the calibration file (`robot/robotd/calibration.py`; `/etc/smart-campus-robot/calibration.json` on the Pi, `robot/calibration.dev.json` in development, or `ROBOTD_CALIBRATION` / `--calibration`; `robotd --sim` without a file uses a built-in calibration marked SIMULATED). It pushes **every** firmware parameter at each (re)connection of the serial port, after an ESP32 `EVENT BOOT`, and when the file changes (checked every 2 s, or `ctl.reload_calibration`). Each value is the calibration value, else the compiled default, so nothing left in NVS by an experiment survives. It then compares the parameter hash in each telemetry frame with the expected one. Motion is held until the hash matches. A mismatch 1 s after a push is the fault `param_hash_mismatch`, and the parameters are pushed again every 2 s. `metres_per_tick` comes from the calibration file only (no copy in the config). Without `METRES_PER_TICK`, the moving commands are refused with "not calibrated: METRES_PER_TICK missing …" (the firmware refuses `MODE DRIVE` as well). `robot.status.calibration` is `{status none|partial|complete, updated, is_simulated}`. `calibration.report` goes to the backend at every connect and file change. While robotctl has bring-up on, robotd neither checks nor pushes. When bring-up ends, it sends STOP and MODE SAFE, then pushes the file again.

**Gyro bias (S3.2).** Decision: both methods.
- `CALIBRATE_GYRO` at start (and after an ESP32 reboot), as soon as the tracks are still and the robot is not moving.
- Again when the robot is IDLE and still for 120 s (`gyro_recal_idle_s`).
- Between those, robotd keeps a running estimate of the bias left in the telemetry. When the targets are 0 and the encoders have not moved for 2 s (`gyro_still_s`), the mean `gyro_z` of that window updates the estimate (weight 0.3), and the odometry subtracts it.

Events: `gyro_bias {bias_rad_s, source "start"|"still"}`; the running estimate is reported when it changes by more than 1 mrad/s. The ESP32 refuses DRIVE (BUSY) during its 2 s calibration, so robotd holds motion meanwhile.

**Battery (S3.3).**
- The voltage is low-pass filtered (about 0.4 s).
- The percentage comes from a **placeholder** 3S Li-ion table (9.0 V 0 %, 9.9 V 5 %, 10.5 V 15 %, 11.1 V 40 %, 11.7 V 70 %, 12.0 V 85 %, 12.6 V 100 %; not measured). The calibration key `battery_table` (`[[volts, percent], …]`) replaces it.
- Under 10.5 V: event `battery_warning` (sent also without a run) and a status flag; it clears 0.2 V higher.
- Under 9.9 V (`battery_low`): no `start_roaming`, no `resume` of roaming, no NEW `return_to_start` (it may not finish), and ROAMING → PAUSED.
- Decision: a return that is already running **continues** under 9.9 V, because it is the shortest way back and stopping half-way would strand the robot. A paused return may be resumed.
- The firmware hard cut stays the parameter `BATTERY_CUTOFF_MV = 0` until Mohannd sets it from the pack/BMS data.
- Energy (Wh) is integrated from the INA219 voltage × current since robotd started.
- `robot.status` carries `battery_v`, `battery_pct`, `battery_a`, `energy_wh` and `battery_warning`; each pose sample carries `power {v, a, wh}`. The robot page shows the percentage in its badge.

**Camera ownership (default 13).** On the robot, `visiond` owns the webcam. In robot mode (the page served by robotd), robotd injects `robot-guard.js` as the first script. It sets `window.SCR_ROBOT_MODE` and refuses every `getUserMedia` request with video; audio passes. `robot.html` then never starts its camera (`?camera=1`, `?face_start=1` and the C key are ignored), and `robot-local.js` stops a camera that is already running.

**Rules from the firmware safety review (sprint 5).**
- *Keeping motion alive:* robotd never relies on HEARTBEAT. While it wants a non-zero target, it sends DRIVE at 20 Hz; HEARTBEAT (10 Hz) is sent only while the target is zero. To stop, it sends STOP; it does not simply go quiet. A test measures at least 39 DRIVE frames in 2 s of roaming, no HEARTBEAT, and a STOP on `pause`.
- *After a stall:* on `EVENT LINK_RESTORED`, robotd first sends STOP. After that only DRIVE frames built from the current state go out (LinkCore never queues DRIVE), so nothing sent before the stall can act.
- *Encoder counts:* when `ENCODER_INVERT_*` (ids 15–18) changes, the odometry takes the next frame as its new reference, so no phantom jump enters the pose. This covers `EVENT PARAM_CHANGED` for those ids (robotctl) and a robotd push of new inversions (re-baselined on the first frame with the expected hash). It does the same after `EVENT BOOT`, as well as the clock-jump detection it already had.
- *Centre front sensor:* a missing centre front sensor counts as "front blocked". Roaming does not start (`blocked: no free way in front`) and makes no forward attempts. `sensors.ultrasonic` is `fault` in that case, and `degraded` when only a side or the rear sensor is missing. The simulated ESP32 follows the same firmware rule.
- *Speed sign mismatch:* `EVENT ENCODER_FAULT` arg 10 or 11 (the firmware stops and goes to SAFE) becomes the latched FAULT `speed_sign_mismatch_left` / `_right`. Its event says "run: robotctl motor-test". The fault clears only after an ESP32 reboot or at the end of a robotctl bring-up.

**Several backend addresses.** `BACKEND_URL` may be a comma-separated list. The backend link tries the addresses in order and moves to the next one after a failure. The page uses the address in use when it loads. The settings page can save a list to the local override file.

**Pi health (S6.2).** Every 5 s robotd reads:
- the CPU temperature (`/sys/class/thermal`);
- `vcgencmd get_throttled` (under-voltage and throttling, now and since boot);
- the load, the memory and the disk.

Each value is optional and absent where it cannot be read (Windows). An under-voltage becomes the event `under_voltage` (evidence for the power design). `robot.status.firmware` comes from `DIAG_REPORT`.

**Black box (S6.4).** The last 60 s of telemetry (10 Hz) and events are kept in memory. They are written to `<LOG_DIR>/blackbox-<UTC>.jsonl` on entering FAULT (not during the start-up wait), on an obstacle stop and on a communication loss. `LOG_DIR` defaults to `/var/log/smart-campus-robot` when it is writable, else `robot/logs/`. At most one file is written per 5 s, the newest 50 are kept, and each one is reported with the event `blackbox {file}`. The files hold numbers and event texts only: never images or audio.

## 7. Settings page (S6.1)
A 3 s press on the top-right corner of the robot screen opens `/local/settings`, served by robotd on localhost only.

**Access.** The page asks for the PIN from `robot.env` (`ROBOTD_SETTINGS_PIN`); without a PIN the page is disabled. The PIN is checked on the server. A login lasts 10 minutes, and 5 wrong PINs in 5 minutes lock the page.

**What it shows and changes:**
- the backend address(es): saved to the local override file (`/etc/smart-campus-robot/robotd.override.json`, `robot/robotd.override.json`, or `ROBOTD_SETTINGS_FILE`), never to `.env`; used from the next reconnect and at every start;
- the volume (`wpctl`, Linux only);
- a test tone and a microphone level meter, which run in the browser only: nothing is recorded, stored or sent;
- the Wi-Fi name, signal and IP;
- the versions (robotd, firmware, calibration date);
- the battery and the calibration status.

Arabic first, English second.

## 4. Simulation (rule 5)
- `robotd --sim` runs against a **simulated ESP32** (same serial protocol over a local TCP socket; simulated encoders, gyro and ultrasonics in a 6 × 5 m room). Since sprint 5 it speaks protocol version 2 completely: parameters with the firmware's rules and an in-memory NVS, DIAG_REPORT with build_env 5 and the SIMULATED flag, the bring-up streams, slip, a drifting gyro bias and a slowly falling 3S pack. It **uses** the parameter values it holds, so a fresh one is a never-calibrated firmware that does not move. `python -m robotd.sim_esp32 --port 8766 --profile fresh|sim` runs it on its own for robotctl. Every telemetry frame carries the `SIMULATED` flag; `robotd` then sends `is_simulated: true` in `robot.status` and `run.start`, logs "SIMULATED", and the page and the admin show a SIMULATED label. Simulated runs can never receive return trials.
- `visiond` works with a phone camera (IP Webcam / DroidCam URL), a USB webcam or a video file. A phone camera on the laptop is a development setup, not a test of the robot's camera.

## 5. Where the code is
| Path | What |
|---|---|
| `shared/scr_serial/` | Pi ↔ ESP32 frames (Python); twin of `firmware/lib/protocol` |
| `shared/scr_odometry/` | pose maths and room geometry, one implementation for backend and robot |
| `shared/scr_protocol/` | robot ↔ backend messages (sprint 3) |
| `shared/vision_thresholds.json` | thresholds shared by the browser prototype and `visiond` |
| `robot/robotd/`, `robot/visiond/`, `robot/tests/` | Pi runtime and its tests |
| `robot/deploy/` | Raspberry Pi OS setup, systemd units, kiosk, udev rule |
| `firmware/` | ESP32 firmware (PlatformIO), native tests, `sim-check` build |

## 6. Navigation distances (placeholders, to be tuned on the floor)
| What | Value |
|---|---|
| Roam speed | 0.20 m/s |
| Turn away when the nearest front reading is under | 0.45 m |
| A direction counts as free above | 0.70 m |
| Never push forward under | 0.28 m (the ESP32's own stop is at 0.25 m) |
| Back off 0.15 m | only when the obstacle is under 0.22 m **and** the rear sensor works and shows more than 0.40 m |
| Breadcrumbs | every 0.3 m or on a heading change; a crumb within 0.25 m of an older one cuts the loop |
| Arrival tolerance | 0.15 m (proposed in docs/ODOMETRY.md, to be approved by the supervisor) |
| Vision counts as absent after | 1.5 s without `vision.state` |

None of these is a measurement. `robot/robotd/config.py` holds them.
