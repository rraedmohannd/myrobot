# Sprint 2 log

Task file: `docs/SPRINT_2.md`. Branch: `sprint-2` (created from `demo-sprint` at 346b419).

**Start time: Fri 2026-11-13 17:56 (laptop clock, `date`).** Time budget: 4 hours → stop by about 21:56.

## Questions for Mohannd
1. **Review queue, faculty overview (this is the one golden question that fails).** The curated page "Faculty of Engineering & Design — departments and programs" (source 66) is still `stale`: its six-program text is `pending`. Meanwhile two old crawled website pages are `approved` (sources 30 and 33) and they list **Civil Engineering** and only three programs. Please approve source 66, and decide whether sources 30 and 33 should be rejected as outdated. I did not touch them.

## S0 — Baseline
- `sprint-2` created from `demo-sprint`. `docker compose up -d`, `alembic upgrade head` (already at 0007), `pytest -q`: **130 passed**.
- `stats`: sources approved 14 · stale 1 · rejected 5; chunks approved 113 · pending 1 · stale 1 · rejected 7.
- **Evaluation run 1 of 2** (49 golden questions, gpt-6-luna, streaming, effort low): **48/49 (98 %), 0 numbers outside the sources**, median 2320 ms, p90 3210 ms. Report: `backend/data/golden/results/20261113-145919.md`.
  - `hb-grad-gpa-en`, the failure of the previous run, now passes (the `rag_keep_top` fix of sprint 1 works on the full set).
  - **Still failing: `faculty-programs-en`** ("Which bachelor programs does the Faculty of Engineering & Design offer?"). The robot listed Civil Engineering, Renewable Energy, Intelligent Systems, Architectural Engineering and Interior Design, and missed Graphic Design.
    - Why: the answer is not invented. It comes from approved passages: the old crawled faculty pages (sources 30 and 33) say the faculty has Civil Engineering and three programs, and the correct curated overview (source 66) is not usable because it is `stale` / `pending`. This is a review matter, not a code matter (question 1). In the previous run the Arabic version of this question passed and the English one happened to pass too; the model mixes the passages differently from run to run.
- **Latency baseline for S1 (measurement run 1 of 4)**, same command as sprint 1 (8 golden questions, question text → first answer audio, STT not included): median **4170 ms**, p90 4854 ms (retrieval 217, LLM first piece 1578, LLM whole 2602, first TTS piece 1938). File: `backend/data/latency/20261113-150222.json`. This is slower than the 3500 ms measured earlier today with the same code, so provider speed varies by the hour; S1 compares against this fresh baseline.
