# Demo sprint — backend prototype (task file for Claude Code, Fable 5.2)

Prepared on 2026-11-13. Goal: a **backend demo the student can show to the supervisor next week** (week of 2026-11-16).
You run **unattended for up to 4 hours** on the student's laptop (Windows, Docker Desktop). He will review the result later.
Work through the milestones **in order**. Each milestone must leave the project working, so the student has a usable demo even if you stop early.

Read first: `CLAUDE.md` (hard rules), then `docs/PLAN.md`, `docs/PROTOCOL.md`, `docs/SPEAKERS.md`, `docs/BACKEND_SPEC.md`, `docs/ODOMETRY.md`, `docs/HOW_TO_START.md` and the last entries of `docs/DECISIONS.md`.

---

## Working rules for this run (in addition to CLAUDE.md)

**Git**
- Before anything else, create the branch `demo-sprint` from the current branch. Do all work there.
- Commit after every milestone, and more often if useful: small commits with clear English messages.
- After each milestone, `git push -u origin demo-sprint`. **Never** push to `main`, merge into `main`, force-push, or rewrite history.

**Tests are the guardrail**
- Run `docker compose exec backend pytest -q` at the end of every milestone. It must pass before you commit.
- Never delete, skip or weaken an existing test to make it pass. Fix the code, or if a test is truly wrong, explain why in the log.
- Add tests for every new endpoint, service and migration. Unit tests use the fakes in `tests/fakes.py`, with no network and no OpenAI calls.
- Every DB change goes through a new Alembic migration (next number is `0006`), applied with `docker compose exec backend alembic upgrade head`.

**Secrets and money**
- Never open, print, edit or commit `.env`. New settings go into `app/core/config.py` and `.env.example` with safe defaults.
- OpenAI costs real money. During the whole run you may do **at most 2** `evaluate` runs and **at most 3** `bench` or latency-measurement runs. Everything else uses fakes.
- Do not add ElevenLabs (planned for the final week only, see `docs/PLAN.md`).

**Facts, media and privacy (CLAUDE.md rules 1, 2, 5)**
- Never invent or change university facts. Do not edit the facts in `backend/data/knowledge/*.json`.
- Never approve sources or chunks yourself: approval is the student's human review step. If sources are still `pending`, write that in the log and continue.
- Never download photos or videos from the internet. For demo media, generate clearly labelled placeholder files locally (for example an image that says "صورة تجريبية — Sample media").
- Simulated data must be marked `is_simulated = true` and shown with a visible **"SIMULATED — not a physical result"** label. It must never go into `return_trials` or be presented as a measurement.
- Speakers stay anonymous. Camera processing stays in the browser. No face images or voiceprints are stored or sent, only track ids and flags.

**UI**
- User-facing text is Arabic first (Jordanian, right-to-left), English second.
- The robot screen must work offline after the first load. Keep fonts, scripts and models in `backend/app/static/` (vendor files with their licence files), with no runtime CDN.

**Progress log: `docs/DEMO_SPRINT_LOG.md`**
- At the start, write the start time (`date`).
- After each milestone, append:
  - what was done and the test count;
  - the commit hash;
  - the elapsed time;
  - anything that still needs the student.
- If something needs the student (missing data, an unclear decision, hardware), add it under **"Questions for Mohannd"** and move on. Do not stop and wait.
- Each milestone also gets one row in `docs/DECISIONS.md` (project convention).
- Check the time at every milestone. When about 4 hours have passed, finish the current step cleanly (tests pass, commit, push, log) and stop.

---

## M0 — Baseline (about 15 min)
1. Run `git status`, then create the branch `demo-sprint`.
2. Run `docker compose up -d`, then `alembic upgrade head`, then `pytest -q`, and record the pass count (expected about 66).
3. If step 1 showed uncommitted changes (expected: the Student Handbook, the Biomedical Engineering plan and the speaker fixes from 2026-11-06), commit them **unchanged** as the first commit on `demo-sprint` ("Pending work from 2026-11-06"), but only if the tests pass. If they fail, write the failures in the log and fix them in a separate commit.
4. Run `python -m app.ingestion.cli stats` and record the source and chunk status counts. Note in the log whether the Student Handbook and the Biomedical Engineering plan are approved.
5. If `docs/PLAN.md` has no section "Don't forget … videos while the robot explains", add the text from the appendix at the end of this file.
6. Add `backend/data/media/` to `.gitignore`.
7. Commit and push.

## M1 — Robot screen in the browser: `/robot` (highest priority)
The browser stands in for the robot's **7" HDMI screen (1024×600)** until the hardware exists. The page must say so with a small label: "محاكاة لشاشة الروبوت".
- Use the same `/ws/robot` protocol as `/voice`.
  - Move the shared client code (WebSocket, VAD, PCM16 capture, audio queue, turn-taking) into `static/robot-client.js`, used by both pages.
  - `/voice` must keep working exactly as before.
- **Face.** An animated face in SVG/CSS with one look per `display.update.mode`: idle, attentive, listening, processing, answering, finished, unavailable.
  - It blinks.
  - The mouth moves with the robot's audio amplitude (WebAudio analyser) while `answer.audio` plays.
- **Text.**
  - Large, readable right-to-left subtitles.
  - A card area: title, at most 3 short lines, and a small source line.
  - A badge showing which visitor is being answered ("متحدث 2").
- **Unavailable state.** When the WebSocket drops or heartbeats stop, show the "service unavailable" face and message, and reconnect automatically.
- **Starting a conversation.** A big start button or the space bar sends `session.start`, standing in for "face detected" until M7. A hidden text input (key `T`) is the fallback if the microphone fails during the demo.
- **Backend.**
  - If `display.update` does not yet carry a useful `card` (title, short lines, sources) for answers, add it from the grounded answer and its sources. Never generate card facts that are not in the answer.
  - Keep the protocol backward compatible, and update `docs/PROTOCOL.md`.
- **Tests:** the route returns 200; dialogue tests cover the card fields.

## M2 — Faster replies (target: first answer audio about 4 s; today 5.5–10 s)
1. **Measure first.** Add a CLI command `latency`. It runs N golden questions (default 8) through the real pipeline (retrieval → LLM → first TTS sentence) and prints the median and p90 of each stage and of time-to-first-answer-audio. Record the baseline.
2. **Stream the answer.** Use OpenAI streaming in the LLM adapter, keeping it behind the existing `LLM` protocol (rule 7), and send the answer to TTS **sentence by sentence** as it arrives.
   - The answer format is JSON today (`answer`, `found`, `sources`). Choose a robust streaming format, such as extracting the `answer` string incrementally or a text-first format with a final metadata line, and document the choice.
   - **Safety:** no sentence may be spoken before it passes the invented-numbers check against the retrieved context. A failing sentence is dropped, and the robot says the safe "I don't have that exact information; please ask Admission & Registration" line instead.
   - The `found=false` behaviour and the sources must stay correct.
3. Try `LLM_REASONING_EFFORT=low` with `bench --effort low`. Keep it only if correctness stays 10/10.
4. **Measure again.** Write the before and after numbers honestly in DECISIONS and the log, keeping `ack_ms` (filler) and answer audio (`tts_ms`) separate.
5. **Tests:** streaming with a fake streaming LLM; a sentence with an invented number is never sent to TTS.

## M3 — Admin analytics and tools (`/admin`)
Add tabs to the existing admin page. Admin endpoints stay protected by `X-Admin-Token`.
- **Overview.**
  - Sessions and turns for today and the last 7 days.
  - Median and p90 per stage: STT, retrieval, LLM, ack, answer audio.
  - Found / not-found rate.
  - Speakers per session.
- **Knowledge gaps.** Recent questions answered with `found=false`, grouped, so the student knows which official data to collect next.
- **Conversations.** A list of sessions; open one to see its turns with intent, speaker, answer, sources and timings.
- **Ask the robot.** A test box: a text question returns the answer, `found`, and the retrieved chunks with source titles, URLs and scores (BACKEND_SPEC §7).
- **Evaluation.** Read `data/golden/results/*.json` and show the pass rate per run and the failed questions.
- **Knowledge coverage.** Sources by status (pending, approved, stale), and chunks by category and program.
- **Migration `0006` if needed.** For example `turns.found` and `turns.ack_ms`; nullable, so old rows stay valid.
- **Tests:** each endpoint, with and without the token.

## M4 — Images and video on the robot screen while it explains
See the appendix (student's request).
- **Migration.** Extend `media` with `title`, `mime`, `size_bytes`, `source_note` (where it came from and the permission), `review_status` (pending, approved, rejected; default pending) and `created_at`.
- **Admin.**
  - Upload: multipart; jpg, png, webp, mp4 or webm only; maximum 50 MB; files go to `backend/data/media/`, which is gitignored.
  - Link each file to a program.
  - Approve or reject; preview.
- **Serving.** Serve files only when approved (`/media/{id}`).
- **During an answer.** When the retrieved chunks clearly belong to one program that has approved media, add `card.media {kind, url, alt}` to `display.update`. `/robot` shows the image, or the video **muted** and looping, next to the subtitles. Update `docs/PROTOCOL.md`.
- **Demo data.** One or two generated placeholder images, clearly labelled as samples, approved through a dev-only CLI command `seed-demo-media` that marks them `source_note = "generated placeholder"`. Real MEU media will be added later by the student.
- **Tests:** upload validation, unapproved files are not served, and the card includes media only when approved.

## M5 — Demo pack (do this before any stretch goal)
- **`docs/DEMO.md`**, a 10-minute demo script in Arabic with English headings:
  - how to start everything;
  - the order of pages: `/robot`, `/voice`, `/admin`;
  - 8–10 sample questions taken from golden questions that pass, including a multi-speaker moment and an "I don't know" moment;
  - what to point out in the admin (sources, review, analytics, latency);
  - a fallback plan: if there is no internet, show the unavailable face and explain the design; if the microphone fails, use text input;
  - honest current limitations.
- **Update** `HOW_TO_START.md`, the status table in `PLAN.md`, and `DECISIONS.md`.
- **Final checks:**
  - a full `pytest -q`;
  - **one** `evaluate` run (49 questions), with the result recorded in the log;
  - a commit and a push.

## M6 (stretch) — Robot runs: trajectory and return-to-start, **simulated**
Follow `docs/ODOMETRY.md`. The tables already exist: `robot_runs.is_simulated`, `pose_samples`, `events`, `return_trials`.
- **Protocol.** Telemetry messages on `/ws/robot`, documented in `docs/PROTOCOL.md`: `run.start {is_simulated, odom_config}`, `telemetry {samples[]}` (batched), `event {kind, data}`, `run.end`. Store them.
- **Simulator.** A CLI command `simulate-run` produces a **simulated** Rover 5 skid-steer run:
  - reactive roaming in a room of about 6 × 5 m with a few obstacles, and segments of at most 5 m;
  - realistic noise: encoder slip and gyro bias;
  - then return-to-origin with the ODOMETRY.md method: a direct vector, with breadcrumbs as the fallback when the path is blocked.

  Store it with `is_simulated = true`.
- **Admin "Robot runs" tab.**
  - Plot the trajectory (outbound and return in different colours, with start and end markers) and the events.
  - A big "SIMULATED" banner on simulated runs.
  - A form to enter **physical** return-trial measurements, only for real runs.
  - Return-trial stats (there will be none yet; say so).
- **Tests:** the kinematics update, storage, and that simulated runs cannot get return-trial entries.

## M7 (stretch) — Camera: who is speaking (Level 2, browser prototype)
Follow `docs/SPEAKERS.md` ("Camera part").
- Use the laptop webcam in `/robot` (and optionally `/voice`) with MediaPipe Face Landmarker. Keep the wasm and model files local in `static/vendor/` with their licence.
- **Tracking.** Track faces with stable ids (IoU tracking) and compute the mouth-opening ratio per face. While VAD says someone is speaking, mouth activity decides the active face.
- **Messages.** Send `face_track_id` with `audio.end`, and `overlap: true` when two or more faces are active for more than about 30 % of the voiced frames.
- **Display and session start.** Show a small camera preview with boxes and highlight the active speaker. Optionally send `session.start` when a face has looked at the camera for about 1.5 s (configurable).
- **Privacy.** No frames leave the browser.
- **Testing.** Write manual test notes and results in the log (with 1 person, then 2 people).

---

## Done when
- M0–M5 are complete and logged, all tests pass, the branch is pushed, and `docs/DEMO.md` exists.
- M6 and M7 have been attempted in order if time allowed.
- The log ends with a short summary for the student: what to try first, what is unfinished, and the "Questions for Mohannd".

---

## Appendix — `docs/PLAN.md` section to add if missing
```
## Don't forget (student's request, 2026-11-06): videos while the robot explains
- **On the robot screen:** while the robot explains a program, lab or service, the display can play a short video (or show images), not only subtitles.
  - Videos must be official MEU material, or recorded by the student with permission. They are stored **locally** (project rule: essential media stays local), each with its source and date.
  - Backend: a media library in the admin (upload, link to a program/topic, review status), and an optional `card.media` in `display.update` (docs/PROTOCOL.md).
  - Robot UI: a muted or low-volume video player, so the robot's voice stays clear and the microphone does not pick up the video.
  - Timing: B5 (admin + media), then robot display in weeks 7–8.
- **Demo videos of the robot explaining** (final deliverable: demonstration video), recorded for the report and the defense. Start recording short clips as soon as the robot can talk on its screen, and keep the best takes. This also covers the final-week checklist.
```
