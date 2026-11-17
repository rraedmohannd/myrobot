# Robot software architecture (sprint 4, 2026-11-17)

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
| `/ws/ui` | robotd → page | `local.engage`, `local.vision {face_track_id, overlap, engaged, faces}`, `local.state {state, backend_ok, is_simulated}` |
| `/ws/ui` | page → robotd | `local.vad {speaking}`, `local.conversation {active}` |

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
      │                         PAUSED          any state ── fault ──► FAULT ── fault cleared ──► IDLE
      └──────────────────────────┘ (return_done / operator)
```
| State | Tracks | Leaves when |
|---|---|---|
| IDLE | stopped | `start_roaming` accepted; a person engages → ENGAGED |
| ROAMING | slow reactive roaming | a person engages; `pause`; `return_to_start`; backend lost → IDLE; fault |
| ENGAGED | stopped; the camera turns to the person | the page reports the conversation started → CONVERSATION; the person leaves or nothing starts within the timeout → previous state |
| CONVERSATION | stopped | the page reports the conversation ended → recheck the path, then the previous state (ROAMING continues only if a direction is free) |
| RETURNING | return-to-start controller | `return_done` / `return_failed` → IDLE; `pause` |
| PAUSED | stopped | `resume` → the state it paused; `return_to_start` |
| FAULT | stopped, `MODE SAFE` | the fault is gone (and an operator command, for motor faults) |

**Engagement.** The robot stops when a person has faced it for N seconds (1.5 s, the browser prototype's value). The camera then turns to that person (pan servo, not the base), the page greets and the conversation runs. Afterwards the robot rechecks the path with the ultrasonic sensors before it moves again.

**Roaming.** Speed is low and configurable (placeholder 0.20 m/s). One straight segment is at most 5 m. When the front is blocked the robot turns in place toward the freer side; if no direction is free it **stays stopped**. It reverses only when the rear sensor works and shows free space — and the ESP32 enforces the same rule itself.

**Return-to-start.** docs/ODOMETRY.md §4: the direct vector first, breadcrumbs in reverse when the direct path is blocked, arrival tolerance 0.15 m (proposed), `return_done` / `return_failed {reason}`. Recorded motor commands are never replayed backwards.

**Commands are requests.** `start_roaming`, `pause`, `resume`, `return_to_start`, `set_mode` from the admin page are accepted or refused with a reason (conversation in progress, fault active, low battery, wrong state), always answered with `robot.command.ack`.

**Backend lost.** `robotd` stops roaming (→ IDLE), the page shows "الخدمة غير متوفرة حالياً / service unavailable", telemetry is buffered (bounded) and both reconnect on their own. Stopping never waited for the backend in the first place.

## 4. Simulation (rule 5)
- `robotd --sim` runs against a **simulated ESP32** (same serial protocol over a local TCP socket; simulated encoders, gyro and ultrasonics in a 6 × 5 m room). Every telemetry frame carries the `SIMULATED` flag; `robotd` then sends `is_simulated: true` in `robot.status` and `run.start`, logs "SIMULATED", and the page and the admin show a SIMULATED label. Simulated runs can never receive return trials.
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
