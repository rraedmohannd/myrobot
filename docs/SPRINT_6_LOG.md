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

## P0.5 — Data bundle from the laptop (+ P1 `export-bundle.sh`)
- `scripts/export-bundle.ps1` (Windows PowerShell 5.1, `-IncludeConversations`) and `scripts/export-bundle.sh` (bash, shellcheck-clean, no python/jq, `--include-conversations`) give the same output: `backups/smart-campus-bundle-<stamp>.tar.gz` (git-ignored), flat: `manifest.json`, `db.dump` (`pg_dump -Fc --no-owner --no-privileges`), `media/`, `tts_cache/`.
- Left out by default: the **data** of `sessions`, `turns` (visitors' questions stay on the laptop) and always `admin_users`, `admin_sessions` (the server gets its own admin). Table definitions stay in the dump.
- Foreign keys checked on the real database: only `turns.session_id → sessions` and `admin_sessions.user_id → admin_users` point at excluded tables, and both start from excluded tables, so nothing dangles. Each run re-checks this and stops if a kept table ever references excluded rows (tested with a planted key in a throw-away container).
- The manifest has the git commit (+ `git_dirty`), app version, Alembic revision (`0012`), Postgres version (16.15), counts per table as dumped, `simulated_robot_runs` and the SHA-256 of every file. Counts are taken before and after the dump and must match (the backend keeps running while it dumps).
- A secret scan compares every staged file, the manifest and the text form of the dump with the laptop's `.env` secret values and an `sk-` pattern, and refuses to write the bundle on a hit (tested with planted fake secrets).
- **Real run:** `smart-campus-bundle-20261119-012024.tar.gz`, 88.4 MB, 353 files (db.dump 1.65 MB, 5 media files, 347 speech-cache mp3s). Rows: chunks 121, sources 20, faqs 38, explainers 12, explainer_segments 74, pitches 12, programs 6, faculties 1, media 5, robot_runs 4 (all 4 simulated), events 29, pose_samples 2166; sessions/turns 0 (13/20 stay on the laptop).
- **Checked:** 4 bundles (PowerShell, PowerShell with conversations, bash, final): 353/353 SHA-256 OK with `sha256sum -c`, no extra files, `pg_restore --exit-on-error` into a throw-away pgvector container with 0 errors, counts equal to the manifest for all 22 tables (with conversations: sessions 13, turns 20). PowerShell and bash manifests agree. Developer database counts unchanged.
- **Not verified:** the bash script on real Linux/macOS (only Git Bash on Windows). `pg_dump` keeps the sequence positions of the excluded tables, so the server can see roughly how many conversations existed, never their content.

## P0.7 — The robot over the internet
- `robotd` works with `BACKEND_URL=wss://<domain>/ws/robot`, alone or in the comma list (e.g. the server first, the laptop as a fallback).
- **Certificate check:** it was already on (aiohttp's default context: system CAs and hostname check). Now explicit in `robot/robotd/tls.py`, used by both connections to the backend (the WebSocket, and the UI server's HTTP session for the page, `/api/robot` and `/media`). There is no switch to turn it off. `BACKEND_CA_FILE` exists only for a test or self-hosted CA.
- **Token leak fixed:** when the handshake failed (for example Caddy answering 502/503 while the backend restarts), aiohttp's error text contained the URL with `?token=…`, and robotd wrote it to journald. Now it is redacted (`token=***`) in `last_error`, and a filter on the root log handlers also covers aiohttp's own loggers.
- **Two server-mode breakages found and fixed:**
  - The Pi's local robot page built a `ws://` address to the backend. robotd now tells the page the backend is secure (`SCR_BACKEND_SECURE`), and `robot-guard.js` upgrades only that address to `wss://`.
  - In production the backend requires the token for `/robot` and `/api/robot`. robotd's page requests now send it as an `Authorization: Bearer` header (a header, so it does not appear in access logs).
- **Safety unchanged** (rule 4): with no server or an untrusted certificate, the robot stays IDLE, the ESP32 link keeps running and robotd keeps retrying every 2 s (tested).
- **Tests:** 10 new in `robot/tests/robotd/test_robotd_tls.py`, with a throw-away CA made by `openssl`:
  - trusted CA connects and the token arrives;
  - untrusted certificate refused;
  - hostname mismatch refused;
  - unreadable CA file: keeps retrying;
  - redaction;
  - the guard's wss upgrade (in Node).
- **Results:** robot suite **340 passed** (was 331), firmware tools 29 passed, ruff clean.
- `docs/DEPLOY.md` has a new section "Server mode (backend on a rented server)": settings, certificates, safety without the server, why conversation needs the internet anyway, how to check from the Pi.
- **Latency note:** the round trip Jordan ↔ Germany adds about 60–100 ms per message. This is an estimate, to be measured at the demo site; it is small next to the speech services (seconds).
- **Not verified:** the robot page over wss in a real Chromium (only the logic in Node). The live run against the rehearsal stack is in P0.6 below.

## P0.2 — Production mode of the backend (`APP_ENV=production`) + P1 admin "Server" card
Everything below is active only with `APP_ENV=production`; the laptop setup is unchanged (all 268 earlier tests pass unchanged). Backend version **0.6.0**.
1. **Refuses to start with weak or missing secrets.** The app and the CLI (`create-admin`, `usage-today`, …) both check, and report every problem at once, by setting name only, never the value:
   - `ADMIN_TOKEN` shorter than 32 characters, or an example value;
   - `ROBOT_TOKEN` missing;
   - `POSTGRES_PASSWORD` shorter than 16 characters;
   - `PUBLIC_BASE_URL` missing or not `https://`;
   - `SECRET_KEY` shorter than 32 characters (new: it signs the visitor cookie);
   - `SITE_ACCESS_CODE` missing while `PUBLIC_PAGES=true`.
2. **Who may use the paid endpoints.** `/robot`, `/voice`, `POST /api/chat`, `/api/tts*` and `/ws/robot` need either:
   - the **robot token** (`?token=`, `Authorization: Bearer` or `X-Robot-Token`); or
   - a **visitor session**: `/access` (Arabic first, then English) asks once for `SITE_ACCESS_CODE` and sets a cookie signed with HMAC-SHA256 (HttpOnly, Secure, SameSite=Strict, `VISITOR_SESSION_HOURS`).

   Without access:
   - pages redirect to `/access?next=…` (local paths only);
   - the API answers 401; the WebSocket closes with 4401;
   - wrong codes are rate limited (10 per IP per 10 min).

   Other rules:
   - `PUBLIC_PAGES=false` turns the code page off, so only the robot token works.
   - `ROBOT_LOCAL_NETS` is ignored in production: every robot needs the token.
   - `/static`, `/media/{id}` and `/api/robot/screen` stay public (no paid content).
   - `/health` shows only `status` and `version`; an admin who is logged in sees the full body.
3. **Cost guard.**
   - Per-IP limits per minute (visitors `RATE_LIMIT_PAID_PER_MIN`=12, robot token `RATE_LIMIT_ROBOT_PER_MIN`=60) on `/api/chat`, `/api/tts` and every WebSocket turn (typed question, and spoken question before speech-to-text).
   - A global **daily cap** `DAILY_PAID_CALL_LIMIT`=500, counted per real provider call (embedding, speech-to-text, LLM, text-to-speech; cache hits do not count; retries do).
     - Checked in the resilience wrapper that every provider call goes through.
     - Stored in the database (migration `0013`, table `paid_call_days`), so it survives restarts.
     - If the counter cannot be read, the call is refused (fail closed).
   - Over a limit:
     - the robot says its usual polite offline reply;
     - HTTP answers 429;
     - the circuit breaker is not touched;
     - the admin Server card shows a warning.
4. **Admin.**
   - Login only: `X-Admin-Token` is ignored unless `ADMIN_TOKEN_SCRIPTS=true`.
   - Lockout after `LOGIN_MAX_FAILURES`=5 wrong passwords per user and per IP, for `LOGIN_LOCKOUT_MIN`=15.
   - Cookies are always Secure, HttpOnly and SameSite=Strict; session length is `ADMIN_SESSION_HOURS`.
   - The admin "System" page no longer shows the robot token inside the robot URLs in production.
5. **Locked down.**
   - CORS allows only `PUBLIC_BASE_URL`.
   - `/docs`, `/redoc` and `/openapi.json` are off.
   - Media uploads: size `MEDIA_MAX_MB`=50 and type checks are tested.
   - The client IP comes only from `request.client.host`, which uvicorn sets from the proxy headers only when the request comes from `FORWARDED_ALLOW_IPS`. A test shows that a forged `X-Forwarded-For` does not reset a rate limit.
6. **Secrets out of the logs.** A log filter (app, CLI, uvicorn access/error) replaces `token=<value>` with `token=***` and hides the literal values of the tokens, the access code, the secret key, the API keys and the database password. Found by the robot agent: uvicorn logs every WebSocket accept with its query string.
7. **`FAKE_PROVIDERS=true`.** Deterministic fake speech-to-text, LLM, text-to-speech and embeddings, for rehearsals only.
   - A loud "SIMULATED providers" warning is logged, and the admin health view and the Server card show it.
   - Fake calls count against the limits, so the rehearsal can trigger them without OpenAI.
8. **P1 Server card.** `GET /api/admin/server` and a "Server" card on the admin System tab show:
   - production mode on/off, and fake providers on/off;
   - today's paid calls against the cap, and refusals;
   - the last backup (time, sizes, from `/backups/last-backup.json`);
   - disk usage;
   - the earliest certificate expiry found in Caddy's data.
   
   New CLI command: `usage-today`.
9. **`docs/PRIVACY.md`** has a new "Server mode" section:
   - the server is rented in Germany;
   - who has access: Mohannd and the friend who runs it. A server admin with root access can technically read the database; this is said plainly;
   - text only, no audio or face images;
   - 30-day retention, 14-day backups;
   - the laptop's conversations are not copied;
   - OpenAI requests now come from the server.
10. **Tests:** 28 new in `backend/tests/test_server_mode.py`. Backend suite **296 passed** (throw-away `scr-test` database), ruff format clean, no new ruff findings.

**Not verified here:** behind the real proxy (that is the rehearsal, P0.6). The rate limiters and the login lockout are in-process, which is one reason for `WEB_CONCURRENCY=1`. If a visitor's cookie expires while the robot page is open, the page keeps retrying the WebSocket until it is reloaded. The new WebSocket error code `rate_limited` is a free string and is not yet listed in `docs/PROTOCOL.md`.
