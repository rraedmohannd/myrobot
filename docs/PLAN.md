# Project plan and status

12 working weeks: week 1 starts on 2026-11-02 and week 12 ends on 2027-01-24; buffer and defense in the week after (to be confirmed with the official date).
Milestones: **M1** 6 Dec (base moves + conversation works) · **M2** 27 Dec (return-to-origin trials) · **M3** 10 Jan (full build) · **M4** 24 Jan (final version + results + report).

## Backend status
| Phase | Scope | Status |
|---|---|---|
| B1 | Skeleton, Docker, Postgres + pgvector, Alembic, /health | ✅ done 2026-11-06 |
| B2 | Crawler, review queue, curated knowledge (6 programs incl. Biomedical Engineering, official 2025/2026 plans; Student Handbook) | ✅ done 2026-11-06 |
| B3 | Hybrid retrieval, /api/chat, golden set | ✅ 35/35 on 2026-11-06. Golden set is now 49: **48/49 (98 %) on 2026-11-13**, 0 invented numbers, after the student approved the Student Handbook and the Biomedical Engineering plan (37/49 earlier the same day, before the approval). The one failure (an English question answered by an Arabic passage) was a retrieval miss, fixed in retrieval and checked with `ask` on 4 questions; the full set was not re-run after the fix |
| B4 | Voice: STT/TTS, dialogue manager, /ws/robot, /voice page | 🟡 works end to end. Streaming answers + reasoning effort low (2026-11-13): question text → first answer audio, median 5.3 s → 3.5 s (STT not included); about 4.7–5.2 s from end of speech (estimate). Target about 4 s not reached; TTS is now the largest stage |
| B4+ | Multi-speaker Level 2 (voice + camera), docs/SPEAKERS.md | 🟡 voice part done; camera part: browser prototype built on 2026-11-13 (`/robot?camera=1`, off by default), **not yet tried with real people**; validation and the Pi version (YuNet, pan servo) are next |
| B5 | Admin complete, analytics, telemetry + trajectory view, return-trial stats | 🟡 analytics, knowledge gaps, conversations, ask-the-robot, evaluation, coverage and media tabs done (2026-11-13, static page). Telemetry messages, trajectory view and return-trial entry/stats exist and are exercised with **simulated** runs only. Still open: real telemetry from the robot, React panel and login |
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

## Final week (week 12, 18–24 Jan 2027, or the last week before the defense) — checklist
- [ ] **Switch TTS to ElevenLabs** (student's decision on 2026-11-06: OpenAI TTS for all development and testing, ElevenLabs only at the end, so paid credits are kept for the final tests and the defense).
  - **Deadline moved up (found on 2026-11-13):** OpenAI's deprecations page lists `gpt-4o-mini-tts`, the model used today, with shutdown on **2027-01-06**. That is before week 12, so the TTS switch (ElevenLabs, or another OpenAI voice path) must be done and tested **before 2027-01-06**, and the approved explainers and fixed phrases must be regenerated with the new voice (`prepare-phrases`, `prepare-explainers`).
  - Add the `ElevenLabsTTS` adapter (TTS_PROVIDER=elevenlabs, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL). This takes about 1 hour of code; the free plan is enough to test it.
  - Choose an Arabic or Levantine voice from the ElevenLabs library by listening; do not clone a real person's voice without explicit consent.
  - Subscribe to Starter (about $6/month, 30k credits ≈ 150 answers) only for this period.
  - Re-run `evaluate`, and measure voice latency again with the /voice timings table.
  - Report: state that TTS is configurable, show OpenAI vs ElevenLabs latency and quality, and say which was used in the demo.
- [ ] Final golden-set run and multi-speaker validation (docs/SPEAKERS.md), with the results saved in data/golden/results/.
- [ ] 30-minute mixed-operation test.
- [ ] Rehearsal of the demo script; fallback plan if the internet fails (text subtitles and the "service unavailable" face).

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
