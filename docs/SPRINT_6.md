# Sprint 6 — publish on GitHub and deploy to a server (task file for Claude Code, Fable 5.2)

Prepared on 2026-11-19. Run it **after sprint 5** (`docs/SPRINT_5_LOG.md`).

**Goal (Mohannd, 2026-11-19).**
1. Put the whole project on GitHub, cleanly and safely.
2. Prepare everything so that **a friend of Mohannd can run the backend on a rented Linux server in Germany** with a few commands, using the knowledge, approvals and media that are on Mohannd's laptop today.

The robot (Pi) can then reach the backend over the internet at an `https://` address, and the laptop setup keeps working as before.

**The friend is not a project member.** He gets only:
- read access to the code;
- a data bundle;
- a server guide.

He never gets Mohannd's laptop `.env`. The OpenAI key on the server should be a **separate key with a monthly spending limit**, which Mohannd creates.

Read first:
- `CLAUDE.md`;
- `docs/DEPLOY.md`, `docs/PRIVACY.md`, `docs/PROTOCOL.md`;
- `docker-compose.yml`, `backend/Dockerfile`, `.env.example`, `.gitignore`;
- `scripts/backup-db.ps1`, `scripts/restore-db.ps1`;
- `.github/workflows/ci.yml`.

**Rules.** Same as earlier sprints and `CLAUDE.md`, with these changes:
- **Branch:** `release` from `sprint-5`. **Merging into `main` is allowed in this sprint** (Mohannd's decision, 2026-11-19), and only:
  - through a pull request;
  - after CI is green;
  - after the secret scan (P0.1) is clean.
- **Log:** `docs/SPRINT_6_LOG.md`, ending with:
  - "Questions for Mohannd";
  - "What Mohannd does";
  - "Message for the friend";
  - "What is not verified".
- **Stop** after 4 hours. P0 first.
- **OpenAI budget:** at most 3 real questions on the production stack test (P0.6). Everything else uses fake providers.
- **Never touch the developer database or its volume.** Production tests run under a **separate compose project name** with their own volumes, and are removed afterwards.
- **No secret** (keys, tokens, passwords, the laptop's `ADMIN_TOKEN`) may appear in a commit, a log, a test fixture or the bundle manifest.

---

## P0

### P0.1 — Secret scan and repository check (do this first; stop on a real finding)
1. **Scan the whole git history** (all branches) with `gitleaks` in Docker, plus a search for:
   - `sk-`;
   - the current values of the local `.env` keys (read them only to compare, never print them);
   - `ADMIN_TOKEN=`, `ROBOT_TOKEN=`, `PASSWORD=`.

   **If a real secret was ever committed:** stop the merge part of this sprint. Write in the log which file, which commit and which kind of key (not the value), and that the key must be **revoked and replaced** by Mohannd. Do **not** rewrite history; that needs Mohannd's decision.
2. **`gh repo view --json visibility,name,url`:** the repository must be **private** (it contains official MEU PDFs and Mohannd's work). If it is public, say so at the top of the log as question 1. Do not change the visibility yourself.
3. **`.gitignore`:** add the new outputs of this sprint: `*.bundle.tar.gz`, `deploy/server/.env*` except the example, certificates, `backups/`, `tools/video/out/`.

   Check the large files in history (`git rev-list --objects --all` sorted by size). Report anything over 20 MB; do not rewrite history.
4. **`.impeccable/` and `deliverables/`:** check what is in them. `deliverables/` (Follow-Up Protocol docx/pdf) belongs in the repo; for `.impeccable/`, decide and say why.
5. **Root `README.md`** (there is none):
   - what the project is;
   - the folder map;
   - how to run it locally (`docker compose up -d`);
   - links to `docs/`;
   - the safety and simulation rules in two lines;
   - "built with the help of Claude Code" stated honestly.

### P0.2 — Production mode of the backend (`APP_ENV=production`)
Everything below is active only when `APP_ENV=production`; the laptop setup is unchanged. Tests for each item.
1. **Refuse to start with weak or missing secrets:**
   - `ADMIN_TOKEN` shorter than 32 characters or equal to the example value;
   - a missing `ROBOT_TOKEN`;
   - a `POSTGRES_PASSWORD` shorter than 16 characters;
   - a missing `PUBLIC_BASE_URL` starting with `https://`;
   - a missing `SITE_ACCESS_CODE` while public pages are on.

   The error message names the setting, never its value.
2. **Who may use the paid endpoints.** `/robot`, `/voice`, `/api/chat`, `/api/tts`, and the page side of `/ws/robot` require either:
   - the **robot token**; or
   - a **visitor session**: a small page asks once for `SITE_ACCESS_CODE` and sets a signed cookie.

   `/health` stays public but shows only `status` and `version` (provider details only to the admin). This stops strangers from spending Mohannd's OpenAI credit.
3. **Cost guard:**
   - per-IP rate limits on everything that calls OpenAI (questions, TTS, STT, turns on the WebSocket);
   - a **global daily cap** of paid calls (`DAILY_PAID_CALL_LIMIT`, default 500).

   Over a limit, the robot gives the existing polite offline reply and the admin shows a warning; nothing crashes. The robot's own token gets a higher per-IP limit.
4. **Admin:**
   - login only (argon2 users from sprint 3);
   - `X-Admin-Token` disabled unless `ADMIN_TOKEN_SCRIPTS=true`;
   - lockout after repeated failed logins;
   - cookies `Secure`, `HttpOnly`, `SameSite=Strict`;
   - session length from `ADMIN_SESSION_HOURS`.
5. **Locked down:**
   - CORS limited to `PUBLIC_BASE_URL`;
   - FastAPI `/docs`, `/redoc` and `/openapi.json` off;
   - upload size and type limits for media checked;
   - client IP taken from the reverse proxy only when the request comes from it (`--proxy-headers` with the proxy's address).
6. Update `docs/PRIVACY.md`: the data now lives on a rented server in Germany, who has access (Mohannd and the server admin), the retention period, and what is stored (text only, no audio or face images).

### P0.3 — Production stack
1. **`docker-compose.prod.yml`** (used with `docker compose -f docker-compose.prod.yml`):
   - **db:** no published port, named volume, strong password from `.env`, healthcheck.
   - **backend:**
     - built image (no code bind mount, no `--reload`);
     - an entrypoint that runs `alembic upgrade head` and then uvicorn with a few workers and proxy headers;
     - a non-root user, a healthcheck;
     - named volumes for `data/media`, `data/tts_cache` and the golden or latency results;
     - `restart: unless-stopped`, log rotation.
   - **caddy** (profile `caddy`, default):
     - ports 80/443;
     - automatic HTTPS for `DOMAIN`, or `<ip-with-dashes>.sslip.io` when there is no domain;
     - WebSockets;
     - security headers (HSTS etc.);
     - a body size limit.
   - **backup:**
     - a daily `pg_dump` plus a tarball of media and the speech cache into a `backups` volume;
     - keeps 14 days;
     - documented restore.
2. **"Behind an existing web server" mode,** because the friend's server may already serve other sites on 80/443:
   - without the `caddy` profile, the backend listens on `127.0.0.1:8000` only;
   - `deploy/server/nginx-site.conf.example` covers WebSockets, timeouts and headers;
   - a certbot note.
3. **Image:**
   - builds for `linux/amd64` and `linux/arm64`;
   - the production install does not pull dev or test extras unless needed;
   - say in the log how big the image is.

### P0.4 — Server scripts (`deploy/server/`, all `shellcheck`-clean)
| Script | What it does |
|---|---|
| `install.sh` | Ubuntu 22.04/24.04 or Debian 12/13: Docker from the official repository, `ufw` (22, 80, 443 only), unattended security upgrades; puts the code in `/opt/smart-campus-robot` (clone with a read-only deploy key or HTTPS); creates `.env` from `.env.production.example` with **generated** secrets (`openssl rand`); asks for the domain (or uses sslip.io) and the OpenAI key (hidden input, never echoed or logged); starts the stack; prints the URLs and the next steps. Re-running it is safe (idempotent). |
| `restore-bundle.sh` | Restores the bundle from the laptop (P0.5): database, media, speech cache; checks the SHA-256 manifest; refuses to overwrite a non-empty database without `--replace` (and backs it up first). |
| `create-admin.sh` | Wraps the existing `create-admin` command. |
| `update.sh` | Backup, `git pull` (main or a tag), build, migrate, restart, then a health check; **rolls back** to the previous image if the check fails. |
| `status.sh` | Containers, health, disk, last backup, today's paid-call count. |

Add `.env.production.example` with every production setting, commented.

### P0.5 — Data bundle from the laptop (`scripts/export-bundle.ps1`)
- **Database:** `pg_dump` in custom format of the live database, **without the data of the conversation tables** (privacy: visitors' questions stay on the laptop; a `-IncludeConversations` switch exists). Everything else: knowledge, approvals, FAQs, pitches, explainers, places, labs, interests, media records, evaluation and latency runs, and simulated robot runs (labelled).
- **Files:**
  - `backend/data/media`;
  - `backend/data/tts_cache`;
  - a `manifest.json` with versions, the Alembic revision, the git commit, counts per table and SHA-256 of every file;
  - **no `.env`, no keys**.
- **Output:** `smart-campus-bundle-<date>.tar.gz` in `backups/` (git-ignored).
- **Check:** restore it with `restore-bundle.sh` into the production test stack (P0.6) and compare the counts.

### P0.6 — Full rehearsal on the laptop (no server yet)
1. Run the production stack under a **separate project name**, with Caddy's internal certificate on `https://localhost:8443` (or similar), and restore the bundle into it.
2. Check over HTTPS:
   - `/health`;
   - the access-code page;
   - `/robot` and `/voice` behind the code;
   - admin login, and the token refused;
   - `/ws/robot` with the robot token through the proxy;
   - a rate limit triggered with fake providers;
   - `update.sh` with a forced failed health check → rollback;
   - a backup made and restored;
   - **at most 3 real questions** (one FAQ, one RAG, one "I don't know") to prove OpenAI works from the stack.
3. **`install.sh` in a fresh Ubuntu 24.04 container** (systemd and Docker-in-Docker where possible; otherwise up to the Docker step), to catch package and script errors. Say plainly what could not be run without a real server.
4. **Remove the rehearsal containers and volumes.** Confirm the developer stack and its data are untouched (same counts as before).

### P0.7 — The robot over the internet
- `robotd` with `BACKEND_URL=wss://<domain>/ws/robot`: TLS certificate verification on, reconnect, and the token sent the way `robotd` already does. Test against the rehearsal stack (the internal CA trusted for the test only).
- `docs/DEPLOY.md` gets a "Server mode" section with the robot settings. It explains that the robot still stops safely without the server (rule 4) and that conversation needs the internet anyway.
- Note in the log: the round trip Jordan ↔ Germany adds about 60–100 ms (to be measured at the demo site), small next to the speech services.

### P0.8 — `docs/DEPLOY_SERVER.md` (for the friend, plain English, step by step)
1. **Server requirements:** Ubuntu 24.04 or Debian 12/13, at least 2 vCPU, 4 GB RAM and 20 GB disk, amd64 or arm64.
2. **What Mohannd sends:** repository access (deploy key), the bundle file, the domain name if any. The OpenAI key is typed in by Mohannd or sent separately, never by chat in clear text.
3. **First install:**
   - `install.sh`;
   - `restore-bundle.sh`;
   - `create-admin.sh`;
   - the checks (a list with expected results).
4. **Existing web server:** the nginx variant.
5. **Updates, backups and restore, logs, disk space, rotating the keys.**
6. **Security notes:**
   - SSH keys only;
   - no other ports open;
   - the access code;
   - the daily cap;
   - who to call if something looks wrong.
7. **Troubleshooting:** certificate, port in use, WebSocket blocked, out of memory.

### P0.9 — Publish
1. Run every suite (backend, robot, firmware, pin checker, JS) and `shellcheck`. CI must be green on `release`.
2. Open a PR `release` → `main` with a summary of sprints 1–6.

   **Do not merge if:**
   - P0.1 found a secret;
   - the repository is public;
   - CI is red.

   Otherwise merge, tag `v0.6.0`, and create a GitHub release with notes. Never include the bundle or any key.
3. Prepare, but **do not do**, the access steps for the friend (write them in the log):
   - **Option A (recommended):** a read-only **deploy key** made on the server, which Mohannd adds in GitHub → Settings → Deploy keys.
   - **Option B:** add the friend as a collaborator with read access.

## P1
- **Admin "Server" card:**
  - production mode on or off;
  - today's paid calls against the cap;
  - the last backup's time and size;
  - disk usage;
  - the certificate expiry date when known.
- **`scripts/export-bundle.sh`** for Linux and macOS (same as the PowerShell one).
- **GitHub Actions:**
  - build the production image on tags and push it to GitHub Container Registry (private);
  - `update.sh --image` can use it instead of building on the server (faster on a small server).
- **Optional uptime check** documented (for example a free external ping to `/health`). No new paid service.

## Done when
- P0 is complete with tests and commits.
- Every suite passes.
- The rehearsal ran end to end on the laptop and was removed, and the developer data is untouched.
- `main` is updated and tagged (or the log explains exactly why not).
- The log ends with a short, copy-ready **message for the friend**: what he needs to have, the commands in order, and what he must never do (share the keys, open the database port, turn off the access code).
