# Sprint 3 log

Task file: `docs/SPRINT_3.md`. Branch: `sprint-3` (created from `sprint-2` at 4f95d07).

**Start time: Fri 2026-11-13 19:19 (laptop clock, `date`).** Time budget: 4 hours → stop by about 23:19.

The run was interrupted at about 20:00 by the usage limit of the Claude session and resumed at 21:01. S0–S7 were done before the pause, S8–S10 after it. Working time was about 1 hour 20 minutes; the sprint finished at about 21:40, inside the 4-hour window.

## Questions for Mohannd
1. **Approve FAQs and pitches**: `/admin` → "FAQs & pitches". 38 FAQ drafts and 12 pitch drafts are pending; I approved none. **19 FAQ drafts ask a generic question without naming their program** ("كم ساعة معتمدة بتطلب الخطة؟"): the page shows this in red and refuses approval until you add the program name to the question.
2. **Enter places and labs**: `/admin` → "Places & labs". Both tables are empty; I entered nothing, because locations must come from you.
3. **Create the admin user**: `docker compose exec backend python -m app.ingestion.cli create-admin yourname` (it asks for the password; at least 10 characters).
4. **Firewall, fixed address, ROBOT_TOKEN**: follow `docs/DEPLOY.md`. These need administrator rights and your `.env`, which I do not touch.
5. **Streaming TTS: please listen and decide.** Measured back to back this time, streaming saved about 0.7 s (2.8 s against 3.6 s to the first answer audio). The single run of sprint 2 had shown no gain, and nobody has heard the streamed audio. It is still off. Try `TTS_STREAMING=true` in `.env`, ask a few questions on `/robot`, and keep it if the voice starts cleanly without a gap.
6. **Docker image was rebuilt** (new packages: `argon2-cffi`, `jsonschema`, `matplotlib`), and `docker-compose.yml` now mounts `./shared`, `./docs` and `./reports`. The old image is kept as `smart-campus-robot-backend:pre-sprint3`; delete it when you are happy (`docker rmi smart-campus-robot-backend:pre-sprint3`).
7. **Left in your database by my checks**: one SIMULATED robot run (id 205) with its command events, from trying `simulate-robot` on the live backend. It is labelled as simulated; delete it or keep it as an example. The scratch copies of the database used for screen checks and the soak test were dropped.
8. **Privacy notice near the robot** (docs/PRIVACY.md, last section): the robot does not ask for consent. Agree a short notice with your supervisor.
9. **React admin panel**: the admin is still one static page with 14 tabs. It works, but B5 in the plan mentions a React panel. Do you still want it, or is the static page enough for the project?
10. The open questions of sprint 2 are unchanged (voice pace by ear, `VAD_SILENCE_MS`, the TTS model shutdown on 2027-01-06, study-plan links, real photos, the stale faculty overview page).

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

## S8 — Numbers and figures for the report
- `export-report` writes CSV files and PNG charts to `reports/` (git-ignored): latency per stage (median, p90, box plot), latency measurement runs, evaluation pass rate over time, found / not-found counts, speaker confidence, a trajectory plot per run, and return-trial statistics for real runs only.
- Simulated runs: the file name ends in `_SIMULATED`, the title starts with "SIMULATED — not a physical result", and the plot has a watermark. With no data a chart says so; nothing is made up.
- I ran it on your data (18 files) and looked at the charts. What the data says: only 11 recorded question turns, with a median of about 9.1 s to answer audio (they were recorded before the speed work); the found rate rests on 3 turns; all 3 robot runs are simulated; there are no physical return trials.
- Charts are in English only (the chart font cannot shape Arabic).
- Tests: 3 in `tests/test_report.py`.
- Commits: code in `a0dbf41` (module and tests) and `7c90a07` (command); docs and decision record in `70cbf6e`.

## S9 — 30-minute soak test (a SIMULATION result)
- `soak --minutes 30` ran once, for 30.0 minutes, against a preview backend with **fake AI providers** and a scratch copy of the database, with the **simulated robot**. It refuses to run against the real backend.
- Result (file `backend/data/soak/20261113-183122.json`):

  | | |
  |---|---|
  | conversations | 88 (a typed question every 20 s) |
  | robot | 1798 status messages, 3391 pose samples, 11 simulated runs |
  | command requests | 72: 56 accepted, 16 refused by the simulator's rules, 0 unanswered |
  | errors | 0, and no error line in the backend log |
  | answer time (backend overhead only) | median 203 ms; first third 192 ms, last third 215 ms (drift +23 ms); max 717 ms |
  | backend memory | 86 MB at start, 172 MB after warm-up, 173 MB at the end: **+1.1 MB in 30 minutes** |

- What it shows: the backend process stays stable through a long mixed session. What it does not show: anything about the real robot, the real providers, the network, audio, or a browser page left open for 30 minutes. The project's real 30-minute test is still to be done with the hardware.
- Tests: 2 in `tests/test_soak.py` (the summary arithmetic).
- Commits: script in `af6d193`; the run, its result file and the tests in `6b992b4`.

## S10 — Wrap-up
- `docs/DEMO.md` (new Part 1c: FAQ, comparison, directions, robot tab, weak internet; preparation steps; numbers; limits), `docs/HOW_TO_START.md` (sprint 3 section), `docs/PLAN.md` (B5 status and your to-do list), `docs/DECISIONS.md` (one row per step).
- **Evaluation (1 of 2 runs used):** **48/49 as recorded, 0 numbers outside the sources**, median 1988 ms, p90 2467 ms. Report: `backend/data/golden/results/20261113-183507.md`.
  - The one recorded failure, `ise-robot-prereq-ar`, had a correct answer ("الأنظمة المضمّنة"). The model wrote the word with a shadda and the checker compared diacritics. I fixed the checker (`normalize` now drops Arabic diacritics, with a test). Re-checking the stored answers of this run offline gives 49/49; I did not spend the second run to record that.
  - No answer changed because of the curated layer: no FAQ, pitch or place is approved yet.
- **Latency (2 of 2 runs used), the back-to-back comparison sprint 2 asked for** (8 questions each, through the conversation manager, STT not included):

  | | LLM first piece | first answer audio (median / p90) |
  |---|---|---|
  | whole mp3 pieces (today's default) | 1102 ms | **3561 / 4338 ms** |
  | streamed PCM chunks (`TTS_STREAMING=true`) | 1328 ms | **2828 / 3923 ms** |

  Streaming was about 0.7 s faster here, even with a slower LLM step. One pair of runs; the setting stays off until you have heard it (question 5).
- OpenAI budget used: `evaluate` 1 of 2; `latency` 2 of 2; drafting 7 LLM calls of the 12 allowed; one embedding call for the FAQ calibration.
- Final `docker compose exec backend pytest -q`: **222 passed, 0 failed** (165 at the start of the sprint). `node backend/tests/js/speaker_logic_test.mjs`: all checks passed.
- Commit: `e326d7a`. Pushed: `origin/sprint-3` (never `main` or `master`).

# Summary for Mohannd

## What you must do
1. `docker compose build backend`, `docker compose up -d`, `docker compose exec backend alembic upgrade head`, `docker compose exec backend pytest -q` → 222 passed. (On this laptop the image is already built.)
2. **Create the admin user** and log in: `docker compose exec backend python -m app.ingestion.cli create-admin yourname`.
3. **Approve FAQs and pitches** in `/admin` → FAQs & pitches. Add the program name to the 19 generic questions first.
4. **Enter places and labs** in `/admin` → Places & labs, and approve them.
5. **Set up the network** when you need another device or the robot: fixed address, **firewall rule**, `ROBOT_TOKEN`, `PUBLIC_BASE_URL` (`docs/DEPLOY.md`).
6. Listen to streamed audio and decide (question 5).
7. Make a backup before each demo: `powershell -ExecutionPolicy Bypass -File scripts\backup-db.ps1`.

## What is done
| Step | Result | Commit |
|---|---|---|
| S0 baseline | 165 tests; state after sprint 2 recorded | `b396172` |
| S1 protocol contract | `shared/scr_protocol`, protocol version 2, generated schema and doc tables, contract tests | `900270c` |
| S2 robot control | live status, command requests with acks and timeout, admin "Robot (live)" tab, simulated robot | `644a329` |
| S3 FAQs and pitches | instant approved answers before search, comparisons, 38 + 12 drafts pending | `a0dbf41` |
| S4 places and labs | approved-only directions, "ask reception" fallback, admin tab | `03cd59c` |
| S5 resilience | time limits, retry, circuit breaker, cached offline reply with QR, provider status | `b9af850` |
| S6 LAN | `docs/DEPLOY.md`, token for non-local robots, QR on the admin page | `82af12c` |
| S7 login, privacy, backups | argon2id login, retention purge, `docs/PRIVACY.md`, backup scripts | `7c90a07` |
| S8 report figures | `export-report`: CSVs and charts, simulated runs labelled | `a0dbf41`, `7c90a07`, `70cbf6e` |
| S9 soak test | 30 minutes simulated: 88 conversations, 0 errors, +1.1 MB | `af6d193`, `6b992b4` |
| S10 wrap-up | docs, evaluation 48/49 (49/49 after the checker fix), latency comparison | `e326d7a` |

## What is not verified, said plainly
- **No sprint 3 feature was tried with a real voice or a real robot.** Screens were checked in a headless browser with fake providers on a scratch database; the robot side is a software simulator.
- **The backend → robot commands have never reached real hardware.** The Pi and ESP32 code is sprint 4.
- **Offline behaviour was tested with fake failing providers**, not with the internet actually cut.
- **The FAQ threshold (0.88)** comes from a small sample with two true matches.
- **The restore script's `-Replace` mode was not run** (it replaces the live database). The backup and the restore into a check database were.
- **Self-signed HTTPS** answered over HTTPS once; it was not tried with a microphone from a second device.
- **Streaming TTS**: one pair of runs in its favour, one earlier run against, never heard.
- `ruff` long-line warnings grew again; no formatting pass was made.
- 48/49 is the recorded evaluation number; 49/49 is a re-check of the same answers after fixing the checker.
