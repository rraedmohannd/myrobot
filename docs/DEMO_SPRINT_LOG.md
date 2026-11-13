# Demo sprint log

Task file: `docs/DEMO_SPRINT.md`. Branch: `demo-sprint` (created from `master` at d20be5d).

**Start time: Fri 2026-11-13 16:31 (laptop clock, `date`).** Time budget: 4 hours → stop by about 20:31.

## Questions for Mohannd
1. **Nothing was pushed: no git remote is configured in this folder** (`git remote -v` is empty, and the `gh` tool is not installed). `git push -u origin demo-sprint` answered "'origin' does not appear to be a git repository". I did not create or guess a remote. All work is committed locally on the branch `demo-sprint` (10 commits). To push it yourself:
   `git remote add origin <your GitHub repo URL>` then `git push -u origin demo-sprint`.
   (DECISIONS says the repo was pushed to GitHub on 2026-11-06; that was probably done from another copy of the folder.)
2. **The Student Handbook (source 346, 19 chunks) and the Biomedical Engineering plan (source 345, 13 chunks) are still `pending`, and the faculty-overview page (source 66) is `stale`.** The robot cannot use them until you review them in `/admin`. I did not approve anything. Until then: handbook and Biomedical questions get "I don't have this information", the Arabic "which programs" answer is incomplete, the robot may say Biomedical Engineering is "not listed" (wrong), and the golden set stays at 37/49.
3. The local branch is called `master`, while the task file says `main`. Nothing was merged or pushed to either.
4. **Reasoning effort:** `LLM_REASONING_EFFORT` now defaults to `low` (10/10 on the bench, and 37/38 on approved content in `evaluate`). If your `.env` has its own `LLM_REASONING_EFFORT=` line, that line wins; set it to `low` or delete it. To go back: `LLM_REASONING_EFFORT=` (empty) is not enough because the default is now `low`; use `medium`.
5. **Latency target:** about 4.7–5.2 s from end of speech to the answer (estimate) against the 4 s target. The next real gain is in TTS (about 1.8 s per piece). Do you want TTS audio streaming (a protocol change to PCM chunks, about half a day), or wait for the ElevenLabs switch in the final week?
6. **Camera prototype:** it needs you and one more person for 10 minutes (docs/SPEAKERS.md → Validation, item 4). Until then keep it off in the demo.
7. **Repository size:** the vendored MediaPipe files add 18.5 MB to git (asked for by the task file: local, no CDN). Tell me if you prefer to download them in the Docker build instead.
8. **Media:** real MEU photos or videos need the university's permission. Who can give it (dean's office, media office)? The placeholders say clearly that they are samples.
9. **Simulator numbers:** track width (0.19 m), slip and gyro noise are guesses. When the Rover 5 is on your desk, measure the track width and run the calibration of ODOMETRY.md §5, and I will replace them.
10. Some Arabic answers address the visitor in the feminine form ("اسألي"). Should the prompt ask for a neutral or masculine default? It needs one more `evaluate` run afterwards (one run is left from today's budget, unused).

## M0 — Baseline
- `git status` at the start: clean except the untracked `docs/DEMO_SPRINT.md` (the pending work from 2026-11-06 was already committed as d20be5d, so no "Pending work" commit was needed).
- `docker compose up -d`, `alembic upgrade head` (already at 0005), `pytest -q`: **66 passed**.
- `stats`: sources approved 12 · rejected 5 · pending 2 · stale 1; chunks approved 81 · pending 33 · rejected 7 · stale 1.
  - Pending sources: Biomedical Engineering study plan (id 345) and Student Handbook 2025/2026 (id 346). **Neither is approved.**
  - Stale: "Faculty of Engineering & Design — departments and programs" (id 66), changed when Biomedical Engineering was added; needs re-review.
- Added the "Don't forget … videos while the robot explains" section to `docs/PLAN.md`; added `backend/data/media/` to `.gitignore`.
- Commit: `db30197` (16:32). Elapsed: 1 min. Not pushed (no remote, see question 1).

## M1 — Robot screen in the browser: `/robot`
- New page `http://localhost:8000/robot` (1024×600 stage, scaled to the window, label "محاكاة لشاشة الروبوت").
  - SVG/CSS face with one look per mode (idle, attentive, listening, processing, answering, finished, unavailable); it blinks; the mouth follows the robot's voice through a WebAudio analyser.
  - Large right-to-left subtitles that follow the audio piece being spoken; card (title, ≤ 3 lines, source line); "متحدث N" badge; what the robot heard.
  - WebSocket drop (noticed at once) or 40 s without any message → "service unavailable" face and message, reconnect every 2 s.
  - Start button or Space = `session.start`; `T` = hidden text input (microphone fallback); `Esc` = end; `F` = fullscreen. If the microphone cannot be opened, the conversation still starts and the page tells the visitor to press T.
- Shared client `static/robot-client.js` (WebSocket, heartbeat, VAD, PCM16, audio queue, turn-taking), now used by both `/voice` and `/robot`. `/voice` keeps the same controls and log; it now also sends heartbeats.
- Backend: `display.update.card` now carries `title`, `lines` (sentences of the answer, at most 3) and `sources`; built in `services/display.py` only from the answer and its sources; no card for `found=false`. `/static` is served locally (no CDN). `docs/PROTOCOL.md` updated.
- Checked in headless Edge (screenshots of each mode, and a real session with the cached greeting: audio plays, mouth level 0.08–0.11, LISTENING afterwards). **Not checked: a real microphone conversation on `/robot` and `/voice`** — please try both once (see the summary).
- Fonts: the page uses the system fonts (Segoe UI / Tahoma on the laptop). No font file was vendored; the Raspberry Pi will need an Arabic font installed (for example `fonts-noto`), or a font file added to `static/` later.
- Tests: **69 passed** (3 new: card fields, no card when not found, pages + shared script served).
- Commit: `b8e14fa` (16:38). Elapsed: 7 min. Not pushed (no remote).

## M2 — Faster replies
- **New command** `python -m app.ingestion.cli latency [--n 8] [--no-stream] [--effort low]`: runs golden questions through the real pipeline (retrieval → LLM → first TTS piece) and prints the median and p90 per stage. Results are saved in `backend/data/latency/`.
- **Streaming.** `OpenAILLM.stream_json` uses OpenAI streaming behind the `LLM` protocol (adapters that cannot stream still work through `complete_json`). The model keeps its JSON format; `AnswerExtractor` reads the `answer` string out of the partial JSON, `SpeechPieces` cuts it into a short first piece and then sentences, and each piece goes to TTS at once. `found` and `sources` still come from the complete JSON.
- **Safety.** Every piece is checked against the retrieved passages before TTS. A piece with a number that is not in the passages is never spoken or shown; the robot says the safe "ask Admission & Registration" line once instead. Tests prove this with a fake streaming LLM.
- **Other small gains:** one shared OpenAI client per process (connections stay open), a tiny warm-up request while the greeting plays, and an explicit "ANSWER LANGUAGE" line in the prompt (in the baseline run an English question was answered in Arabic; after the change it was answered in English).
- **Reasoning effort low:** `bench --models gpt-6-luna --effort low` on the streaming path → **10/10 correct**, LLM median 2324 ms, max 2883 ms. Kept: `LLM_REASONING_EFFORT` now defaults to `low` (config and `.env.example`). The bench set changed by two questions, because `faculty-programs-ar` and `meu-founded-ar` depend on stale / pending sources.
- **Measured (8 golden questions, gpt-6-luna, OpenAI TTS coral, from question text to first answer audio; STT is not included):**

  | stage | before: no streaming, default effort (median / p90 ms) | after: streaming + effort low (median / p90 ms) |
  |---|---|---|
  | retrieval | 223 / 263 | 221 / 242 |
  | LLM until first speakable piece | 2718 / 4349 | 1200 / 1320 |
  | LLM whole answer | 2718 / 4349 | 2100 / 2372 |
  | TTS of the first piece | 2176 / 2708 | 1771 / 2350 |
  | **question text → first answer audio** | **5298 / 11438** | **3500 / 3988** |

  Honest notes:
  - One run each, 8 questions, a non-deterministic model: treat the numbers as indicative. The "before" p90 contains two slow outliers (a cold first request, 4.2 s retrieval, and one 9.6 s LLM call); the medians are the fair comparison.
  - The budget allowed 3 measurement runs, so streaming and effort-low were measured **together**; their separate effects are not known.
  - These numbers start at the question text. A spoken question adds STT (1.2–1.7 s on 2026-11-06), so end of speech → answer audio is now roughly **4.7–5.2 s** (estimate, not measured today), against 5.5–10 s before. **The 4 s target is not reached yet.** The filler (`ack_ms`) still plays at once and is reported separately from the answer audio (`tts_ms`).
  - TTS is now the largest stage (about 1.8 s for one short piece). Next options: stream the TTS audio itself (needs a protocol change to PCM chunks), or the planned ElevenLabs switch.
  - Not measured with a real microphone today. Please do 5 spoken questions on `/voice` and read the timings table.
- OpenAI budget used so far: 0 of 2 `evaluate` runs, **3 of 3** bench/latency runs.
- Tests: **83 passed** (14 new in `tests/test_streaming.py`).
- Commit: `e8a65d7` (16:49). Elapsed: 18 min. Not pushed (no remote).

## M3 — Admin analytics and tools
- `/admin` now has tabs: **Review queue** (unchanged), **Overview**, **Knowledge gaps**, **Conversations**, **Ask the robot**, **Evaluation**, **Knowledge coverage**. All new endpoints are under `/api/admin/…` and need `X-Admin-Token`.
  - Overview: conversations and turns today / last 7–90 days, turns per day, found / not-found rate, speakers per conversation, median and p90 per stage (STT, search, LLM first piece, LLM, ack, answer audio).
  - Knowledge gaps: questions answered with `found=false`, grouped by normalised text, most frequent first.
  - Conversations: list, then the turns of one conversation with intent, speaker, answer, sources and timings.
  - Ask the robot: a text question → answer, `found`, and every retrieved passage with source title, URL and score. It calls the real model and stores nothing.
  - Evaluation: reads `data/golden/results/*.json`; pass rate per run and the failed questions.
  - Coverage: sources and chunks by status; chunks by program and by category.
- Migration **0006**: `turns.found`, `turns.ack_ms`, `turns.llm_first_ms` (nullable; older turns show "unknown" and are left out of the found rate). Applied with `alembic upgrade head`.
- New setting `LOCAL_TIMEZONE` (default `Asia/Amman`) decides what "today" means.
- The admin page stays in English (it is the student's tool; the visitor-facing pages are Arabic first).
- Checked in headless Edge against the real database through a temporary preview container with a throwaway token (I never read `.env` or the real admin token). "Ask the robot" was tested with fakes only, not with the real model.
- Note: my `/robot` smoke tests created two conversations in your database (ids 114 and 536). I deleted exactly those two rows at the end of the sprint, so the analytics only show your own conversations.
- Tests: **99 passed** (16 new in `tests/test_analytics_api.py`: each endpoint with and without the token).
- Commit: `1d98213` (16:56). Elapsed: 25 min. Not pushed (no remote).

## M4 — Images and video on the robot screen
- Migration **0007**: `media.title`, `mime`, `size_bytes`, `source_note`, `review_status` (pending / approved / rejected, default pending), `created_at`.
- Admin → **Media** tab: upload (multipart; jpg, png, webp, mp4, webm; at most 50 MB; the content is checked, not only the extension), `source_note` is required (where the file came from and the permission), link to a program, preview, approve / reject, delete. Files are stored under random names in `backend/data/media/` (git-ignored).
- `GET /media/{id}` serves a file **only when approved** (404 otherwise).
- During an answer: when the answer's sources belong to one program that has approved media, `display.update.card.media = {kind, url, alt}`; `/robot` shows the image, or the video muted and looping, in the card next to the subtitles. `docs/PROTOCOL.md` updated.
- Demo data: two placeholder images **generated on this laptop** (rendered from a local HTML page; nothing was downloaded), clearly labelled "صورة تجريبية — Sample media / Placeholder — not a real MEU photo". `python -m app.ingestion.cli seed-demo-media` (development only) adds them as approved media with `source_note = "generated placeholder"`, linked to Intelligent Systems Engineering and Renewable Energy Engineering. I ran it once: media ids 7 and 8 exist in your database now.
- Multipart uploads are parsed by a small built-in parser, because `python-multipart` is not in the Docker image and adding it would force an image rebuild before the demo.
- Not tested: a real mp4/webm video on the screen (only the type check and the muted/loop attributes are covered). Please try one short clip.
- Tests: **112 passed** (13 new in `tests/test_media.py`: upload validation, unapproved files not served, card media only when approved).
- Commit: `1529d13` (17:00). Elapsed: 29 min. Not pushed (no remote).

## M5 — Demo pack
- `docs/DEMO.md`: 10-minute script in Arabic with English headings (start, `/robot` → `/voice` → `/admin`, 8 sample questions from golden questions that passed today plus a multi-speaker moment and an "I don't know" moment, what to show in the admin, numbers to quote, fallback plan, questions to avoid, honest limitations).
- Updated `docs/HOW_TO_START.md`, the status table in `docs/PLAN.md`, `docs/DECISIONS.md`.
- **Evaluation run (1 of 2 allowed), 49 golden questions, gpt-6-luna, streaming, effort low: 37/49 (76 %), 0 numbers outside the sources**, median 2714 ms, p90 3581 ms (text: retrieval + whole LLM answer). Report: `backend/data/golden/results/20261113-140307.md`.
  - 11 failures are the Student Handbook and Biomedical Engineering questions: those sources are still `pending` (question 2), so the robot correctly says it does not know.
  - 1 failure (`faculty-programs-ar`) comes from the stale faculty-overview page: the Arabic answer lists three programs only.
  - On approved content: 37 of 38. For comparison, 2026-11-06 gave 36/49 with the same pending sources.
  - **Watch out:** `hb-biomedical-en` "passes" the automatic check, but the answer says Biomedical Engineering is *not listed*. That is wrong since the program was confirmed; it will fix itself when you approve the Biomedical plan and the overview page. Do not ask about it in the demo before that.
  - Some Arabic answers address the visitor in the feminine form ("اسألي"). I did not change the evaluated prompt for this; it is listed under limitations.
- OpenAI budget used: **1 of 2** `evaluate` runs, **3 of 3** bench/latency runs.
- Final `docker compose exec backend pytest -q`: **112 passed**.
- Commit: `5b01942` (17:04). Elapsed: 33 min. `git push -u origin demo-sprint` was tried and failed: "'origin' does not appear to be a git repository" (question 1).

## M6 (stretch) — Robot runs: trajectory and return-to-start, SIMULATED
- **Protocol** (`docs/PROTOCOL.md`): `run.start {is_simulated, odom_config}`, `telemetry {samples[]}`, `event {kind, data}`, `run.end` on `/ws/robot`, stored in `robot_runs`, `pose_samples`, `events`. `is_simulated` is required (no default). Conversations during a run are linked to it.
- **Odometry maths** `app/telemetry/odometry.py`: the pose update of ODOMETRY.md §3 (encoders for distance, gyro for heading, mid-heading integration, wrap), written so the Raspberry Pi can reuse it.
- **Simulator** `python -m app.ingestion.cli simulate-run [--seed N]`: a simulated Rover 5 in a 6 × 5 m room with three obstacles; reactive roaming with segments of at most 5 m; track slip and gyro bias/noise; return-to-origin by direct vector, with breadcrumbs (every 0.3 m and at each turn) when the direct path is blocked. Stored with `is_simulated = true`.
  - I stored two runs in your database: run 13 (seed 7, direct return) and run 14 (seed 16, direct path blocked → breadcrumbs).
  - **The noise values are guesses, not measurements.** The "simulated true error" printed by the command (0.11 m and 0.18 m) is a simulation value; it says nothing about the real robot.
- **Admin → Robot runs**: list, trajectory plot (outbound blue, return orange, start and end markers, events, and for simulations the room and the dashed simulated true path), a big "SIMULATED — not a physical result" banner, an entry form for **physical** return-trial measurements (only on real runs; the API answers 409 for simulated runs), and return-trial statistics ("No physical return trials have been recorded yet").
- No ESP32 pins or firmware were written (rule 8), and no return trial was created.
- Tests: **127 passed** (15 new in `tests/test_telemetry.py`: kinematics, simulator, storage, simulated runs cannot get return trials).
- Commit: `df7f3ef` (17:10). Elapsed: 39 min. Not pushed (no remote).

## M7 (stretch) — Camera: who is speaking (browser prototype) — built, NOT validated with people
- `/robot?camera=1` (or the `C` key): webcam → MediaPipe Face Landmarker → IoU tracking with stable ids → mouth-opening ratio → mouth activity. While the VAD hears speech, the most active face is the speaker; `audio.end` carries `face_track_id`, and `overlap: true` when two or more faces talk in more than 30 % of the frames. Small preview with boxes; the active speaker's box is green. Optional `?face_start=1`: a face looking at the camera for 1.5 s starts the conversation.
- **The camera is off by default**, so the demo of M1–M5 does not depend on it.
- MediaPipe files (`@mediapipe/tasks-vision` 1.2.3 and the float16 face landmarker model, 18.5 MB together) are vendored in `backend/app/static/vendor/mediapipe/` with the Apache-2.0 licence and a README (sources, versions, SHA-256). These are library files downloaded from npm / Google storage; no photo or video was downloaded.
- Privacy: frames stay in the browser; only a track id and a flag are sent.
- **Manual test notes and results**
  - What I could check without people:
    - `node backend/tests/js/speaker_logic_test.mjs` → "all checks passed" (tracking ids, active speaker, single quiet face, overlap above and below 30 %, looking timer). It uses synthetic landmark data.
    - Headless Edge with a synthetic camera stream: the model and wasm load from `/static/vendor` (no external request), the loop runs at 320×240, 0 faces found (correct: the synthetic picture has no face), `audio.end` then carries `{overlap: false}` and no track id.
  - **1 person: NOT DONE.** **2 people: NOT DONE.** I have no camera with people in front of it, and I did not download any face video. So there is no evidence yet that real faces are detected well, that the mouth threshold (0.025) is right, or that overlap is detected.
  - Please run the two checks in `docs/SPEAKERS.md` → Validation, item 4, and write the counts here.
- Risk if switched on untested: a wrong threshold could send `overlap: true` and make the robot say "واحد واحد" instead of answering. That is why it is off by default.
- Tests: **129 passed** (2 new: the camera files are served locally; `audio.end` with `face_track_id` / `overlap`).
- Commit: `8fdc4f8` (17:15). Elapsed: 44 min. Not pushed (no remote).

## After M7 — final checks
- End-to-end browser check of `/robot` against the real app and database with **fake AI providers** (a throwaway preview container; no OpenAI call): typed question → filler → answer pieces spoken in order while the fake model was still writing → the sentence with an invented number replaced by the safe line → card with media → "عندك سؤال ثاني؟" → "لا شكرا" → goodbye → idle screen with the start button. All as expected.
- Two small hardening changes: the screen declares the backend lost after 40 s of silence instead of 25 s (a slow model call must not show the "unavailable" face; a closed socket is still noticed at once), and `/static` files are sent with `Cache-Control: no-cache`, so the browser never runs an old script after an update.
- `.gitattributes` keeps the vendored MediaPipe files byte-exact.
- Final `docker compose exec backend pytest -q`: **129 passed, 0 failed**.
- Total time used: about 50 minutes of the 4 hours (start 16:31, end about 17:20). I stopped because every milestone M0–M7 was attempted; the time limit was not reached.
- OpenAI budget used: 1 of 2 `evaluate` runs, 3 of 3 bench/latency runs. Everything else used fakes.

# Summary for Mohannd

## What to try first (15 minutes)
1. `docker compose up -d`, then `docker compose exec backend alembic upgrade head`, then `docker compose exec backend pytest -q` → 129 passed.
2. Open **http://localhost:8000/robot** in Chrome or Edge, press **Space**, and have a real spoken conversation. This is the one thing I could not do: **no real microphone conversation was run today**, on `/robot` or on `/voice`. Check that the mouth moves, the subtitles follow the voice, and the card with the sample picture appears for "كم ساعة معتمدة بدها هندسة الأنظمة الذكية؟".
3. Open **http://localhost:8000/voice**, do the same once, and read the timings table (ack and answer are separate columns).
4. Open **http://localhost:8000/admin**, connect with your token, and click through the tabs: Overview, Knowledge gaps, Conversations, Ask the robot, Evaluation, Knowledge coverage, Media, Robot runs.
5. Review the three sources in the Review queue (question 2), then read `docs/DEMO.md` and rehearse it once.

## What is done
| Milestone | Result |
|---|---|
| M0 baseline | 66 tests passed at the start; log, PLAN section, `.gitignore` |
| M1 robot screen | `/robot` with animated face, subtitles, card, speaker badge, unavailable state, text fallback; shared `robot-client.js` |
| M2 faster replies | streaming answers with a per-sentence number check; median to first answer audio 5.3 s → 3.5 s (STT not included); effort low |
| M3 admin analytics | 6 new tabs and endpoints, migration 0006 |
| M4 media | upload / review / serve only approved, `card.media`, labelled placeholders, migration 0007 |
| M5 demo pack | `docs/DEMO.md`, evaluation 37/49 with 0 invented numbers, docs updated |
| M6 simulated runs | telemetry messages, odometry maths, simulator, Robot runs tab with SIMULATED banner |
| M7 camera | browser prototype, off by default |

## What is unfinished or not verified
- **Not pushed** (question 1).
- **No live voice test** with a microphone, and no live conversation through the WebSocket with the real OpenAI providers. The real providers were exercised by the `latency`, `bench` and `evaluate` commands only (including streaming).
- **4 s latency target not reached** (about 4.7–5.2 s estimated from end of speech).
- **Golden set 37/49**, below the 90 % target, because three sources wait for your review.
- **Camera prototype not validated with people** (0 real faces tested).
- **A real video file** was never played on the robot screen (only images).
- **Robot runs are simulation only**; there is no physical trial and no ESP32 or Pi code.
- Admin "Ask the robot" was tested with fakes only.
- The React admin panel and admin login are still deferred (the admin is one static page with a token).
- The Raspberry Pi will need an Arabic font installed; the pages use system fonts.

## Questions for Mohannd
See the list at the top of this file (10 questions). The first two matter before the demo: the missing git remote, and the three sources waiting for review.
