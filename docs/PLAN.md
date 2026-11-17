# Project plan and status

12 working weeks: week 1 starts on 2026-11-02 and week 12 ends on 2027-01-24; buffer and defense in the week after (to be confirmed with the official date).
Milestones: **M1** 6 Dec (base moves + conversation works) · **M2** 27 Dec (return-to-origin trials) · **M3** 10 Jan (full build) · **M4** 24 Jan (final version + results + report).

## Backend status
| Phase | Scope | Status |
|---|---|---|
| B1 | Skeleton, Docker, Postgres + pgvector, Alembic, /health | ✅ done 2026-11-06 |
| B2 | Crawler, review queue, curated knowledge (6 programs incl. Biomedical Engineering, official 2025/2026 plans; Student Handbook) | ✅ done 2026-11-06 |
| B3 | Hybrid retrieval, /api/chat, golden set | ✅ 35/35 on 2026-11-06. Golden set is now 49: **49/49 on 2026-11-13 (end of sprint 2; 48/49 at its start, the difference is model variance on one question that depends on a stale page)**; earlier the same day 48/49 (98 %), 0 invented numbers, after the student approved the Student Handbook and the Biomedical Engineering plan (37/49 earlier the same day, before the approval). The one failure (an English question answered by an Arabic passage) was a retrieval miss, fixed in retrieval and checked with `ask` on 4 questions; the full set was not re-run after the fix |
| B4 | Voice: STT/TTS, dialogue manager, /ws/robot, /voice page | 🟡 works end to end. Sprint 2 (2026-11-13, evening): question text → first answer audio 4.2 s → 3.1 s in the same hour (6-word first piece, TTS speed 1.12); about 3.9–4.8 s from end of speech (estimate). Earlier: Streaming answers + reasoning effort low (2026-11-13): question text → first answer audio, median 5.3 s → 3.5 s (STT not included); about 4.7–5.2 s from end of speech (estimate). Target about 4 s not reached; TTS is now the largest stage |
| B4+ | Multi-speaker Level 2 (voice + camera), docs/SPEAKERS.md | 🟡 voice part done; camera part: browser prototype built on 2026-11-13 (`/robot?camera=1`, off by default), **not yet tried with real people**; the Pi version (`visiond`: YuNet, MediaPipe, pan-servo target) was written in sprint 4 (2026-11-17) and tested with synthetic data only; validation with people is next |
| B5 | Admin complete, analytics, telemetry + trajectory view, return-trial stats | 🟡 analytics, knowledge gaps, conversations, ask-the-robot, evaluation, coverage and media tabs done (2026-11-13, static page). Telemetry messages, trajectory view and return-trial entry/stats exist and are exercised with **simulated** runs only. Still open: real telemetry from the robot, React panel and login |
| B5+ | Sprint 3, 2026-11-13 (docs/SPRINT_3.md): what the robot still needed from the backend | ✅ built and tested with fakes (docs/SPRINT_3_LOG.md): shared protocol package (version 2) with contract tests; live robot status and command requests (admin "Robot (live)" tab, exercised with a **simulated** robot only); FAQs and pitches before retrieval; places and labs; provider time limits, retries and circuit breaker with a cached offline reply; LAN guide and robot token; admin login (argon2id), retention purge, backups; report export; 30-minute simulated soak test. **Waiting on the student:** approve FAQ and pitch drafts, enter places and labs, create the admin user, firewall and token. Still open in B5: the React admin panel (the admin stays one static page), real telemetry from the robot. Next: Raspberry Pi and ESP32 code (docs/SPRINT_4.md) |
| Sprint 2 | Sprint 2, 2026-11-13 (docs/SPRINT_2.md): faster voice, program explainers with animated scenes, interest matching, attract mode | ✅ S0–S5 built and tested with fakes (docs/SPRINT_2_LOG.md). Voice: TTS speed 1.12, answers streamed as PCM chunks, VAD silence 700 ms. 12 explainer drafts (6 programs × ar/en) are **pending the student's review**; nothing plays until he approves them. Not judged by ear and not tried with a microphone yet |
| Demo | Demo sprint 2026-11-13 (docs/DEMO_SPRINT.md): `/robot` screen page, streaming, admin analytics, media, demo script | ✅ M0–M6 done (M6 = simulated robot runs); M7 camera prototype built but not validated with people; see docs/DEMO.md and docs/DEMO_SPRINT_LOG.md |

## Robot and hardware (from the Follow-Up Protocol plan)
| Weeks | Work |
|---|---|
| 1–2 | Requirements, hardware verification, voice + knowledge demo |
| 3–4 | Base control, sensors, ESP32 ↔ Pi communication |
| 5–6 | Calibration, odometry, trajectory recording |
| 7–8 | Camera, pan servo, display, **active speaker on the Pi**, integration |
| 9–10 | Return-to-start, interaction improvements |
| 11–12 | Power, brackets, assembly, stability, final tests |

## Robot software status (sprint 5, 2026-11-17) — **code complete, bring-up pending**
Every program for the Raspberry Pi and the ESP32 is written, together with the tools that take the robot from "wired" to "calibrated". Nothing has run on the real parts yet: everything is tested in simulation, in Docker, or in an emulated arm64 container (not a Pi).

| Part | Status |
|---|---|
| Architecture (docs/ROBOT_SOFTWARE.md) | ✅ decided and documented |
| Pi ↔ ESP32 serial protocol v2 (docs/SERIAL_PROTOCOL.md) | ✅ Python and C++ codecs cross-tested on 22 shared frames; runtime parameters, diagnostics, bring-up modes |
| Pin plan (docs/PIN_PLAN.md) and checker (`firmware/tools/pins_from_table.py`) | ✅ proposal on paper (option B, MCP23017 for the triggers); pins enter the firmware only through the checked table — **the student wires, fills the table and confirms option A/B** |
| ESP32 firmware (`firmware/`) | ✅ code complete; native tests, `sim-check` with fault injection, compile checks for Arduino core 2.x and 3.x; **never run on an ESP32**; every tuning value is a runtime parameter with a placeholder that does not move the robot |
| `robotd` (`robot/robotd/`) | ✅ code complete (calibration file and parameter push, gyro bias, battery rules, black box, settings page, Pi health); tested against the **simulated** ESP32 only |
| `visiond` (`robot/visiond/`) | ✅ code complete; synthetic data only; **no real face, phone or Pi yet** |
| Bring-up kit (`robotctl`, docs/BRINGUP.md) | ✅ every calibration step as a command; tested against the simulated ESP32 — **the student runs it on the real robot** |
| Pi installation (`robot/deploy/`) | ✅ install, kiosk, update, flash scripts; dependencies locked; verified in an emulated arm64 Raspberry Pi OS (trixie) container — **never run on a Pi or the screen** |
| Admin "Bring-up" tab, calibration records, power chart | ✅ backend done; nothing real feeds it yet |
| AprilTag at the start point (docs/APRILTAG.md) | 🧪 experimental, off by default, never counted in results |
| Mechanical parts (`mechanical/`) | 🧪 parametric drafts; no dimension verified |

**When the parts arrive:** follow `docs/BRINGUP.md` step by step (the admin "Bring-up" tab keeps the ticks). No step needs new code. Weeks 3–6 of the table above become wiring, calibration and real tests.

## Final week (week 12, 18–24 Jan 2027, or the last week before the defense) — checklist
- [ ] **TTS provider — decision needed from the student by 2026-12-20** (question asked on 2026-11-17, sprint 4 P0.2). OpenAI shuts down `gpt-4o-mini-tts`, the voice used today, on **2027-01-06** (OpenAI deprecations page, read again on 2026-11-17). That is before week 12, so "switch to ElevenLabs in the final week" (decision of 2026-11-06) no longer works on its own: between 6 Jan and the final week the robot would have no voice. Both adapters now exist and are chosen with `TTS_PROVIDER` in `.env`; the default is still `openai` (`gpt-4o-mini-tts`). Choose one:
  - **Option A — `openai_realtime` before 2027-01-06, ElevenLabs in the final week.** Set `TTS_PROVIDER=openai_realtime` (model `gpt-realtime-2.1-mini`, the replacement OpenAI names; same OpenAI key, no new subscription). ElevenLabs credits stay for the final tests and the defense, as planned. To know first: it is a conversational model told to read the text word for word, not a text-to-speech model; in the one real check (4 short phrases, 2026-11-17) the transcript matched the text every time and the first audio came after 1.4–1.7 s, but **nobody has listened to it yet** (accent, pace, wording). Its audio is WAV, not mp3.
  - **Option B — ElevenLabs earlier (before 2027-01-06) and until the defense.** One voice for all remaining tests and the report, but the paid period is about three weeks longer (Starter is about $6/month, 30k credits ≈ 150 answers). The `elevenlabs` adapter was tested with a fake server only; **it has never been called for real**.
  - Either way, **by 2026-12-20**: listen to the new voice on `/voice` (Jordanian accent, pace), then regenerate the cached audio with `prepare-phrases` and `prepare-explainers`, re-run `evaluate`, and measure voice latency again with the /voice timings table. The audio cache keeps each provider's files apart, so switching back needs no cleanup.
  - For ElevenLabs (final week in option A, earlier in option B): set `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` and, if wanted, `ELEVENLABS_MODEL` in `.env`. Choose an Arabic or Levantine voice from the ElevenLabs library by listening; do not clone a real person's voice without explicit consent. Subscribe to Starter only for the period needed.
  - Report: state that TTS is configurable, show the latency and quality of the providers that were used, and say which one spoke in the demo.
- [ ] Final golden-set run and multi-speaker validation (docs/SPEAKERS.md), with the results saved in data/golden/results/.
- [ ] 30-minute mixed-operation test.
- [ ] Rehearsal of the demo script; fallback plan if the internet fails (text subtitles and the "service unavailable" face).

## To do by the student after sprint 3 (2026-11-13)
- [ ] `docker compose build backend`, `docker compose up -d`, `alembic upgrade head` (once after pulling `sprint-3`).
- [ ] Create the admin user (`create-admin`) and log in.
- [ ] Approve FAQs and pitches in `/admin` → FAQs & pitches (19 FAQ drafts need the program name added to the question first).
- [ ] Enter and approve places and labs in `/admin` → Places & labs.
- [ ] Before a demo on another device or with the robot: fixed address, firewall rule, `ROBOT_TOKEN`, `PUBLIC_BASE_URL` (docs/DEPLOY.md).
- [ ] Make a backup before each demo (`scriptsackup-db.ps1`) and run `purge-conversations` from time to time (docs/PRIVACY.md).
- [ ] Agree a privacy notice near the robot with the supervisor (docs/PRIVACY.md, last section).

## To do by the student after sprint 2 (2026-11-13)
- [ ] Review and approve the 12 explainer drafts in `/admin` → Explainers, then seed and approve the interest mapping.
- [ ] Listen to the new voice pace (`TTS_SPEED=1.12`) and to a streamed answer on `/robot`; adjust in `.env` if needed.
- [ ] Try `VAD_SILENCE_MS=700` with your own voice (pauses in the middle of a sentence); go back to 900 if it cuts you off.
- [ ] Collect real photos or short videos per program (with permission) for the `media` scenes, and the official study-plan web addresses of the five programs that only have a document id (for the QR scene).

## Don't forget (student's request, 2026-11-06): videos while the robot explains
- **On the robot screen:** while the robot explains a program, lab or service, the display can play a short video (or show images), not only subtitles.
  - Videos must be official MEU material, or recorded by the student with permission. They are stored **locally** (project rule: essential media stays local), each with its source and date.
  - Backend: a media library in the admin (upload, link to a program/topic, review status), and an optional `card.media` in `display.update` (docs/PROTOCOL.md).
  - Robot UI: a muted or low-volume video player, so the robot's voice stays clear and the microphone does not pick up the video.
  - Timing: B5 (admin + media), then robot display in weeks 7–8.
- **Demo videos of the robot explaining** (final deliverable: demonstration video), recorded for the report and the defense. Start recording short clips as soon as the robot can talk on its screen, and keep the best takes. This also covers the final-week checklist.
