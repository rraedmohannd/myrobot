# Sprint 3 log

Task file: `docs/SPRINT_3.md`. Branch: `sprint-3` (created from `sprint-2` at 4f95d07).

**Start time: Fri 2026-11-13 19:19 (laptop clock, `date`).** Time budget: 4 hours → stop by about 23:19.

**The sprint is NOT complete.** The run was cut short at about 20:00 by the usage limit of the Claude session, not by the time budget. S0–S7 are done and committed. S8 and S9 are built but not finished (see below). S10 (wrap-up) was not done.

## Questions for Mohannd
1. **Sprint 3 stopped early: continue it?** Left to do: the 30-minute soak run (S9), the wrap-up (S10: `DEMO.md`, `HOW_TO_START.md`, `PLAN.md`, the final `evaluate`), and the two `latency --dialogue` runs that would settle streaming TTS. None of the `evaluate` or `latency` budget was used.
2. **Approve FAQs and pitches**: `/admin` → "FAQs & pitches". 38 FAQ drafts and 12 pitch drafts are pending; I approved none. **19 FAQ drafts ask a generic question without naming their program** ("كم ساعة معتمدة بتطلب الخطة؟"): the page shows this in red and refuses approval until you add the program name to the question.
3. **Enter places and labs**: `/admin` → "Places & labs". Both tables are empty; I entered nothing, because locations must come from you.
4. **Create the admin user**: `docker compose exec backend python -m app.ingestion.cli create-admin yourname` (it asks for the password; at least 10 characters).
5. **Firewall, fixed address, ROBOT_TOKEN**: follow `docs/DEPLOY.md`. These need administrator rights and your `.env`, which I do not touch.
6. **Docker image was rebuilt** (new packages: `argon2-cffi`, `jsonschema`, `matplotlib`), and `docker-compose.yml` now mounts `./shared`, `./docs` and `./reports`. The old image is kept as `smart-campus-robot-backend:pre-sprint3`; delete it when you are happy (`docker rmi smart-campus-robot-backend:pre-sprint3`).
7. **Things I left in your database**: one SIMULATED robot run (id 205) with its command events, from trying `simulate-robot` on the live backend. It is labelled as simulated. A scratch copy of the database for screen checks was dropped at the end.
8. The open questions of sprint 2 are unchanged (voice pace by ear, `VAD_SILENCE_MS`, TTS model shutdown on 2027-01-06, study-plan links, real photos, the stale faculty overview page).

## S0 — Baseline
- `sprint-3` created from `sprint-2`. `pytest -q`: **165 passed**. `docs/SPRINT_3.md` and `docs/SPRINT_4.md` (your untracked files) committed unchanged.
- State found: since sprint 2 you approved all 12 explainers and 13 interest rows (7 rejected). `faqs`, `labs`, `admin_users` empty. Nothing from sprint 2 was half-built; its open points are decisions for you.
- Commit: `b396172`.

## S1 — Shared protocol contract
- `shared/scr_protocol/` (installable package `smart-campus-protocol`): pydantic v2 models for every message, the single source of truth. `python -m scr_protocol.generate` writes `shared/protocol/*.schema.json` and the two message tables of `docs/PROTOCOL.md`; tests fail when either is out of date.
- `protocol_version: 2` (adds `robot.status`, `robot.command`, `robot.command.ack`). Version 1 clients still work; `/voice` stays on 1 on purpose.
- Contract tests: in the test environment every message the backend sends is checked against the contract, so all tests enforce it; one long scenario is checked again with the `jsonschema` library; every documented robot message is accepted over the real WebSocket.
- The WebSocket now answers `heartbeat`, `robot.status` and acks at once, also while an answer is being prepared.
- Tests: 183 passed. Commit: `900270c`.

## S2 — Robot control and live status
- Admin "Robot (live)" tab: state, battery, faults, sensors, pose, small trail, SIMULATED banner, "robot offline". Buttons Start roaming / Pause / Resume / Return to start ask for confirmation and show the robot's ack.
- Commands are requests: accepted, refused (with the robot's reason) or "no answer" after 5 s. With no robot connected nothing is sent (503). Commands and acks are stored as `events` (migration 0009). An accepted return-to-start on a real run opens a `pending` return trial; simulated runs never get one.
- `simulate-robot`: a labelled SIMULATED protocol-2 client. Tried on the live backend: 6 requests, 4 accepted, 2 refused as designed.
- Tests: 183 passed (with S1). Commit: `644a329`. Pushed.

## S3 — FAQs, pitches and comparisons
- An approved FAQ answers before retrieval with no language model: same question after normalisation, or nearly the same (similarity ≥ 0.88, same program, same numbers). The threshold comes from one embedding call over the drafts and the golden questions (same question reworded 0.90–0.99; different questions about one program ≤ 0.80). Small sample.
- Pitches (`why`, `compare`) in the `pitches` table. "X ولا <program of another faculty>؟" is answered from X's approved `compare` pitch, flagged promotional; it only talks about our program. Approval is refused for numbers outside the sources, promises, and "better than" wording.
- `draft-faqs`: **7 LLM calls** (budget 12) → 38 FAQs and 12 pitches, all pending.
- Admin tab "FAQs & pitches" with edit, approve, reject. The "Ask the robot" tab shows where an answer came from.
- Tests: 199 passed. Commit: `a0dbf41` (it also holds the S4 code and the S5 guards, which share the answer pipeline).

## S4 — Labs, facilities and directions
- New `places` table and reviewed `labs` (migration 0010). Admin tab "Places & labs" (add, edit, approve, delete). "وين …؟" is answered only from approved entries, word for word as typed, with the approved photo if there is one. Contact numbers are linked to the approved handbook passage, not retyped.
- No approved match and nothing in the sources → "ما بعرف وين بالضبط. اسأل موظف الاستقبال".
- Commit: `03cd59c`.

## S5 — Resilience
- Every provider call has a time limit, one retry and a circuit breaker. A failure gives the cached "الإنترنت ضعيف هسا…" phrase, a QR to the official faculty page (from the records, not guessed), buttons for the approved explainers, and the conversation listens again.
- `/health` and the admin "System" tab show provider state and recent errors.
- Tested with fake failing and hanging providers only: fallback within 2.5 s, no crash, state LISTENING. No real outage was simulated.
- Tests: 211 passed. Commit: `b9af850`.

## S6 — Running on the LAN
- `docs/DEPLOY.md`: fixed address, firewall rule, `ROBOT_TOKEN`, QR on the admin page, microphone note, checklist, backups.
- When `ROBOT_TOKEN` is set it is required for every peer that is not local (decided from the network address, never from a header). Under Docker Desktop the laptop's own browser also needs `?token=`; this is documented.
- Self-signed HTTPS: documented and tried once (`/health` and `/robot` answered); not tried with a microphone from a second device.
- Commit: `82af12c` (its test is in the S7 commit).

## S7 — Admin login, privacy and data
- Login with argon2id hashes, an HttpOnly session cookie (only its SHA-256 is stored, migration 0011), logout, lock after five wrong passwords. `X-Admin-Token` still works for scripts. `create-admin` asks for the password in the terminal.
- `CONVERSATION_RETENTION_DAYS` (30) and `purge-conversations [--dry-run]`. `docs/PRIVACY.md`.
- `scripts/backup-db.ps1` and `scripts/restore-db.ps1`. I ran the backup once (`backups\smart_campus-20261113-195237.dump`, 1.1 MB) and restored it into a separate check database (row counts matched), then dropped that database. The `-Replace` mode was **not** run.
- Tests: 220 passed. Commit: `7c90a07`.

## S8 — Numbers and figures for the report (built, not wrapped up)
- `export-report` works: I ran it on your data and it wrote 18 files to `reports/` (git-ignored): latency per stage, latency runs, evaluation pass rate, found rate, speaker confidence, three trajectory plots titled "SIMULATED — not a physical result", and the return-trial files (empty: there are no physical trials).
- What the data says, honestly: only 11 recorded question turns; their median time to answer audio is about 9.1 s, far above the 3–4 s of the measurement runs (they are older conversations from before the speed work); the found rate rests on 3 turns.
- Not done: Arabic labels in the charts (the default font cannot shape Arabic), a `DECISIONS.md` row, the `HOW_TO_START.md` section.
- Code: `backend/app/services/report.py` and its 3 tests are in commit `a0dbf41`; the CLI command is in `7c90a07`.

## S9 — 30-minute soak test (script built, the 30-minute run NOT done)
- `python -m app.ingestion.cli soak --minutes 30` drives a simulated robot, typed conversations and command requests against a backend with fake providers, and reports memory, latency drift and errors. It refuses to run against the real backend.
- Only a **1-minute check** was run (simulation, fake providers): 2 conversations, 60 status messages, 240 pose samples, 3 commands accepted, 0 errors. That is not the 30-minute result the task asks for.
- Commit: the commit that adds this log (see `git log`).

## S10 — Wrap-up: NOT done
- `DEMO.md`, `HOW_TO_START.md` and `PLAN.md` are not updated for sprint 3. No `evaluate` was run in this sprint.
- Last test run: `docker compose exec backend pytest -q` → **220 passed, 0 failed**.

## Known gaps to check before relying on this branch
- No sprint 3 feature was tried with the real voice or a real robot. Screens were checked in a headless browser with fake providers and a scratch database.
- The 0.88 FAQ threshold comes from two true matches.
- `ruff` long-line warnings grew again; no formatting pass was made.
