# Sprint 4 log

Task file: `docs/SPRINT_4.md`. Branch: `sprint-4` (created from `sprint-3` at 626e9aa).

**Start time: Tue 2026-11-17 19:53 (laptop clock, `date`).** Time budget: 4 hours → stop by 23:53.

The sprint finished at about 20:55, roughly 1 hour after the start: P0 and the R steps were worked on in parallel.

**The questions for Mohannd, the summary and the checklist for when the parts arrive are at the end of this file.**

## P0 — Sprint 3 leftovers
Baseline before any change: `docker compose exec backend pytest -q` → **222 passed**.

### P0.1 — A rejected source switches off all its chunks
- Retrieval (full-text, vector, `embed_missing`) now needs the chunk **and its source** to be `approved`. The same rule is used where passages are read for explainer drafts, FAQ / pitch drafts, the known-number checks and place contact passages.
- Rejecting a source rejects its pending and approved chunks. Stale chunks stay `stale` (they never answer; changing them would have meant editing an existing test).
- The admin overview shows a red warning card listing approved chunks of non-approved sources. The card was not opened in a browser; only its presence in the page is tested.
- Side effect to know: a source that goes `stale` (page changed, or a failed fetch on re-crawl) also stops answering until it is reviewed again (question 3).
- Your database, read only: 0 approved chunks under non-approved sources. No review status was changed.
- Tests: 9 new → 231 passed. Commit: `076d3a4`.
- Extra commit `1fcfd2f`: while the sprint ran, the FAQs and pitches in your database became approved (38 FAQs, 12 pitches; not by me — I approve nothing). Two existing tests assumed none were approved and began to fail. They now set live approved rows aside inside the rolled-back test transaction; no assertion changed.

### P0.2 — TTS model shutdown on 2027-01-06 (sprint 3 S5b)
- `TTS_PROVIDER` = `openai` (default, still `gpt-4o-mini-tts`), `openai_realtime` (`gpt-realtime-2.1-mini`) or `elevenlabs`. Both new adapters sit behind the TTS interface (`backend/app/services/tts_adapters.py`).
- OpenAI's documentation (read on 2026-11-17) confirms the model, and that it is served only by the Realtime API. Its audio is **WAV, not mp3** (question 2). It is a conversational model told to read word for word; the adapter compares the API's transcript with the text and logs a warning on a difference. That is a check, not a guarantee.
- `elevenlabs`: written from the API reference, tested with fakes only. **It was never called.**
- Cache key: the provider is part of the key for non-default providers; existing cached audio stays valid. Tested.
- **Real check of `openai_realtime`** (voice coral, speed 1.12; 5 sends used, the first lost to a bug in the check script, 4 measured). Times count from the start of the connection:

  | Phrase | First audio | Whole call | Audio length | Transcript = text |
  |---|---|---|---|---|
  | أهلاً وسهلاً فيك! | 1405 ms | 3798 ms | 1.40 s | yes |
  | عندك سؤال ثاني؟ أنا جاهز. | 1701 ms | 4167 ms | 2.06 s | yes |
  | Welcome! How can I help you today? | 1580 ms | 4073 ms | 2.29 s | yes |
  | One, two, three. Thank you for visiting. | 1722 ms | 4245 ms | 3.45 s | yes |

  The API accepted the voice, the speed and the full Jordanian style instructions. **Accent and pace were not judged: nobody listened.** Every call took about 4 s whatever the audio length; not investigated (budget spent).
- `docs/PLAN.md`: the ElevenLabs line is now a decision question dated 2026-11-17 (question 1).
- Tests: 19 new → 250 passed. Commit: `0257942`.

### P0.3 — Formatting pass
- `ruff format` over `backend/app`, `tests`, `migrations`; all 607 long-line warnings fixed; 72 files. Line length stays 100. The syntax tree of all 116 Python files is identical before and after (apart from whitespace inside docstrings).
- Left alone because fixing them changes code: 7 import-order, 4 ASYNC240, 2 unused-import, 1 F541 and 1 B008 warnings.
- Tests: 250 passed before and after. Commit: `08ca906`.

### P0.4 — An FAQ can be linked to the wrong program (added by you during the sprint)
- An FAQ whose source belongs to a program must be linked to that program; approval is refused otherwise (409, "wrong program: the source '…' belongs to X, but the FAQ is linked to Y"), and the issue shows in the FAQ list. Pitches have no source field, so there is nothing to compare for them.
- FAQ numbers are checked against the passages of the FAQ's own program plus sources that belong to no program. Pitch number checks were already per program.
- Limit, said plainly: source 66 (the faculty departments page) belongs to no program and contains 165, so the number rule alone would **not** have caught FAQ 16. The program-link rule does (question 4).
- **Re-check, read only** (`python -m app.ingestion.cli check-curated`): found 38 FAQs, **all approved** (not 27 pending / 11 approved as in the task file), and 12 pitches, all approved. **0 wrong-program links and 0 other issues.** FAQ 16 is linked to Architectural Engineering with source 65. Nothing was changed.
- Tests: 3 new → **253 passed**. Commit: `fd6cd4b` (after the formatting pass, because the tree was already reformatted when the item arrived).

---
**Everything below (R0–R6) was written and tested on the laptop, before the hardware arrived. Nothing ran on a Raspberry Pi, on a real ESP32, with a real motor, sensor, serial port, camera or face. Results from the simulated ESP32 are simulation results (rule 5).**

## R0 — Architecture (`docs/ROBOT_SOFTWARE.md`)
- Who decides what (ESP32: motor safety; Pi: behaviour and pose; backend: conversation and requests), the two Pi processes and the Chromium kiosk page on `http://localhost:8765`, the local messages between them, the seven states with their transitions, the roaming and return rules, what happens when the backend is lost, and the navigation distances (all placeholders).
- The document was corrected after R3 so it says what the code does (backend lost → PAUSED and automatic resume; after a fault the robot never moves by itself).
- Commit: `0c7d2df` (first version); the corrections are in the R6 commit.

## R1 — Pi ↔ ESP32 serial protocol (`docs/SERIAL_PROTOCOL.md`)
- USB serial at 460800 baud; frame = `COBS(type | seq | body | CRC-16/CCITT-FALSE)` + `0x00`. Pi → ESP32: `DRIVE`, `STOP`, `SERVO`, `MODE`, `HEARTBEAT`, `CALIBRATE_GYRO`. ESP32 → Pi: `TELEMETRY` (50 Hz, 44 bytes, raw values only), `EVENT`, `ACK`. Watchdog 300 ms. The safety rules that live on the ESP32 are section 5; the choices made where the first text was silent are section 9.
- Python codec: `shared/scr_serial/` (standard library only). C++ codec: `firmware/lib/protocol/`.
- **Cross-tests:** both sides encode the same ten messages into `shared/serial_fixtures/frames_from_python.txt` and `frames_from_cpp.txt`; the tests of each side decode the file of the other side field by field and compare the bytes with their own encoding. The two files are byte-identical.
- Tests: 15 in `robot/tests/test_serial_protocol.py`, 16 in the firmware suite `test_protocol`.
- Commit: `ee64df6`.

## R2 — ESP32 firmware (`firmware/`)
- PlatformIO project. All logic is plain C++ in `firmware/lib/` (protocol, PID, ramps, servo limits and slew, gyro bias calibration, ultrasonic scheduling, safety, the application loop) behind driver interfaces; the real Arduino-ESP32 drivers are in `src/drivers_esp32/`, stub drivers in `lib/drivers_stub/`. FreeRTOS tasks: control 100 Hz, telemetry 50 Hz.
- **Rule 8:** `firmware/include/pins.h` has every pin at `-1` and a compile-time `#error` unless `PINS_CONFIRMED` is defined; a driver with a negative pin also refuses to start at run time. No GPIO number appears anywhere in `firmware/` (checked with a search). The pin-map table in `docs/HARDWARE.md` is empty, for you to fill in.
- **An uncalibrated robot cannot move:** the placeholders in `config.h` (`METRES_PER_TICK`, PID gains, ramp, maximum speed, duty caps) are all 0, `MODE DRIVE` is refused while `METRES_PER_TICK` is 0, and a test enforces it.
- Build environments: `esp32dev` (fails with the `pins.h` error, as intended), `native` (unit tests), `sim-check` (the whole application with stub drivers, compiled and run for a few simulated seconds), `esp32-compile-check` (see below).
- **How the tests run:** there is no C++ compiler on this laptop, so they run in Docker: `firmware/tools/test.sh` (Git Bash) or `powershell -ExecutionPolicy Bypass -File firmware\tools\test.ps1`.
- **Real ESP32 sources: compiled and linked only.** `esp32-compile-check` (pins stay `-1`; that build refuses to drive anything) built in Docker with platform espressif32 6.13.0 and Arduino core 2.0.17 (RAM 7.0 %, flash 22.5 %). `esp32dev` with `-DPINS_CONFIRMED` and pins still `-1` also compiles. **Never executed.** Not checked against real devices: the ICM-20948 and INA219 register addresses and scale factors (written from the datasheets from memory), PCNT wrap handling, the echo interrupts, task timing. The code branch for Arduino core 3.x was not compiled.
- Test result: see R7 (final run).
- Commit: `e112ede` (obstacle-stop hysteresis added later, see R6).

## R3 — `robotd` (`robot/robotd/`)
- Modules: `config`, `odometry` (telemetry → pose exactly as docs/ODOMETRY.md §3), `sim_esp32` (**SIMULATED** ESP32 speaking the real protocol over TCP, with its own watchdog and obstacle stop), `transport` (serial or TCP), `link` (DRIVE 20 Hz / HEARTBEAT 10 Hz), `navigation` (roaming, breadcrumbs, return-to-start), `supervisor` (pure state machine), `core` (the "brain", no I/O), `backend_link`, `ui_server` (local page, read-only proxy to the backend, `/ws/ui`, `/ws/vision`), `ui/robot-local.js`.
- **One odometry implementation:** the pose maths and the room geometry moved to `shared/scr_odometry/`; `backend/app/telemetry/odometry.py` and `simulator.py` now import from it.
- `backend/app/static/robot-client.js`: two lines so the page can be served by the robot and still talk to the backend (`window.SCR_BACKEND`, `window.SCR_ROBOT_TOKEN`). Pages served by the backend behave as before.
- Tests: **83** in `robot/tests/robotd/` against the simulated ESP32 in simulated time: roam with an obstacle → turn; segments ≤ 5 m; return-to-start direct and through breadcrumbs when blocked; heartbeat loss → the simulated ESP32 stops; a backend command accepted and one refused (fake backend); backend loss → safe state and reconnect; engagement → stop → conversation → resume; the SIMULATED label in the status.
- One smoke run against your real backend (simulated, no conversation, no OpenAI cost): **run id 543 in your database, `is_simulated = true`**, 378 samples, 4 events, 25 s of roaming then `return_done`. It is labelled as simulated; keep it as an example or delete it.
- Simulator numbers, for orientation only: in an ad-hoc check of 40 seeds, 39 returns ended `return_done` (13 direct, 26 by breadcrumbs) and 1 `return_failed {blocked}`; simulated odometry drift reached 0.3–0.7 m after 2–3 minutes. **These say nothing about the real robot**; the noise values are guesses, and the check script was not kept.
- Not done: the gyro bias is not re-estimated while the robot stands still (ODOMETRY.md §3 asks for it); `robotd` never sends `CALIBRATE_GYRO` yet.
- Commit: `1e3fb0f`.

## R4 — `visiond` (`robot/visiond/`)
- Camera source: a device index, a phone URL (IP Webcam `http://<phone-ip>:8080/video`, DroidCam `http://<phone-ip>:4747/video`) or a video file. YuNet face detection on the frame, then MediaPipe Face Landmarker on a crop per face; without MediaPipe, a weak fallback from the 5 points YuNet gives. Pure logic (no OpenCV) for tracking, engagement (1.5 s facing), active speaker, overlap, one selected participant with hysteresis, and the pan-servo target (dead band, smoothing, speed limit, limits).
- Thresholds: `shared/vision_thresholds.json` holds a copy of the constants of the browser prototype; a test fails if the JS file and the JSON drift apart.
- Privacy: only ids, boxes and flags leave the process; a test scans the source for anything that writes images.
- Models (git-ignored, fetched by `robot/models/download_models.py`): YuNet `face_detection_yunet_2023mar.onnx` from the OpenCV Zoo (MIT); `face_landmarker.task` from the MediaPipe model storage of Google (Apache-2.0), the same file as the one in `backend/app/static/vendor/mediapipe/`.
- **MediaPipe on the Pi:** PyPI (read 2026-11-17) lists an aarch64 wheel for mediapipe 1.2.3 (`manylinux_2_28_aarch64`), and pip could download it for Python 3.11. Nobody installed or ran it on a Pi.
- Tests: **84** in `robot/tests/visiond/`, all with synthetic data.
- What ran for real, on the laptop: the whole loop on a locally generated video of moving shapes (no people): 180 of 180 frames, about 29 frames per second, 0 faces. With zero faces the landmark model is never called, so the cost per face is **not measured**. On this laptop OpenCV 5.1 and mediapipe 1.2.3 installed under Python 3.14.
- **No real face was ever shown to it.** Detection quality, the mouth-activity threshold, yaw / pitch signs and the servo direction are all unverified. The phone test below is yours to do.
- Commit: `44575aa`.

### Phone camera test — to be done by you (nothing below was run)
1. Phone: install "IP Webcam" (Android), same Wi-Fi as the laptop, front camera at 640×480, "Start server". The phone shows an address such as `http://192.168.1.23:8080`.
2. Laptop, once: `cd robot`, `.venv\Scripts\python -m pip install -e ".[vision]"`, `.venv\Scripts\python models\download_models.py`.
3. Run `.venv\Scripts\python scripts\phone_camera_test.py 192.168.1.23` (DroidCam: add `--app droidcam`; laptop webcam: `--source 0`; `--mirror` if left and right are swapped; `--debug-window` for a local preview, nothing is saved).
4. The console prints one line per change or per second, for example
   `12.3s cam=ok fps=14.8 faces=1 #1[FM x=0.52 yaw=+3 pitch=-2 mouth=0.21] | selected=1 engaged=YES | vad=ASSUMED(mouth motion)=on speaker=1 overlap=no | servo SIMULATED at=-1.0 target=-1.2`
   (F = facing, M = mouth moving, x = face centre from 0 left to 1 right, target = pan angle, positive = left). In this test "speaking" is **assumed from mouth motion**; it is not a real voice detector.
5. **One person**, about 1 m away:
   - a. Face the phone → `faces=1`, F, and after about 1.5 s `engaged=YES`.
   - b. Turn the head well to the side or look down → `engaged=no`; face it again → engaged after about 1.5 s.
   - c. Move slowly to the right of the picture, then to the left → `target` goes negative for right, positive for left, and settles without swinging.
   - d. Stay silent, smile or nod → M should stay off. Talk → M and `speaker=1`. If M comes on while silent, or never while talking, write it down: the threshold then needs calibrating.
   - e. Leave the picture for more than 1 s and come back → a new id; after 3 s with no face the target drifts back to 0.
6. **Two people** side by side (0.5–1.5 m), both facing:
   - a. The first who faced the camera is `selected`.
   - b. Take turns, 5 sentences each → `speaker=` follows the talker; `selected` moves only after the other person has talked for about 1 s, and not more often than every 2 s.
   - c. A one-word remark from the other person → `speaker` may change, `selected` should not.
   - d. Both talk at once for 3 s, 5 times → `overlap=YES`.
   - e. Write down for each: correct / total, distance, lighting, phone model, the fps shown.
7. Repeat step 5 once with `--landmarks yunet` to see the weak fallback (the M flag is expected to be unreliable there).

## R5 — Pi installation and operations (`robot/deploy/`, `robot/README.md`)
- `setup.sh` (packages, virtualenv, OpenCV / MediaPipe with a fallback, models, udev rule, systemd units, kiosk autostart, optional I2S amplifier overlay), `99-esp32.rules` (stable name `/dev/esp32`), `robotd.service` and `visiond.service`, `kiosk.sh` + `robot-kiosk.desktop` (Chromium kiosk, touch enabled, microphone granted to the local page), `audio-select.sh` (speaker and microphone with `wpctl`), `robot.env.example` → `/etc/smart-campus-robot/robot.env`. Logs: the systemd journal and `/var/log/smart-campus-robot/`.
- `robot/README.md`: first boot, setting the backend address and `ROBOT_TOKEN`, checks, updating the code.
- **None of these files has been run on a Raspberry Pi.** Only `bash -n` (syntax) was checked. The kiosk autostart method and the screen mode on Bookworm are the parts I trust least.
- Commit: `799195e`.

## R6 — End-to-end on the laptop (SIMULATION, no hardware)
**How it was run.** `robotd --sim` (simulated ESP32) + a **preview backend with fake AI providers** on port 8001 with a **scratch copy of the database** and throwaway tokens + the robot page from `http://localhost:8765/` in **headless Edge** (fake silent microphone). There was no phone and no person, so the camera step was done in two labelled ways: (a) the real `visiond` on a generated video without faces, (b) a scripted stand-in, `robot/scripts/fake_vision.py` ("SIMULATED vision client, not a camera"). No OpenAI call, your real admin token and `.env` were never used, and your real database was only read (4 robot runs, 5 sessions, 29 events before and after). The scratch database and the temporary container were removed.

| Step | What was observed | Result |
|---|---|---|
| 1. The simulated robot roams | `start_roaming` through the admin API was accepted; state ROAMING; simulated odometry moved 1.0–1.6 m in 8 s | worked (simulation) |
| 2a. Real `visiond` ↔ real `robotd` | synthetic video, 0 faces, about 14 fps; `sensors.camera` went `absent` → `ok` and back when `visiond` stopped; the voice-activity message reached `visiond` | the two processes work together; **it cannot engage without a face. A real face or phone camera was not tried** |
| 2b. Engagement with the scripted stand-in | ENGAGED 2.5 s after the script started; the robot moved 0.000–0.006 m until the end of the conversation; servo targets from −12.0° to +17.7° reached the simulated ESP32 | worked (simulation) |
| 3. Conversation on the localhost page | page and assets loaded from `robotd`; badge "محاكاة · SIMULATED"; the page connected to the preview backend, got `local.engage`, sent `session.start`; greeting (a test tone) played; a typed question got the fake answer; state CONVERSATION; `return_to_start` was refused during the conversation ("a conversation is in progress") | **worked in 2 of 3 browser runs.** In the first run the conversation never timed out in the 72 s that were waited; the cause was not found and it did not happen again. In that case the robot stays stopped in CONVERSATION |
| 4. It resumes roaming | after the session ended, ROAMING within about 0.1 s, same run | worked in runs 2 and 3 |
| 5. Return to start | accepted; RETURNING → `return_done` (direct) → IDLE in 16–23 s; odometry end 0.15 m from the origin (the tolerance) | worked (simulation; odometry numbers, not floor measurements) |
| 6. Trajectory in the admin, marked SIMULATED | scratch run 558: `is_simulated` true, 596 samples, 9 events, no return trial possible; the "Robot runs" tab showed "⚠ SIMULATED", the banner "SIMULATED — not a physical result" and the plot | worked |
| Backend killed while roaming | ROAMING → PAUSED within about 0.3 s, 0.000 m moved afterwards, page "الخدمة مش متاحة · service unavailable" (I looked at the screenshot); after the restart ROAMING again 2.7 s later. The interrupted run stays without an end time and the rest goes into a new run | worked (simulation) |
| `robotd` killed, standalone simulated ESP32 | one second later the telemetry had the `WATCHDOG_STOP` flag and the encoder counts no longer changed | worked (simulation); the 300 ms itself was not measured |

- **Not checked:** sound was never heard (only the page's playback state); the microphone and speech-to-text path; a normal browser window (without the autoplay flag the page needs one touch before sound); `visiond` with a face; the real backend with real answers; everything on a Pi.
- Repeatable check without a browser: `robot/scripts/e2e_sim.py` (17 steps, 17 PASS in 46 s on the last run). It refuses a backend that is not the preview with fake providers.
- Small things seen, not fixed: the SIMULATED badge overlaps the page's own "محاكاة لشاشة الروبوت" label; `/favicon.ico` gives 404 from `robotd`; engagement and conversation are stored as sample states, not as run events; `robotd` runs have no simulated walls in the admin plot.
- **Backend change found by R3:** conversations run on the page's connection while the run is opened on `robotd`'s connection, so `sessions.robot_run_id` stayed empty. Now a session that starts on a connection without its own run is linked to the run open on the live robot link (2 tests). Known limit: the backend assumes one robot, so a conversation from any page (for example `/voice`) during an open run is linked to it. Commit `43e7f3e`.
- **Firmware change found by R3:** with noise at the threshold the simulated obstacle stop released and re-triggered, so the firmware got the same 40 mm hysteresis for the front and rear stop, with one event per blockage (7 new tests, 100 in total; `docs/SERIAL_PROTOCOL.md` §5 rule 6). Commit `acf880d`.
- `docs/DEMO.md`: new optional Part 1d "Robot software in simulation". It says which parts were tried and which were not.
- Tests added: 4 (`robot/tests/robotd/test_fake_vision.py`).
- Commit: `8afed05`.

## R7 — Wrap-up
- `docs/PLAN.md` (new "Robot software status" table; the camera row of the backend table), `docs/DECISIONS.md` (rows for P0.1–P0.4 and R0–R6), `docs/HARDWARE.md` (the empty pin-map table and the VNH / L298N open point), `docs/DEMO.md` (Part 1d), `docs/ROBOT_SOFTWARE.md`, `docs/SERIAL_PROTOCOL.md`, `robot/README.md`, `firmware/README.md`.
- **Final run of every suite (2026-11-17, 20:52, after the last code change):**

  | Suite | Command | Result |
  |---|---|---|
  | Backend | `docker compose exec backend pytest -q` | **255 passed, 0 failed** (222 at the start of the sprint) |
  | Robot (`robotd`, `visiond`, serial protocol) | `cd robot && .venv\Scripts\python -m pytest -q` | **186 passed, 0 failed** |
  | Firmware native tests | `firmware/tools/test.sh` (PlatformIO in Docker) | **100 test cases: 100 succeeded** |
  | Firmware `sim-check` build and run | same script | SUCCESS, "sim-check finished: 0 failure(s)" |
  | Browser speaker logic | `node backend/tests/js/speaker_logic_test.mjs` | all checks passed |

- The firmware compiles for `native` and `sim-check`; `esp32dev` stops at the `pins.h` error as intended; `esp32-compile-check` compiled and linked (last run after the hysteresis change).
- OpenAI use in this sprint: 5 short `openai_realtime` phrases (P0.2). No `evaluate`, no `latency` run, no ElevenLabs call. The golden set was **not** re-run after P0.1 and P0.4 changed which passages may answer; your database had no chunk affected by the new rule (0 approved chunks under non-approved sources), but a re-run is still worth one of your next evaluation runs.
- Elapsed time: about 1 hour of the 4 (most steps ran in parallel).
- Commit: `8afed05` (R6 and wrap-up documents); this log is committed after it. Pushed: `origin/sprint-4` (never `main` or `master`).

# Summary for Mohannd

## Questions for Mohannd
1. **TTS before 2027-01-06 — decide by 2026-12-20** (docs/PLAN.md, final-week checklist). Option A: `openai_realtime` before 6 January and ElevenLabs in the final week. Option B: ElevenLabs earlier. Either way, first listen to `TTS_PROVIDER=openai_realtime` on `/voice` (accent, pace, exact wording). Nobody has listened to it.
2. **WAV audio.** `openai_realtime` gives WAV, not mp3 (larger cached files). Acceptable, or should an mp3 encoder be added to the image?
3. **Stale sources.** A source that goes `stale` (page changed or a failed fetch) now stops answering until you review it again. Is that what you want, or should stale sources keep answering?
4. **Source 66** (the faculty departments page) belongs to no program but lists numbers of several programs, so its numbers count as known for every program. Should such overview pages be left out of the number check for program FAQs?
5. **Motor drivers: VNH or L298N?** `docs/SPRINT_4.md` says 2 × dual VNH shields, `docs/HARDWARE.md` says 2 × L298N. The firmware has a switch in `config.h` (default L298N).
6. **Motor voltage.** The Rover 5 motors are 7.2 V and the pack is 12.6 V. What limits the motor voltage? `MOTOR_MAX_DUTY` is 0 until this is decided.
7. **Pin budget.** The firmware needs 25 signals (6 motor, 8 encoder, 8 ultrasonic, 2 I2C, 1 servo). That is tight on a 38-pin DevKit once the input-only, strapping and flash pins are excluded. Check it on paper before wiring. The 4 encoders alone need 8 level-shifter channels, and `docs/HARDWARE.md` lists 2 converters.
8. **I2C.** The ICM-20948 and the INA219 share one bus; their addresses, the pull-ups to 3.3 V and the INA219 shunt value need checking on the real boards.
9. **Low-battery threshold.** Off in the firmware (0) and a 3S guess in `robotd` (9.9 V). It needs the data of your pack and BMS.
10. **Robot behaviour to approve or change** (all placeholders in `robot/robotd/config.py`): arrival tolerance 0.15 m, roam speed 0.20 m/s, the obstacle distances (docs/ROBOT_SOFTWARE.md §6).
11. **Backend lost while roaming:** the robot pauses and resumes by itself when the backend is back. Wanted, or should it wait for the admin?
12. **Engagement:** a visitor can engage the robot by looking at it only while it is IDLE or ROAMING (the start button works always). Should looking also work while it is PAUSED or RETURNING? Should a short look-away (for example 0.3 s) be tolerated before "engaged" drops?
13. **Camera ownership:** on the robot the page's own camera (`?camera=1`) must stay off so `visiond` owns the webcam. Confirm.
14. **"Stop roaming" command:** the protocol has no command to end roaming without returning to start. Is one wanted?
15. **Which webcam and servo?** `visiond` needs the camera's horizontal field of view and the servo's mechanical limits and mounting direction; today they are placeholders (60°, ±80°).
16. **Simulated run 543** is in your database from the `robotd` smoke run (labelled simulated). Keep it or delete it.
17. **`docs/VIDEO_SCRIPTS.md`** appeared in the working folder during the sprint (not written by this sprint). I left it untouched and uncommitted.
18. The open questions of sprints 2 and 3 are unchanged, except that the FAQs and pitches are now approved.

## Checklist for when the parts arrive
Do the steps in this order. Each one names its document. Nothing in this list has been done.

**A. Before any power**
1. Decide questions 5–8 on paper: driver type, motor voltage limit, pin budget, level-shifter channels, I2C addresses.
2. Wire the robot. Fill in the **pin-map table in `docs/HARDWARE.md`** from the wired robot (signal by signal, with who checked it and when).
3. Copy the numbers from that table into `firmware/include/pins.h`. That file is the only place with pin numbers.
4. Set the driver type in `firmware/include/config.h` (VNH or L298N).

**B. First firmware build and flash** (`firmware/README.md`)
5. Add `-DPINS_CONFIRMED` to `[env:esp32dev]` in `firmware/platformio.ini`. Build and flash (`pio run -e esp32dev -t upload`).
6. Run the firmware tests once more: `firmware/tools/test.sh` (or `test.ps1`).
7. Plug the ESP32 into the laptop first (not the Pi). Check that telemetry arrives at 460800 baud and the `SIMULATED` flag is **not** set.

**C. First motor test — tracks off the ground**
8. Put the robot on a stand so the tracks turn freely. Keep a hand on the power switch.
9. Set small values for `TEST_MAX_DUTY` and `MOTOR_MAX_DUTY` in `config.h` (they are 0 now, so nothing moves until you choose them).
10. Use `MODE TEST` and a small `DRIVE` on one side at a time. Check for each side: both motors of the side turn, and in the forward direction. If a motor turns backwards, swap its two motor wires (or its direction pins in `pins.h`) and write it in the table.
11. Stop sending commands: the tracks must stop within 300 ms (the watchdog). Unplug the USB cable while the tracks turn: they must stop.

**D. Encoders**
12. Turn each track forward by hand and watch the four encoder counts in the telemetry: each must count **up** when its track moves forward. Fix a wrong sign by swapping that encoder's A and B (wiring or `pins.h`).
13. Front and rear encoder of one side must show about the same count.

**E. Sensors**
14. Ultrasonics: put a box at a known distance (tape measure) in front of each of the four sensors and compare with the telemetry. Check that no sensor shows `absent`.
15. Local obstacle stop: tracks off the ground, drive forward, hold a hand in front of the front sensors: the tracks must stop and an `OBSTACLE_STOP` event must arrive, even while forward commands keep coming.
16. IMU: robot still on the floor, send `CALIBRATE_GYRO`, wait for `GYRO_CAL_DONE`. Then turn the robot by hand counter-clockwise: `gyro_z` must be positive.
17. Power: compare the battery voltage in the telemetry with a multimeter. Set the low-battery thresholds (question 9).
18. Servo: send `SERVO` 0, then small angles each way. Positive must turn the camera to the **left**. Set the angle limits in `config.h` before going further.

**F. Calibration** (`docs/ODOMETRY.md` §5) — record the real numbers
19. `metres_per_tick`: drive 3 m straight, 5 runs, tape measure. Put the result in `firmware/include/config.h` (`METRES_PER_TICK`) **and** in the `robotd` configuration (`metres_per_tick`): the two must be equal. Closed-loop driving (`MODE DRIVE`) is refused by the firmware until this is set.
20. Tune the speed PID gains, the ramp and the maximum speed in `config.h` (all 0 now), tracks off the ground first, then on the floor at low speed.
21. Rotation: 360° in place, 5 runs → gyro scale check. Measure `track_width_m` (used only for the encoder-heading comparison).
22. Square test: 2 m × 2 m, clockwise and counter-clockwise.

**G. Raspberry Pi** (`robot/README.md`)
23. Run `robot/deploy/setup.sh`, set `BACKEND_URL` and `ROBOT_TOKEN`, reboot. Note every change you had to make to the script: it was never run before.
24. Check `/dev/esp32`, `systemctl status robotd visiond`, the page on the 7" screen, touch, the speaker and the microphone.
25. Do the phone-camera test steps (section R4 above) again with the real USB webcam on the servo: 1 person, then 2 people.

**H. First drives on the floor**
26. Short roaming in an empty area at low speed, one hand on the switch. Check the turn-away distance and the stop.
27. Return-to-start trials (`docs/ODOMETRY.md` §6): at least 10, measuring the **physical** distance from the start mark with a tape and typing it into `/admin` → Robot runs. Only these count as results.
28. Blocked-return test, sensor-unplugged test, backend-off test, and the 30-minute mixed test of the plan.

## What to try first
1. `docker compose exec backend pytest -q` → 255 passed; `cd robot`, set up the virtualenv as in `robot/README.md`, `python -m pytest -q` → 186 passed; `firmware\tools\test.ps1` → 100 succeeded (needs Docker; the first run downloads PlatformIO).
2. Simulated robot on your laptop: `cd robot`, `.venv\Scripts\python -m robotd --sim`, open `http://localhost:8765/`, then `/admin` → Robot (live) → Start roaming. Everything is labelled SIMULATED. A conversation on the real backend costs OpenAI credit.
3. The phone-camera test (section R4): this is the first time `visiond` will see a face. Please write down what you see.
4. Read `docs/ROBOT_SOFTWARE.md` and `docs/SERIAL_PROTOCOL.md` with your supervisor, and answer questions 5–15.

## What is done
| Step | Result | Commit |
|---|---|---|
| P0.1 rejected source | retrieval needs an approved source; reject switches off all chunks; admin warning | `076d3a4` (+ test isolation `1fcfd2f`) |
| P0.2 TTS adapters | `openai_realtime` and `elevenlabs` behind the TTS interface; default unchanged; 4 real phrases measured | `0257942` |
| P0.3 formatting | `ruff format`, 607 long lines, no behaviour change | `08ca906` |
| P0.4 FAQ program | program must match the source; numbers per program; re-check: 0 mismatches | `fd6cd4b` |
| R0 architecture | `docs/ROBOT_SOFTWARE.md` | `0c7d2df`, `8afed05` |
| R1 serial protocol | spec, Python and C++ codecs, cross-tests | `ee64df6` |
| R2 firmware | modules, native tests, `sim-check`, `pins.h` guard, hysteresis | `e112ede`, `acf880d` |
| R3 `robotd` | simulated ESP32, odometry, navigation, supervisor, backend link, local page | `1e3fb0f` (+ backend session link `43e7f3e`) |
| R4 `visiond` | camera sources, models, logic, servo target | `44575aa` |
| R5 Pi operations | `robot/deploy/`, `robot/README.md` | `799195e` |
| R6 end-to-end simulation | scenario run in simulation, `e2e_sim.py`, DEMO Part 1d | `8afed05` |
| R7 wrap-up | documents, final test runs, this log | `8afed05` and the log commits |

## What is not verified, said plainly
- **Nothing ran on hardware.** No Raspberry Pi, no ESP32, no motor, encoder, IMU, ultrasonic sensor, servo, INA219, serial cable, screen, speaker or microphone. The firmware was compiled and linked for the ESP32 but never executed.
- **`visiond` has never seen a face**, and the MediaPipe path ran only on a black image and through fakes. The engagement step of the end-to-end run used a scripted stand-in.
- **All robot numbers are placeholders or simulator outputs**: speeds, distances, PID gains, `metres_per_tick`, `track_width_m`, battery limits, servo limits, field of view, the hysteresis, simulated drift and return results. None may go into the report as a result.
- **The device register maps** (ICM-20948, INA219) were written from memory of the datasheets and not checked against the devices.
- **The Pi setup script, the systemd units, the kiosk and the audio script were never run.**
- **The conversation on the local page was never heard and never used a microphone**; in one of three headless runs the session did not time out and the cause is unknown.
- **`openai_realtime` speech was not listened to**; ElevenLabs was never called.
- **The admin warning card of P0.1** was not opened in a browser.
- **Cross-machine use** (backend on the laptop, `robotd` on another machine with `ROBOT_TOKEN`) was tested only against a fake backend.
- **The golden set was not re-run** in this sprint.
