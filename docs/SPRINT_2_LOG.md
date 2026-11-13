# Sprint 2 log

Task file: `docs/SPRINT_2.md`. Branch: `sprint-2` (created from `demo-sprint` at 346b419).

**Start time: Fri 2026-11-13 17:56 (laptop clock, `date`).** Time budget: 4 hours → stop by about 21:56.

## Questions for Mohannd
1. **Review queue, faculty overview (this is the one golden question that fails).** The curated page "Faculty of Engineering & Design — departments and programs" (source 66) is still `stale`: its six-program text is `pending`. Meanwhile two old crawled website pages are `approved` (sources 30 and 33) and they list **Civil Engineering** and only three programs. Please approve source 66, and decide whether sources 30 and 33 should be rejected as outdated. I did not touch them.
2. **Explainer drafts: 12 are waiting for you** in `/admin` → Explainers (6 programs × Arabic and English). Nothing new appears on the robot until you approve them. While reading, three things I noticed and did not change:
   - Architecture (ar), segment 3 ends with its own "بتحبوا نكمل…؟", and Interior Design (en), segment 3 ends with "Would you like to hear more?". The robot asks "بدك أكمل ولا عندك سؤال؟" right after segment 3 anyway, so these sentences are doubled; delete them if you agree.
   - Intelligent Systems (en), last segment: the accreditation sentence sits in the QR segment. It is in the sources, but it may read better as its own segment.
   - The Arabic is polite but in places closer to formal Arabic than to Jordanian ("خريجو البرنامج مؤهلون"). Edit freely; an edited explainer just needs your approval again.
3. **Voice pace: listen and decide.** `TTS_SPEED=1.12` and a "lively, slightly fast" instruction are active. I measured that the speed setting changes the audio length, but I cannot hear whether 1.12 sounds natural. Change the number in `.env` if needed, then run `prepare-phrases`.
4. **End of speech at 0.7 s (`VAD_SILENCE_MS=700`, was about 0.9 s): try it with your own voice**, with a pause in the middle of a sentence. If the robot cuts you off, set `VAD_SILENCE_MS=900`. I could not test this.
5. **Streaming of the answer audio is built but switched off.** The measurements did not show a reliable gain (details in S1). To settle it I need two more measurement runs back to back (`latency --dialogue` and `latency --dialogue --no-stream-audio`); the budget of this sprint (4 runs) is used. Shall I do that next time?
6. **The TTS model shuts down before your deadline.** OpenAI's deprecations page lists `gpt-4o-mini-tts` with shutdown on **2027-01-06** (your deadline is 2027-01-24). The ElevenLabs switch planned for the last week must move before 2027-01-06. When do you want to do it? (I read this from the page through a fetch tool; please open https://developers.openai.com/api/docs/deprecations once and confirm.)
7. **Official study-plan web addresses.** Only Biomedical Engineering has a real address in the records, so only its explainer shows a QR code. For the other five programs, send me the official links (like `https://meu.edu.jo/plan/….pdf`) and I will add them as `study_plan_url` in the knowledge files. I did not guess them.
8. **Real photos or short videos per program** (with permission) for the `media` scene. Today it falls back to the line drawing. Who can give permission (same question as in sprint 1)?
9. **Interest → program table.** My first draft is in `backend/app/services/interests.py` (`DEFAULT_MAPPING`): computers and AI → Intelligent Systems; energy and environment → Renewable Energy; buildings and drawing → Architecture, Interior Design; art and design → Graphic Design, Interior Design; medicine and biology → Biomedical; maths and physics → Intelligent Systems, Renewable Energy. It is only a proposal: rows are created as pending (after you approve explainers) and you approve or change each one.
10. **Two questions or three?** The task file allowed 2–3 interest questions. I used two, because with six programs a third one adds waiting without changing the suggestion. Tell me if you want a third.
11. **Study years.** The official plans group courses by requirement type, not by year, so no draft uses the "years 1 → N" timeline. If the university has an official semester-by-semester plan, give it to me and the timeline scene can show real courses per year.
12. **Code style.** `ruff` was not clean before this sprint (134 long-line warnings) and my code follows the same long-line style, so the count is now higher (see the summary). Do you want a formatting pass in a separate commit?
13. The open questions of sprint 1 (`docs/DEMO_SPRINT_LOG.md`: feminine address in some answers, camera validation with two people, simulator numbers, live microphone test) are unchanged.

## S0 — Baseline
- `sprint-2` created from `demo-sprint`. `docker compose up -d`, `alembic upgrade head` (already at 0007), `pytest -q`: **130 passed**.
- `stats`: sources approved 14 · stale 1 · rejected 5; chunks approved 113 · pending 1 · stale 1 · rejected 7.
- **Evaluation run 1 of 2** (49 golden questions, gpt-6-luna, streaming, effort low): **48/49 (98 %), 0 numbers outside the sources**, median 2320 ms, p90 3210 ms. Report: `backend/data/golden/results/20261113-145919.md`.
  - `hb-grad-gpa-en`, the failure of the previous run, now passes (the `rag_keep_top` fix of sprint 1 works on the full set).
  - **Still failing: `faculty-programs-en`** ("Which bachelor programs does the Faculty of Engineering & Design offer?"). The robot listed Civil Engineering, Renewable Energy, Intelligent Systems, Architectural Engineering and Interior Design, and missed Graphic Design.
    - Why: the answer is not invented. It comes from approved passages: the old crawled faculty pages (sources 30 and 33) say the faculty has Civil Engineering and three programs, and the correct curated overview (source 66) is not usable because it is `stale` / `pending`. This is a review matter, not a code matter (question 1).
- **Latency baseline for S1 (measurement run 1 of 4)**, same command as sprint 1 (8 golden questions, question text → first answer audio, STT not included): median **4170 ms**, p90 4854 ms (retrieval 217, LLM first piece 1578, LLM whole 2602, first TTS piece 1938). File: `backend/data/latency/20261113-150222.json`. This is slower than the 3500 ms measured earlier today with the same code, so provider speed varies by the hour; S1 compares against this fresh baseline.
- Commit: `00c1530` (18:03). Elapsed: 7 min.

## S1 — A faster voice
**Speaking pace**
- The official API reference page for speech could not be opened from here (HTTP 403), and the installed OpenAI SDK documents `speed` (0.25–4.0) without saying which models accept it. So I measured it: the same sentence with `gpt-4o-mini-tts` gave 9.25–10.0 s of audio at speed 1.0, 6.25–6.58 s at 1.5 and 13.0–13.8 s at 0.7 (8 short TTS calls). **The parameter works**, so it is used: `TTS_SPEED=1.12`.
- `TTS_INSTRUCTIONS` now asks for "a lively, slightly fast, natural conversational pace … with only short pauses". If your `.env` has its own `TTS_INSTRUCTIONS=` line, that line wins (the tests show it has none today).
- The client playback rate exists as the task file describes (`SPEECH_RATE`, sent in `hello.ack`, applied in `robot-client.js` with `preservesPitch = true`, so `/robot`, `/voice` and later the Pi share it), but its default is **1.0**, because the provider speed already does the work and the two would multiply.
- The TTS cache key now contains model, voice, instructions and speed. `prepare-phrases` (new command) regenerated the fixed phrases: 59 pieces are cached with the new pace.
- **Not judged by ear** (question 3).

**Waiting time** (8 golden questions, question text → first answer audio, STT not included; one run each, same hour; medians / p90 in ms)

| | retrieval | LLM first piece | first TTS piece | **first answer audio** |
|---|---|---|---|---|
| Run 1: before (sprint 1 code) | 217 / 243 | 1578 / 2098 | 1938 / 2619 | **4170 / 4854** |
| Run 2: first piece of 6 words + speed 1.12 + new instructions | 228 / 626 | 1272 / 1813 | 1620 / 1869 | **3107 / 3767** |
| Run 4: as run 2, through the conversation manager, answers streamed as PCM chunks | 242 / 291 | 1233 / 1825 | – | **3310 / 3821** |

- **Kept: the shorter first piece** (`TTS_FIRST_PIECE_WORDS=6`, was 10). The first piece is released sooner by the LLM stream and is quicker to synthesize. Honest limit: one run each, and the hour-to-hour drift of the provider (3.5 s in the morning, 4.2 s in the evening with identical code) is as large as the gain, so "about 1 s faster" is indicative, not proven.
- **Streaming TTS: tried, not kept as default.** In run 2 I also requested the first piece as a stream: the first chunk came after 1056 ms (mp3) and 682 ms (PCM), against 1620 ms for the whole mp3. That looked like 0.9 s, so I built it (PCM16 24 kHz chunks, `answer.audio.start/chunk/end`, WebAudio playback in `robot-client.js`, only for clients that ask for it). Run 4, through the real conversation manager, then showed **no gain** (3310 ms against 3107 ms). A check on three phrases explained it: OpenAI sends the first 4096 bytes (85 ms of audio) early, and the continuous audio starts 0.45–0.65 s later, at 1.2–1.8 s; compared with the whole mp3 the gain was +0.5 s, −0.3 s and +1.4 s. The task file says to keep it only if it saves at least 300 ms; that is not demonstrated, so **`TTS_STREAMING=false` is the default** and the robot sends whole mp3 pieces as before. The code stays, tested with fakes (question 5).
- **End of speech:** the VAD silence is now `VAD_SILENCE_MS` (in `hello.ack`), default **700 ms**: 0.2 s less on every turn. Not tested with a voice (question 4).
- **STT (run 3):** `bench --stt-only` (new): 8 sentences spoken by the TTS voice, 2 passes, the same audio for both models. `gpt-transcribe`: median 773 ms, 16/16 exact. `gpt-4o-transcribe`: median 816 ms, 14/16 (it wrote "Tell me about Biomedical Engineering" in Arabic twice). **No switch**: no speed gain, and gpt-4o-transcribe shuts down on 2027-02-26. These are synthetic recordings, not Jordanian speakers.
- **From end of speech (estimate, not measured with a microphone):** 3.1 s plus STT (0.8 s on today's synthetic sentences; 1.2–1.7 s with a real microphone on 2026-11-06) ≈ **3.9–4.8 s**, against 4.7–5.2 s estimated after sprint 1.
- OpenAI budget after S1: 1 of 2 `evaluate`, **4 of 4** latency/bench runs (run 4 was done during S2). Besides these: 8 TTS calls for the speed check, 6 for the streaming check, and the phrase generation.
- Tests: **140 passed** at the S1 commit (10 new in `tests/test_voice_pace.py`: cache key, speed and instructions sent to the provider, settings from the environment, `hello.ack`, client code, first-piece length, streamed audio messages). 4 more were added with run 4.
- Commit: `7297098` (18:12). Elapsed: 16 min. This commit also contains migration 0008, the explainer models and the vendored QR library, which were ready at that moment. The streaming default was switched off later, in the S2 commit.

## S2 — Program explainers
- **Data:** migration **0008**: `explainers` (program, lang, status pending/approved/rejected, created_at, updated_at) and `explainer_segments` (`position`, spoken text, scene JSON, `audio_cache_key`).
- **Drafting:** `draft-explainers [--program slug] [--lang ar|en] [--force]`. One LLM call per program and language, from the program's approved chunks (department page, study plan, courses) and the handbook's general study rules. Drafts are stored as `pending`; an approved explainer is not overwritten without `--force`, and then it becomes pending again.
- **Checks (pitch rules):** every number in the speech or in a scene, and every course name shown on screen, must be written in the program's approved sources; promise words ("مضمون", "guarantee") are refused. The admin shows the reasons and answers 409 instead of approving. The same check runs again at playback: if a source was changed or withdrawn, the explainer is no longer played.
- **Admin → Explainers:** list with status and things to fix; per segment the spoken text, the scene (JSON), a live preview of the scene at 60 %, save, delete, add; approve / reject. Approval generates the speech into the cache (`prepare-explainers` does it for all approved explainers).
- **Dialogue:** "احكيلي عن تخصص X", "شو هو X", "ليش أدرس X", "tell me about X", or a program button on the screen. A specific question that only mentions a program ("كم ساعة…", "شو هي شروط القبول…") is answered normally. After segment 3: "بدك أكمل ولا عندك سؤال؟" → "اه" / the "كمّل" button / silence continues; a question ends the presentation and is answered. No approved explainer in the visitor's language → the normal RAG answer. On screen: **التالي** (skip) and **إيقاف** (stop).
- **Protocol:** `explainer.start`, `explainer.segment {index, total, scene}` (sent right before the `answer.audio` of its segment, shown when that audio starts), `explainer.pause`, `explainer.end`; `docs/PROTOCOL.md` updated.
- **Scenes** (`static/explainer-scenes.js`, used by `/robot` and the admin preview, everything local): `title` (the drawing draws itself), `keypoints` (written word by word with the audio position), `stat` (counts up to the sourced number), `timeline`, `courses`, `diagram`, `media` (approved file, else the drawing), `qr` (drawn locally with the vendored `qrcode-generator` 1.4.4, MIT; the address comes from the source record).
- **Line drawings:** six original SVG drawings made for this project: robot head and neural network; sun, solar panel and wind turbine; building facade; room plan with furniture; pen tool and bezier curve; heart and ECG line. No logos, no people, nothing copied or downloaded.
- **The 12 drafts** (12 LLM calls, the whole budget): 6–7 segments, 110–183 words each, scenes title → stat → courses → keypoints → qr, **0 issues** in the automatic check. All are **pending**; I approved none (question 2).
- **Checked in a headless browser** (Edge, 1024×600) with a preview app that uses fake providers and a test tone (`tests/preview_app.py`) and a **scratch copy of the database** in which I created sample approved explainers: every scene type, the pause with its buttons, skip, stop, the QR scene of Biomedical Engineering, a streamed answer, and the admin tab (including the refused approval). The scratch database was deleted at the end; **your database has no approved explainer and no sample text**.
- **Not checked:** a presentation with the real voice (no explainer is approved, so none has been played), the QR code with a phone, the scenes on the real 7-inch screen, touch on a touch screen.
- Things you should know:
  - Only Biomedical Engineering has a web address for its plan, so the other five show the drawing and "اسأل دائرة القبول والتسجيل" instead of a code (question 7).
  - No draft uses `timeline`, `diagram` or `media`: the model chose the simpler scenes. You can change a scene type in the admin (the JSON box), for example to `{"type": "diagram", "nodes": [...]}`.
  - The automatic check does not catch a number written in words ("خمس سنوات") or a claim without numbers. Your reading is the real check.
- Tests: **164 passed** at the commit (20 new in `tests/test_explainers.py`, 4 more in `tests/test_voice_pace.py`).
- Commit: `c57cd1f` (18:38). Elapsed: 42 min. It also contains the dialogue and screen code of S3 and S4, which live in the same files.

## S3 — Interest matching ("ساعدني أختار")
- "مش عارف شو أدرس", "ساعدني أختار", "help me choose", or the screen button → two short questions, answered by voice ("بحب الكمبيوتر") or with buttons (computers and AI, energy and environment, buildings and drawing, art and design, medicine and biology, maths and physics; "بس هيك" for no second one) → one or two programs → "بدك أحكيلك عن X؟" → "اه" or a tap starts the explainer.
- The mapping is promotional, curated data stored in the existing `pitches` table (topic `interest:<key>`), used only when **approved**. `seed-interests` (or the button in the admin) creates pending rows for programs whose explainer is approved. The spoken suggestion is a fixed sentence without claims, and promise words are refused in the rows.
- Without an approved mapping, or when the visitor asks a real question in the middle, the robot answers normally.
- I ran `seed-interests` on your database: it created nothing, as intended, because no explainer is approved yet. **So this flow is off until you approve explainers, seed, and approve the rows** (question 9).
- Tests: **165 passed** (`tests/test_interests.py`: words, the flow, approved rows only, the explainer offered at the end, fallbacks, seeding and the review API).
- Commits: code in `c57cd1f`; own tests, admin table and decision record in `aa75483` (18:40). Elapsed: 44 min.

## S4 — Attract mode
- While nobody is talking, `/robot` reads `GET /api/robot/screen` (approved content only, refreshed every minute) and cycles every 7 s through the `title` and `stat` scenes of the playable explainers, with "قرّب واحكي معي 👋", a button per program and "ساعدني أختار". Scenes that are not a title show the program name above the number.
- It stops at once when the start button or a touch button is pressed and on `session.started` (checked in the browser: the timer is cleared and the box is empty right after pressing Space).
- With nothing approved, which is the state of your database now, the idle screen is the same as after sprint 1.
- Tests: **165 passed** (`tests/test_idle_screen.py`: approved-only content, rejected and withdrawn content disappears, the page stops the attract mode).
- Commit: `07f3ba1` (18:40). Elapsed: 44 min. Pushed: `origin/sprint-2`.

## S5 — Demo pack
- `docs/DEMO.md`: new "Part 1b" (attract mode, a program explainer with the drawing and the writing, the interest flow), new preparation steps (approve the drafts, listen to the pace), the new latency numbers, and the new limits. `docs/HOW_TO_START.md`, `docs/PLAN.md` (status row, a to-do list for you, the TTS shutdown date) and `docs/DECISIONS.md` (one row per step) are updated.
- **Evaluation run 2 of 2** (49 golden questions, gpt-6-luna, effort low): **49/49 (100 %), 0 numbers outside the sources**, median 2151 ms, p90 2653 ms. Report: `backend/data/golden/results/20261113-154248.md`.
  - Read this with care: the run at the start of the sprint was 48/49 with the same data. `faculty-programs-en` passed this time only because the model weighed the passages differently; its cause is still there (question 1). No answer-related code changed between the two runs.
- Final `docker compose exec backend pytest -q`: **165 passed, 0 failed** (130 at the start). `node backend/tests/js/speaker_logic_test.mjs`: all checks passed. Migration state: 0008.
- OpenAI budget used: `evaluate` **2 of 2**; latency/bench **4 of 4**; explainer drafting **12 of 12** LLM calls; TTS: 59 fixed-phrase pieces generated, plus 14 short diagnostic calls (speed and streaming checks). No explainer speech was generated, because no explainer is approved.
- Your database after the run: 12 explainers `pending`, no interest rows, sources and chunks untouched, no test conversations left (the measurement sessions were deleted; the two conversations of this evening in the list are your own). The scratch database used for screen checks was dropped.
- Commit: see the summary below for the hash of the S5 commit. Elapsed: 49 min. Pushed: `origin/sprint-2` (never `main` or `master`).

# Summary for Mohannd

Total time used: about 50 minutes of the 4 hours (start 17:56). I stopped because S0–S5 are done; the time limit was not reached.

## What you must do (in this order)
1. `docker compose up -d`, then `docker compose exec backend alembic upgrade head` (migration 0008), then `docker compose exec backend pytest -q` → 165 passed.
2. **Listen to the voice** on http://localhost:8000/robot: is the pace right (`TTS_SPEED=1.12`)? Does the robot wait long enough when you pause mid-sentence (`VAD_SILENCE_MS=700`)? Questions 3 and 4.
3. **Approve the explainer drafts**: http://localhost:8000/admin → Explainers. Read each segment against the official plan and department page, edit, preview, approve. Start with the Arabic ones you want to show.
4. Same tab → "Seed from approved explainers" → review and approve the interest rows.
5. Reload `/robot`: the idle screen now cycles through the approved programs. Say "احكيلي عن تخصص هندسة الأنظمة الذكية", then try "مش عارف شو أدرس". Scan the Biomedical Engineering QR code with your phone once.
6. Review the faculty overview page in the Review queue (question 1).
7. **Collect real photos or short videos** for each program, with permission, and the official study-plan links of the five programs without one (questions 7 and 8).
8. Decide about the TTS model shutdown on 2027-01-06 (question 6).

## What is done
| Step | Result | Commit |
|---|---|---|
| S0 baseline | 130 tests, evaluation 48/49, latency baseline 4.2 s | `00c1530` |
| S1 faster voice | TTS speed 1.12 (measured to work), lively-pace instructions, cache key with speed, first piece of 6 words: 4.2 s → 3.1 s in the same hour (STT not included); VAD silence 0.7 s; STT model kept; streaming TTS built, measured, left off | `7297098` |
| S2 explainers | drafting from approved sources, number and course checks, review tab with preview, cached speech, 8 scene types, 6 line drawings, QR, protocol; 12 drafts pending | `c57cd1f` |
| S3 interest matching | two questions by voice or buttons, approved mapping only, explainer offered at the end | `c57cd1f`, `aa75483` |
| S4 attract mode | idle screen cycles approved title and stat scenes, program buttons, stops at once | `c57cd1f`, `07f3ba1` |
| S5 demo pack | DEMO.md, HOW_TO_START, PLAN, DECISIONS; evaluation 49/49; 165 tests | the commit that contains this line (see `git log`) |

## What is not verified, said plainly
- **Nobody has heard the new voice pace**, and no microphone conversation was run in this sprint. Everything conversational was tested with fake providers; the real providers were used only by `evaluate`, `latency`, `bench`, drafting and phrase generation.
- **No explainer has been played with the real voice**, because none is approved. Scenes, buttons and timing were checked in a headless browser with a test tone and sample data in a scratch database.
- **The 1 s latency gain is one run against one run**; the provider drifted by 0.7 s within the same day. The streaming result (no gain) is also one run.
- The 3.9–4.8 s "from end of speech" figure is an estimate, as in sprint 1.
- The QR code was not scanned with a phone. Touch was simulated with clicks.
- `ruff` reports 282 warnings (272 long lines; 144 warnings before the sprint, 134 of them long lines). The other 10 are the same as before.
- 49/49 on the golden set includes one question that passes or fails depending on the run (question 1).

## Questions for Mohannd
See the list at the top of this file (13 items). The first four decide what you can show next week.
