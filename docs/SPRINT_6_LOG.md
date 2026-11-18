# Sprint 6 log — publish on GitHub and deploy to a server

Branch `release`, created from `sprint-5` (commit `6ab1207`) on 2026-11-19. Task file: `docs/SPRINT_6.md`.

> ## ⚠ READ FIRST: the GitHub repository is PUBLIC
> `rraedmohannd/myrobot` is **public** (GitHub API, 2026-11-19: `"visibility": "public"`, `"private": false`; it answers without logging in).
> It holds the official MEU PDFs and all of your work. The sprint rule says it must be private, so:
> - I did **not** change the visibility (your decision; GitHub → Settings → General → Danger Zone → Change visibility → Private);
> - I did **not** merge `release` into `main`, did not tag `v0.6.0` and did not make a GitHub release. The pull request steps are written below, ready to run once it is private.
>
> The secret scan found **no real secret** in the history (P0.1), so making it private is the only blocker.
> Also: the repository has **no `main` branch** (its default branch is `demo-sprint`; a local branch `master` exists from before). See question 2.

## P0.1 — Secret scan and repository check
- **gitleaks 8.21.2 (Docker), whole history of all branches** (`git --log-opts=--all`, 123 commits, local and `origin/*`): **no leaks found**.
- **Own search over every patch of every commit** (`git log --all -p`, a small script that never prints a value):
  - `sk-` followed by 20+ key characters: **0 hits**.
  - Values of the laptop `.env` (`OPENAI_API_KEY`, `ADMIN_TOKEN`, `POSTGRES_PASSWORD`; `ROBOT_TOKEN` and the other keys are empty): `OPENAI_API_KEY` **not found**, `ADMIN_TOKEN` **not found**. `POSTGRES_PASSWORD` was found in 4 commits, because the laptop still uses the public example value `change-me` from `.env.example` (checked by comparison, not printed). It is not a secret; it only protects the laptop's database, which is not reachable from outside. The server gets a generated password.
  - `ADMIN_TOKEN=`, `ROBOT_TOKEN=`, `PASSWORD=` lines added in any commit: only placeholders (`change-me-admin`, `change-me`, `<a long random string>`) and the throw-away `preview-token` of a documented local preview command in `docs/HOW_TO_START.md`.
  - **Result: no real secret was ever committed.** Nothing to revoke.
- **Visibility:** **public** (see the box above). `gh` is not installed on this laptop; the check used the GitHub REST API without a login, which only works for public repositories.
- **Large files in history** (`git rev-list --objects --all`, by size): the largest is `backend/app/static/vendor/mediapipe/vision_wasm_internal.wasm` at 14.0 MB, then the student handbook PDF (6.5 MB) and `face_landmarker.task` (3.6 MB). **Nothing over 20 MB.** The whole packed history is 17.6 MB.
- **`.gitignore`:** added `*.bundle.tar.gz`, `smart-campus-bundle-*.tar.gz`, `deploy/server/.env*` (except `deploy/server/.env.production.example`), certificates and keys (`*.pem`, `*.key`, `*.crt`, `backend/data/certs/`), `tools/video/out/` and `.impeccable/`. `backups/` was already ignored. Checked that no tracked file is newly ignored.
- **`deliverables/`:** the Follow-Up Protocol (`.docx`, `.pdf`, 0.7 MB each) and `CHANGELOG_rev2.md`, already tracked. They stay in the repo.
- **`.impeccable/`:** only `hook.cache.json`, the cache of a design-review tool: absolute paths of this laptop and Claude Code session ids, no project content. It was only excluded on this laptop (`.git/info/exclude`); now `.impeccable/` is in `.gitignore` for everyone. It does not belong in the repo.
- **`README.md`** at the root: what the project is, folder map, how to run it locally, links to `docs/`, the safety and simulation rules, and that it was built with the help of Claude Code.
- **Developer database, before any work** (row counts, read-only, kept to compare at the end): admin_sessions 0, admin_users 0, alembic_version 1, bringup_steps 0, calibration_records 0, chunks 121, events 29, explainer_segments 74, explainers 12, faculties 1, faqs 38, labs 0, media 5, pitches 12, places 0, pose_samples 2166, programs 6, return_trials 0, robot_runs 4, sessions 13, sources 20, turns 20.
- **Tests in this sprint never use the developer database:** the backend suite normally runs inside the developer stack (with rolled-back transactions). In this sprint it runs in a separate throw-away compose project (`scr-test`, database in memory, migrated and seeded like CI). Baseline there: **268 passed**.
