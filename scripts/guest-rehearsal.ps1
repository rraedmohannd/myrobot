# Guest stack on THIS PC with a public HTTPS address (Cloudflare quick tunnel): the backup route when
# the server is not ready. Same files as the server (docker-compose.guest.yml), its own project
# "guest-rehearsal" and its own volumes; it never touches the developer stack (project smart-campus-robot).
#
#   powershell -ExecutionPolicy Bypass -File scripts\guest-rehearsal.ps1 setup      # once
#   powershell -ExecutionPolicy Bypass -File scripts\guest-rehearsal.ps1 up         # THE command on the day
#   powershell -ExecutionPolicy Bypass -File scripts\guest-rehearsal.ps1 url
#   powershell -ExecutionPolicy Bypass -File scripts\guest-rehearsal.ps1 status
#   powershell -ExecutionPolicy Bypass -File scripts\guest-rehearsal.ps1 admin NAME  # you type the password
#   powershell -ExecutionPolicy Bypass -File scripts\guest-rehearsal.ps1 logs [SERVICE] [-Follow]
#   powershell -ExecutionPolicy Bypass -File scripts\guest-rehearsal.ps1 down       # stop; data is kept
#
# setup   builds the image guest-backend:0.6.0 (linux/amd64, no dev packages), writes
#         deploy\server\.env.guest-rehearsal (git-ignored) with new random secrets and the OpenAI key copied
#         from the repo's .env (never shown), and restores the newest backups\smart-campus-bundle-*.tar.gz
#         into the empty guest database (-Replace overwrites a filled one; -SkipBuild, -SkipRestore).
# up      starts db, backend, backup and cloudflared, waits for health and for the public address,
#         writes it to PUBLIC_BASE_URL and re-creates only the backend (what deploy/server/guest/tunnel-url.sh
#         does on the server), then prints the links and the visitor access code. The address is new
#         whenever the cloudflared container is re-created (after "down", for example).
#
# Windows PowerShell 5.1: no "&&", and no double quotes inside arguments of native programs (they are
# mangled), so the shell snippets for the containers use unquoted variables.

param(
    [Parameter(Position = 0)][string]$Command = "help",
    [Parameter(Position = 1)][string]$Name = "",
    [switch]$Replace,
    [switch]$SkipBuild,
    [switch]$SkipRestore,
    [switch]$Follow,
    [int]$TimeoutSec = 240
)

$ErrorActionPreference = "Stop"
$Project = "guest-rehearsal"
$EnvRel = "deploy/server/.env.guest-rehearsal"
$ComposeRel = "docker-compose.guest.yml"
$Image = "guest-backend:0.6.0"
$Root = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $Root "deploy\server\.env.guest-rehearsal"
# docker-compose.guest.yml reads the same file through env_file: ${GUEST_ENV_FILE}
$env:GUEST_ENV_FILE = "./$EnvRel"

function Info([string]$m) { Write-Host "== $m" -ForegroundColor Cyan }
function Note([string]$m) { Write-Host "   $m" }
function Warn([string]$m) { Write-Host "!! $m" -ForegroundColor Yellow }
function Die([string]$m) { Write-Host "!! $m" -ForegroundColor Red; throw $m }

# docker compose for THIS project only (never the dev project). Always with the tunnel profile, so
# ps/logs/down see the tunnel too; "up -d SERVICE" still starts only that service.
function dc {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & docker compose -p $Project --env-file $EnvRel -f $ComposeRel --profile tunnel @args
    } finally {
        $ErrorActionPreference = $old
    }
}

# Same, but standard error merged into the output as plain text (for parsing; PS 5.1 would turn
# stderr lines into errors).
function dc-text {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & docker compose -p $Project --env-file $EnvRel -f $ComposeRel --profile tunnel @args 2>&1 |
            ForEach-Object { "$_" }
    } finally {
        $ErrorActionPreference = $old
    }
}

# ---------------- env file ----------------
function Env-Get([string]$key) {
    if (-not (Test-Path $EnvPath)) { return "" }
    $val = ""
    foreach ($line in [IO.File]::ReadAllLines($EnvPath)) {
        if ($line.StartsWith("$key=")) { $val = $line.Substring($key.Length + 1).Trim() }
    }
    return $val
}

# Replaces KEY=... (or appends it). The value is never printed.
function Env-Set([string]$key, [string]$value) {
    $lines = [Collections.Generic.List[string]]::new()
    $done = $false
    foreach ($line in [IO.File]::ReadAllLines($EnvPath)) {
        if ($line.StartsWith("$key=")) {
            if (-not $done) { $lines.Add("$key=$value"); $done = $true }
        } else { $lines.Add($line) }
    }
    if (-not $done) { $lines.Add("$key=$value") }
    Write-EnvFile ($lines -join "`n")
}

# UTF-8 without BOM, LF endings (a BOM would become part of the first key name).
function Write-EnvFile([string]$text) {
    if (-not $text.EndsWith("`n")) { $text += "`n" }
    [IO.File]::WriteAllText($EnvPath, $text, (New-Object Text.UTF8Encoding($false)))
}

function New-Secret([int]$length, [string]$alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789") {
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 1
    $sb = New-Object Text.StringBuilder
    $limit = 256 - (256 % $alphabet.Length) # no modulo bias
    while ($sb.Length -lt $length) {
        $rng.GetBytes($bytes)
        if ($bytes[0] -lt $limit) { [void]$sb.Append($alphabet[$bytes[0] % $alphabet.Length]) }
    }
    $rng.Dispose()
    return $sb.ToString()
}

function New-EnvFile {
    if (Test-Path $EnvPath) { Note "$EnvRel exists: kept (delete it to make new secrets)"; return }
    # The OpenAI key: read from the repo's .env and written straight into the new file, never shown.
    $openai = ""
    $rootEnv = Join-Path $Root ".env"
    if (Test-Path $rootEnv) {
        foreach ($line in [IO.File]::ReadAllLines($rootEnv)) {
            if ($line -match '^OPENAI_API_KEY=(.*)$') { $openai = $Matches[1].Trim().Trim('"').Trim("'") }
        }
    }
    if (-not $openai) { Warn "no OPENAI_API_KEY in .env: set it in $EnvRel before the real test" }
    $codeAlphabet = "abcdefghijkmnpqrstuvwxyz23456789" # no 0/o, 1/l: easy to type on a phone
    $code = (New-Secret 4 $codeAlphabet) + "-" + (New-Secret 4 $codeAlphabet) + "-" + (New-Secret 4 $codeAlphabet)
    $text = @"
# Guest stack on this PC (scripts/guest-rehearsal.ps1, project guest-rehearsal). Git-ignored. Never share it.
APP_ENV=production
LOG_LEVEL=INFO
# "up" replaces it with the tunnel's address (it changes whenever cloudflared is re-created).
PUBLIC_BASE_URL=https://tunnel-address-not-known-yet.invalid
POSTGRES_USER=robot
POSTGRES_PASSWORD=$(New-Secret 32)
POSTGRES_DB=smart_campus
ADMIN_TOKEN=$(New-Secret 48)
ADMIN_TOKEN_SCRIPTS=false
ROBOT_TOKEN=$(New-Secret 40)
SECRET_KEY=$(New-Secret 48)
PUBLIC_PAGES=true
SITE_ACCESS_CODE=$code
VISITOR_SESSION_HOURS=12
ADMIN_SESSION_HOURS=12
DAILY_PAID_CALL_LIMIT=500
RATE_LIMIT_PAID_PER_MIN=12
RATE_LIMIT_ROBOT_PER_MIN=60
LOGIN_MAX_FAILURES=5
LOGIN_LOCKOUT_MIN=15
MEDIA_MAX_MB=50
# Only cloudflared (its fixed address on the guest network) may set X-Forwarded-For.
FORWARDED_ALLOW_IPS=10.213.47.10
GUEST_SUBNET=10.213.47.0/24
GUEST_TUNNEL_IP=10.213.47.10
WEB_CONCURRENCY=1
GUEST_BIND=127.0.0.1
GUEST_PORT=8100
GUEST_IMAGE=$Image
BACKUP_HOUR=3
BACKUP_KEEP_DAYS=14
BACKUP_FILES_EVERY_DAYS=7
CONVERSATION_RETENTION_DAYS=30
LOCAL_TIMEZONE=Asia/Amman
ROBOT_LOCAL_NETS=
# true = SIMULATED answers and speech (rehearsal only). Keep false for the real test.
FAKE_PROVIDERS=false
OPENAI_API_KEY=$openai
LLM_PROVIDER=openai
LLM_MODEL=gpt-6-luna
LLM_REASONING_EFFORT=low
LLM_STREAMING=true
EMBEDDING_MODEL=text-embedding-3-small
STT_MODEL=gpt-transcribe
TTS_PROVIDER=openai
TTS_MODEL=gpt-4o-mini-tts
TTS_VOICE=coral
TTS_SPEED=1.12
SPEECH_RATE=1.0
TTS_STREAMING=false
"@
    $openai = $null
    Write-EnvFile ($text -replace "`r`n", "`n")
    $text = $null
    Note "wrote $EnvRel (new secrets, access code, OpenAI key copied: $(if ((Env-Get 'OPENAI_API_KEY')) { 'yes' } else { 'NO' }))"
}

# ---------------- helpers ----------------
function Require-Env { if (-not (Test-Path $EnvPath)) { Die "$EnvRel not found: run 'setup' first" } }

function Require-Image {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & docker image inspect $Image *> $null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $old
    if (-not $ok) { Die "image $Image not found: run 'setup' first" }
}

function Port { $p = Env-Get "GUEST_PORT"; if ($p) { return $p } else { return "8100" } }

# HTTP status of /health on the published port (0 = no answer).
function Health-Code {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -Uri "http://127.0.0.1:$(Port)/health"
        return [int]$r.StatusCode
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

function Wait-Healthy([int]$timeout) {
    Write-Host -NoNewline "   waiting for the backend health check (up to $timeout s) "
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $timeout) {
        if ((Health-Code) -eq 200) { Write-Host " ok ($([int]$sw.Elapsed.TotalSeconds) s)"; return $true }
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 3
    }
    Write-Host " TIMEOUT"
    dc logs --no-color --tail 30 backend
    return $false
}

function Wait-Db {
    for ($i = 0; $i -lt 40; $i++) {
        dc exec -T db sh -c 'pg_isready -q -U $POSTGRES_USER -d $POSTGRES_DB' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 2
    }
    Die "the database did not start (dc logs db)"
}

# psql in the guest database, unaligned, tuples only. SQL must not contain double quotes.
function Psql([string]$sql) {
    $out = dc exec -T db psql -v ON_ERROR_STOP=1 -U (Env-Get "POSTGRES_USER") -d (Env-Get "POSTGRES_DB") -At -c $sql
    if ($LASTEXITCODE -ne 0) { Die "psql failed: $sql" }
    return $out
}

# The newest public address in the tunnel's log ("" if none yet).
function Tunnel-Url {
    $lines = dc-text logs --no-color --no-log-prefix cloudflared
    $url = ""
    foreach ($l in $lines) {
        foreach ($m in [regex]::Matches($l, 'https://[a-z0-9-]+\.trycloudflare\.com')) {
            if (-not $m.Value.StartsWith("https://api.")) { $url = $m.Value }
        }
    }
    return $url
}

function Tunnel-Running {
    $id = dc-text ps --status running -q cloudflared | Where-Object { $_ -match '^[0-9a-f]{12,}$' }
    return [bool]$id
}

function Wait-TunnelUrl([int]$timeout) {
    Write-Host -NoNewline "   waiting for the public address (up to $timeout s) "
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $timeout) {
        if (Tunnel-Running) {
            $u = Tunnel-Url
            if ($u) { Write-Host " ok"; return $u }
        }
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 2
    }
    Write-Host " TIMEOUT"
    return ""
}

# Waits until the address answers from the internet (Cloudflare needs a few seconds after the log line).
# The new name is not in DNS for the first seconds. curl.exe asks Cloudflare's DNS directly
# (--doh-url), so an early "not found" is not cached by Windows for this PC's browser.
function Wait-Public([string]$url, [int]$timeout) {
    Write-Host -NoNewline "   checking $url/health from the internet "
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $timeout) {
        $code = ""
        if ($curl) {
            $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            $code = & curl.exe -s -m 10 --doh-url https://1.1.1.1/dns-query -o NUL -w "%{http_code}" "$url/health" 2>$null
            $ErrorActionPreference = $old
        } else {
            try {
                [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
                $code = [string](Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 -Uri "$url/health").StatusCode
            } catch { }
        }
        if ($code -eq "200") { Write-Host " ok ($([int]$sw.Elapsed.TotalSeconds) s)"; return $true }
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 3
    }
    Write-Host " not yet (it can take a minute more; try the link anyway)"
    Note "if this PC's browser says the site cannot be found: wait a minute, then run ipconfig /flushdns"
    return $false
}

function Show-Links([string]$url) {
    $code = Env-Get "SITE_ACCESS_CODE"
    Write-Host ""
    Write-Host "  Public address (new whenever cloudflared is re-created):" -ForegroundColor Green
    Write-Host "    $url" -ForegroundColor Green
    Write-Host "    Robot screen : $url/robot"
    Write-Host "    Voice test   : $url/voice   (microphone: allow it in the browser)"
    Write-Host "    Admin        : $url/admin"
    Write-Host "    Access code  : $code   (asked once per browser, on /access)"
    Write-Host "  On this PC also: http://localhost:$(Port)/robot"
    Write-Host ""
}

# ---------------- setup ----------------
function Build-Image {
    Info "build $Image (linux/amd64, no dev packages)"
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & docker build --platform linux/amd64 --build-context shared=shared --build-arg EXTRAS= -t $Image backend
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($code -ne 0) { Die "docker build failed" }
}

function Restore-Bundle {
    $bundle = Get-ChildItem -Path (Join-Path $Root "backups") -Filter "smart-campus-bundle-*.tar.gz" -ErrorAction SilentlyContinue |
        Sort-Object Name | Select-Object -Last 1
    if (-not $bundle) { Warn "no backups\smart-campus-bundle-*.tar.gz: the database stays empty (scripts\export-bundle.ps1 makes one)"; return }
    Info "restore $($bundle.Name)"

    Note "starting the database"
    dc up -d db | Out-Null
    Wait-Db
    $hasSources = Psql "SELECT to_regclass('public.sources') IS NOT NULL"
    $n = 0
    if ($hasSources -eq "t") { $n = [int](Psql "SELECT count(*) FROM sources") }
    if ($n -gt 0 -and -not $Replace) { Note "the guest database already holds $n sources: restore skipped (-Replace overwrites it)"; return }

    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("guest-bundle-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        # The bundle is data: no absolute paths, no "..", no links or special files.
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $listing = & tar.exe -tvzf $bundle.FullName
        $names = & tar.exe -tzf $bundle.FullName
        $ErrorActionPreference = $old
        if ($LASTEXITCODE -ne 0) { Die "cannot read the bundle" }
        foreach ($l in $listing) { if ($l.Length -gt 0 -and "-d".IndexOf($l[0]) -lt 0) { Die "the bundle holds links or special files: refused" } }
        foreach ($nm in $names) { if ($nm -match '(^/|^[A-Za-z]:|(^|/)\.\.(/|$)|\\)') { Die "the bundle holds absolute paths or '..': refused" } }
        & tar.exe -xzf $bundle.FullName -C $tmp
        if ($LASTEXITCODE -ne 0) { Die "unpacking the bundle failed" }

        $manifestPath = Join-Path $tmp "manifest.json"
        if (-not (Test-Path $manifestPath)) { Die "manifest.json is missing in the bundle" }
        $m = [IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json
        if ($m.format -ne 1) { Die "unknown bundle format '$($m.format)'" }
        Note "created $($m.created_at), app $($m.app_version), revision $($m.alembic_revision), conversations included: $($m.include_conversations)"

        # every listed file: SHA-256 matches; no unlisted file
        $listed = @{}
        foreach ($p in $m.files.PSObject.Properties) {
            $listed[$p.Name] = $true
            $f = Join-Path $tmp ($p.Name -replace '/', '\')
            if (-not (Test-Path -LiteralPath $f)) { Die "missing in the bundle: $($p.Name)" }
            $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $f).Hash.ToLower()
            if ($h -ne $p.Value) { Die "SHA-256 check FAILED for $($p.Name): the bundle is damaged; nothing was restored" }
        }
        $all = Get-ChildItem -LiteralPath $tmp -Recurse -File | ForEach-Object { $_.FullName.Substring($tmp.Length + 1) -replace '\\', '/' }
        foreach ($rel in $all) { if ($rel -ne "manifest.json" -and -not $listed.ContainsKey($rel)) { Die "file not listed in manifest.json: $rel" } }
        Note "SHA-256 OK for $($listed.Count) files"

        # the code must know the bundle's Alembic revision
        $revs = Get-ChildItem (Join-Path $Root "backend\migrations\versions") -Filter *.py | ForEach-Object {
            $t = [IO.File]::ReadAllText($_.FullName)
            if ($t -match '(?m)^revision(: *str)? *= *"([^"]+)"') { $Matches[2] }
        }
        if ($revs -notcontains $m.alembic_revision) { Die "the bundle is at revision $($m.alembic_revision), which this code does not know" }

        Note "database: recreate and pg_restore"
        dc stop backend 2>$null | Out-Null
        $db = (dc-text ps -q db | Where-Object { $_ -match '^[0-9a-f]{12,}$' } | Select-Object -First 1)
        if (-not $db) { Die "the db container is not running" }
        # docker cp, not the PowerShell pipeline (it would corrupt a binary file)
        & docker cp (Join-Path $tmp "db.dump") "${db}:/tmp/restore.dump"
        if ($LASTEXITCODE -ne 0) { Die "docker cp of db.dump failed" }
        dc exec -T db sh -euc 'dropdb -U $POSTGRES_USER --if-exists --force $POSTGRES_DB; createdb -U $POSTGRES_USER -O $POSTGRES_USER $POSTGRES_DB; psql -v ON_ERROR_STOP=1 -q -U $POSTGRES_USER -d $POSTGRES_DB -c CREATE\ EXTENSION\ IF\ NOT\ EXISTS\ vector; pg_restore -U $POSTGRES_USER -d $POSTGRES_DB --no-owner --no-privileges --exit-on-error --single-transaction /tmp/restore.dump; rm -f /tmp/restore.dump'
        if ($LASTEXITCODE -ne 0) { Die "pg_restore failed (see above)" }

        Note "media and speech cache into the volumes (as the backend user 10001)"
        $dirs = @()
        foreach ($d in @("media", "tts_cache")) { if (Test-Path (Join-Path $tmp $d)) { $dirs += $d } }
        if ($dirs.Count -gt 0) {
            dc run --rm --no-deps -T --user 10001:10001 -v "${tmp}:/bundle:ro" --entrypoint sh backend -euc 'for d in media tts_cache; do if [ -d /bundle/$d ]; then find /app/data/$d -mindepth 1 -delete; cp -R /bundle/$d/. /app/data/$d/; echo $d: $(find /app/data/$d -type f | wc -l) files; fi; done'
            if ($LASTEXITCODE -ne 0) { Die "copying media/tts_cache failed" }
        }

        Note "starting the backend (it migrates to the code's head)"
        dc up -d backend | Out-Null
        if (-not (Wait-Healthy $TimeoutSec)) { Die "the backend is not healthy after the restore" }

        $bad = 0
        Write-Host ("   {0,-28} {1,8} {2,8}  {3}" -f "table", "bundle", "restored", "result")
        foreach ($p in $m.tables.PSObject.Properties) {
            if ($p.Name -notmatch '^[a-z_][a-z0-9_]*$') { Die "odd table name in manifest.json: $($p.Name)" }
            $exists = Psql "SELECT to_regclass('public.$($p.Name)') IS NOT NULL"
            $got = "missing"
            if ($exists -eq "t") { $got = (Psql "SELECT count(*) FROM $($p.Name)") }
            $res = "OK"
            if ("$got" -ne "$($p.Value)") { $res = "DIFF"; $bad = 1 }
            Write-Host ("   {0,-28} {1,8} {2,8}  {3}" -f $p.Name, $p.Value, $got, $res)
        }
        if ($bad) { Warn "some counts differ (DIFF above)" } else { Note "all table counts match" }
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Do-Setup {
    if (-not $SkipBuild) { Build-Image } else { Require-Image }
    Info "settings $EnvRel"
    New-EnvFile
    if (-not $SkipRestore) { Restore-Bundle }
    Info "setup done. Start it with: powershell -ExecutionPolicy Bypass -File scripts\guest-rehearsal.ps1 up"
    Note "admin login: powershell -ExecutionPolicy Bypass -File scripts\guest-rehearsal.ps1 admin NAME"
}

# ---------------- up / url / status / down / admin / logs ----------------
function Do-Up {
    Require-Env; Require-Image
    if ((Env-Get "FAKE_PROVIDERS") -eq "true") { Warn "FAKE_PROVIDERS=true in ${EnvRel}: answers and speech are SIMULATED" }
    Info "start db, backend, backup and cloudflared (project $Project)"
    dc up -d --remove-orphans
    if ($LASTEXITCODE -ne 0) { Die "docker compose up failed" }
    if (-not (Wait-Healthy $TimeoutSec)) { Die "the backend is not healthy" }
    $url = Wait-TunnelUrl 90
    if (-not $url) {
        Warn "no public address yet: the last tunnel log lines:"
        dc logs --no-color --tail 20 cloudflared
        Die "the tunnel did not start (internet? try: scripts\guest-rehearsal.ps1 up again)"
    }
    # PUBLIC_BASE_URL = the address (CORS, admin QR); re-create ONLY the backend, never cloudflared.
    if ((Env-Get "PUBLIC_BASE_URL") -ne $url) {
        Env-Set "PUBLIC_BASE_URL" $url
        Info "PUBLIC_BASE_URL=$url written; re-creating only the backend"
        dc up -d --no-deps --force-recreate backend
        if ($LASTEXITCODE -ne 0) { Die "re-creating the backend failed" }
        if (-not (Wait-Healthy $TimeoutSec)) { Die "the backend is not healthy" }
    }
    [void](Wait-Public $url 120)
    Show-Links $url
}

function Do-Url {
    Require-Env
    $u = ""
    if (Tunnel-Running) { $u = Tunnel-Url }
    if (-not $u) { Die "no tunnel running (scripts\guest-rehearsal.ps1 up)" }
    Show-Links $u
}

function Do-Status {
    Require-Env
    Info "containers (project $Project)"
    dc ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}"
    Info "backend health"
    Note "http://127.0.0.1:$(Port)/health -> HTTP $(Health-Code)"
    Info "public address"
    if (Tunnel-Running) { Note (Tunnel-Url) } else { Note "tunnel not running" }
    Info "memory and CPU (one sample)"
    $ids = dc-text ps -q | Where-Object { $_ -match '^[0-9a-f]{12,}$' }
    if ($ids) { & docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}" @ids }
    Info "paid OpenAI calls today"
    dc exec -T backend python -m app.ingestion.cli usage-today
    Info "last backup"
    dc exec -T backup cat /backups/last-backup.json 2>$null
    if ($LASTEXITCODE -ne 0) { Note "none yet" }
    Note "FAKE_PROVIDERS=$(Env-Get 'FAKE_PROVIDERS')"
}

function Do-Down {
    Require-Env
    Info "stop project $Project (the data volumes are kept)"
    dc down
}

function Do-Admin {
    Require-Env
    if ($Name -notmatch '^[A-Za-z0-9._-]{2,64}$') { Die "usage: guest-rehearsal.ps1 admin NAME (2-64 letters, digits, '.', '_' or '-')" }
    Info "admin login '$Name': type the password (hidden) twice"
    dc exec backend python -m app.ingestion.cli create-admin $Name
}

function Do-Logs {
    Require-Env
    $a = @("logs", "--no-color", "--tail", "200")
    if ($Follow) { $a += "-f" }
    if ($Name) { $a += $Name }
    dc @a
}

Push-Location $Root
try {
    switch ($Command) {
        "setup" { Do-Setup }
        "up" { Do-Up }
        "url" { Do-Url }
        "status" { Do-Status }
        "down" { Do-Down }
        "admin" { Do-Admin }
        "logs" { Do-Logs }
        default {
            Get-Content $PSCommandPath -TotalCount 26 | Where-Object { $_ -like "#*" } | ForEach-Object { $_ -replace '^# ?', '' }
        }
    }
} finally {
    Pop-Location
}
