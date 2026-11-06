# Project plan and status

12 working weeks: week 1 starts on 2026-11-02 and week 12 ends on 2027-01-24; buffer and defense in the week after (to be confirmed with the official date).
Milestones: **M1** 6 Dec (base moves + conversation works) · **M2** 27 Dec (return-to-origin trials) · **M3** 10 Jan (full build) · **M4** 24 Jan (final version + results + report).

## Backend status
| Phase | Scope | Status |
|---|---|---|
| B1 | Skeleton, Docker, Postgres + pgvector, Alembic, /health | ✅ done 2026-11-06 |
| B2 | Crawler, review queue, curated knowledge (6 programs incl. Biomedical Engineering, official 2025/2026 plans; Student Handbook) | ✅ done 2026-11-06 |
| B3 | Hybrid retrieval, /api/chat, golden set | ✅ 35/35 on 2026-11-06; Student Handbook 2025/2026 and Biomedical Engineering added (golden set now 49, re-run pending) |
| B4 | Voice: STT/TTS, dialogue manager, /ws/robot, /voice page | 🟡 works end to end; latency 10 s → target about 4 s (bench, shorter answers) |
| B4+ | Multi-speaker Level 2 (voice + camera), docs/SPEAKERS.md | 🟡 voice part done; camera part next |
| B5 | Admin complete, analytics, telemetry + trajectory view, return-trial stats | ⏳ |

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
  - Add the `ElevenLabsTTS` adapter (TTS_PROVIDER=elevenlabs, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL). This takes about 1 hour of code; the free plan is enough to test it.
  - Choose an Arabic or Levantine voice from the ElevenLabs library by listening; do not clone a real person's voice without explicit consent.
  - Subscribe to Starter (about $6/month, 30k credits ≈ 150 answers) only for this period.
  - Re-run `evaluate`, and measure voice latency again with the /voice timings table.
  - Report: state that TTS is configurable, show OpenAI vs ElevenLabs latency and quality, and say which was used in the demo.
- [ ] Final golden-set run and multi-speaker validation (docs/SPEAKERS.md), with the results saved in data/golden/results/.
- [ ] 30-minute mixed-operation test.
- [ ] Rehearsal of the demo script; fallback plan if the internet fails (text subtitles and the "service unavailable" face).
