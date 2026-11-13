# Demo sprint log

Task file: `docs/DEMO_SPRINT.md`. Branch: `demo-sprint` (created from `master` at d20be5d).

**Start time: Fri 2026-11-13 16:31 (laptop clock, `date`).** Time budget: 4 hours → stop by about 20:31.

## Questions for Mohannd
1. **No git remote is configured in this folder** (`git remote -v` is empty, and the `gh` tool is not installed), so `git push -u origin demo-sprint` cannot work. I did not create or guess a remote. All work is committed locally on `demo-sprint`. To push it yourself:
   `git remote add origin <your GitHub repo URL>` then `git push -u origin demo-sprint`.
   (DECISIONS says the repo was pushed to GitHub on 2026-11-06; that was probably done from another copy of the folder.)
2. **The Student Handbook (source 346, 19 chunks) and the Biomedical Engineering plan (source 345, 13 chunks) are still `pending`.** The robot cannot use them until you approve them in `/admin`. I did not approve anything. Until then, handbook and Biomedical questions get the "I don't have this information" answer, and those golden questions fail in `evaluate`.
3. The local branch is called `master`, while the task file says `main`. Nothing was merged or pushed to either.

## M0 — Baseline
- `git status` at the start: clean except the untracked `docs/DEMO_SPRINT.md` (the pending work from 2026-11-06 was already committed as d20be5d, so no "Pending work" commit was needed).
- `docker compose up -d`, `alembic upgrade head` (already at 0005), `pytest -q`: **66 passed**.
- `stats`: sources approved 12 · rejected 5 · pending 2 · stale 1; chunks approved 81 · pending 33 · rejected 7 · stale 1.
  - Pending sources: Biomedical Engineering study plan (id 345) and Student Handbook 2025/2026 (id 346). **Neither is approved.**
  - Stale: "Faculty of Engineering & Design — departments and programs" (id 66), changed when Biomedical Engineering was added; needs re-review.
- Added the "Don't forget … videos while the robot explains" section to `docs/PLAN.md`; added `backend/data/media/` to `.gitignore`.
