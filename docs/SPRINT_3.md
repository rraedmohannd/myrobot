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

## S0 — Baseline and sprint 2 leftovers
1. Create the branch.
2. Run `pytest`.
3. Read `SPRINT_2_LOG.md` and record the state. Note anything from sprint 2 that was left unfinished, and finish it first if it is small.
4. **Arabic explainers in natural Jordanian dialect.**
   - For each **Arabic** explainer that is still `pending`, draft it again: the student found the first drafts too close to formal Arabic. That is at most 6 LLM calls.
   - Keep the same scenes and checks, and remove closing lines like "بدك تسمع أكثر؟", because the robot already asks after segment 3.
   - **Never touch an approved explainer.** The new drafts stay `pending`.
5. **Bug found on 2026-11-13: rejecting a source left its approved chunks in use.**
   - **What happened.** `POST /api/admin/sources/{id}/review {"decision":"reject"}` only rejects **pending** chunks. Retrieval filters on the chunk status alone, so a rejected source whose chunks were already approved kept answering. Source 33 was "rejected" but its chunk 48 (Civil Engineering) still answered.
   - **What was done by hand.** Chunks 45 and 48 were set to rejected through the API.
   - **Fix.**
     - Rejecting a source rejects **all** its chunks.
     - Retrieval also requires the source to be approved (join `sources`).
     - The admin shows a warning if any approved chunk belongs to a source that is not approved.
   - **Test:** an approved chunk of a rejected source is never retrieved.
6. **Formatting pass.** Run `ruff format` and fix the long lines in a separate commit that changes nothing else. If line length is the real issue, agree it in `pyproject.toml` (for example 110) and document it. Tests must pass before and after.

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

## S5b — The TTS model shuts down on 2027-01-06
OpenAI's deprecations page lists `gpt-4o-mini-tts` (and `tts-1`, `tts-1-hd`) with shutdown on **Jan 6, 2027**. It names `gpt-realtime-2.1-mini` as the replacement. That date is before the project deadline (2027-01-24) and the defense. The student had planned ElevenLabs only for the final week, to save credits.
- **Two new TTS adapters behind the existing `TTS` interface (rule 7), both selected by config:**
  - `openai_realtime`: speech from `gpt-realtime-2.1-mini` through OpenAI's current API for it. Check the official docs for how to get audio from text.
  - `elevenlabs`: the ElevenLabs text-to-speech API. It needs `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` in `.env.example` only; never touch `.env`.
- **Keep `gpt-4o-mini-tts` as the default for now.** Do **not** switch the default, and do **not** call ElevenLabs (it costs the student's credits). Test both adapters with fakes or recorded responses only.
- **One short real check** of `openai_realtime`: at most 5 short phrases (Arabic and English). Record the time to first audio and whether the Jordanian pace and accent instructions are followed. Write the result in the log.
- **Fixed-phrase cache.** Switching provider must not mix old and new audio: the cache key already includes the model, voice, instructions and speed.
- **`docs/PLAN.md`:** replace "switch to ElevenLabs in the final week" with a dated decision for the student. Either switch to `openai_realtime` before 2027-01-06 and ElevenLabs in the final week, or move ElevenLabs earlier. Ask in "Questions for Mohannd".

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
