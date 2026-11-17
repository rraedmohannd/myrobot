# Sprint 4 log

Task file: `docs/SPRINT_4.md`. Branch: `sprint-4` (created from `sprint-3` at 626e9aa).

**Start time: Tue 2026-11-17 19:53 (laptop clock, `date`).** Time budget: 4 hours → stop by 23:53.

## Questions for Mohannd
(filled in during the sprint; see the end of this file)

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
