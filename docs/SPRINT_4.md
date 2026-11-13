# Sprint 4 — Raspberry Pi and ESP32 software, before the hardware arrives (task file for Claude Code, Fable 5.2)

Prepared on 2026-11-13. Run it **after sprint 3** (`docs/SPRINT_3.md`).

**Goal.** Write the complete robot software now, test it on the laptop with **simulation** and the student's **Android phone camera**, so that when the parts arrive only wiring, pin confirmation and calibration are left.

Read first:
- `CLAUDE.md` (especially rules 4, 5 and 8);
- `docs/ODOMETRY.md`, `docs/SPEAKERS.md`, `docs/PROTOCOL.md` and `docs/HARDWARE.md`;
- the BOM in `docs/` if present (hardware-bom-v1);
- the `shared/` package from sprint 3;
- `backend/app/telemetry/` (odometry maths and simulator from sprint 1).

**Rules.** Same as `docs/DEMO_SPRINT.md` and `CLAUDE.md`, with these changes:
- **Branch:** `sprint-4`, created from `sprint-3`.
- **Log:** `docs/SPRINT_4_LOG.md`, with "Questions for Mohannd".
- **Stop** after 4 hours.

**Hardware facts we have (not bought yet, so treat everything as provisional and configurable)**
- **Base:** Rover 5, tracked skid-steer, 4 DC motors with 4 quadrature encoders.
- **Motor control:** ESP32 DevKit 38-pin with 2 × dual VNH motor-driver shields (provisional). Logic-level converters are in the BOM.
- **Sensors:** ICM-20948 IMU, INA219 current/voltage sensor, 4 × HC-SR04 ultrasonic sensors (5 V echo, needs level shifting).
- **Camera and servo:** MG996R pan servo; USB webcam (2K with microphone) on the Pi 5; a USB microphone.
- **Display and audio:** Waveshare 7" HDMI (C) touch screen at 1024×600; MAX98357 I2S amplifier and speaker, or the student's existing speaker.
- **Power:** 12.6 V Li-ion pack.

**Rule 8: no pin assignments.** Every pin goes in one file:
- `firmware/include/pins.h`, with every value `-1`;
- a compile-time `#error` unless `PINS_CONFIRMED` is defined;
- a "Pin map — to be confirmed after wiring" table in `docs/HARDWARE.md`, left empty for the student.

Native unit tests and a `sim` build environment must compile without pins.

**Rule 5: simulation is labelled.** Every simulated part shows "SIMULATED" in logs, status (`is_simulated`) and UI, and none of it counts as a physical result.

---

## R0 — Architecture document (`docs/ROBOT_SOFTWARE.md`)
Decide and document the following (short and clear, with a diagram in text):
- **Processes on the Pi.**
  - `robotd`: asyncio, with the serial link, odometry, navigation, the supervisor state machine and the backend link.
  - `visiond`: camera → faces → engagement / active speaker → servo target. It is a separate process for CPU.
  - The **UI is Chromium in kiosk mode**, opening the robot page served by `robotd` on `http://localhost` (a secure context, so the microphone and camera work without HTTPS). The page connects to the backend `/ws/robot` on the laptop, and to `robotd` on a local WebSocket for face and engagement events.
- **Who decides what.**
  - ESP32: motor safety, timeouts, local obstacle stop.
  - Pi: behaviour, pose, return-to-start.
  - Backend: conversation, knowledge, logs.

  Nothing on the backend is needed for the robot to stop (rule 4).
- **States.** IDLE, ROAMING, ENGAGED, CONVERSATION, RETURNING, PAUSED, FAULT, and the transitions between them, following the project behaviour:
  - Engagement:
    - the robot stops when a person faces it for N seconds;
    - the camera turns to that person, then the robot greets and talks;
    - after the conversation it rechecks the path, then resumes.
  - Roaming:
    - speed is low and configurable;
    - one straight segment is at most 5 m;
    - the robot stays stopped if there is no free direction;
    - it reverses only with rear sensing.

## R1 — Pi ↔ ESP32 serial protocol (`docs/SERIAL_PROTOCOL.md` + `shared/`)
- **Link.** USB serial (the ESP32 DevKit's USB-UART), 460800 baud. Binary frames with COBS encoding, a CRC-16/CCITT check, and a sequence number.
- **Messages.**
  - Pi → ESP32: `DRIVE {left, right}` (target track speeds in m/s), `STOP`, `SERVO {pan_deg}`, `MODE`, `HEARTBEAT`, `CALIBRATE_GYRO`.
  - ESP32 → Pi: `TELEMETRY` at 50 Hz: `t_ms`, the 4 encoder counts, `gyro_z`, accelerometer, the 4 ultrasonic distances, battery V and I, flags. Also `EVENT` (fault codes, obstacle stop, timeout stop) and `ACK`.
- **Watchdog.** No valid command or heartbeat for **300 ms** → the ESP32 stops the motors and reports a timeout event.
- **Code on both sides.**
  - One encoder/decoder in Python (`shared/`) and one in C++ (`firmware/lib/protocol`).
  - Cross-tests: the Python tests decode frames from fixtures written by the C++ native tests, and the other way round.

## R2 — ESP32 firmware (`firmware/`, PlatformIO, Arduino-ESP32 core)
- **Modules.**

  | Module | What it does |
  |---|---|
  | `motors` | PWM and direction for the VNH drivers, ramp limits, both motors of one side together |
  | `encoders` | PCNT quadrature, 4 channels |
  | `speed_pid` | One PID per track |
  | `imu` | ICM-20948 gyro z, with bias calibration while still |
  | `ultrasonic` | HC-SR04 ×4, staggered triggering to avoid crosstalk, timeouts |
  | `servo` | LEDC at 50 Hz, angle limits, slew-rate limit |
  | `power` | INA219 voltage and current, low-battery event |
  | `link` | The R1 protocol |
  | `safety` | Watchdog stop, and a **local obstacle stop**: front distance below a configurable threshold while driving forward → stop and report, whatever the Pi says |
- **Structure.** FreeRTOS tasks with fixed rates: control at 100 Hz, telemetry at 50 Hz.
- **Build environments.** `esp32dev` (needs `PINS_CONFIRMED`), `native` (unit tests: protocol, PID, watchdog, obstacle-stop logic, ramping) and `sim-check`, which compiles everything with stub drivers.
- **Docs.** A README covering how to build, flash and run the tests, and the calibration constants (`metres_per_tick`, `track_width`) that the student will measure later.

## R3 — Pi runtime `robotd` (`robot/`, Python 3.11+, runnable on Windows/Linux for development)
- **Serial link.** A real serial port, or a **simulated ESP32** (`--sim`): the sprint 1 simulator speaks the R1 protocol over a TCP socket or pty, with simulated encoders, gyro and ultrasonics in a 6 × 5 m room. It is labelled SIMULATED.
- **Odometry.** Exactly as in `docs/ODOMETRY.md`. Reuse the backend maths through `shared/`, so there is one implementation.
- **Navigation.**
  - Reactive roaming as described in R0.
  - Return-to-start: the direct vector, with breadcrumbs as the fallback, the arrival tolerance as config (0.15 m proposed), and `return_done` / `return_failed{reason}` events.
  - Reversing recorded motor commands is not allowed.
- **Supervisor.** The state machine from R0. It reacts to the vision and engagement events and to backend `robot.command` requests (sprint 3), and it can refuse with a reason.
- **Backend link.**
  - Telemetry, events and `robot.status` to the backend (sprint 3 protocol).
  - If the backend is lost, the robot keeps operating safely: it stops roaming, the UI shows "service unavailable", and it reconnects.
- **Local UI server.** It serves the robot page (a copy of the backend's `/robot` assets, synced at startup or bundled) on `http://localhost:<port>`, plus a local WebSocket to the page for `face_track_id`, overlap and engagement → `session.start`.
- **Tests:** pytest against the simulated ESP32, covering:
  - roam with an obstacle → turn;
  - return-to-start in the open, and when blocked → breadcrumbs;
  - heartbeat loss → ESP32 stop;
  - a backend command accepted or refused;
  - backend loss → safe state.

## R4 — Vision `visiond` with the phone camera (development) and the USB camera (robot)
- **Camera source.** `CAMERA_SOURCE` is a device index (USB webcam) **or a URL**:
  - the Android **IP Webcam** app: `http://<phone-ip>:8080/video`;
  - **DroidCam**: `http://<phone-ip>:4747/video`;
  - or a video file for repeatable tests.
- **Models (all local, CPU).**
  - Face detection with **YuNet** (`cv2.FaceDetectorYN`, ONNX from the OpenCV Zoo, with its licence). Port the earlier robot code's tracking ideas (BACKEND_SPEC §9).
  - Face landmarks with **MediaPipe Face Landmarker** (Python), for the mouth-opening ratio and head pose. Check that it works on Raspberry Pi 5 aarch64, and keep a YuNet-landmark fallback.
- **Logic** (same rules as the browser prototype `static/speaker-camera.js`; share thresholds through config):
  - IoU tracking with stable ids;
  - **engagement:** facing the camera within a yaw/pitch limit for N seconds;
  - **active speaker:** mouth activity while the VAD says someone is speaking;
  - **overlap** detection;
  - one selected participant, without switching back and forth.
- **Pan servo.** A target angle that keeps the selected face centred, with a dead band, smoothing and a speed limit, sent to the ESP32 `SERVO` message. Camera rotation is separate from base heading.
- **Privacy.** Frames never leave `visiond`. Only ids, boxes and flags are sent, nothing is stored. A debug window is optional and local only.
- **Laptop test with the phone.** Write a script plus steps in the log: phone camera → `visiond` → the console shows faces, engagement and the active speaker, and the simulated servo angle follows the face. Write the steps the student must try (1 person, then 2 people talking in turn).
- **Tests:** the pure logic (tracking, engagement timer, active-speaker selection, servo smoothing) with recorded or synthetic landmarks.

## R5 — Pi installation and operations
- **`robot/deploy/`:**
  - setup script for Raspberry Pi OS Bookworm 64-bit (packages, a Python venv, OpenCV and MediaPipe);
  - a udev rule with a stable name for the ESP32 serial port;
  - systemd units for `robotd` and `visiond`;
  - Chromium kiosk autostart on the 7" screen with the touch screen enabled;
  - audio output selection (I2S amplifier or USB);
  - a log location.
- **`robot/README.md`:** first boot, setting the backend address and `ROBOT_TOKEN`, how to update the code.

## R6 — End-to-end on the laptop (no hardware)
- `robotd --sim` + `visiond` with the phone camera + the backend:
  1. The simulated robot roams.
  2. A real face from the phone engages it, so it stops and the camera turns.
  3. The conversation runs on the localhost UI.
  4. It resumes roaming.
  5. The admin asks it to return to start.
  6. The trajectory appears in the admin, marked SIMULATED.
- Record what worked in the log, honestly, as simulation.
- Update `docs/DEMO.md` with an optional "robot software in simulation" part.

## R7 — Wrap-up
- Update `PLAN.md` (robot software status), `DECISIONS.md` (architecture, protocol, models) and `HARDWARE.md` (the pin-map table to fill in).
- Run all tests: backend pytest, robot pytest and PlatformIO native tests.
- Commit, push and write the log summary.
- **Checklist for when the parts arrive:** wiring, filling in `pins.h`, defining `PINS_CONFIRMED`, the first motor test with the wheels off the ground, encoder direction checks, gyro calibration, and `metres_per_tick` calibration from docs/ODOMETRY.md §5.

## Done when
- R0–R7 are complete and logged with commit hashes.
- All test suites pass.
- The firmware compiles for `native` and `sim-check`.
- The laptop end-to-end simulation is documented.
- The log lists exactly what the student must do with the real hardware.
