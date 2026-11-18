# Sprint 6 addendum: "guest" mode, deploying beside another live project

Added 2026-11-19. This changes the deployment target of Sprint 6. The Caddy path (ports 80/443, automatic HTTPS) stays
in the repo for a dedicated server later, but it must **not** be used on the server we actually have.

## The situation

The server (rented in Germany, 2 vCPU, about 3.8 GB RAM, 40 GB disk) already runs a live product for a paying client.
Its owner wrote rules for a second project. They are kept **outside this repository**:

    C:\Users\hanan\Documents\server-notes\GUEST_SERVER_RULES.md

Read that file first, completely. It wins over everything in this repo.

The rules file names the other project's folder, containers, ports and addresses, and the server's IP address. **Never copy
any of those into a tracked file**: the repository is public at the moment, and they are not ours to publish. In tracked files
write placeholders: `<server-ip>`, `<ssh-user>`, `<the other project>`.

### The rules in short (generic, safe to keep here)

- Our folder is `/srv/guest`. Our Compose project is `guest`, named on **every** command (`docker compose -p guest ...`).
- Ports 8100–8199 only. Never 80 or 443.
- A memory limit on every container; **all of ours together at most 1 GB**. `restart: unless-stopped`.
- Logs limited: json-file, `max-size: 10m`, `max-file: 3`.
- No host-wide action of any kind:
  - no `apt`, no Docker install or upgrade, no `daemon.json`;
  - no firewall changes, no cron, no users, no SSH settings, no reboot;
  - no `prune` of any kind;
  - no `docker compose down` without `-p guest`;
  - no `rm -rf` outside `/srv/guest`.
- HTTPS or a domain name can only come through the other project's proxy. Only its owner may change that proxy, so for us
  it is a request to him, never a change we make.
- The other project's containers: never `docker inspect` in full (it prints their environment, which holds secret keys),
  never `logs`, `exec`, `stop` or `restart`. Allowed: `docker ps`, `docker compose ls`, and only this exact form, to compare
  start times before and after:

      docker inspect --format '{{.Name}} {{.State.Status}} {{.State.StartedAt}} {{.RestartCount}}' <name>

## Why plain `http://<server-ip>:8100` is not enough

Our production mode sets Secure cookies (visitor code, admin login), and browsers allow the microphone only on HTTPS or
`localhost`. On plain HTTP at a public address, the access page, the admin login and `/voice` would not work. A switch that
weakens this would also send the admin password and the robot token in clear text over the internet. So: **no "insecure
HTTP" switch.** Instead there are two phases.

- **Phase A, now (needs nothing from the owner).** The backend is published on `127.0.0.1:8100` on the server, and no port is
  open to the internet. Mohannd reaches it through an SSH tunnel:

      ssh -N -L 8100:127.0.0.1:8100 <ssh-user>@<server-ip>

  Then he opens `http://localhost:8100` on the laptop. Browsers treat `localhost` as a secure context, so the microphone,
  Secure cookies, login and WebSockets work, and the traffic is encrypted by SSH. Production checks stay on, with
  `PUBLIC_BASE_URL` set to the future HTTPS address.

  The robot hardware has not arrived, so the robot does not need the server yet.

- **Phase B, later (after the owner agrees).** The owner adds one site to his proxy that forwards a new `sslip.io` name to our
  port. Then we get public HTTPS for visitors and `wss://` for the robot. We prepare the request and the change on our side;
  we never touch his proxy.

## Tasks

### G1. `docker-compose.guest.yml`
A standalone file, not an override of `docker-compose.prod.yml`: no Caddy and no fixed subnet, so it cannot clash with the
other project's networks.

- **Project name.** Top-level `name: guest`, and every script also passes `-p guest`.
- **Services.**
  - `db`, `backend` and `backup`, with the same hardening as prod:
    - backend as uid 10001, read-only root filesystem, tmpfs `/tmp`;
    - `cap_drop: ALL`, `no-new-privileges`;
    - healthchecks;
    - named volumes, created by Compose as `guest_*`.
  - No database port is published.
- **Limits.**
  - Every service gets `mem_limit` and `cpus`. Start at backend 512m / 1.0, db 320m / 0.5, backup 128m / 0.25: total
    960 MB, under 1 GB. Measure in G6 and adjust within 1 GB.
  - If the backend cannot stay under its limit with real use, **stop and report**; never go over 1 GB in total.
  - Tune Postgres for small memory: `shared_buffers=64MB`, `max_connections=20`, a small `work_mem`.
- **Logging and restart.** json-file 10m × 3 on every service, `restart: unless-stopped`.
- **Backend port.** `${GUEST_BIND:-127.0.0.1}:${GUEST_PORT:-8100}:8000`. The default is localhost only (phase A). Any other
  bind address is a phase B decision, written in `deploy/server/guest/PROXY_REQUEST.md`.
- **Trusted proxy addresses.** `FORWARDED_ALLOW_IPS` trusts nothing in phase A, because there is no proxy. In phase B it
  trusts only the address the owner's proxy connects from.

### G2. Tunnel access works fully
On the rehearsal stack, through `http://localhost:<port>`, check each of these, each with a test or a recorded manual check:

- the `/access` code page and its cookie;
- admin login and lockout;
- `/voice` (the page loads with microphone permission allowed);
- `/robot`;
- `POST /api/chat`;
- the WebSocket;
- the admin Server card.

If something refuses because the origin is not `PUBLIC_BASE_URL`, add an explicit setting `EXTRA_ALLOWED_ORIGINS`: empty by
default, accepting only `http://localhost:<port>` and `http://127.0.0.1:<port>` values, and logged at start-up. Do not add a
general insecure switch.

### G3. Phase B request, prepared only
Write `deploy/server/guest/PROXY_REQUEST.md` for the owner:

- **The Caddy site block he would add:**
  - `reverse_proxy` to our port;
  - **no `log` directive**, because the robot's WebSocket URL carries its token;
  - `request_body { max_size 60MB }`;
  - WebSockets need nothing extra in Caddy.
- **The options for how his proxy container reaches our port.** Examples: our port bound on an address his container can
  reach, or him connecting his proxy to a network we create. Let him choose. We do not inspect his containers to find out.
- **What we change on our side for each option:** `GUEST_BIND`, `FORWARDED_ALLOW_IPS`, `PUBLIC_BASE_URL`, and the robot's
  `BACKEND_URL=wss://...`.
- **A way to see the proxy's source address from our side only.** For example, a one-off admin-only endpoint or log line
  that shows the peer address of a test request, removed afterwards.

### G4. Scripts in `deploy/server/guest/`
All are bash, shellcheck-clean, and refuse to run outside `/srv/guest`. They never call `install.sh`: it installs packages,
enables a firewall with default deny and turns on automatic upgrades, all of which are forbidden there.

- **`preflight.sh` (read-only):**
  - runs `docker ps`, `docker compose ls`, `df -h`, `free -m` and `ss -tlnp`;
  - prints free disk, free memory and whether ports 8100–8199 are free;
  - exits non-zero if disk is under 6 GB, memory is under 1 GB, or our port is taken.
- **`snapshot-others.sh`:** for every running container **not** in project `guest`, prints only the exact inspect format
  above into `/srv/guest/state/others-<stamp>.txt`. `compare-others.sh` diffs two snapshots and shouts if anything
  restarted, stopped or changed its start time.
- **`load-image.sh` (image built on the laptop):** the backend image is built on the laptop for linux/amd64 and tagged
  `guest-backend:<version>`. It is moved with `docker save | gzip`, `scp`, then `docker load` on the server. Building on the
  server would load its 2 CPUs for minutes and slow down the client's product. Pulling from GHCR is fine later, if the
  package is private.
- **`install-guest.sh`:**
  - creates `/srv/guest` and an env file with generated secrets (`chmod 600`, never printed);
  - asks for the OpenAI key hidden; it must be the separate server key with a monthly spending limit;
  - starts the stack and waits for health.
- **`restore-bundle.sh`:** the existing logic, with `-p guest`.
- **`create-admin.sh`:** the password is typed by Mohannd, never by the assistant.
- **`update-guest.sh`:**
  - loads the new image, then `up -d`;
  - rolls back to the previous tag if health fails;
  - removes old **guest** images only by exact tag, never by prune.
- **`status-guest.sh`** gives a short status.
- **`uninstall-guest.sh`:**
  - runs `docker compose -p guest down`;
  - removes volumes and `/srv/guest` only with `--purge` and a typed confirmation.
- **A check in CI** (a small test or script) fails if any file under `deploy/server/guest/` contains a forbidden command:
  - any `prune`;
  - `systemctl`, `apt`, `ufw`, `iptables`, `reboot` or `shutdown`;
  - `daemon.json` or `crontab`;
  - a `compose` command without `-p guest`;
  - `rm -rf` on a path that does not start with `/srv/guest`.

### G5. Disk budget
Everything of ours together stays under 4 GB: images, volumes and backups. Report the estimate.

The bundle alone is about 90 MB, most of it the speech cache. If 14 days of daily file backups would pass the budget, back up
the database daily and the files weekly. The speech cache can be regenerated, but that costs money, so keep at least one copy.

### G6. Rehearsal on the laptop

- Use a separate project name (for example `guest-rehearsal`) with `docker-compose.guest.yml`, the real bundle and a
  throw-away env.
- **Never touch the developer stack or its database volume.** Compare its row counts before and after, as in P0.1.
- **Measure memory under load.** Run the 49 golden questions (`FAKE_PROVIDERS=true` is fine for load, plus a few real
  questions if a key is available). Record the peak `docker stats` per service. Confirm nothing hits its limit and nothing is
  OOM-killed.
- **Check the tunnel path (G2)** through `http://localhost:<port>`.
- **Test the scripts:** run `snapshot-others.sh` / `compare-others.sh` against a dummy container that you restart on purpose,
  so the alarm is proven.
- Remove the rehearsal project afterwards, with its own `-p` only.

### G7. `docs/DEPLOY_GUEST.md`
A step-by-step guide for Mohannd, with placeholders only (no IP, no names of the other project). It covers:

- SSH key setup;
- preflight;
- snapshot;
- loading the image;
- install, restore and admin;
- compare-others;
- opening the tunnel (Windows PowerShell command);
- update and rollback;
- uninstall;
- what to send the owner for phase B.

### G8. Release
The rule from P0.9 is unchanged: no merge to `main` and no tag while the repository is public.

**No connection to any server in this sprint.** The real deployment is a separate session with its own prompt.

## Log
Write everything under `## Guest mode (G1–G8)` in `docs/SPRINT_6_LOG.md`: what was done, measured numbers, what was not
verified, and questions.
