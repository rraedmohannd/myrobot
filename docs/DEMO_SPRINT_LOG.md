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
- Commit: `db30197`. Elapsed: about 20 min. Not pushed (no remote, see question 1).

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
