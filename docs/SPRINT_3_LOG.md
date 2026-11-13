# Sprint 3 log

Task file: `docs/SPRINT_3.md`. Branch: `sprint-3` (created from `sprint-2` at 4f95d07).

**Start time: Fri 2026-11-13 19:19 (laptop clock, `date`).** Time budget: 4 hours → stop by about 23:19.

## Questions for Mohannd
(filled in as the sprint goes; the final list is in the summary at the end)

## S0 — Baseline
- `sprint-3` created from `sprint-2`. `docker compose up -d`, `alembic upgrade head` (at 0008), `pytest -q`: **165 passed**.
- `docs/SPRINT_3.md` and `docs/SPRINT_4.md` were untracked files you added; they are committed unchanged as the first commit of the branch.
- **State after sprint 2 (read from `docs/SPRINT_2_LOG.md` and the database):**
  - Since the sprint 2 log was written you **approved all 12 explainers** (6 Arabic, 6 English) and **13 interest rows** (7 more are rejected). So explainers, "ساعدني أختار" and the attract mode are live now.
  - Sources: 14 approved, 5 rejected, 1 stale (the faculty overview, source 66, question 1 of sprint 2 is still open).
  - Empty tables that this sprint fills with tools: `faqs` (0), `labs` (0), `admin_users` (0). Robot runs: 2, both simulated.
- **Left unfinished by sprint 2, and what happens to it here:**
  - Nothing was half-built. The open points were decisions and checks that only you can make (voice pace by ear, `VAD_SILENCE_MS=700` with a real voice, the QR scan, the TTS model shutdown on 2027-01-06, study-plan links, real photos). They stay in the questions list.
  - Streaming TTS stays off: settling it needs two back-to-back `latency --dialogue` runs, which is exactly this sprint's latency budget. They are run in S10 if nothing else needs the budget.
  - `ruff` long-line warnings: left as they are (question 12 of sprint 2 is unanswered).
