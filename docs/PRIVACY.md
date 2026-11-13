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
