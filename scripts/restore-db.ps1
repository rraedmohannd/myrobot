# Restore a backup made by scripts\backup-db.ps1.
#
#   powershell -ExecutionPolicy Bypass -File scripts\restore-db.ps1 backups\smart_campus-20261113-200000.dump
#       -> restores into a NEW database "smart_campus_restore" (safe: the live data is untouched),
#          so you can check the backup first.
#
#   powershell -ExecutionPolicy Bypass -File scripts\restore-db.ps1 backups\<file>.dump -Replace
#       -> REPLACES the live database with the backup. Everything changed since the backup is
#          lost. The script asks you to type YES, and makes a fresh backup first.

param(
    [Parameter(Mandatory = $true)][string]$File,
    [switch]$Replace
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path $File)) { throw "File not found: $File" }

$container = (docker compose ps -q db).Trim()
if (-not $container) { throw "The db container is not running (docker compose up -d)." }
docker cp $File "${container}:/tmp/restore.dump"
if ($LASTEXITCODE -ne 0) { throw "could not copy the dump into the container" }

if ($Replace) {
    Write-Host "This REPLACES the live database with $File. Changes made after that backup will be lost."
    $answer = Read-Host "Type YES to continue"
    if ($answer -ne "YES") { Write-Host "Cancelled. Nothing was changed."; exit 1 }
    & "$PSScriptRoot\backup-db.ps1"
    docker compose stop backend
    docker compose exec -T db sh -c 'pg_restore -U $POSTGRES_USER -d $POSTGRES_DB --clean --if-exists --no-owner /tmp/restore.dump'
    $code = $LASTEXITCODE
    docker compose start backend
    if ($code -ne 0) { throw "pg_restore reported errors. Check the output above; your fresh backup is in backups\." }
    docker compose exec -T backend alembic upgrade head
    Write-Host "Live database restored from $File."
}
else {
    # (no double quotes inside the commands: Windows PowerShell 5 mangles them for native programs)
    docker compose exec -T db sh -c 'dropdb -U $POSTGRES_USER --if-exists smart_campus_restore && createdb -U $POSTGRES_USER smart_campus_restore && pg_restore -U $POSTGRES_USER -d smart_campus_restore --no-owner /tmp/restore.dump'
    if ($LASTEXITCODE -ne 0) { throw "pg_restore reported errors. Check the output above." }
    foreach ($table in "sources", "chunks", "sessions", "explainers", "faqs") {
        $count = docker compose exec -T db sh -c "psql -U `$POSTGRES_USER -d smart_campus_restore -Atc 'select count(*) from $table'"
        Write-Host ("  {0}: {1} rows" -f $table, $count)
    }
    Write-Host "Restored into the separate database smart_campus_restore (the live database was not touched)."
    Write-Host "When you have checked it, remove it with:"
    Write-Host '  docker compose exec db sh -c "dropdb -U $POSTGRES_USER smart_campus_restore"'
}
docker compose exec -T db rm -f /tmp/restore.dump
