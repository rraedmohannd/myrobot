# Smart Campus Robot — MEU Faculty of Engineering & Design

An interactive reception robot for the **Faculty of Engineering & Design (كلية الهندسة والتصميم), Middle East University (MEU), Jordan**. Graduation Project 2, a solo student project.

Visitors talk to the robot in Jordanian Arabic or English. It answers questions about the faculty's six programs only from approved official documents (with their source), shows short explainers and media on its screen, and can guide a visitor and return to its start point.

- The **backend** (FastAPI + PostgreSQL/pgvector) holds the knowledge base, the conversation pipeline (speech-to-text → retrieval → language model → text-to-speech) and the admin page.
- The **robot** (Raspberry Pi 5 + ESP32 on a Rover 5 chassis) runs its own screen, camera, audio and motion, and talks to the backend over one WebSocket.

## Folder map
| Folder | What is in it |
|---|---|
| `backend/` | FastAPI app, Alembic migrations, tests, curated knowledge (`backend/data/knowledge/`, built from the official documents in `backend/data/knowledge/documents/`) |
| `shared/` | The robot ↔ backend message contract (JSON Schema + pydantic), serial protocol and odometry shared by the Pi and the ESP32 tools |
| `robot/` | Raspberry Pi runtime: `robotd` (state machine, odometry, screen UI), `visiond`, `robotctl` (bring-up and calibration), install scripts |
| `firmware/` | ESP32 firmware (motors, encoders, IMU, ultrasonics, servo, watchdog) with native tests and a simulator |
| `deploy/server/` | Running the backend on a rented Linux server (install, restore, update, status scripts) |
| `scripts/` | Laptop helpers: database backup/restore, data bundle export |
| `tools/video/` | Program explainer videos rendered from approved facts |
| `docs/` | Specifications, protocols, decisions, sprint logs |
| `deliverables/` | Documents handed in to the university (Follow-Up Protocol) |

## Run it on a laptop
Needs Docker Desktop.
```
cp .env.example .env          # fill in your own keys; never commit .env
docker compose up -d
docker compose exec backend alembic upgrade head
```
Then open http://localhost:8000/health, http://localhost:8000/admin and http://localhost:8000/robot.

On a server: `docs/DEPLOY_SERVER.md`. On the local network at the demo site: `docs/DEPLOY.md`.

## Documentation
- `CLAUDE.md` — project rules (read first)
- `docs/BACKEND_SPEC.md` — backend, database, conversation pipeline
- `docs/PROTOCOL.md` — robot ↔ backend WebSocket protocol
- `docs/HARDWARE.md`, `docs/PIN_PLAN.md`, `docs/BRINGUP.md` — parts, wiring plan, bring-up steps
- `docs/ODOMETRY.md`, `docs/SPEAKERS.md`, `docs/PRIVACY.md` — pose estimation, speakers, privacy
- `docs/DEPLOY.md`, `docs/DEPLOY_SERVER.md` — laptop and server deployment
- `docs/PLAN.md`, `docs/DECISIONS.md`, `docs/SPRINT_*_LOG.md` — status, decisions, sprint logs

## Two rules
- **Safety is local:** motor stop, obstacle response and command timeouts live on the ESP32 and the Pi; the robot stops safely without the backend or the internet.
- **Simulation is labelled:** simulated telemetry and mock hardware are always marked SIMULATED and never presented as physical test results. Until the parts are wired, robot motion results are simulation only.

## How it was built
Written by the student with the help of **Claude Code** (Anthropic's AI coding assistant), which wrote much of the code, tests and documentation under the student's direction and review. Facts about the university come only from official MEU documents, never from the AI.
