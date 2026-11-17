# CI (GitHub Actions)

Sprint 5, S9. Workflow: `.github/workflows/ci.yml`.

## When it runs
- On every push to a `sprint-*` branch, on every pull request, and by hand ("Run workflow" in the
  Actions tab).
- A newer push to the same branch cancels the run that is still going.
- It only tests. It never deploys, never flashes an ESP32, never touches the Pi, and needs **no
  secret** (no OpenAI key: the backend tests use fake STT/LLM/TTS/embedding providers).

## What runs (three jobs, in parallel)

| Job | What it does | Expected time |
|---|---|---|
| **backend** | Ubuntu, Python 3.12, service `pgvector/pgvector:pg16`. `pip install "./backend[dev,report]"`, `PYTHONPATH=backend:shared` (like the Dockerfile's `/app:/shared`), `alembic upgrade head`, test data (below), `pytest -q` in `backend/`, then `node backend/tests/js/speaker_logic_test.mjs` (Node 20). | 2–4 min |
| **robot** | Python 3.12 and 3.13 (matrix). `pip install ./shared "./robot[dev]"`, Node 20 (robotd's local UI JavaScript tests are called from pytest and skip without Node). `pytest -q` in `robot/`, then `pytest -q firmware/tools/tests` (pin table → `pins_generated.h`). On 3.12 only: `opencv-python-headless` + numpy and the YuNet model (cached), so the visiond camera/detector tests on synthetic pictures run instead of being skipped. 3.13 (the Pi's Python) runs without them: those tests are skipped there. | 2–3 min per Python |
| **firmware** | Python 3.12, `platformio==6.2.0` + `intelhex` (same as `firmware/tools/Dockerfile`), `~/.platformio` cached. In `firmware/`: `pio test -e native` (unit tests + Python ↔ C++ protocol cross-test), `pio run -e sim-check -t exec` (whole application with stub drivers, SIMULATED), compile checks `esp32-compile-check`, `esp32-ci-pins`, `esp32-ci-pins-core3` (FAKE pins, never flashed), and a check that `pio run -e esp32dev` **fails** with the "Pins are not confirmed" `#error`. Once `include/pins_generated.h` is committed, that last step instead requires `esp32dev` to build. | first run 10–20 min (toolchain downloads), then about 4–8 min |

### Test data in the backend job
Some backend tests expect what the laptop's database holds: the curated knowledge of
`backend/data/knowledge/` imported **and reviewed**. The CI database is empty and thrown away
after the run, so the job runs `python -m app.ingestion.cli import-knowledge` and then marks the
imported `sources` and `chunks` as `approved` **in the CI database only**. This is not a review
and changes nothing in the real database; on the real database every source is still reviewed by
hand in the admin page. Without this step 2–16 tests fail on an empty database (checked locally
on 2026-11-17 with `git archive HEAD`, Python 3.12 + pgvector pg16: 268 passed with the step).

### What was verified before the first push
- The workflow file was **linted only** with actionlint 1.7.12 (`docker run --rm -v "$PWD:/repo"
  -w /repo rhysd/actionlint:latest`, with shellcheck and pyflakes): 0 findings. It has not run on
  GitHub-hosted runners yet; the first push of a `sprint-*` branch is its first real run.
- The commands of the backend and robot jobs were run locally in Docker (`python:3.12`,
  `python:3.13`, `pgvector/pgvector:pg16`) on a clean export of the branch: backend 268 passed;
  robot 286 passed on 3.12 (with OpenCV + YuNet + Node), 276 passed + 3 skipped on 3.13 (no
  OpenCV, no Node in that container); firmware/tools 21 passed; JS speaker test passed.
- `pio run -e esp32dev` was checked to fail with "Pins are not confirmed" (PlatformIO 6.2.0).
  The other firmware steps are the ones `firmware/tools/test.sh` and S1.3 already run in Docker.

## What CI does NOT cover
- **Hardware.** No ESP32, motors, encoders, IMU, ultrasonics, servo, camera, microphone or screen.
  `sim-check` and the robot tests use simulated hardware and are labelled as such (CLAUDE.md
  rule 5); a green CI is not a physical test result.
- **The real firmware with the real pins.** Only the FAKE CI pins (`include/pins_ci.h`) are
  compiled until the pin table in `docs/HARDWARE.md` is filled and `pins_generated.h` committed.
- **OpenAI / ElevenLabs.** No provider is called: no STT, LLM, TTS or embedding quality,
  latency or cost is measured. Use `check-openai`, `bench`, `evaluate` on the laptop for that.
- **The speaker model.** `SPEAKER_MODEL_PATH` is empty in CI; tests use `FakeSpeakerEmbedder`.
  The real CAM++ model (downloaded by `backend/Dockerfile`) is not exercised.
- **mediapipe and the face landmarker model.** Not installed; only OpenCV + YuNet on 3.12.
- **The arm64 container test** (`robot/scripts/container_test.sh`, the hashed
  `robot/requirements.lock` on linux/aarch64 Python 3.13). GitHub's standard runners are x86-64;
  run that script on the laptop before copying the lock file to the Pi.
- **The Docker images and docker-compose** (the backend job installs with pip, not the image).
- **The admin panel build and linting** (`ruff` is installed but not run as a gate).
- **The Pi deploy scripts** (`robot/deploy/*.sh`, kiosk, systemd units).

## If it fails
- Open the run in the repository's **Actions** tab; each step shows its log.
- To repeat a job locally: the backend and robot commands above work in Docker
  (`python:3.12` + a `pgvector/pgvector:pg16` container); the firmware steps run with
  `bash firmware/tools/test.sh "<pio command>"`.
