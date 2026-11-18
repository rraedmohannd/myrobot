# Guest mode: the backend on the shared server

The backend runs on a rented server **beside another live project** (`<the other project>`). The owner's rules (kept
outside this repo) win over everything here. In short: everything of ours is in `/srv/guest`, every command names the
Compose project `guest`, our ports are 8100–8199 only, all our containers together use at most 1 GB, and nothing
host-wide is ever run (no sudo, no packages, no firewall, no prune). Background: `docs/SPRINT_6_GUEST.md`.

Placeholders: `<server-ip>`, `<ssh-user>`. Public HTTPS comes from a **Cloudflare quick tunnel** inside our project
(service `cloudflared`): no account, no port opened, nothing asked of the owner.

## Tomorrow with the supervisor (checklist)

1. On the server: `bash /srv/guest/app/deploy/server/guest/status-guest.sh` → backend `HTTP 200`, a public address
   `https://<words>.trycloudflare.com`, paid calls well under the cap.
2. Send him, by **private message**:
   - `https://<words>.trycloudflare.com/robot` (the robot screen; type or speak a question);
   - `https://<words>.trycloudflare.com/voice` (speaking test);
   - optional: `https://<words>.trycloudflare.com/admin` with **his own** admin account (name + password, see
     "Admin accounts");
   - the **access code** (`xxxx-xxxx-xxxx`) in a separate message.
3. What he does: open a link, type the access code once on the page that appears (`/access`; a cookie remembers it for
   12 hours), and **allow the microphone** when the browser asks.
4. Costs are capped: at most `DAILY_PAID_CALL_LIMIT` (500) paid OpenAI calls per day for everybody together, plus the
   monthly limit of the server's OpenAI key.
5. If the server is not ready or fails: the PC backup (last section) gives the same pages from this PC.

The quick-tunnel address is **new whenever the `cloudflared` container is re-created** (`tunnel.sh restart`,
`tunnel.sh down` + `up`, uninstall). Quick tunnels have no uptime guarantee (Cloudflare: meant for testing), at most
200 requests at a time and no Server-Sent Events. Fine for a demo; a stable address is a later step.

## 1. SSH key (already done)

The PC's ed25519 key is already authorised. Check (PowerShell):

```powershell
ssh <ssh-user>@<server-ip> true      # no password question, no error = OK
```

Only if a new PC needs one: `ssh-keygen -t ed25519` (Enter for the defaults, choose a passphrase), then give the
**public** file `%USERPROFILE%\.ssh\id_ed25519.pub` to whoever manages the server account. Never send the private file.

## 2. On the PC: image, app folder, bundle

In **Git Bash**, in the repository folder, on the branch that should run on the server:

```bash
docker build --platform linux/amd64 --build-context shared=shared --build-arg EXTRAS= -t guest-backend:0.6.0 backend
mkdir -p out
docker save guest-backend:0.6.0 | gzip > out/guest-backend-0.6.0.tar.gz
(cd out && sha256sum guest-backend-0.6.0.tar.gz > guest-backend-0.6.0.tar.gz.sha256)
git archive --format=tar.gz -o out/guest-app.tar.gz HEAD docker-compose.guest.yml deploy
ls backups/smart-campus-bundle-*.tar.gz      # the newest one is restored below
```

(`out/` holds no secret; delete it afterwards. `git archive` takes the **committed** files only.)
The same in PowerShell, if Git Bash is not at hand:

```powershell
docker save -o out\guest-backend-0.6.0.tar guest-backend:0.6.0
(Get-FileHash out\guest-backend-0.6.0.tar -Algorithm SHA256).Hash | Set-Content -Encoding ascii out\guest-backend-0.6.0.tar.sha256
```

(an uncompressed `.tar` of about 580 MB instead of about 150 MB; `load-image.sh` takes both).

Copy them (PowerShell or Git Bash):

```powershell
ssh <ssh-user>@<server-ip> "mkdir -p /srv/guest/app /srv/guest/images /srv/guest/bundles"
scp out/guest-app.tar.gz <ssh-user>@<server-ip>:/srv/guest/
scp out/guest-backend-0.6.0.tar.gz out/guest-backend-0.6.0.tar.gz.sha256 <ssh-user>@<server-ip>:/srv/guest/images/
scp backups/smart-campus-bundle-YYYYmmdd-HHMMSS.tar.gz <ssh-user>@<server-ip>:/srv/guest/bundles/
```

`/srv/guest` already exists and belongs to `<ssh-user>`. If it ever does not, ask the server owner to create it owned by
`<ssh-user>`: we never use sudo.

## 3. On the server: install

`ssh <ssh-user>@<server-ip>`, then:

```bash
tar -xzf /srv/guest/guest-app.tar.gz -C /srv/guest/app
G=/srv/guest/app/deploy/server/guest
bash $G/preflight.sh                  # read only; must end with "preflight OK"
bash $G/load-image.sh /srv/guest/images/guest-backend-0.6.0.tar.gz
bash $G/install-guest.sh              # asks for the OpenAI key (hidden)
```

- **preflight** runs `docker ps`, `docker compose ls`, `df -h`, `free -m`, `ss -tlnp` and stops below 6 GB disk,
  1 GB memory, or when port 8100 is taken. Nothing is changed.
- **load-image** checks the SHA-256 and that the file holds only `guest-backend:<version>`, then `docker load`.
- **install-guest**: creates `state/ bundles/ images/`, writes `/srv/guest/.env` (mode 600) with new secrets, takes a
  **snapshot** of the other project's start times (the first one is kept as the baseline), starts db, backend, backup
  and cloudflared, waits for health, writes the tunnel address into `PUBLIC_BASE_URL` (tunnel-url.sh) and checks that
  nothing of the other project changed. It prints the **visitor access code once**: note it in a safe place.
  The OpenAI key must be a **separate key for this server, with a monthly spending limit** set in the OpenAI dashboard.
  An existing `/srv/guest/.env` is kept as it is.

Then the data and the admin accounts:

```bash
bash $G/restore-bundle.sh /srv/guest/bundles/smart-campus-bundle-YYYYmmdd-HHMMSS.tar.gz
bash $G/create-admin.sh mohannd       # you type the password (hidden)
bash $G/create-admin.sh supervisor    # his own account; send him the password privately
bash $G/compare-others.sh             # must end with "unchanged"; exit code 1 = tell the owner at once
bash $G/status-guest.sh
```

`restore-bundle.sh` checks every SHA-256, refuses a database that already has knowledge (unless `--replace`, which makes a
backup first), restores, and prints a table of row counts (all `OK`). The same `create-admin.sh NAME` again sets a new
password for that account.

## 4. The public address

```bash
bash $G/tunnel.sh url        # print it
bash $G/tunnel.sh up         # start the tunnel if it is not running (a running one is never re-created)
bash $G/tunnel.sh restart    # re-create it: NEW address (only if it stopped working)
bash $G/tunnel.sh down       # stop it; the backend keeps running
```

After a new address: send the new links. The access code stays the same.

## 5. Checks over SSH (optional)

The backend is also on `127.0.0.1:8100` of the server, reachable only through SSH (PowerShell):

```powershell
ssh -N -L 8100:127.0.0.1:8100 <ssh-user>@<server-ip>
```

then open `http://localhost:8100/health` on the PC (Ctrl+C ends the tunnel).

## 6. The other project: before/after check

`compare-others.sh` compares the baseline snapshot with one taken now (only the start-time line the owner allows, never a
full inspect). It shouts if a container of the other project stopped, restarted or has a new start time, and exits 1:
then stop and tell the owner. Run it after every change. A new snapshot by hand: `snapshot-others.sh`.

## 7. PC backup (if the server is not ready)

On this PC, Docker Desktop running, in the repository folder (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\guest-rehearsal.ps1 up
```

It prints the public links and the access code. It works only while the PC is on and awake. Stop it with `... down`.

## Later

- **Update:** build and copy the new image as in step 2 (new version number), copy a new `guest-app.tar.gz` if
  `docker-compose.guest.yml` or the scripts changed and unpack it as in step 3, then
  `bash $G/update-guest.sh /srv/guest/images/guest-backend-<v>.tar.gz`. It makes a backup, switches `GUEST_IMAGE`,
  checks health and rolls back to the previous tag by itself if that fails (database migrations are not reversed: the
  backup is the way back). Old `guest-backend` images are removed one by one by exact tag. Manual rollback:
  `update-guest.sh --tag guest-backend:<old>`. After editing `/srv/guest/.env` by hand: `update-guest.sh --restart`.
  The tunnel is not re-created by an update, so the address stays.
- **Uninstall:** `uninstall-guest.sh` stops and removes our containers (data kept). `uninstall-guest.sh --purge` also
  deletes our volumes, our images (by tag) and `/srv/guest`, after you type `PURGE GUEST`.
- **A stable address:** a site on the owner's proxy (only if he ever agrees; no log directive, because the robot's
  WebSocket URL carries its token) or a named Cloudflare tunnel on a domain of our own.

## For maintainers

- The scripts refuse to run unless they are in `/srv/guest/app/deploy/server/guest`. `GUEST_TEST_ROOT=<folder named
  guest*>` replaces `/srv/guest` **for tests only** (never on the server).
- `deploy/server/guest/check-forbidden.sh` (CI) fails on any forbidden command in that folder: prune, host-wide commands,
  daemon.json/crontab, a compose call without `-p guest`, a recursive rm outside `/srv/guest`, direct
  `docker logs/exec/stop/...`, or an inspect other than the allowed start-time format. Its tests:
  `deploy/server/tests/test-guest-check-forbidden.sh`.
