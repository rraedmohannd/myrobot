# Sprint 2 — faster voice and program explainers (task file for Claude Code, Fable 5.2)

Prepared on 2026-11-13, after demo sprint 1 (see `docs/DEMO_SPRINT_LOG.md`). The student tested `/robot` and approved all sources in `/admin`.

**What he asked for:**
1. The robot's voice should be **faster**. Its speaking pace feels slow, and he wants less waiting too.
2. Since this is a **recruitment** robot, it should explain each program **in depth**. While it talks, the screen should show something alive (writing, drawing, images, video).
3. There are **no real photos or videos** for most programs yet, so the visuals must work without them.

**Rules.** Same working rules as `docs/DEMO_SPRINT.md` (git, tests, secrets, facts, simulation, privacy, UI, progress log) and `CLAUDE.md`, with these changes:
- **Branch:** work on `sprint-2`, created from `demo-sprint`. Push it if a remote exists; never push to `main`.
- **Log:** `docs/SPRINT_2_LOG.md`, with the same format and its own "Questions for Mohannd" section.
- **OpenAI budget for this run:**
  - `evaluate`: at most 2 runs.
  - `latency` and `bench`: at most 4 runs together.
  - Explainer drafting: one LLM call per program and language, 12 calls at most.
  - TTS pre-generation for approved explainers: unlimited; it is cached and cheap.
- **Stop** after 4 hours, as before.

---

## S0 — Baseline (about 10 min)
1. Create `sprint-2` from `demo-sprint`.
2. Run `docker compose up -d`, then `alembic upgrade head`, then `pytest -q`.
3. Run **one** `evaluate`. The sources are now approved, so this is the new baseline (sprint 1 got 37/49 with sources still pending).
4. Record the results in the log, list any question that still fails, and say why it fails.

## S1 — A faster voice
**Speaking pace (the student's main complaint)**
- Check in the official OpenAI docs whether the `speed` parameter works with the configured TTS model (`TTS_MODEL`). Use it if it does.
- Otherwise, steer the pace through `TTS_INSTRUCTIONS`: a lively, slightly fast, natural Jordanian pace with short pauses. Also add a client-side playback rate:
  - setting `SPEECH_RATE`, default `1.12`;
  - sent in `hello.ack`;
  - applied in `robot-client.js` with `preservesPitch = true`, so `/robot`, `/voice` and later the Pi all use it.
- **The TTS cache key must include** the model, voice, instructions and speed. Otherwise old slow audio is replayed. The fixed phrases must be regenerated.

**Waiting time** (sprint 1: 3.5 s from question text to first answer audio; TTS is now the largest stage). Measure with `latency` before and after. Keep only changes that help, and record the numbers honestly in DECISIONS:
- TTS: a shorter first piece (about 6 words). Generate the next pieces in parallel while the first plays. Try streaming TTS output (for example PCM played through WebAudio) and keep it only if it saves at least 300 ms to first audio.
- End of speech: make the VAD silence before `audio.end` configurable (today about 0.9 s). Try 0.7 s. Keep it only if short pauses mid-sentence do not cut people off; note this in the log for the student to check with his own voice.
- STT: compare `gpt-transcribe` with `gpt-4o-transcribe` on the same recordings, using the `bench --stt` code. Note that gpt-4o-transcribe is deprecated on 2027-02-26, close to the defense, so switch only if it clearly helps, and write the risk in DECISIONS.

**Tests:** cache-key changes, settings in `hello.ack`, and VAD config passed to the client.

## S2 — Program explainers: "the robot presents the program" (main feature)
The goal is a 60–90 second presentation per program. It is spoken in segments, and each segment has an animated scene on the screen. It is **pre-approved and pre-generated**, so it starts instantly (no LLM wait).

**Data**
- Add a migration and models: `explainers` (program, lang, status pending/approved/rejected, created_at, updated_at) and `explainer_segments` (order, spoken text, scene JSON, `audio_cache_key`).
- Follow the `pitches` rules: the content is promotional in tone but factual, and **every number must appear in the program's approved sources**. Reuse the invented-numbers check.

**Drafting.** A CLI command `draft-explainers [--program slug] [--lang ar|en]`:
- builds a draft from the program's **approved** chunks (department page, study plan, handbook) with one LLM call;
- uses 5–7 segments: what the program is, what you study (real courses from the plan), labs and projects (only if the sources mention them), skills, and how to learn more;
- writes Arabic in Jordanian dialect and English in a friendly tone;
- **does not invent** careers, salaries, rankings or comparisons with other universities;
- stores drafts as **pending**. The student reviews, edits and approves them in a new admin tab "Explainers", which has a per-segment preview.

**Audio.** Approved segments get their TTS generated and cached (CLI `prepare-explainers`, plus automatically on approval), so playback is instant.

**Dialogue**
- **Triggers:**
  - questions such as "احكيلي عن تخصص X", "شو هو X", "ليش أدرس X", "tell me about X";
  - a program button on the screen. The Waveshare 7" (C) is a touch screen, so add tappable program buttons on `/robot`.
- **During playback:**
  - A stop/skip button on screen; the voice can't interrupt, because the microphone is deaf while the robot talks.
  - After segment 3, the robot asks "بدك أكمل ولا عندك سؤال؟", then continues or listens.
- **Fallback:** no approved explainer → the normal RAG answer.

**Protocol**
- Add `explainer.segment {index, total, scene}`, sent when a segment's audio starts, so scenes stay in sync with speech.
- Update `docs/PROTOCOL.md`.

**Scenes** (rendered by `/robot`, everything local, no internet):

| Scene | What it shows |
|---|---|
| `title` | Program name, plus an **original line drawing** of the program that draws itself while the robot speaks (SVG stroke animation, "the robot is drawing") |
| `keypoints` | 2–4 short phrases written word by word in time with the speech ("the robot is writing") |
| `stat` | A big number that counts up, for example 162 credit hours or 5 years. Only numbers present in the approved sources |
| `timeline` | Study years 1 → N, with real courses from the plan appearing on each year |
| `courses` | Chips of the most interesting real courses |
| `diagram` | A simple animated diagram (for example sensors → processing → action) |
| `media` | An approved photo or video of the program if one exists; otherwise this falls back to the drawing |
| `qr` | A QR code, generated locally, to the program's official study-plan URL from its source record ("صوّر الكود") |

- **Line drawings.** Make one original, simple line drawing per program as SVG paths:
  - Intelligent Systems Engineering: robot head and neural network;
  - Renewable Energy Engineering: solar panel, sun and wind turbine;
  - Architectural Engineering: building facade;
  - Interior Design: room plan with furniture;
  - Graphic Design: pen tool and bezier curve;
  - Biomedical Engineering: ECG line and heart.

  They must be clearly illustrations: no logos, no real people, no copied artwork.
- **Visual style:** Arabic first and right-to-left, large text, the robot's colours. Scenes must be readable on 1024×600.

**Tests:** drafting stores pending only, an invented number blocks approval, approved-only playback, segment order and messages, QR uses the source URL, and the fallback to RAG when there is no explainer.

## S3 — "What do you like?" — interest matching (recruitment)
- **When the visitor is unsure** ("مش عارف شو أدرس", "ساعدني أختار"), or taps "ساعدني أختار":
  - the robot asks **2–3 short questions**, by voice or with on-screen buttons (computers and AI, energy and environment, buildings and drawing, art and design, medicine and biology, maths and physics);
  - it then suggests 1–2 of the faculty's programs and offers their explainer.
- **The interest → program mapping** is curated, promotional data, stored and approved like pitches (seeded from the approved explainers, editable in the admin). The robot never claims a program "guarantees" anything.
- **Tests:** the flow, approved mapping only, and the explainer offered at the end.

## S4 — Attract mode (idle screen)
- When nobody is engaged, `/robot` cycles silently through the programs' approved `title` and `stat` scenes and their line drawings, with "قرّب واحكي معي 👋".
- It stops instantly on `session.start`.
- It must never show unapproved content.

## S5 — Demo pack update
- Update `docs/DEMO.md` with:
  - a program explainer (show the drawing and the writing);
  - the interest-matching flow;
  - the attract mode;
  - the new latency numbers.
- Update `HOW_TO_START.md`, `PLAN.md` and `DECISIONS.md`.
- Run the full `pytest`, then one `evaluate`.
- Commit, push (if a remote exists), and write the log summary.

## Done when
- S0–S5 are complete and logged with commit hashes.
- All tests pass, and `docs/DEMO.md` is updated.
- The log lists what the student must do: approve the explainer drafts in the admin, check the voice pace with his ears, and collect real photos or videos.
