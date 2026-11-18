# Sprint 6 addendum: "guest" mode, deploying beside another live project

Updated 2026-11-19 02:25 (v3: route 2 decided). This changes the deployment target of Sprint 6. The Caddy path (ports 80/443, automatic HTTPS)
stays in the repo for a dedicated server later, but it must **not** be used on the server we actually have.

## The goal and the deadline

**Tomorrow, Mohannd gives his supervisor links (`/robot`, `/voice`, and possibly `/admin`). The supervisor opens them on his
own device, wherever he is, and tries the robot: speaking (microphone), listening, explainers.**

So we need a **public HTTPS** address within hours, not a tunnel that only Mohannd can use.

Mohannd works on a **desktop PC at home** (Windows, Docker Desktop). There is no laptop. Wherever older docs say "laptop",
read "this PC".

## Server facts (read-only check done 2026-11-19 02:15)

They are in `C:\Users\hanan\Documents\server-notes\SERVER_FACTS.md`, also outside the repo:

- the SSH user, `mohannd`: in the docker group, no sudo;
- the CPU: x86_64, so the image is built for linux/amd64;
- free disk and memory: 11 GB and 2.3 GB; both meet the rules;
- port 8100: free;
- the start-time baseline of the other project, already saved on the server.

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
  - no firewall changes beyond one guest port, no cron, no users, no SSH settings, no reboot;
  - no `prune` of any kind;
  - no `docker compose down` without `-p guest`;
  - no `rm -rf` outside `/srv/guest`.
- **HTTPS or a domain name needs the owner's OK.** Only he may change his proxy.
- The other project's containers: never `docker inspect` in full (it prints their environment, which holds secret keys),
  never `logs`, `exec`, `stop` or `restart`. Allowed: `docker ps`, `docker compose ls`, and only this exact form, to compare
  start times before and after:

      docker inspect --format '{{.Name}} {{.State.Status}} {{.State.StartedAt}} {{.RestartCount}}' <name>

## Why plain `http://<server-ip>:8100` is not enough

Our production mode sets Secure cookies (visitor code, admin login), and browsers allow the microphone only on HTTPS or
`localhost`. On plain HTTP at a public address:

- the access page and the admin login fail;
- the supervisor cannot speak to the robot;
- a weaker switch would send the admin password and the robot token in clear text.

**No "insecure HTTP" switch.**

## Two ways to get HTTPS without touching ports 80/443

**Decision (02:05): route 2 only.** Nobody will change the other project's proxy, so route 1 is cancelled for now. Do not
write `PROXY_REQUEST.md`. Route 1 stays described below only as a possible later step (L4).

1. **Route through the owner's Caddy (cancelled for now).**
   - The owner adds one site, for example `scr.<server-ip-dashed>.sslip.io`, with `reverse_proxy` to our port.
   - There is **no `log` directive**, because the robot's WebSocket URL carries its token.
   - The site sets `request_body { max_size 60MB }`. WebSockets need nothing extra in Caddy.
   - He decides how his proxy container reaches our port. Two examples: our port bound on an address his container can
     reach, or his proxy joining a network we create. We never inspect his containers to find out.
2. **Cloudflare quick tunnel inside our project (THE route: no ports at all).**
   - A `cloudflared` service in the `guest` project, under a Compose profile `tunnel` (image pinned to an exact version,
     `mem_limit` 64m). It runs `tunnel --no-autoupdate --url http://backend:8000`.
   - It only makes an outbound connection and gives an address like `https://<random>.trycloudflare.com`. No account and no
     domain are needed, and no port is opened.
   - **Limits** (Cloudflare docs):
     - the address changes every time the tunnel container is re-created;
     - no uptime guarantee;
     - at most 200 requests in flight;
     - **no Server-Sent Events**.

     Check that nothing we need uses SSE; WebSockets must work, so test them.
   - Because the address is known only after the tunnel starts, `tunnel-url.sh` does three things:
     - reads it from `docker compose -p guest logs cloudflared`;
     - writes it to `PUBLIC_BASE_URL` in `/srv/guest/.env`;
     - re-creates **only** the backend.

## Tasks, in order: T1–T5 are needed for tomorrow, L1–L4 come later

### T1. `docker-compose.guest.yml`
A standalone file, not an override of `docker-compose.prod.yml`: no Caddy.

- **Project name.** Top-level `name: guest`, and every script also passes `-p guest`.
- **Services.**
  - `db`, `backend` and `backup`, with the same hardening as prod:
    - backend as uid 10001, read-only root filesystem, tmpfs `/tmp`;
    - `cap_drop: ALL`, `no-new-privileges`;
    - healthchecks;
    - named volumes `guest_*`.
  - The optional `cloudflared` service (profile `tunnel`).
  - No database port is published.
- **Limits.**
  - Every service gets `mem_limit` and `cpus`. Start at backend 512m / 1.0, db 320m / 0.5, backup 96m / 0.25 and
    cloudflared 64m / 0.25: total 992 MB, under 1 GB. Measure in T4.
  - If the backend cannot stay under its limit with real use, **stop and report**; never go over 1 GB in total.
  - Tune Postgres for small memory: `shared_buffers=64MB`, `max_connections=20`, a small `work_mem`.
- **Logging and restart.** json-file 10m × 3 on every service, `restart: unless-stopped`.
- **Backend port.** `${GUEST_BIND:-127.0.0.1}:${GUEST_PORT:-8100}:8000`. In route 1 the deploy session sets `GUEST_BIND` to
  what the owner asks for. In route 2 the port can stay on 127.0.0.1, which is useful for checks over SSH.
- **Trusted proxy addresses.** `FORWARDED_ALLOW_IPS` trusts only the proxy in use: the owner's proxy address, or our
  `cloudflared` container.
- **Subnet.** If you pin a subnet so that `cloudflared` has a fixed address, use an unusual private /24 (for example in
  10.x). An overlap makes Compose fail without touching anything else, so then pick another; do not inspect other networks.

### T2. HTTPS end to end
Prove it on this PC with route 2: the rehearsal stack plus a real quick tunnel. Through the `https://...trycloudflare.com`
address, from a browser, check each of these:

- the `/access` code page and its cookie;
- admin login;
- `/voice` with the microphone allowed and a spoken question answered;
- `/robot` with a typed and a spoken question;
- the WebSocket;
- an explainer;
- `/media` videos.

(Route 1 is cancelled: no `PROXY_REQUEST.md`.)

### T3. Scripts in `deploy/server/guest/`
All are bash, shellcheck-clean, and refuse to run outside `/srv/guest`. They never call `install.sh`: it installs packages,
enables a firewall with default deny and turns on automatic upgrades, all forbidden there.

- **`preflight.sh` (read-only):**
  - runs `docker ps`, `docker compose ls`, `df -h`, `free -m` and `ss -tlnp`;
  - prints free disk, free memory and whether our port is free;
  - exits non-zero if disk is under 6 GB, memory is under 1 GB, or the port is taken.
- **`snapshot-others.sh`:** for every running container **not** in project `guest`, prints only the exact inspect format
  above into `/srv/guest/state/others-<stamp>.txt`. `compare-others.sh` diffs two snapshots and shouts if anything
  restarted, stopped or changed its start time.
- **`load-image.sh` (image built on this PC):** the backend image is built on this PC for linux/amd64 and tagged
  `guest-backend:<version>`. It is moved with `docker save | gzip`, `scp`, then `docker load` on the server. Building on the
  server would load its 2 CPUs for minutes and slow down the client's product. The pinned `cloudflared` and pgvector images
  are pulled on the server (small).
- **`install-guest.sh`:**
  - creates `/srv/guest` and an env file with generated secrets (`chmod 600`, never printed);
  - asks for the OpenAI key hidden; it must be the separate server key with a monthly spending limit;
  - starts the stack (with `--profile tunnel` for route 2) and waits for health.
- **`tunnel-url.sh`** (route 2), described above.
- **`restore-bundle.sh`:** the existing logic, with `-p guest`. Use the newest bundle from P0.5.
- **`create-admin.sh`:** the password is typed by Mohannd, never by the assistant. A second admin account for the supervisor
  must be possible.
- **`status-guest.sh`:**
  - health;
  - the public address;
  - today's paid calls against the cap;
  - memory per container (`docker stats --no-stream` for `guest` containers only).

### T4. Rehearsal on this PC (it is also the backup for tomorrow)

- Use a separate project name (for example `guest-rehearsal`) with `docker-compose.guest.yml`, the real bundle, real
  providers and the profile `tunnel`.
- **Never touch the developer stack or its database volume.** Compare its row counts before and after.
- **Measure memory.** Run the 49 golden questions and a few spoken ones, and record the peak `docker stats` per service.
  Confirm nothing hits its limit and nothing is OOM-killed.
- **Check T2** through the real `trycloudflare.com` address.
- **Prove the alarm.** Run `snapshot-others.sh` / `compare-others.sh` against a dummy container that you restart on purpose.
- **Leave the rehearsal stack stopped but intact,** and write the one-line command that starts it with a tunnel. **If the
  server is not ready tomorrow, this PC is the backup:** same production settings, access code and daily cap, as long as the
  PC stays on.

### T5. `docs/DEPLOY_GUEST.md` (short and exact)
Placeholders only (no IP, no names of the other project). It covers:

- SSH key setup on Windows;
- preflight;
- snapshot;
- loading the image;
- install, restore and admin accounts;
- route 1 or route 2;
- compare-others;
- the links to give the supervisor and how he enters the access code;
- the PC backup.

### Later (after tomorrow)

- **L1. `update-guest.sh`:**
  - loads the new image, then `up -d`;
  - rolls back to the previous tag if health fails;
  - removes old **guest** images only by exact tag, never by prune.
- **L1. `uninstall-guest.sh`:**
  - runs `docker compose -p guest down`;
  - removes volumes and `/srv/guest` only with `--purge` and a typed confirmation.
- **L2.** A check in CI that fails if any file under `deploy/server/guest/` contains a forbidden command:
  - any `prune`;
  - `systemctl`, `apt`, `ufw`, `iptables`, `reboot` or `shutdown`;
  - `daemon.json` or `crontab`;
  - a `compose` command without `-p guest`;
  - `rm -rf` on a path that does not start with `/srv/guest`.
- **L3. Disk budget:** everything of ours stays under 4 GB (images, volumes, backups). If 14 days of file backups would pass
  that, back up the database daily and the files weekly.
- **L4.** A stable address later: route 1 if the owner ever agrees, or a named Cloudflare tunnel on a domain of our own.
  Until then the quick-tunnel address changes whenever the `cloudflared` container is re-created, so `tunnel-url.sh`
  must print the current address clearly, and nothing may re-create that container without a reason.

**Release rule unchanged:** no merge to `main` and no tag while the repository is public.

**No connection to any server in this sprint.** The real deployment is a separate session with its own prompt.

## Log
Write everything under `## Guest mode` in `docs/SPRINT_6_LOG.md`: what was done, measured numbers, what was not
verified, and questions.
