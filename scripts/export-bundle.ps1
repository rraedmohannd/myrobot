# Export the laptop's data as one bundle for the server (docs/SPRINT_6.md P0.5)
#
#   powershell -ExecutionPolicy Bypass -File scripts\export-bundle.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\export-bundle.ps1 -IncludeConversations
#
# Writes backups\smart-campus-bundle-YYYYmmdd-HHMMSS.tar.gz (backups\ is git-ignored) holding:
#   manifest.json  versions, Alembic revision, git commit, rows per table, SHA-256 of every file
#   db.dump        pg_dump -Fc --no-owner --no-privileges of the running dev database
#   media\         backend\data\media
#   tts_cache\     backend\data\tts_cache
# Restore it on the server with deploy/server/restore-bundle.sh.
#
# Privacy: the DATA of sessions and turns (visitors' questions) stays on the laptop unless
# -IncludeConversations is given. admin_users and admin_sessions data never travels (the
# server makes its own admin with create-admin). Their table definitions are in the dump.
#
# No .env and no keys go into the bundle. Before the archive is written, every staged file
# and the text form of the dump are searched for the secret values of the local .env (keys
# named *PASSWORD*, *SECRET*, *TOKEN*, *KEY*, *ACCESS_CODE*) and for "sk-..." API keys; on a
# hit the script stops and writes nothing. Secret values are never printed.
#
# The database is only read: pg_dump and SELECT. It must be running (docker compose up -d).
# Same output as scripts/export-bundle.sh (Linux/macOS).

param([switch]$IncludeConversations)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$name = "smart-campus-bundle-$stamp.tar.gz"
$out = "backups\$name"
$stage = "backups\.bundle-staging-$stamp"
$ctrDump = "/tmp/scr-bundle-$stamp.dump"
$ctrSql = "/tmp/scr-bundle-$stamp.sql"
$sqlCopy = "backups\.bundle-scan-$stamp.sql"

$excluded = @("admin_sessions", "admin_users")
if (-not $IncludeConversations) { $excluded += @("sessions", "turns") }
$excluded = @($excluded | Sort-Object)

# psql inside the db container; the SQL goes in on stdin.
# (no double quotes inside the command: Windows PowerShell 5 mangles them for native programs)
function Invoke-Psql([string]$sql) {
    $res = $sql | docker compose exec -T db sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -X -q -At -v ON_ERROR_STOP=1'
    if ($LASTEXITCODE -ne 0) { throw "psql failed. Is Docker running (docker compose up -d)?" }
    return @($res | Where-Object { $_ -ne $null -and $_.Trim() -ne "" } | ForEach-Object { $_.Trim() })
}

function Get-TableCounts {
    $lines = Invoke-Psql @'
SELECT string_agg(format('SELECT %L, count(*) FROM public.%I', tablename, tablename), ' UNION ALL ' ORDER BY tablename)
FROM pg_tables WHERE schemaname = 'public' \gexec
'@
    $counts = [ordered]@{}
    foreach ($l in ($lines | Sort-Object)) {
        $parts = $l.Split("|")
        $counts[$parts[0]] = [int64]$parts[1]
    }
    return $counts
}

function Get-EnvSecrets {
    # name -> value of the secret keys in .env; values never leave this function except for comparison
    $secrets = @{}
    if (-not (Test-Path ".env")) { return $secrets }
    foreach ($line in [IO.File]::ReadAllLines((Join-Path $root ".env"))) {
        $t = $line.Trim()
        if ($t -eq "" -or $t.StartsWith("#")) { continue }
        if ($t.StartsWith("export ")) { $t = $t.Substring(7).Trim() }
        $eq = $t.IndexOf("=")
        if ($eq -lt 1) { continue }
        $k = $t.Substring(0, $eq).Trim()
        $v = $t.Substring($eq + 1).Trim()
        if ($v.Length -ge 2 -and (($v[0] -eq '"' -and $v[-1] -eq '"') -or ($v[0] -eq "'" -and $v[-1] -eq "'"))) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        if ($k -notmatch "PASSWORD|SECRET|TOKEN|KEY|ACCESS_CODE") { continue }
        if ($v.Length -lt 6) { continue }   # empty or placeholder values cannot be told apart from normal text
        $secrets[$k] = $v
    }
    return $secrets
}

function Find-Secrets([string[]]$paths, $secrets) {
    # byte-exact search: each file is read as Latin-1 (one char per byte), the secrets as their UTF-8 bytes
    $latin1 = [Text.Encoding]::GetEncoding(28591)
    $needles = @{}
    foreach ($k in $secrets.Keys) { $needles[$k] = $latin1.GetString([Text.Encoding]::UTF8.GetBytes($secrets[$k])) }
    $keyPattern = New-Object Text.RegularExpressions.Regex("sk-[A-Za-z0-9_-]{20,}")
    $hits = @()
    foreach ($p in $paths) {
        $text = $latin1.GetString([IO.File]::ReadAllBytes($p))
        foreach ($k in $needles.Keys) {
            if ($text.IndexOf($needles[$k], [StringComparison]::Ordinal) -ge 0) { $hits += "$p contains the value of $k from .env" }
        }
        if ($keyPattern.IsMatch($text)) { $hits += "$p contains an sk-... API key pattern" }
    }
    return $hits
}

$container = (docker compose ps -q db | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $container -eq "") { throw "the db container is not running (docker compose up -d)" }

try {
    New-Item -ItemType Directory -Force -Path $stage | Out-Null

    # 1. Foreign keys from a dumped table into a table whose data is left out. pg_restore would fail
    #    to create such a constraint if a row points at a missing row. Checked at every run.
    $arr = "ARRAY[" + (($excluded | ForEach-Object { "'$_'" }) -join ",") + "]::text[]"
    $fkSql = @"
SELECT format('SELECT %L, count(*) FROM %s WHERE %s', c.conname || ' on ' || c.conrelid::regclass::text,
              c.conrelid::regclass, string_agg(format('%I IS NOT NULL', a.attname), ' AND '))
FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
WHERE c.contype = 'f' AND c.confrelid::regclass::text = ANY ($arr)
  AND NOT (c.conrelid::regclass::text = ANY ($arr))
GROUP BY c.conname, c.conrelid \gexec
"@
    foreach ($l in (Invoke-Psql $fkSql)) {
        $parts = $l.Split("|")
        if ([int64]$parts[1] -gt 0) {
            throw "foreign key $($parts[0]): $($parts[1]) rows point into a table whose data is excluded. Use -IncludeConversations or clear those references first."
        }
    }

    # 2. Counts before, dump, counts after (the backend may be writing; the dump is one snapshot)
    $before = Get-TableCounts
    $exArgs = ($excluded | ForEach-Object { "--exclude-table-data=public.$_" }) -join " "
    docker compose exec -T db sh -c ('pg_dump -U $POSTGRES_USER -d $POSTGRES_DB -Fc --no-owner --no-privileges ' + $exArgs + ' -f ' + $ctrDump)
    if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
    $after = Get-TableCounts
    foreach ($t in $after.Keys) {
        if (-not $before.Contains($t) -or $before[$t] -ne $after[$t]) {
            throw "table $t changed while dumping ($($before[$t]) -> $($after[$t])). Run the script again."
        }
    }
    docker cp "${container}:$ctrDump" "$stage\db.dump"
    if ($LASTEXITCODE -ne 0) { throw "could not copy the dump out of the container" }

    # text form of the dump, only for the secret scan below (never part of the bundle)
    docker compose exec -T db sh -c ('pg_restore -f ' + $ctrSql + ' ' + $ctrDump)
    if ($LASTEXITCODE -ne 0) { throw "pg_restore (text form for the secret scan) failed" }
    docker cp "${container}:$ctrSql" $sqlCopy
    if ($LASTEXITCODE -ne 0) { throw "could not copy the text dump out of the container" }

    $alembic = @(Invoke-Psql "SELECT version_num FROM alembic_version;")[0]
    $pgVersion = @(Invoke-Psql "SHOW server_version;")[0]
    $simulated = [int64]@(Invoke-Psql "SELECT count(*) FROM robot_runs WHERE is_simulated;")[0]

    # 3. Files
    foreach ($d in @("media", "tts_cache")) {
        $src = "backend\data\$d"
        $dst = "$stage\$d"
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        if (Test-Path $src) { Copy-Item -Path "$src\*" -Destination $dst -Recurse -Force }
    }
    $stageFull = (Resolve-Path $stage).Path.TrimEnd("\")
    $staged = @(Get-ChildItem -Path $stageFull -Recurse -File -Force)
    $badNames = @($staged | Where-Object { $_.Name -like ".env*" })
    if ($badNames.Count -gt 0) { throw "refusing: a .env file is among the files to export: $($badNames[0].FullName)" }

    $files = [ordered]@{}
    $rels = @()
    foreach ($f in $staged) { $rels += $f.FullName.Substring($stageFull.Length + 1).Replace("\", "/") }
    foreach ($rel in ($rels | Sort-Object { if ($_ -eq "db.dump") { "" } else { $_ } })) {
        $files[$rel] = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $stageFull $rel)).Hash.ToLowerInvariant()
    }

    # 4. Manifest
    $commit = (git rev-parse HEAD | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $commit -eq "") { $commit = "unknown" }
    $dirty = ((git status --porcelain | Out-String).Trim() -ne "")
    $verLine = Select-String -Path "backend\app\__init__.py" -Pattern '__version__\s*=\s*["'']([^"'']+)' | Select-Object -First 1
    $appVersion = if ($verLine) { $verLine.Matches[0].Groups[1].Value } else { "unknown" }

    $tables = [ordered]@{}
    foreach ($t in $after.Keys) { $tables[$t] = if ($excluded -contains $t) { [int64]0 } else { $after[$t] } }

    $manifest = [ordered]@{
        format                  = 1
        created_at              = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
        git_commit              = $commit
        git_dirty               = $dirty
        app_version             = $appVersion
        alembic_revision        = $alembic
        postgres_server_version = $pgVersion
        include_conversations   = [bool]$IncludeConversations
        excluded_table_data     = [string[]]$excluded
        tables                  = $tables
        simulated_robot_runs    = $simulated
        files                   = $files
    }
    $json = (ConvertTo-Json -InputObject $manifest -Depth 6).Replace("`r`n", "`n")
    [IO.File]::WriteAllText((Join-Path $stageFull "manifest.json"), $json + "`n", (New-Object Text.UTF8Encoding($false)))

    # 5. Secret scan: every staged file, the manifest and the text form of the dump
    $secrets = Get-EnvSecrets
    $scan = @(Get-ChildItem -Path $stageFull -Recurse -File -Force | ForEach-Object { $_.FullName }) + @((Resolve-Path $sqlCopy).Path)
    $hits = @(Find-Secrets $scan $secrets)
    $secretCount = $secrets.Count
    $secrets = $null
    if ($hits.Count -gt 0) {
        $hits | ForEach-Object { Write-Host "SECRET FOUND: $_" }
        throw "refusing to write the bundle: secrets found (see above). Nothing was written."
    }
    Write-Host "Secret scan: $($scan.Count) files clean ($secretCount .env secret values and the sk- pattern checked)"

    # 6. Archive (Windows ships bsdtar as tar.exe; entries are relative, manifest.json first)
    $tarExe = Join-Path $env:SystemRoot "System32\tar.exe"
    if (-not (Test-Path $tarExe)) { $tarExe = "tar" }
    & $tarExe -czf $out -C $stage manifest.json db.dump media tts_cache
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
}
finally {
    docker compose exec -T db rm -f $ctrDump $ctrSql | Out-Null
    if (Test-Path $sqlCopy) { Remove-Item -Force $sqlCopy }
    if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
}

$size = [math]::Round((Get-Item $out).Length / 1MB, 2)
Write-Host ""
Write-Host "Rows per table in the bundle:"
foreach ($t in $tables.Keys) {
    $note = if ($excluded -contains $t) { "  (data excluded, $($after[$t]) rows stay on the laptop)" } else { "" }
    Write-Host ("  {0,-22} {1,8}{2}" -f $t, $tables[$t], $note)
}
Write-Host "Simulated robot runs (labelled): $simulated"
Write-Host "Files: $($files.Count) (db.dump + media + tts_cache)"
Write-Host "Alembic $alembic, app $appVersion, commit $commit$(if ($dirty) { ' (dirty tree)' })"
Write-Host ""
Write-Host "Bundle written: $out ($size MB)"
Write-Host "It contains knowledge and robot data$(if ($IncludeConversations) { ' AND visitors'' conversations' }). Keep it private."
