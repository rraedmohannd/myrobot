# Running the Smart Campus Robot backend on a server

> **This guide is for a server dedicated to this project (it takes ports 80/443 and changes the firewall).
> On a server that already runs other sites, use `docs/DEPLOY_GUEST.md` instead (guest mode, no host changes).**

This guide is for the server administrator. You do not need to know the project. You need `sudo` on a Linux server
and about one hour. Mohannd (the student who owns the project) gives you the access and the data described in section 2.

**What gets installed:** a PostgreSQL database, the backend (a Python web app), Caddy (the HTTPS web server, which gets
its certificate from Let's Encrypt by itself) and a backup service. Everything runs in Docker, in one Compose project
called `smart-campus-prod`, from the folder `/opt/smart-campus-robot`. The robot (a Raspberry Pi) connects to it over
the internet on `wss://<domain>/ws/robot`.

**Status of this guide (honest note):** the install was rehearsed **only in containers** (Ubuntu 24.04 and Debian 12 in
Docker, with Caddy's internal test certificate). It has **not** yet run on a real server. Not tested anywhere yet: a real
Let's Encrypt certificate, real systemd, the SSH port check against a real `sshd`, the nginx variant (section 5) and the
interactive questions (domain, hidden key, `YES`). If something differs from this guide, tell Mohannd.

Placeholders used below: `<domain>` (the server's public name), `<server-ip>` (its IPv4 address), `<ssh-user>` (your
login on the server), `<ref>` (the release tag Mohannd gives you, for example `v0.6.0`), `<stamp>` (a date like
`20270110-030000`).

Every server script has `--help` (for example `sudo bash /opt/smart-campus-robot/deploy/server/update.sh --help`).

---

## 1. Server requirements

| | Minimum |
|---|---|
| Operating system | **Ubuntu 24.04** or **Debian 12 / 13** (the script also accepts Ubuntu 22.04; others are refused unless `--force`) |
| CPU | 2 vCPU, **amd64 or arm64** |
| Memory | 4 GB RAM |
| Disk | 20 GB free |
| Network | a public IPv4 address; ports **80** and **443** reachable from the internet (also in the hosting provider's own firewall, if it has one) |
| Access | SSH with a key, a user with `sudo` |

A domain name is optional. Without one, the install uses `<ip-with-dashes>.sslip.io` (for example `203-0-113-7.sslip.io`),
a free name that points to the server's address, and still gets a real certificate.

Ports 80 and 443 must be free (no other web server). If another web server already uses them, see section 5.

## 2. What Mohannd sends you

1. **Access to the code repository** (GitHub, `rraedmohannd/myrobot`). Two options:
   - **Option A, recommended: a read-only deploy key.** It gives this server read access to this one repository and nothing else.
     1. On the server, make a key (no passphrase, so updates can run unattended):
        ```bash
        sudo ssh-keygen -t ed25519 -N '' -C 'smart-campus-deploy' -f /root/.ssh/smart-campus-deploy
        sudo cat /root/.ssh/smart-campus-deploy.pub
        ```
     2. Send Mohannd the **`.pub` line only** (it is a public key, not a secret). Never send the file without `.pub`.
     3. Mohannd adds it in GitHub → the repository → **Settings → Deploy keys → Add deploy key**, with **"Allow write access" left off**.
   - **Option B: you as a collaborator with read access.** Mohannd invites your GitHub account. Then you clone with an
     SSH key that is registered on **your** GitHub account. Less good: that key reaches everything your account can reach.
2. **The release to install** (`<ref>`, a tag such as `v0.6.0`). Always use the tag he names.
3. **The data bundle**: one file `smart-campus-bundle-YYYYmmdd-HHMMSS.tar.gz` (about 90 MB; the knowledge base, media
   and speech cache, no passwords or keys). Mohannd copies it to the server with `scp`, for example:
   ```bash
   # on Mohannd's laptop
   scp backups/smart-campus-bundle-<stamp>.tar.gz <ssh-user>@<server-ip>:~/
   ```
   For that he needs an SSH login on the server (you add his public key), or he sends the file to you another private way.
4. **The domain name**, if there is one. Before the install, its DNS **A record** must point to `<server-ip>`.
5. **The OpenAI API key.** This is the one secret that costs money.
   - It should be a **separate key for this server**, made by Mohannd in the OpenAI dashboard, with a **monthly spending limit**.
   - Best: **Mohannd types it himself** when the install asks for it (hidden input), for example while you share the
     terminal session.
   - Otherwise he sends it **separately** from everything else, through a password manager share or an encrypted message.
     **Never by chat or e-mail in clear text.**
   - You can also install without a key and add it later (section 3.1 and section 9).

## 3. First install

### 3.0 Get the code

The install script runs from a checkout of the repository, so clone it first, into `/opt/smart-campus-robot`.

```bash
sudo apt-get update && sudo apt-get install -y git

# Option A (deploy key from section 2):
sudo git -c core.sshCommand="ssh -i /root/.ssh/smart-campus-deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
    clone --branch <ref> git@github.com:rraedmohannd/myrobot.git /opt/smart-campus-robot
# remember the key for later updates (update.sh runs "git fetch"):
sudo git -C /opt/smart-campus-robot config core.sshCommand \
    "ssh -i /root/.ssh/smart-campus-deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
```
With option B use the path of your own key instead of `/root/.ssh/smart-campus-deploy`. If Mohannd tells you the
repository is public, `sudo git clone --branch <ref> https://github.com/rraedmohannd/myrobot.git /opt/smart-campus-robot`
is enough.

### 3.1 `install.sh`

```bash
sudo bash /opt/smart-campus-robot/deploy/server/install.sh --domain <domain>
```
Leave out `--domain <domain>` if there is no domain: it then asks, and an empty answer means `sslip.io`.

What it does (each step is safe to run again):
1. installs `ca-certificates curl git openssl jq ufw unattended-upgrades`;
2. installs Docker Engine and the compose plugin from **Docker's own apt repository** (it stops if the distribution's
   `docker.io` or `podman-docker` packages are installed: remove them, or use `--skip-docker-install` if Docker is already set up);
3. **firewall (ufw):** allows SSH, 80/tcp, 443/tcp, 443/udp, and **denies everything else incoming**. It refuses to
   continue if `sshd` listens on another port than the one it keeps open (default 22; use `--ssh-port N`);
4. turns on automatic security updates (Docker itself is not upgraded automatically);
5. keeps the checkout from step 3.0;
6. creates the settings file **`/opt/smart-campus-robot/deploy/server/.env`** with **newly generated** passwords and
   tokens (owner root, mode 600), sets the domain, and **asks for the OpenAI key with hidden input** (Mohannd types it;
   empty = no key for now);
7. builds and starts the stack, waits for the health check, and prints the addresses and the next steps.

At the end it shows the **access code for the visitor pages once** on the terminal (it is not written to the log). It is
also in `.env` under `SITE_ACCESS_CODE`. Give it to Mohannd.

All options (from `install.sh --help`):

| Option | Meaning |
|---|---|
| `--domain NAME` | the public name (DNS A record to this server); default: ask, or sslip.io |
| `--no-caddy` | behind an existing web server (nginx), see section 5 |
| `--repo URL` | git URL to clone (only used when `/opt/smart-campus-robot` does not exist yet) |
| `--deploy-key FILE` | SSH private key of a read-only deploy key (for `git@` URLs; only used together with that clone) |
| `--ref REF` | branch or tag to clone (default `main`; only used for that clone) |
| `--ssh-port N` | the SSH port to keep open in the firewall (default 22) |
| `--ask-openai-key` | ask for the OpenAI key again even if `.env` already has one |
| `--skip-docker-install` | Docker is already installed |
| `--skip-firewall` | do not touch ufw |
| `--no-start` | prepare everything but do not build or start the stack |
| `--yes` | no questions: domain from `--domain` or sslip.io, OpenAI key from the file named by the environment variable `OPENAI_API_KEY_FILE` (or left empty) |
| `--force` | run on an untested operating system |

The install log is `/var/log/smart-campus-install.log` (no secrets in it). The first build takes several minutes.

### 3.2 `restore-bundle.sh` (the knowledge base)

```bash
sudo bash /opt/smart-campus-robot/deploy/server/restore-bundle.sh ~/smart-campus-bundle-<stamp>.tar.gz
```
It checks the SHA-256 of every file in the bundle, refuses damaged or unexpected files, refuses a bundle from newer code,
and refuses a database that already holds knowledge. Then it restores the database, the media and the speech cache,
restarts the backend and prints one line per table:
```
   table                                  bundle   restored  result
   chunks                                    121        121  OK
   ...
== bundle restored, all 22 table counts match. Next: create-admin.sh NAME
```
Expected: every line `OK`, exit code 0. Any `DIFF` → exit code 1: send the output to Mohannd.

To load a newer bundle over an existing one later: add `--replace` (it asks you to type `YES`, or add `--yes`, and makes
a backup first).

Afterwards, keep the bundle file only as root (`sudo mv ~/smart-campus-bundle-*.tar.gz /root/ && sudo chmod 600 /root/smart-campus-bundle-*.tar.gz`) or delete it.

### 3.3 `create-admin.sh` (a login for the admin panel)

Admin logins are not in the bundle. Make one per person (Mohannd at least):
```bash
sudo bash /opt/smart-campus-robot/deploy/server/create-admin.sh <name>
```
`<name>`: 2–64 letters, digits, `.`, `_` or `-`. It asks for the password twice (hidden, at least 10 characters), so run
it in an interactive SSH session. It prints `created admin user '<name>'. Log in at https://<domain>/admin`. Running it
again for the same name sets a new password. Without a terminal:
`printf '%s\n' "$PASSWORD" | sudo bash /opt/smart-campus-robot/deploy/server/create-admin.sh <name> --password-stdin`.

Better: let Mohannd run it himself and type his own password.

### 3.4 Checks (expected results)

Run these after 3.1–3.3. Commands with `curl` can run on the server or on any other computer.

| # | Check | Expected |
|---|---|---|
| 1 | `sudo bash /opt/smart-campus-robot/deploy/server/status.sh` | see the sample below: 4 containers `Up`, backend and db `(healthy)`, `HTTP 200: status=ok version=0.6.0`, a `paid_calls` line, a certificate with `issuer=` Let's Encrypt (or ZeroSSL) |
| 2 | `curl -sS https://<domain>/health` | `{"status":"ok","version":"0.6.0"}` and **no certificate warning** |
| 3 | `curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' https://<domain>/robot` | `303 https://<domain>/access?next=%2Frobot` (the robot page needs the access code) |
| 4 | browser: `https://<domain>/access` | a page asking for the access code (Arabic first, then English); the code from 3.1 opens the robot page |
| 5 | browser: `https://<domain>/admin` | the admin login; the user from 3.3 can log in; **System** tab → "Server" card shows production mode **on** and fake providers **off** |
| 6 | `curl -sS -o /dev/null -w '%{http_code}\n' https://<domain>/docs` | `404` (API docs are off in production) |
| 7 | WebSocket through the proxy, **without** the robot token (command below) | `403` (refused) |
| 8 | WebSocket **with** the robot token (command below) | `101` (switched protocols); curl then waits until `--max-time` ends it, a timeout message after `101` is normal |
| 9 | `sudo ss -tlnp` | listening on all addresses only: `22` (sshd), `80` and `443` (docker-proxy). Nothing on 5432 or 8000 |
| 10 | `sudo ufw status verbose` | `Status: active`, default deny incoming, allow 22/tcp (or your SSH port), 80/tcp, 443/tcp, 443/udp |
| 11 | `sudo bash /opt/smart-campus-robot/deploy/server/backup-now.sh` | ends with the newest `db-<stamp>.dump` and `files-<stamp>.tar.gz` and a `last-backup.json` line |
| 12 | Mohannd sets the robot to `wss://<domain>/ws/robot` (`docs/DEPLOY.md`, "Server mode") | the admin page shows the robot as connected |

WebSocket checks 7 and 8 (the token is read from `.env` and never printed):
```bash
WS='-sS -o /dev/null -w %{http_code}\n --http1.1 --max-time 5 -H Connection:Upgrade -H Upgrade:websocket -H Sec-WebSocket-Version:13 -H Sec-WebSocket-Key:dGhlIHNhbXBsZSBub25jZQ=='
curl $WS https://<domain>/ws/robot                                   # 7: expect 403
curl $WS -H "X-Robot-Token: $(sudo grep '^ROBOT_TOKEN=' /opt/smart-campus-robot/deploy/server/.env | cut -d= -f2-)" \
     https://<domain>/ws/robot                                       # 8: expect 101
```

Sample `status.sh` output (from the container rehearsal; your domain, sizes and issuer differ):
```
== project smart-campus-prod, code <commit> <date> <message>
   domain: <domain>, Caddy: yes, backend image: smart-campus-backend:local
== containers
SERVICE   STATUS                    PORTS
backend   Up 21 seconds (healthy)   8000/tcp
backup    Up 21 seconds             5432/tcp
caddy     Up 21 seconds             0.0.0.0:80->80/tcp, ... 0.0.0.0:443->443/tcp, ... 0.0.0.0:443->443/udp, ...
db        Up 27 seconds (healthy)   5432/tcp
== backend health
   HTTP 200: status=ok version=0.6.0 database=None pgvector=None migration=None
== disk
   (df -h / and docker system df)
== last backup
   none yet (the first nightly backup runs at BACKUP_HOUR, or run backup-now.sh)
== paid OpenAI calls today
   {"date": "2026-11-19", "paid_calls": 0, "limit": 500}
== HTTPS certificate
   notAfter=<date>
   issuer=<Let's Encrypt ...>
   (Caddy renews it by itself about 30 days before it expires)
```
`database=None ...` in the health line is normal in production: the public health answer shows only `status` and
`version`. The `5432/tcp` and `8000/tcp` in the PORTS column are internal ports of the containers, not published ones.

## 4. Running Docker Compose by hand

The scripts never call `docker compose` directly. They use a helper `dc` from `deploy/server/lib.sh`, which runs:
```
docker compose -p smart-campus-prod --env-file /opt/smart-campus-robot/deploy/server/.env \
    -f /opt/smart-campus-robot/docker-compose.prod.yml --profile caddy ...
```
(in nginx mode, `-f /opt/smart-campus-robot/docker-compose.prod.nginx.yml` instead of `--profile caddy`).

Two ways to use it yourself. **Always name the project (`-p smart-campus-prod`)**; never run a bare `docker compose`.
```bash
# 1. through the scripts' own helper (picks Caddy or nginx mode from .env by itself):
sudo bash -c 'source /opt/smart-campus-robot/deploy/server/lib.sh && dc ps'

# 2. written out (Caddy mode):
cd /opt/smart-campus-robot
sudo docker compose -p smart-campus-prod --env-file deploy/server/.env -f docker-compose.prod.yml --profile caddy ps
```
The services are `db`, `backend`, `caddy` and `backup`; the containers are `smart-campus-prod-<service>-1`.

## 5. A web server is already running (nginx variant)

Only if ports 80/443 are already used by nginx for other sites on this machine (if the machine is shared with other
projects in general, use `docs/DEPLOY_GUEST.md` instead). Then the stack runs **without Caddy**: the backend listens on
`127.0.0.1:8000` only, and your nginx does HTTPS and forwards to it.

1. Install with `--no-caddy` (it writes `USE_CADDY=false` into `.env`):
   ```bash
   sudo bash /opt/smart-campus-robot/deploy/server/install.sh --domain <domain> --no-caddy
   ```
   The firewall step still allows 80/443 (nginx needs them); add `--skip-firewall` if you manage the firewall yourself.
2. The nginx site (from `deploy/server/nginx-site.conf.example`; replace every `robot.example.org` with `<domain>`):
   ```bash
   sudo cp /opt/smart-campus-robot/deploy/server/nginx-site.conf.example /etc/nginx/sites-available/smart-campus
   sudo nano /etc/nginx/sites-available/smart-campus
   sudo ln -s /etc/nginx/sites-available/smart-campus /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   ```
   Keep from the example: the WebSocket upgrade headers on `location /ws/`, `proxy_read_timeout 3600s` there,
   `X-Forwarded-For $remote_addr` (overwritten, so a client cannot fake its address), `proxy_buffering off`,
   `client_max_body_size 60m`, and **`access_log off`** (the robot's URL `/ws/robot?token=...` carries its token; never
   log query strings).
3. The certificate with certbot (it adds the 443 block and the http → https redirect itself; renewal is automatic):
   ```bash
   sudo apt install certbot python3-certbot-nginx
   sudo certbot --nginx -d <domain>
   sudo certbot renew --dry-run
   ```
4. Check: `curl -sS https://<domain>/health` → `{"status":"ok","version":"0.6.0"}`, then the checks of section 3.4.
   `status.sh` says the certificate is "not managed here" in this mode.

The backend trusts the forwarded client address only from `172.31.240.1` (the Docker bridge gateway, where connections
to `127.0.0.1:8000` appear to come from; setting `FORWARDED_ALLOW_IPS`). This mode is **not yet tested on a real Linux
host**; if every visitor shows the same address in the rate limits, tell Mohannd. Never set `BACKEND_BIND` to
`0.0.0.0:...`: it would bypass nginx and HTTPS.

Decide the mode at install time. Switching later means stopping Caddy and editing `USE_CADDY` by hand: ask Mohannd.

## 6. Updates

Mohannd tells you when there is a new release and its tag.
```bash
sudo bash /opt/smart-campus-robot/deploy/server/update.sh --ref <ref>
```
| Form | Meaning |
|---|---|
| `update.sh` | pull the current branch (fast-forward only). Does not work when the checkout is at a tag (it says "pass --ref REF"), which is the normal case when you cloned a tag |
| `update.sh --ref <ref>` | a tag, a branch or a commit |
| `update.sh --ref <ref> --image ghcr.io/<owner>/smart-campus-backend:<ref>` | use the ready-made image built by GitHub Actions instead of building on the server (faster, less memory). If the image is private: `sudo docker login ghcr.io` first, with a token that can only read packages (from Mohannd) |

Steps: backup → keep the running image as `smart-campus-backend:rollback` → git fetch and checkout → build (or pull
with `--image`) → start (the backend migrates the database) → health check.

**If the health check fails, it rolls back by itself:** the previous image, commit and `BACKEND_IMAGE` setting come back,
the stack restarts, and the script exits 1. Database migrations are **not** reversed; the script prints the backup stamp
from the first step and the command to restore it (`restore-backup.sh <stamp>`). Tell Mohannd before restoring.

Notes:
- `update.sh` refuses to run if files in the checkout were edited by hand (it lists them). `deploy/server/.env` is not
  part of the checkout, so editing it is fine.
- Once you use `--image`, its name is stored as `BACKEND_IMAGE` in `.env`; keep using `--image` for later updates.
- `--simulate-failed-health` is for testing the rollback only. Do not use it on the live server.

## 7. Backups and restore

**Automatic:** the `backup` service makes a backup every day at `BACKUP_HOUR` (default **3**, UTC) and deletes backups
older than `BACKUP_KEEP_DAYS` (**14** days). Each backup is:
- `db-<stamp>.dump`: the whole database (`pg_dump` custom format);
- `files-<stamp>.tar.gz`: media and speech cache;
- `last-backup.json`: time and sizes of the newest backup (also shown by `status.sh` and on the admin "Server" card).

**Where:** in the Docker volume `smart-campus-prod_backups` (normally `/var/lib/docker/volumes/smart-campus-prod_backups/_data`
on the host), **on the same server**. A backup on the same disk does not protect against losing the server.

**Copy backups off the server** (at least weekly, and before any risky change):
```bash
sudo bash /opt/smart-campus-robot/deploy/server/restore-backup.sh --list      # see the stamps
sudo docker cp smart-campus-prod-backup-1:/backups/db-<stamp>.dump    /root/
sudo docker cp smart-campus-prod-backup-1:/backups/files-<stamp>.tar.gz /root/
# then from another machine: scp <ssh-user>@<server-ip>:<path> .   (keep them private: they hold the conversation texts)
```

**Backup now:**
```bash
sudo bash /opt/smart-campus-robot/deploy/server/backup-now.sh
```

**Restore** a backup (replaces the live database and files; everything changed after that backup is lost):
```bash
sudo bash /opt/smart-campus-robot/deploy/server/restore-backup.sh --list
sudo bash /opt/smart-campus-robot/deploy/server/restore-backup.sh <stamp>          # asks you to type YES
sudo bash /opt/smart-campus-robot/deploy/server/restore-backup.sh <stamp> --yes    # without the question
```
It makes a fresh backup first (so the restore itself can be undone), stops the backend, restores, starts and
health-checks it, and prints the number of sources and chunks. Talk to Mohannd before a restore.

## 8. Logs and disk space

**Logs** (the exact form the scripts use, see section 4):
```bash
sudo bash -c 'source /opt/smart-campus-robot/deploy/server/lib.sh && dc logs --tail 200 backend'
sudo bash -c 'source /opt/smart-campus-robot/deploy/server/lib.sh && dc logs -f backend'        # follow, Ctrl+C to stop
sudo bash -c 'source /opt/smart-campus-robot/deploy/server/lib.sh && dc logs --tail 100 caddy'  # certificates
sudo bash -c 'source /opt/smart-campus-robot/deploy/server/lib.sh && dc logs --tail 100 backup'
```
Written out (Caddy mode): `cd /opt/smart-campus-robot && sudo docker compose -p smart-campus-prod --env-file deploy/server/.env -f docker-compose.prod.yml --profile caddy logs --tail 200 backend`.

- There is deliberately **no web access log** (Caddy and the nginx example both have it off), so the robot token and the
  visitors' addresses are not stored. Caddy logs only its own events (certificates, errors).
- Secrets are removed from the backend's log lines (`token=***`).
- Each container's log is limited to 3 × 10 MB, so logs cannot fill the disk.
- The install log: `/var/log/smart-campus-install.log`.

**Disk space:**
```bash
df -h /
sudo docker system df
```
What grows: the database (slowly: conversation texts are deleted after `CONVERSATION_RETENTION_DAYS`=30), the backups
(14 days), and Docker build cache and old images after updates. To free build cache: `sudo docker builder prune`.
Keep the image tagged `smart-campus-backend:rollback` (the last update's way back). Never remove the
`smart-campus-prod_*` volumes: they hold the data.

## 9. Changing (rotating) keys and passwords

All keys are in **`/opt/smart-campus-robot/deploy/server/.env`** (root only, mode 600). Edit it with
`sudo nano /opt/smart-campus-robot/deploy/server/.env`. Never copy it anywhere, never send it by chat. A new random value:
```bash
openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-48; echo
```
After editing, apply it (recreates the containers with the new settings):
```bash
sudo bash -c 'source /opt/smart-campus-robot/deploy/server/lib.sh && dc up -d --force-recreate backend backup'
```

| Key in `.env` | What it is | Also change | Effect |
|---|---|---|---|
| `OPENAI_API_KEY` | the paid OpenAI key | — | Easiest: `sudo bash /opt/smart-campus-robot/deploy/server/install.sh --ask-openai-key` (Mohannd types the new key hidden; it keeps all other settings and restarts the stack; repeat `--ssh-port N`, `--skip-firewall` if you used them). Then Mohannd **revokes the old key in the OpenAI dashboard** (API keys → delete). If the key may have leaked: revoke it **first**. |
| `ROBOT_TOKEN` | the robot's password for `/ws/robot` | **on the Pi**: `ROBOT_TOKEN` in `/etc/smart-campus-robot/robot.env`, then `sudo systemctl restart robotd`; and in the laptop's `.env` if the laptop is the robot's fallback (Mohannd does this) | the robot is disconnected until the Pi has the new value |
| `SITE_ACCESS_CODE` | the code visitors type on `/access` | give the new code to Mohannd | new visitors need the new code |
| `SECRET_KEY` | signs the visitor cookies | — | every visitor must type the code again |
| `ADMIN_TOKEN` | only for scripts (`ADMIN_TOKEN_SCRIPTS=false`, so unused) | — | none for people |
| `POSTGRES_PASSWORD` | the database password | **first** change it inside the database, see below | changing only `.env` breaks the backend's database connection |
| admin passwords | not in `.env` | — | `create-admin.sh <name>` again sets a new one |

The database password (rarely needed; it never leaves the server and the database has no open port):
```bash
# robot and smart_campus are the defaults of POSTGRES_USER and POSTGRES_DB in .env; use yours if they differ
sudo bash -c 'source /opt/smart-campus-robot/deploy/server/lib.sh && dc exec db psql -U robot -d smart_campus'
```
At the `smart_campus=#` prompt type `\password robot`, then the new password twice (letters and digits only, at least
16 characters), then `\q`. Put the same value in `POSTGRES_PASSWORD` in `.env`, then
run `dc up -d --force-recreate backend backup` as above. Do not use `--force-recreate db` for this: the database keeps its
own copy of the password.

## 10. Security notes

- **SSH keys only.** Turn off password logins for SSH (`PasswordAuthentication no`), and test a new SSH session with
  your key **before** closing the current one. The scripts do not change SSH settings.
- **No other ports.** The firewall allows only SSH, 80 and 443. Docker-published ports **bypass ufw**, so never add a
  `ports:` entry to the compose files, and **never publish the database port (5432)**. The backend is published only in
  nginx mode, and only on `127.0.0.1`.
- **Keep the access code on.** `PUBLIC_PAGES=true` with a `SITE_ACCESS_CODE`: the visitor pages spend the OpenAI credit,
  so without the code anyone on the internet could. Give the code only to people at the demo. `PUBLIC_PAGES=false` turns
  the visitor pages off completely (only the robot, with its token, works).
- **The daily cap.** `DAILY_PAID_CALL_LIMIT` (default **500** paid OpenAI calls per day for the whole server, stored in the
  database, so a restart does not reset it). Over it, the robot answers with a polite "offline" reply and the admin
  "Server" card shows a warning. There are also per-minute limits per address (`RATE_LIMIT_PAID_PER_MIN`=12,
  `RATE_LIMIT_ROBOT_PER_MIN`=60) and an admin login lockout (5 failures → 15 minutes). The OpenAI key itself also has a
  monthly limit (set by Mohannd in the OpenAI dashboard).
- **Production checks.** With `APP_ENV=production` the backend refuses to start with weak or missing secrets and says
  which setting is wrong (never its value). Keep `FAKE_PROVIDERS=false` and `ADMIN_TOKEN_SCRIPTS=false`.
- **Keep one worker.** `WEB_CONCURRENCY=1`: the live robot link, the rate limits and the login lockout live inside the
  process; several workers would split them.
- **Root can read the data.** Anyone with root on this server can read the database (conversation texts, no audio or
  images). This is stated in the project's privacy notes.
- **Safety does not depend on this server.** If you stop the backend, the robot stops roaming by itself and stays safe.
  Emergency stop of the whole service:
  `sudo bash -c 'source /opt/smart-campus-robot/deploy/server/lib.sh && dc stop backend'` (start again with `dc up -d`).
- **Who to call: Mohannd**, the project owner, if anything looks wrong: paid calls rising with nobody at the demo, an admin
  login you do not recognise, a failed update or restore, a disk filling up, or a possible leak of `.env`. In case of a
  possible leak, the OpenAI key is revoked first (section 9).

## 11. Troubleshooting

**The certificate is not issued** (browser warning, `status.sh` says "no certificate served yet"):
- Look at `dc logs --tail 100 caddy` (section 8).
- DNS: `dig +short <domain>` (or `getent hosts <domain>`) must give `<server-ip>`. A new DNS record can take some time.
  With sslip.io this is always right.
- Ports **80 and 443 must be reachable from the internet**: `sudo ufw status`, and the hosting provider's firewall or
  security group. Test from another computer: `curl -I http://<domain>` should answer (a redirect to https).
- Rate limits: Let's Encrypt limits repeated failures and repeated certificates for the same name (for example 5 failed
  validations per hour). Do not restart Caddy in a loop; fix DNS/ports and wait an hour. Caddy also tries ZeroSSL by itself.
- After a fix: `sudo bash -c 'source /opt/smart-campus-robot/deploy/server/lib.sh && dc restart caddy'`.

**Port 80 or 443 is already in use** (Caddy does not start, `address already in use`):
- `sudo ss -tlnp | grep -E ':(80|443)\b'` shows which program holds it.
- If it is a web server that should stay, use the nginx variant (section 5, `install.sh ... --no-caddy`). If it is an
  unused default web server (for example a default nginx or apache page), stop and disable it
  (`sudo systemctl disable --now nginx` or `apache2`), then start the stack again:
  `sudo bash -c 'source /opt/smart-campus-robot/deploy/server/lib.sh && dc up -d'`.

**The robot's WebSocket is blocked** (the robot does not connect, check 8 does not give `101`):
- `403` in check 8 → wrong `ROBOT_TOKEN` on the Pi (section 9).
- `400`/`404`/connection closed at once behind nginx → the `location /ws/` block lacks the `Upgrade` and `Connection`
  headers or `proxy_http_version 1.1` (section 5).
- It connects but drops every minute or so → a proxy timeout: `proxy_read_timeout 3600s` and `proxy_send_timeout 3600s`
  in nginx; with a CDN or another proxy in front, WebSockets must be allowed there and its idle timeout long enough.
- `502`/`503` → the backend is restarting or down: `status.sh`, `dc logs backend`.
- On the Pi: `journalctl -u robotd -f` (certificate errors there usually mean a wrong clock on the Pi or a missing certificate here).

**Out of memory** (containers restart, the build stops, the server is slow):
- `free -h`, `sudo docker stats --no-stream`, `sudo dmesg | grep -i -E 'oom|killed process'`.
- Keep `WEB_CONCURRENCY=1` in `.env`.
- Building the image on the server needs the most memory: use the ready-made image (`update.sh --ref <ref> --image ...`, section 6).
- Add swap (2 GB):
  ```bash
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```

**Anything else:** run `sudo bash /opt/smart-campus-robot/deploy/server/status.sh` and the last 200 lines of the backend
log (section 8), and send both to Mohannd. Neither prints a secret, but check before you send.
