# Demo sprint log

Task file: `docs/DEMO_SPRINT.md`. Branch: `demo-sprint` (created from `master` at d20be5d).

**Start time: Fri 2026-11-13 16:31 (laptop clock, `date`).** Time budget: 4 hours → stop by about 20:31.

## Questions for Mohannd
1. **No git remote is configured in this folder** (`git remote -v` is empty, and the `gh` tool is not installed), so `git push -u origin demo-sprint` cannot work. I did not create or guess a remote. All work is committed locally on `demo-sprint`. To push it yourself:
   `git remote add origin <your GitHub repo URL>` then `git push -u origin demo-sprint`.
   (DECISIONS says the repo was pushed to GitHub on 2026-11-06; that was probably done from another copy of the folder.)
2. **The Student Handbook (source 346, 19 chunks) and the Biomedical Engineering plan (source 345, 13 chunks) are still `pending`.** The robot cannot use them until you approve them in `/admin`. I did not approve anything. Until then, handbook and Biomedical questions get the "I don't have this information" answer, and those golden questions fail in `evaluate`.
3. The local branch is called `master`, while the task file says `main`. Nothing was merged or pushed to either.

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
  - WebSocket drop or 25 s without any message → "service unavailable" face and message, reconnect every 2 s.
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
