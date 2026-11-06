# Backend & Database Specification

Status: approved direction, 2026-11-02. Implementation not started.

## 1. Context
- Robot: Raspberry Pi 5 on a Rover 5 chassis, 7" HDMI touch screen, USB webcam on a pan servo, USB mic, speaker, ESP32 low-level controller.
- Backend runs on a **laptop at the demo site**, same Wi-Fi/LAN as the robot. No sleeping cloud host.
- Cloud AI: **OpenAI for STT, LLM and TTS** (one provider, one key). Anthropic has no STT/TTS, so it is kept only as an optional alternative LLM behind the adapter. Target voice: **Jordanian Arabic** — OpenAI TTS dialect quality must be verified by a listening test in B4; OpenAI's speech-to-speech option (Realtime / GPT-Live) is evaluated as a latency alternative. Fallback voice providers if the Jordanian accent is poor: Azure Speech (verify ar-JO voices), ElevenLabs.
- Knowledge scope: **MEU Faculty of Engineering only** (all its programs, labs, facilities, admission info published for engineering).

## 2. Architecture
```
Robot (Pi 5) ──WebSocket /ws/robot──► FastAPI backend ──► OpenAI (STT · LLM · TTS)
      │ display + audio events            ▼
      └──────────────────────────── PostgreSQL 16 + pgvector
                                          ▲
                         Admin panel (React) ── REST /api/admin/*
```

### Stack
| Part | Choice |
|---|---|
| API | Python 3.12, FastAPI, uvicorn, pydantic v2 |
| DB | PostgreSQL 16 + pgvector, async SQLAlchemy 2.x, Alembic |
| Search | Hybrid: Postgres full-text (`tsvector`, Arabic+English configs) + pgvector cosine; merged with reciprocal rank fusion |
| Embeddings | Multilingual model behind an adapter (local `bge-m3`/`multilingual-e5` via sentence-transformers, or a hosted API) — pick in B3 by Arabic retrieval quality |
| Admin | React + TypeScript + Vite, simple component library |
| Run | Docker Compose: `db`, `backend`, `admin` |
| Tests | pytest, pytest-asyncio, httpx; golden-question evaluation set |

### Backend module layout
```
backend/app/
  main.py
  core/        config (pydantic-settings), logging, security
  db/          session, base, models/, migrations (alembic)
  api/         rest/ (chat, admin, robot telemetry, health) ; ws/ (robot socket)
  services/
    dialogue/  conversation state machine, intents, closing logic
    rag/       retrieval (FAQ → full-text → vector), fusion, citation packing
    llm/       LLMProvider interface + OpenAIProvider (AnthropicProvider optional)
    stt/       STTProvider interface + providers
    tts/       TTSProvider interface + providers, audio caching
    display/   builds screen payloads (subtitles, images, cards)
  ingestion/   crawler, cleaner, chunker, embedder, review workflow
  telemetry/   robot runs, pose samples, events, trajectory metrics
tests/
```

## 3. Database schema (v1)

### Knowledge
- `faculties(id, slug, name_ar, name_en, description_ar, description_en)`
- `programs(id, faculty_id, slug, name_ar, name_en, degree, duration_years, credit_hours, study_plan_url, summary_ar, summary_en, review_status, source_id, academic_year, updated_at)`
- `labs(id, program_id NULL, name_ar, name_en, description_ar, description_en, location_hint, media_id NULL, source_id)`
- `sources(id, url UNIQUE, title, lang, fetched_at, content_hash, http_status, review_status[pending|approved|rejected|stale], notes)`
- `chunks(id, source_id, program_id NULL, lang, category, text, tsv tsvector, embedding vector(N), academic_year NULL, review_status, created_at)`
- `faqs(id, program_id NULL, question_ar, question_en, answer_ar, answer_en, source_id NULL, priority, review_status)` — curated, checked before RAG.
- `pitches(id, program_id NULL, topic, text_ar, text_en, is_promotional BOOLEAN DEFAULT true, review_status)` — engineering talking points and comparisons (e.g. ISE vs Software Engineering).
- `media(id, kind[image|video], path, alt_ar, alt_en, source_id NULL, program_id NULL)`

Rule: answers may only use rows with `review_status = approved`.

### Conversations
- `sessions(id, started_at, ended_at, lang_detected, end_reason[user_done|timeout|lost_face|error], robot_run_id NULL)`
- `turns(id, session_id, idx, user_text, user_lang, intent, answer_text, used_chunk_ids int[], used_faq_id, used_pitch_ids int[], stt_ms, retrieval_ms, llm_ms, tts_ms, total_ms, error NULL, created_at)`

### Robot telemetry
- `robot_runs(id, started_at, ended_at, notes, odom_config jsonb)`
- `pose_samples(id, run_id, t_ms, x_m, y_m, theta_rad, v_mps, omega_rps, enc jsonb, imu jsonb, state)` — time series, index on (run_id, t_ms).
- `events(id, run_id, t_ms, kind[obstacle|return_start|return_done|return_failed|estop|comm_loss|...], data jsonb)`
- `return_trials(id, run_id, odom_end_x, odom_end_y, measured_err_m, measured_heading_err_deg, success, notes)` — physical error measured with tape, entered via admin.

### Admin
- `admin_users(id, username, password_hash, created_at)` (single user is fine).

## 4. Conversation pipeline
1. **Robot side**: VAD (e.g. Silero) detects start/end of speech → streams audio over WebSocket. No fixed 4-second recording.
2. **STT**: transcribe + detect language (ar / en).
3. **Dialogue manager** (`services/dialogue`) states: `GREETING → LISTENING → ANSWERING → FOLLOW_UP ("عندك سؤال ثاني؟") → CLOSING`.
   - Intents: question, thanks/done, greeting, repeat, unclear.
   - "done/thanks" or no reply within `FOLLOW_UP_TIMEOUT` → polite goodbye, session closed.
   - Keeps last N turns as short context.
4. **Retrieval**: FAQ exact/semantic match → hybrid search over approved chunks → attach relevant pitches for comparison/"why engineering" intents.
5. **LLM (OpenAI)**: system prompt = Jordanian, warm, proud-of-Engineering persona + strict grounding rules (facts only from provided context; say "I don't know, ask Admission" when missing; never invent numbers). Short spoken answer + optional display card.
6. **TTS**: synthesize answer; cache frequent phrases (greeting, follow-up, goodbye).
7. **Display**: send `display.update` with subtitles (Arabic RTL), program card, image ids — in sync with audio playback.

Latency target (to be measured, not promised): first audio ≤ ~4 s after user stops speaking. Use streaming LLM + sentence-level TTS if needed.

## 5. Robot ↔ backend protocol (`shared/`)
WebSocket `/ws/robot`, JSON messages with `type`, `id`, `ts`:
- robot → backend: `hello{robot_id, fw_version}`, `session.start`, `audio.chunk{seq, pcm16_b64}`, `audio.end`, `session.end{reason}`, `telemetry.pose{...}` (batched), `telemetry.event{...}`, `heartbeat`
- backend → robot: `transcript{text, lang}`, `answer.text{text}`, `answer.audio{seq, mp3_b64|url}`, `display.update{mode, subtitle, card, images[]}`, `dialogue.state{state}`, `error{code, message}`, `heartbeat`
Versioned schema (`protocol_version`). Robot must handle backend loss gracefully (status face + "service unavailable" message).

## 6. Ingestion (MEU Faculty of Engineering)
- Seed URLs: faculty and program pages on `meu.edu.jo` (Arabic + English), including Intelligent Systems Engineering.
- Crawler respects robots.txt, rate-limits, stores raw HTML + cleaned text, `content_hash` for change detection.
- Cleaner → chunker (~300–500 tokens, heading-aware) → embedder.
- All new/changed sources land as `pending` in the admin **review queue**; only approved content is used.
- Re-crawl command marks changed pages `stale` for re-review.

## 7. Admin panel
- Review queue (approve/reject/edit chunks, link to program).
- Programs, labs, FAQs, pitches, media CRUD.
- "Ask the robot" test box (text) showing retrieved context and sources.
- Conversation log with per-stage latency.
- Robot runs: trajectory plot (x,y), events, return-trial entry and error stats.

## 8. Phases and acceptance
| Phase | Scope | Done when |
|---|---|---|
| B1 | Repo skeleton, Docker Compose, Postgres+pgvector, Alembic, config, `/health`, CI-style test run | `docker compose up` works; migrations apply; tests pass |
| B2 | Crawler, cleaner, chunker, review queue (API + minimal admin page) | Engineering faculty content stored and reviewable; re-crawl detects changes |
| B3 | Embeddings, hybrid retrieval, FAQ/pitch priority, `/api/chat` text endpoint, golden set (≥30 Q in ar+en incl. ISE vs SE) | ≥90% of golden questions answered correctly with sources; zero invented fees/dates in the set |
| B4 | Dialogue manager, `/ws/robot`, STT/TTS adapters, provider evaluation (Jordanian voice), laptop mic client for testing | Full voice conversation from a laptop mic: greet → Q&A → "عندك سؤال ثاني؟" → goodbye |
| B5 | Admin panel complete, conversation analytics, telemetry ingest + trajectory view, return-trial stats | Ready for demo and report figures |

## 9. Reuse from the previous robot code (June 2026 SD card)
Worth porting into `robot/`: YuNet face tracking state machine, geometric head-pose estimator, servo smoothing logic. Replace: Render backend, fixed-length recording, blocking greeting in the main loop, per-request health checks, hard-coded ALSA device indices.
