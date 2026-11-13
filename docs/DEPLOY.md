# Running the backend on the local network (demo site)

The backend runs on **your laptop**. The robot (Raspberry Pi) and the admin page reach it over the same Wi-Fi or cable. Nothing here is needed for the robot to stop safely: motor stop and obstacle handling stay on the ESP32 and the Pi (CLAUDE.md rule 4).

```
 Raspberry Pi (robot)  ── Wi-Fi / LAN ──►  laptop: Docker → backend :8000 → PostgreSQL
   its own screen UI on http://localhost          │
   WebSocket ws://<laptop>:8000/ws/robot          └──► internet (OpenAI) for speech and answers
```

## 1. Start it
```powershell
cd C:\Users\hanan\Documents\smart-campus-robot
docker compose up -d
docker compose exec backend alembic upgrade head
```
Check on the laptop: http://localhost:8000/health → `"status":"ok"`. The `providers` part of that answer shows whether OpenAI is reachable from this place.

## 2. A fixed address for the laptop
The robot must find the laptop at the same address every time.
1. Find the current address: `ipconfig` → "IPv4 Address" of the Wi-Fi adapter (for example `192.168.1.20`).
2. Make it stable. Pick one:
   - **Router reservation** (best): in the router's DHCP page, reserve that address for the laptop.
   - **Your own hotspot**: let the laptop or a phone make the network; note the laptop's address there.
   - **Hostname**: on many networks `http://<laptop-name>.local:8000` or `http://<laptop-name>:8000` works (`hostname` prints the name). Test it from another device before relying on it.
3. Put it in `.env` so the admin page and its QR code show the right address:
   ```
   PUBLIC_BASE_URL=http://192.168.1.20:8000
   ```
   then `docker compose up -d`.

University networks often block devices from talking to each other ("client isolation"). If the phone test in step 5 fails although the firewall rule exists, use your own router or hotspot for the demo.

## 3. Windows firewall: allow port 8000
Run **PowerShell as Administrator** once:
```powershell
New-NetFirewallRule -DisplayName "Smart Campus Robot backend (8000)" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
```
- `-Profile Private`: the rule works only on networks marked "Private". Mark the demo network as private (Settings → Network → the connection → Private), or use `-Profile Private,Public` if you cannot.
- Remove it after the demo: `Remove-NetFirewallRule -DisplayName "Smart Campus Robot backend (8000)"`.
- Docker Desktop must also be allowed through the firewall (it asks the first time).
- Do **not** open port 5433 (the database). Only the backend talks to it.

## 4. ROBOT_TOKEN: required for robots that are not on the laptop
Without a token, anyone on the same network could connect to `/ws/robot` as a robot. Set one in `.env`:
```
ROBOT_TOKEN=<a long random string>
```
Make one with: `python -c "import secrets; print(secrets.token_urlsafe(24))"`. Then `docker compose up -d`.

- Every connection that does not come from the backend's own machine must add `?token=...`:
  `ws://192.168.1.20:8000/ws/robot?token=<ROBOT_TOKEN>`. A wrong or missing token is closed with code 4401.
- **Docker Desktop detail:** the backend runs inside Docker, so even the laptop's own browser reaches it through Docker's network and is *not* seen as local. With a token set, open the laptop pages with the token too: `http://localhost:8000/robot?token=<ROBOT_TOKEN>` (the `/voice` page has no token field: use `/robot`, or test `/voice` before setting the token). The admin page → **System** shows the ready-made addresses.
- The token check looks only at the network address of the peer, never at headers a client can fake. `ROBOT_LOCAL_NETS` (default `127.0.0.1/32,::1/128`) lists networks that may connect without a token; widen it only on a network you control.
- The token travels in clear text on the local network (no HTTPS). It keeps casual visitors out; it is not strong security. Use a network you control for the demo.

## 5. Test from another device
1. Phone on the same network → `http://192.168.1.20:8000/health` → `"status":"ok"`. If it does not load: firewall rule, network profile, client isolation.
2. Admin page on the laptop → **System** → the QR code holds the robot page address (with the token, so do not show it to the public). Scan it with the phone: the robot screen opens.
3. Simulated robot from the laptop: `docker compose exec backend python -m app.ingestion.cli simulate-robot` → admin → **Robot (live)** shows it with the SIMULATED banner.

## 6. Microphone and camera: why the Pi serves its own page
Browsers give the microphone and the camera only to pages on `localhost` or HTTPS. A page opened as `http://192.168.1.20:8000/robot` on another device can show the screen and play sound, but **cannot listen**.

Decision for the robot: the Raspberry Pi serves its own screen UI on `http://localhost` (sprint 4) and only talks to the backend through the WebSocket. So the backend does **not** need HTTPS.

### Optional: self-signed HTTPS, to try `/robot` with a microphone from another device
For testing only (tried once on 2026-11-13: `/health` and `/robot` answered over HTTPS; the microphone from a second device was not tried).
```powershell
# 1. a certificate for 30 days (stays in backend\data\certs, which you should not commit)
docker compose exec backend sh -c "mkdir -p data/certs && openssl req -x509 -newkey rsa:2048 -nodes -days 30 -keyout data/certs/key.pem -out data/certs/cert.pem -subj /CN=robot-backend"
# 2. a second backend on port 8443 with TLS (the normal one on 8000 keeps running)
docker compose run --rm -d --name robot-https -p 8443:8443 backend uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile data/certs/key.pem --ssl-certfile data/certs/cert.pem
# 3. on the other device: https://192.168.1.20:8443/robot  -> accept the certificate warning once
# 4. stop it
docker rm -f robot-https
```
Open port 8443 in the firewall the same way as 8000 while you test, and remove the rule afterwards.

## 7. Poor internet at the demo site
- Every call to OpenAI has a time limit, one retry and a circuit breaker (`PROVIDER_TIMEOUT_*`, `PROVIDER_RETRIES`, `BREAKER_*` in `.env.example`).
- When a provider cannot be reached, the robot says a cached phrase ("الإنترنت ضعيف هسا…"), shows a QR code to the official faculty page and offers the approved explainers. Explainers, fixed phrases and FAQs asked with the exact approved wording need no internet, because their speech is stored on the laptop.
- Before the demo, with good internet: `prepare-phrases` and `prepare-explainers` (fills the speech cache), then look at admin → **System** for provider errors.
- Without internet, speech-to-text does not work, so spoken questions cannot be understood: use the touch buttons.
- A phone hotspot as a second internet connection is the cheapest insurance.

## 8. Checklist for the day
- [ ] Docker Desktop running; `docker compose up -d`; `/health` ok on the laptop and on a phone.
- [ ] `PUBLIC_BASE_URL` matches today's address (`ipconfig`).
- [ ] Firewall rule present; network profile Private.
- [ ] `ROBOT_TOKEN` set, and the Pi configured with the same value.
- [ ] Admin user created (`create-admin`), and you can log in.
- [ ] `prepare-phrases`, `prepare-explainers` run with good internet.
- [ ] A backup made: `powershell -ExecutionPolicy Bypass -File scripts\backup-db.ps1`.
- [ ] Laptop on mains power, sleep disabled.

## Backups
```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup-db.ps1                      # -> backups\smart_campus-<date>.dump
powershell -ExecutionPolicy Bypass -File scripts\restore-db.ps1 backups\<file>.dump  # restores into a SEPARATE database to check it
powershell -ExecutionPolicy Bypass -File scripts\restore-db.ps1 backups\<file>.dump -Replace   # replaces the live database (asks for YES, backs up first)
```
- The dump is made with `pg_dump` inside the database container. It holds the knowledge base, approvals, explainers, FAQs, places, conversations (text only) and robot runs. It holds no API keys.
- `backups\` is git-ignored. Copy the file to a USB stick or a private cloud folder; it contains visitors' questions, so do not share it.
- Not in the dump: `.env` (keep your own copy), the speech cache (regenerated by `prepare-phrases` / `prepare-explainers`) and uploaded media files (`backend\data\media`, copy that folder too).
- Make a backup before every demo and before `git pull` + `alembic upgrade head`.
