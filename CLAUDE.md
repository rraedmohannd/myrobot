# CLAUDE.md — Smart Campus Robot (MEU, Graduation Project 2)

Interactive reception robot for the **Faculty of Engineering & Design (كلية الهندسة والتصميم), Middle East University (MEU), Jordan**.
Solo student project. Deadline: project ready by **2027-01-24** (12 working weeks from 2026-11-02), demo week after.

The student discusses in **Arabic (Jordanian)**. Code, comments, commit messages and docs are in **English**.
User-facing robot speech and UI are **Arabic (Jordanian) first, English second**.

## Read first
- `docs/BACKEND_SPEC.md` — backend, database, conversation pipeline, phases B1–B5.
- `docs/ODOMETRY.md` — pose estimation, trajectory logging, return-to-origin.
- `docs/HARDWARE.md` — confirmed parts list (Rover 5, ESP32, Pi 5, USB webcam, HDMI screen).
- `docs/HOW_TO_START.md` — how to run the stack, crawl, import knowledge and review.
- `docs/PROTOCOL.md` — robot ↔ backend WebSocket protocol (/ws/robot), used by /voice and the Pi.
- `docs/PLAN.md` — status per phase, milestones, final-week checklist (incl. ElevenLabs switch).
- `docs/SPEAKERS.md` — multi-speaker "who is speaking now" (voice + camera), privacy rules, validation.
- `backend/data/knowledge/` — curated knowledge files built from official documents (originals in `documents/`).

## Repository layout
```
backend/    FastAPI app (Python 3.12+), Alembic migrations, tests
admin/      React + TypeScript (Vite) admin panel
shared/     Robot<->backend message contract (JSON Schema + pydantic models)
robot/      Raspberry Pi 5 runtime (vision, audio, UI, state machine, odometry) — later phase
firmware/   ESP32 firmware (motors, encoders, IMU, ultrasonics, servo, watchdog) — later phase
docs/       Specs and decision log
docker-compose.yml   postgres+pgvector, backend, admin
```

## Hard rules
1. **Never invent university facts.** Fees, admission requirements, dates, credit hours, scholarships come only from approved records in the database, with their source URL. If missing → say so and refer to Admission & Registration.
2. **Promotion is allowed, fabrication is not.** The robot is openly enthusiastic about Engineering ("pitches" table), but pitches are flagged as promotional and never contain made-up numbers or claims about other faculties.
3. **Scope = Faculty of Engineering & Design only** (Intelligent Systems Engineering, Renewable Energy Engineering, Architectural Engineering, Interior Design, Graphic Design). Do not ingest IT, Business or other faculties, or university service units. Comparison questions (e.g. Intelligent Systems Engineering vs Software Engineering) are answered from curated pitches, steering toward this faculty.
4. **Safety is local.** Motor stop, obstacle response and command timeouts live on the ESP32 / robot. Nothing in the backend may be required for the robot to stop safely.
5. **Simulation is labelled.** Any simulated telemetry or mock hardware must be clearly marked and never presented as physical test results.
6. **No hard-coded secrets.** Use `.env` (+ `.env.example`). Never commit keys.
7. **Providers behind adapters.** STT, LLM, TTS each have an interface; concrete providers are swappable via config.
8. Do not write ESP32 pin assignments until the wiring is confirmed in `docs/HARDWARE.md`.

## Conventions
- Python: `ruff` + `black`, type hints everywhere, `pydantic` v2 models, async SQLAlchemy 2.x, `pytest` (+ `pytest-asyncio`).
- Every DB change goes through an Alembic migration.
- Every phase ends with passing tests and an updated `docs/DECISIONS.md` entry.
- Log per-stage latency (STT / retrieval / LLM / TTS) for every conversation turn.
- Prefer small, reviewable commits.

## Running (target)
```
cp .env.example .env      # fill keys
docker compose up -d      # db + backend + admin
docker compose exec backend alembic upgrade head
docker compose exec backend pytest
```
