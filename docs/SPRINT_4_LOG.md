# Sprint 4 log

Task file: `docs/SPRINT_4.md`. Branch: `sprint-4` (created from `sprint-3` at 626e9aa).

**Start time: Tue 2026-11-17 19:53 (laptop clock, `date`).** Time budget: 4 hours → stop by 23:53.

## Questions for Mohannd
(filled in during the sprint; see the end of this file)

## P0 — Sprint 3 leftovers
Baseline before any change: `docker compose exec backend pytest -q` → **222 passed**.

### P0.1 — A rejected source switches off all its chunks
- Retrieval (full-text, vector, `embed_missing`) now needs the chunk **and its source** to be `approved`. The same rule is used where passages are read for explainer drafts, FAQ / pitch drafts, the known-number checks and place contact passages.
- Rejecting a source rejects its pending and approved chunks. Stale chunks stay `stale` (they never answer; changing them would have meant editing an existing test).
- The admin overview shows a red warning card listing approved chunks of non-approved sources. The card was not opened in a browser; only its presence in the page is tested.
- Side effect to know: a source that goes `stale` (page changed, or a failed fetch on re-crawl) also stops answering until it is reviewed again (question 3).
- Your database, read only: 0 approved chunks under non-approved sources. No review status was changed.
- Tests: 9 new → 231 passed. Commit: `076d3a4`.
- Extra commit `1fcfd2f`: while the sprint ran, the FAQs and pitches in your database became approved (38 FAQs, 12 pitches; not by me — I approve nothing). Two existing tests assumed none were approved and began to fail. They now set live approved rows aside inside the rolled-back test transaction; no assertion changed.

### P0.2 — TTS model shutdown on 2027-01-06 (sprint 3 S5b)
- `TTS_PROVIDER` = `openai` (default, still `gpt-4o-mini-tts`), `openai_realtime` (`gpt-realtime-2.1-mini`) or `elevenlabs`. Both new adapters sit behind the TTS interface (`backend/app/services/tts_adapters.py`).
- OpenAI's documentation (read on 2026-11-17) confirms the model, and that it is served only by the Realtime API. Its audio is **WAV, not mp3** (question 2). It is a conversational model told to read word for word; the adapter compares the API's transcript with the text and logs a warning on a difference. That is a check, not a guarantee.
- `elevenlabs`: written from the API reference, tested with fakes only. **It was never called.**
- Cache key: the provider is part of the key for non-default providers; existing cached audio stays valid. Tested.
- **Real check of `openai_realtime`** (voice coral, speed 1.12; 5 sends used, the first lost to a bug in the check script, 4 measured). Times count from the start of the connection:

  | Phrase | First audio | Whole call | Audio length | Transcript = text |
  |---|---|---|---|---|
  | أهلاً وسهلاً فيك! | 1405 ms | 3798 ms | 1.40 s | yes |
  | عندك سؤال ثاني؟ أنا جاهز. | 1701 ms | 4167 ms | 2.06 s | yes |
  | Welcome! How can I help you today? | 1580 ms | 4073 ms | 2.29 s | yes |
  | One, two, three. Thank you for visiting. | 1722 ms | 4245 ms | 3.45 s | yes |

  The API accepted the voice, the speed and the full Jordanian style instructions. **Accent and pace were not judged: nobody listened.** Every call took about 4 s whatever the audio length; not investigated (budget spent).
- `docs/PLAN.md`: the ElevenLabs line is now a decision question dated 2026-11-17 (question 1).
- Tests: 19 new → 250 passed. Commit: `0257942`.

### P0.3 — Formatting pass
- `ruff format` over `backend/app`, `tests`, `migrations`; all 607 long-line warnings fixed; 72 files. Line length stays 100. The syntax tree of all 116 Python files is identical before and after (apart from whitespace inside docstrings).
- Left alone because fixing them changes code: 7 import-order, 4 ASYNC240, 2 unused-import, 1 F541 and 1 B008 warnings.
- Tests: 250 passed before and after. Commit: `08ca906`.

### P0.4 — An FAQ can be linked to the wrong program (added by you during the sprint)
- An FAQ whose source belongs to a program must be linked to that program; approval is refused otherwise (409, "wrong program: the source '…' belongs to X, but the FAQ is linked to Y"), and the issue shows in the FAQ list. Pitches have no source field, so there is nothing to compare for them.
- FAQ numbers are checked against the passages of the FAQ's own program plus sources that belong to no program. Pitch number checks were already per program.
- Limit, said plainly: source 66 (the faculty departments page) belongs to no program and contains 165, so the number rule alone would **not** have caught FAQ 16. The program-link rule does (question 4).
- **Re-check, read only** (`python -m app.ingestion.cli check-curated`): found 38 FAQs, **all approved** (not 27 pending / 11 approved as in the task file), and 12 pitches, all approved. **0 wrong-program links and 0 other issues.** FAQ 16 is linked to Architectural Engineering with source 65. Nothing was changed.
- Tests: 3 new → **253 passed**. Commit: `fd6cd4b` (after the formatting pass, because the tree was already reformatted when the item arrived).
