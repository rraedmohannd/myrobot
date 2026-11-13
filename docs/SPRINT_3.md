# Sprint 3 — complete the backend (task file for Claude Code, Fable 5.2)

Prepared on 2026-11-13. Run it **after sprint 2** (`docs/SPRINT_2.md`).

**Goal.** After this sprint the backend has everything the robot needs for the rest of the project. From then on, the work moves to the Raspberry Pi and ESP32 code (`docs/SPRINT_4.md`).

**Rules.** Same as `docs/DEMO_SPRINT.md` and `CLAUDE.md`, with these changes:
- **Branch:** `sprint-3`, created from `sprint-2`.
- **Log:** `docs/SPRINT_3_LOG.md`, with its own "Questions for Mohannd" section.
- **OpenAI budget:**
  - `evaluate`: at most 2 runs.
  - `latency`: at most 2 runs.
  - drafting FAQs and pitches: 1 call per program and language.
- **Stop** after 4 hours.

**Safety reminder (rule 4).** Every backend → robot command is a **request**. The robot and the ESP32 decide locally, and they stop safely without the backend.

---

## S0 — Baseline
1. Create the branch.
2. Run `pytest`.
3. Read `SPRINT_2_LOG.md` and record the state. Note anything from sprint 2 that was left unfinished, and finish it first if it is small.

## S1 — Shared protocol contract (`shared/`)
- Put the robot ↔ backend WebSocket messages into `shared/protocol/` as JSON Schema, with matching pydantic v2 models: everything in `docs/PROTOCOL.md`, including the sprint 1 and 2 additions.
- Make it an installable Python package, used by the backend now and by the Pi (`robot/`) in sprint 4.
- Add `protocol_version: 2`. The backend still accepts version 1 clients (`/voice`).
- **Contract tests:** every message the backend sends validates against the schema, and every documented robot message is accepted.
- Generate the message tables in `docs/PROTOCOL.md` from the schema, or at least test that they match.

## S2 — Robot control and live status
**Backend → robot**
- `robot.command {id, cmd}`, where `cmd` is one of `start_roaming`, `pause`, `resume`, `return_to_start`, `set_mode` (with args).
- The robot answers `robot.command.ack {id, accepted, reason?}`.
- Commands are requests only. Document that the robot may refuse them (for example during a conversation or a fault).

**Robot → backend**
- `robot.status` at about 1 Hz: `state` (IDLE, ROAMING, ENGAGED, CONVERSATION, RETURNING, FAULT, PAUSED), `battery_v`, `battery_pct?`, `faults[]`, sensor health, `pose`, `run_id`, `is_simulated`.

**Admin "Robot" tab (live)**
- State, battery, faults, sensor health and a small live trajectory.
- Buttons: Start roaming, Pause, Resume, Return to start. Each button asks for confirmation and shows the ack.
- A clear **SIMULATED** label when the connected robot is a simulator.
- A "robot offline" state.

**Persistence and tests**
- Store commands and acks as `events`.
- A "return_to_start" request starts a return trial record in the pending state. The physical error is still entered only by hand for real runs (rule 5).
- **Tests:** command flow with a fake robot client, the ack timeout, and that nothing is sent when no robot is connected.

## S3 — FAQs, pitches and comparisons
- **FAQs (table `faqs` exists).** Curated question/answer pairs, Arabic and English, checked **before** RAG. Use an exact or near-exact match (normalized text plus embedding similarity above a threshold). This gives an instant answer with pre-generated audio and no LLM wait.
- **Drafting.** A CLI command `draft-faqs` drafts FAQs from approved sources: common visitor questions per program, plus handbook rules. They are stored **pending**; the student approves them in an admin "FAQs" tab with edit and preview.
- **Pitches (table `pitches` exists).** Promotional talking points and **comparisons** such as "هندسة الأنظمة الذكية ولا هندسة البرمجيات؟":
  - The robot answers from approved pitches only, steering toward this faculty (CLAUDE.md rules 2 and 3).
  - Pitches contain **no numbers that are not in the sources** and **no claims about other faculties or universities**.
  - They are drafted as pending and approved in the admin.
- Retrieval attaches relevant pitches for comparison and "why this program" intents.
- **Tests:** FAQ priority over RAG, pending FAQs never used, and a pitch with an invented number is blocked.

## S4 — Labs, facilities and directions
- **Admin CRUD.** Labs (table `labs` exists: name, description, program, `location_hint`, media) and **places** (new: name, building, floor, descriptive directions from the robot's usual position, related office).
- All entries are typed in or approved by the student. **Do not invent locations.**
- **Robot answers.** "وين القبول والتسجيل؟" or "وين مختبر الروبوتات؟" are answered from approved places and labs, with the place's photo if approved media exists.
- **Contact numbers.** Link to the handbook contact numbers already in the knowledge base.
- **Tests:** approved-only use, and the "I don't know where that is — please ask reception" fallback.

## S5 — Resilience (poor internet at the demo site)
- **Timeouts and retries.** Every provider call (STT, LLM, TTS, embeddings) gets a timeout, a retry with backoff, and a circuit breaker.
- **Offline behaviour.** When a provider is down, the robot says a fixed cached phrase ("الإنترنت ضعيف هسا…"), shows a QR to the official faculty page, and offers the cached explainers and FAQs, which still work offline.
- **Status.** `/health` and the admin show provider status and recent errors.
- **Tests:** fake failing and slow providers → fallback within the time limit, no crash, and the dialogue returns to LISTENING.

## S6 — Running on the LAN for the robot
- **Docs.** `docs/DEPLOY.md` covers:
  - running the backend on the laptop at the demo site;
  - the fixed IP or hostname;
  - the Windows firewall rule for port 8000;
  - `ROBOT_TOKEN` required for robots that are not on localhost;
  - a QR on the admin page with the robot URL.
- **Microphone access.** Browsers allow the mic and camera only on `localhost` or HTTPS. Document that the Pi will serve its own UI on `localhost` (sprint 4), so the backend does not need HTTPS. Optional: add a self-signed HTTPS mode for testing `/robot` from another device.
- **Tests:** the token is required for non-local robot connections when it is set.

## S7 — Admin login, privacy and data
- **Admin login.** A real login with `admin_users` (argon2 or bcrypt hash, a session cookie, logout) and a `create-admin` CLI command. Keep `X-Admin-Token` for scripts.
- **Retention.** `CONVERSATION_RETENTION_DAYS` (default 30) and a `purge-conversations` CLI. No audio is ever stored, no face images, and speakers are anonymous. Write a short `docs/PRIVACY.md` for the report.
- **Backups.** `backup-db` / `restore-db` scripts (pg_dump through Docker) and a doc section.

## S8 — Numbers and figures for the graduation report
- A CLI command `export-report` writes CSVs and PNG charts to `reports/` (git-ignored):
  - latency per stage (median, p90 and distribution);
  - evaluation pass rate over time;
  - found / not-found rate;
  - speaker calibration results;
  - trajectory plots per run, with simulated runs clearly titled "SIMULATED";
  - return-trial error stats (real runs only).
- Charts have readable titles and units, in English with Arabic labels where useful.

## S9 — 30-minute soak test
- A script that runs a **simulated** 30-minute mixed session against the backend: conversations with fake providers, telemetry from the simulator, commands, and status messages. This matches the project's 30-minute demo target.
- It reports memory growth, latency drift and errors.
- Run it once (it can be shortened with a flag for CI) and record the result in the log as a simulation result.

## S10 — Wrap-up
- Update `docs/DEMO.md` (the robot tab, FAQs, directions), `HOW_TO_START.md`, `PLAN.md` (B5 status) and `DECISIONS.md`.
- Run the full `pytest` and one `evaluate`.
- Commit, push and write the log summary.

## Done when
- S0–S10 are complete and logged with commit hashes.
- All tests pass.
- The log lists everything the student must do: approve FAQs and pitches, enter places and labs, create the admin user, and set up the firewall.
