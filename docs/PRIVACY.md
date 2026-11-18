# Privacy and data (for the report)

The robot talks to visitors in a public corridor. It is built to keep as little as possible about them. This page states what the software does today; each point names the code or test that enforces it.

## What is never stored
| Data | What happens to it | Where this is enforced |
|---|---|---|
| **Visitor audio** | Sent to the speech-to-text provider for transcription, kept in memory for that one utterance, then dropped. No audio file or audio column exists. | `services/dialogue.py` (`end_of_speech`); test `test_no_audio_or_face_columns_exist` |
| **Face images, video frames** | Processed inside the browser (later: on the Raspberry Pi). Only a track number ("the same face as before") and an "overlap" flag are sent. No image or landmark leaves the device. | `static/speaker-camera.js`; protocol message `audio.end` |
| **Voiceprints** | A voice embedding is computed per utterance to tell speakers apart. It lives in memory for the current conversation and is deleted when the conversation ends. | `services/speakers.py`; `DialogueManager.close_session` |
| **Identity** | Speakers are "Speaker 1, 2, 3" inside one conversation. Nobody is recognised across conversations; no name, phone or student number is asked for. | `turns.speaker_label` holds only the number |

## What is stored
| Data | Table | Why | How long |
|---|---|---|---|
| The **text** of each question and of the robot's answer, the language, the intent | `turns` | to improve the knowledge base ("knowledge gaps") and for the evaluation in the report | `CONVERSATION_RETENTION_DAYS`, default **30 days** |
| Timings per stage, which sources were used, speaker number and its confidence | `turns` | latency and quality measurements | same |
| Start, end and end reason of a conversation | `sessions` | counting conversations | same |
| Robot runs: pose samples, events, command requests, return trials | `robot_runs`, `pose_samples`, `events`, `return_trials` | trajectory plots and return-to-start results (no personal data) | kept |
| The robot's **own** speech (fixed phrases, approved explainers and FAQs) | files in `backend/data/tts_cache` | so the robot answers at once and works with weak internet (this is the robot's voice, never a visitor's) | until the voice settings change |
| Admin users | `admin_users` (argon2id password hash), `admin_sessions` (SHA-256 of the session token) | login to the admin page | until removed |

A visitor may say something personal in a question (a name, a grade). That text is stored like any other question, which is why conversations are deleted after the retention period and why the database and its backups must stay private.

## Deleting old conversations
```powershell
docker compose exec backend python -m app.ingestion.cli purge-conversations --dry-run   # only counts
docker compose exec backend python -m app.ingestion.cli purge-conversations             # deletes conversations older than 30 days
docker compose exec backend python -m app.ingestion.cli purge-conversations --days 7
```
- The limit is `CONVERSATION_RETENTION_DAYS` in `.env` (default 30).
- The purge is a command you run (for example once a week, or with Windows Task Scheduler). It does not run by itself, so nothing disappears without you knowing. The admin page → **System** shows how many conversations are older than the limit.
- Numbers already exported for the report (`export-report`) are aggregates and contain no question text, except the knowledge-gap list in the admin page, which reads the live table.

## What leaves the laptop
- **To the AI provider (OpenAI today):** the audio of an utterance (speech-to-text), the question text with the retrieved passages (language model), and the answer text (text-to-speech). No name or identifier is added by the robot. The provider's own data policy applies to these requests; name the provider and its policy in the report.
- **Nothing else.** Fonts, scripts, the face model and the QR generator are local files; the pages make no request to other sites.

## Who can see the stored data
- The admin page needs a login (`create-admin`) or the `ADMIN_TOKEN` for scripts. Sessions end after `ADMIN_SESSION_HOURS` (12).
- The database listens only on the laptop (port 5433, not opened in the firewall).
- Backups (`backups/`) and the database contain question text: keep them on the laptop or on private storage.

## Limits to state honestly
- The robot does not ask for consent before a conversation; a notice near the robot ("this robot processes your voice to answer you; text of the conversation is kept for 30 days for a student project") is recommended and should be agreed with the supervisor.
- The protection is that of a student project on a laptop: one shared machine, HTTP on the local network, no audit log of admin actions.
- The camera part has not been validated with people yet (docs/SPEAKERS.md).

## Server mode (sprint 6: the backend on a rented server)
From sprint 6 the backend can also run on a rented Linux server in **Germany (EU)** (`APP_ENV=production`, `docs/DEPLOY_SERVER.md`). The rules above stay the same; this is what changes.

**Where the data lives.** The database, the media files and the robot's speech cache live in Docker volumes on that server. The robot (Pi) and visitors' browsers reach it over HTTPS.

**Who has access.**
- **Mohannd** (admin login on the admin page; server access as agreed with the server admin).
- **The server admin**, a friend of Mohannd who is **not a project member**. He has root access to the server, which means he technically **could read the database and the backups**, including the stored question text. He is asked not to, but the software cannot prevent it. This is stated honestly here and should be mentioned in the report.
- Nobody else: the database port is not published, the admin page needs a login (the shared `X-Admin-Token` is refused unless `ADMIN_TOKEN_SCRIPTS=true`), repeated wrong passwords lock the username and the address for `LOGIN_LOCKOUT_MIN` (15) minutes, and admin cookies are `Secure`, `HttpOnly`, `SameSite=Strict`.
- Visitors may only use the robot pages after typing the **access code** (`SITE_ACCESS_CODE`); they cannot see stored conversations. `/health` shows only "ok" and the version to the public.

**What is stored.** The same as on the laptop: the **text** of questions and answers, timings and sources (`sessions`, `turns`), plus a count of paid AI calls per day (`paid_call_days`, no personal data). **No audio, no face images, no voiceprints** are stored, on the server either. The visitor cookie holds only a signed expiry time, nothing about the visitor. Logs do not contain the robot token or the access code (they are redacted).

**Retention.** Conversations are kept for `CONVERSATION_RETENTION_DAYS` (default 30). On the server they are deleted with the same command, run inside the backend container, for example:
```bash
docker compose -p smart-campus-prod -f docker-compose.prod.yml --env-file deploy/server/.env exec backend python -m app.ingestion.cli purge-conversations --dry-run
docker compose -p smart-campus-prod -f docker-compose.prod.yml --env-file deploy/server/.env exec backend python -m app.ingestion.cli purge-conversations
```
(the scripts in `deploy/server/` wrap the compose command).

**The laptop's conversations are not copied.** The data bundle that moves the knowledge, approvals and media to the server excludes the conversation tables by default; visitors' questions asked on the laptop stay on the laptop.

**Backups on the server.** A daily backup (database dump plus media and speech cache) is kept on the server for **14 days** (`BACKUP_KEEP_DAYS`) and then deleted. Backups contain question text: they are as private as the database.

**What leaves the server.** Requests to the AI provider (OpenAI) now come **from the server** instead of the laptop: the utterance audio for speech-to-text, the question with the retrieved passages, and the answer text for text-to-speech, exactly as described in "What leaves the laptop". The server uses a separate OpenAI key with a monthly spending limit, and the backend refuses paid calls above `DAILY_PAID_CALL_LIMIT` per day.
