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
The same page has tabs for analytics (Overview, Knowledge gaps, Conversations), a test box (Ask the robot), the golden-set runs (Evaluation), Knowledge coverage, the program Explainers (with the interest mapping) and the Media library.

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

Camera prototype ("who is speaking", off by default): http://localhost:8000/robot?camera=1 or press `C` on the robot page. Add `&face_start=1` to start a conversation when a face looks at the camera for 1.5 s. Not validated with people yet; see docs/SPEAKERS.md for the manual check. Logic test (on the laptop, needs Node): `node backend/tests/js/speaker_logic_test.mjs`.

Several visitors: each utterance is labelled "متحدث 1/2/3" in the /voice log (anonymous, forgotten when the conversation ends). See docs/SPEAKERS.md.

## Program explainers, "help me choose" and the idle screen (sprint 2)
The robot can **present a program** for 60–90 seconds (speech + animated scenes on the screen), suggest programs from the visitor's interests, and show approved scenes while nobody is talking. All of it uses **approved content only**, and you are the one who approves.
```powershell
docker compose exec backend python -m app.ingestion.cli draft-explainers                      # drafts for every program, ar + en (1 model call each) -> PENDING
docker compose exec backend python -m app.ingestion.cli draft-explainers --program graphic-design --lang ar   # redo one draft
docker compose exec backend python -m app.ingestion.cli prepare-explainers                    # speech of the APPROVED explainers -> cache (also done when you approve)
docker compose exec backend python -m app.ingestion.cli seed-interests                        # PENDING interest -> program rows, for programs with an approved explainer
```
1. http://localhost:8000/admin → **Explainers**: open a draft, read every segment against the official documents, edit the text or the scene, press "▶ Preview scene", then **Approve**. Approval is refused while a number or a course name is not in the program's approved sources (the reason is shown in red).
2. Same tab → **Interests → programs**: "Seed from approved explainers", then edit and approve the rows.
3. http://localhost:8000/robot: the idle screen now cycles through the approved programs ("قرّب واحكي معي 👋") and shows a button per program and "ساعدني أختار". Say "احكيلي عن تخصص هندسة الأنظمة الذكية" or tap a button. During a presentation: **التالي** skips a segment, **إيقاف** stops.

Screens without the AI providers (free, for checking layout only; the "voice" is a test tone and answers are fake):
```powershell
docker compose run --rm -p 8001:8000 backend uvicorn tests.preview_app:app --host 0.0.0.0 --port 8000   # then http://localhost:8001/robot
```

## Voice pace and waiting time (sprint 2)
Settings in `.env` (defaults in `.env.example`): `TTS_SPEED=1.12` (the voice itself is a little faster), `SPEECH_RATE=1.0` (extra playback rate in the browser; leave it), `TTS_STREAMING=false` (experimental: answers sent as audio chunks while they are generated; no reliable gain measured yet), `TTS_FIRST_PIECE_WORDS=6`, `VAD_SILENCE_MS=700` (use 900 if the robot cuts you off mid-sentence).
```powershell
docker compose exec backend python -m app.ingestion.cli prepare-phrases     # after changing the voice, the speed or TTS_INSTRUCTIONS
docker compose exec backend python -m app.ingestion.cli latency --dialogue  # timings through the conversation manager (costs a little)
docker compose exec backend python -m app.ingestion.cli bench --stt-only    # compare speech-to-text models
```

## Sprint 3: what is new and how to use it
After pulling this branch: `docker compose build backend` (new packages), `docker compose up -d`, `docker compose exec backend alembic upgrade head` (migrations 0009–0011).

**Admin login.** Create your user once, then log in on http://localhost:8000/admin (the token still works under "token (scripts)"):
```powershell
docker compose exec backend python -m app.ingestion.cli create-admin yourname     # asks for a password (10+ characters)
```

**FAQs and pitches** (answered at once, no language model). Drafts are pending until you approve them in admin → **FAQs & pitches**:
```powershell
docker compose exec backend python -m app.ingestion.cli draft-faqs                      # every program + handbook rules (1 model call each)
docker compose exec backend python -m app.ingestion.cli draft-faqs --program graphic-design
```
A program FAQ must name its program in the question; the page refuses to approve it otherwise. Admin → **Ask the robot** shows which kind of answer a question gets (FAQ, pitch, place, or search + model).

**Places and labs.** Admin → **Places & labs**: type each place (name, building, floor, directions as the robot should say them, other spoken names), then Approve. The robot answers "وين …؟" only from approved entries.

**Robot (live).** Admin → **Robot (live)** shows the connected robot and sends requests (the robot may refuse). Without the real robot:
```powershell
docker compose exec backend python -m app.ingestion.cli simulate-robot --seconds 120    # a SIMULATED robot; its runs are stored as simulated
```

**Protocol package** (`shared/`). After changing a message in `shared/scr_protocol/messages.py`:
```powershell
docker compose exec backend python -m scr_protocol.generate      # JSON Schema files + the tables of docs/PROTOCOL.md
```

**Local network, firewall, token:** `docs/DEPLOY.md`. **Privacy:** `docs/PRIVACY.md`.
```powershell
docker compose exec backend python -m app.ingestion.cli purge-conversations --dry-run   # conversations older than 30 days
powershell -ExecutionPolicy Bypass -File scripts\backup-db.ps1                          # database backup -> backups\
```

**Figures for the report:**
```powershell
docker compose exec backend python -m app.ingestion.cli export-report                   # CSVs and PNG charts -> reports\ (git-ignored)
```
Simulated runs are titled "SIMULATED — not a physical result". Charts are in English (the chart font cannot shape Arabic).

**Soak test** (30 minutes, SIMULATED robot, fake AI providers, a scratch copy of the database; it never touches your data and costs nothing):
```powershell
# 1. a scratch copy of the database and a preview backend with fake providers
docker compose exec db sh -c "dropdb -U $POSTGRES_USER --if-exists smart_campus_preview && createdb -U $POSTGRES_USER smart_campus_preview && pg_dump -U $POSTGRES_USER $POSTGRES_DB | psql -q -U $POSTGRES_USER -d smart_campus_preview"
docker compose run --rm -d --name scr-preview -e ADMIN_TOKEN=preview-token backend sh -c 'DATABASE_URL=$(echo $DATABASE_URL | sed "s#/[^/]*$#/smart_campus_preview#") exec uvicorn tests.preview_app:app --host 0.0.0.0 --port 8000'
# 2. the soak run (use --minutes 1 for a quick check)
docker compose exec -e SOAK_ADMIN_TOKEN=preview-token backend python -m app.ingestion.cli soak --minutes 30 --url ws://scr-preview:8000/ws/robot --admin-url http://scr-preview:8000
# 3. clean up
docker rm -f scr-preview
docker compose exec db sh -c "dropdb -U $POSTGRES_USER smart_campus_preview"
```
Run step 1 in Git Bash or adapt the quotes for PowerShell. The result is saved in `backend\data\soak\`.

## Robot runs (trajectory view) — simulation only for now
```powershell
docker compose exec backend python -m app.ingestion.cli simulate-run             # direct return
docker compose exec backend python -m app.ingestion.cli simulate-run --seed 16   # blocked path -> breadcrumbs
```
These runs are **SIMULATED** (stored with `is_simulated = true`, shown with a banner). They are for developing the admin view and must never be reported as tests of the robot. See http://localhost:8000/admin → Robot runs.

## Demo for the supervisor
Follow `docs/DEMO.md` (10-minute script in Arabic: `/robot`, then `/voice`, then `/admin`, with a fallback plan).
After pulling new code, always run `docker compose exec backend alembic upgrade head` (the demo sprint added migrations 0006 and 0007; sprint 2 added 0008; sprint 3 added 0009–0011 and needs `docker compose build backend` once).
New optional settings (see `.env.example`): `LLM_STREAMING`, `LLM_REASONING_EFFORT=low`, `LOCAL_TIMEZONE`.

## Save a version (after each phase passes its tests)
```powershell
git add .
git commit -m "B2-crawler-review-queue"
```

## When something fails
Copy the error text, paste it into Claude Code, and ask it to fix it. Then send the result here for review before moving to the next phase.
