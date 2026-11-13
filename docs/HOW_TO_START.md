# How to start — Claude Code + the project (Windows)

Project folder: `C:\Users\hanan\Documents\smart-campus-robot`
(not the OneDrive copy)

## One-time setup
1. Install **Git for Windows** (installer in Downloads) — lets Claude Code use Git Bash.
2. Install **Docker Desktop** (installer in Downloads), restart, open it once.
3. Install **Claude Code**: open PowerShell (Start → "PowerShell") and run:
   ```powershell
   irm https://claude.ai/install.ps1 | iex
   ```
   Close PowerShell, open a new one, check: `claude --version`
   (If "not recognized": see https://code.claude.com/docs/en/troubleshoot-install)
4. First run of `claude` opens the browser to log in (needs a Pro/Max plan or Console account).

## Every time — open Claude Code on the project
1. Open **Docker Desktop** and wait until it says "Engine running".
2. Open PowerShell (or VS Code → Terminal → New Terminal) and run:
   ```powershell
   cd C:\Users\hanan\Documents\smart-campus-robot
   claude
   ```
3. Type your request. To exit: `/exit` (or Ctrl+C twice).
   To continue the last conversation next time: `claude --continue`

## Prompt for phase B1 (paste into Claude Code)
> اقرأ CLAUDE.md وكل ملفات docs، وابدأ بالمرحلة B1 من BACKEND_SPEC.md: هيكل المشروع، Docker Compose فيه PostgreSQL + pgvector، Alembic، الإعدادات، و/health، مع اختبارات. لا تبلّش بأي مرحلة بعدها قبل ما أراجع.

## Run the project (after B1 exists)
Run in the project folder (PowerShell):
```powershell
copy .env.example .env                              # first time only
docker compose up -d                                # start backend + database
docker compose exec backend alembic upgrade head    # apply DB migrations
docker compose exec backend pytest                  # run tests
```
Check: open http://localhost:8000/health in the browser (port per BACKEND_SPEC / compose file).
Stop: `docker compose down`

B1 is done when: compose starts, migrations apply, tests pass.
No OpenAI key is needed until B4.

## Collect and review engineering content (B2)
```powershell
docker compose exec backend python -m app.ingestion.cli crawl     # fetch MEU engineering pages
docker compose exec backend python -m app.ingestion.cli stats     # counts by review status
docker compose exec backend python -m app.ingestion.cli recrawl   # later: detect changed pages
```
Import curated knowledge (official documents turned into data/knowledge/*.json):
```powershell
docker compose exec backend python -m app.ingestion.cli import-knowledge
```
Review page: http://localhost:8000/admin (token = ADMIN_TOKEN in .env).
Only approved content will ever be used by the robot.

## Ask the robot (text) — B3
```powershell
docker compose exec backend python -m app.ingestion.cli check-openai             # key + models OK?
docker compose exec backend python -m app.ingestion.cli ask "كم ساعة هندسة الأنظمة الذكية؟"
docker compose exec backend python -m app.ingestion.cli evaluate                 # 49 golden questions
docker compose exec backend python -m app.ingestion.cli latency                  # timings: search, LLM, first speech piece (8 questions, costs a little)
```
API: POST http://localhost:8000/api/chat  {"message": "..."}  (try it at http://localhost:8000/docs)

## Talk to the robot (voice) — B4
Open http://localhost:8000/voice in Chrome or Edge → "ابدأ محادثة" → allow the microphone.
```powershell
docker compose exec backend python -m app.ingestion.cli tts-samples    # one mp3 per voice in backend\data\voice_samples
```
Pick the best Jordanian-sounding voice → set `TTS_VOICE=...` in .env → `docker compose up -d`.

Robot screen (stands in for the 7" HDMI display): http://localhost:8000/robot → "ابدأ المحادثة" or Space. `T` opens a text box if the microphone fails.
```powershell
docker compose exec backend python -m app.ingestion.cli seed-demo-media   # labelled placeholder images for the demo (development only)
```
Real images and videos: http://localhost:8000/admin → Media → upload, link to a program, approve.

Several visitors: each utterance is labelled "متحدث 1/2/3" in the /voice log (anonymous, forgotten when the conversation ends). See docs/SPEAKERS.md.

## Save a version (after each phase passes its tests)
```powershell
git add .
git commit -m "B2-crawler-review-queue"
```

## When something fails
Copy the error text, paste it into Claude Code, and ask it to fix it. Then send the result here for review before moving to the next phase.
