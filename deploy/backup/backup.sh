#!/bin/sh
# Backups of the production stack (service "backup" in docker-compose.prod.yml, a pgvector/pg16
# image, so pg_dump has the server's major version).
#
#   sh /scr/backup.sh daemon   every day at BACKUP_HOUR:00 (container time = UTC), forever
#   sh /scr/backup.sh once     one full backup now, database AND files (deploy/server/backup-now.sh:
#                              dc exec -T backup sh /scr/backup.sh once)
#   sh /scr/backup.sh scheduled  one backup now with the daemon's rules (files only when due, see
#                              BACKUP_FILES_EVERY_DAYS); for tests and for a missed night
#
# Each backup writes into /backups (the "backups" volume):
#   db-YYYYmmdd-HHMMSS.dump        pg_dump custom format (-Fc) of the whole database
#   files-YYYYmmdd-HHMMSS.tar.gz   media/ and tts_cache/ (mounted read-only at /data)
#   last-backup.json               {"time": ISO-8601 UTC, "stamp": "...", "db_bytes": n, "files_bytes": n,
#                                   "files_stamp": "..."}
# and then deletes db-*.dump / files-*.tar.gz older than BACKUP_KEEP_DAYS (14) days, except that the
# newest files-*.tar.gz is always kept. A failed backup leaves no partial file, does not touch
# last-backup.json and deletes nothing.
#
# BACKUP_FILES_EVERY_DAYS (default 1 = every backup, the production behaviour): the daemon writes the
# files tarball only when the newest one is at least this many days old (the guest stack uses 7:
# the database every night, media + speech cache once a week, to stay in its disk budget).
# "files_bytes"/"files_stamp" in last-backup.json then describe that newest tarball.
#
# Connection: PGHOST/PGPORT/PGUSER/PGDATABASE and PGPASSWORD from the environment (compose fills
# them from deploy/server/.env). The password is never printed; there is no `set -x`.
#
# Restore (deploy/server/restore-backup.sh does this; by hand, with the stack's project name):
#   1. Stop the backend:   dc stop backend
#   2. Database:           dc exec -T backup sh -c 'pg_restore --clean --if-exists --no-owner \
#                              -d "$PGDATABASE" /backups/db-<stamp>.dump'
#      (into an empty database: drop and recreate it first, then pg_restore without --clean)
#   3. Files:              dc run --rm --no-deps --entrypoint sh backend \
#                              -c 'tar -xzf /backups/files-<stamp>.tar.gz -C /app/data'
#      (the backend mounts the backups volume read-only at /backups and runs as uid 10001; the
#      tarball holds media/... and tts_cache/..., relative to /app/data)
#   4. Start again:        dc up -d backend   (alembic upgrade head runs on start)
# To copy a backup off the server: docker cp <project>-backup-1:/backups/db-<stamp>.dump .
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DATA_DIR="${BACKUP_DATA_DIR:-/data}"
BACKUP_HOUR="${BACKUP_HOUR:-3}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
BACKUP_FILES_EVERY_DAYS="${BACKUP_FILES_EVERY_DAYS:-1}"
APP_GID=10001 # group of the backend container user (backend/Dockerfile)

log() { printf '%s backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# Decimal value of a number that may have leading zeros ("08" is not octal here).
num() {
    v=$(printf '%s' "$1" | sed 's/^0*//')
    printf '%s' "${v:-0}"
}

case "$BACKUP_HOUR" in '' | *[!0-9]*) log "BACKUP_HOUR must be 0..23, using 3"; BACKUP_HOUR=3 ;; esac
case "$BACKUP_KEEP_DAYS" in '' | *[!0-9]*) log "BACKUP_KEEP_DAYS must be a number, using 14"; BACKUP_KEEP_DAYS=14 ;; esac
case "$BACKUP_FILES_EVERY_DAYS" in '' | *[!0-9]*) log "BACKUP_FILES_EVERY_DAYS must be a number, using 1"; BACKUP_FILES_EVERY_DAYS=1 ;; esac
BACKUP_HOUR=$(num "$BACKUP_HOUR")
BACKUP_KEEP_DAYS=$(num "$BACKUP_KEEP_DAYS")
BACKUP_FILES_EVERY_DAYS=$(num "$BACKUP_FILES_EVERY_DAYS")
if [ "$BACKUP_HOUR" -gt 23 ]; then BACKUP_HOUR=3; fi
if [ "$BACKUP_KEEP_DAYS" -lt 1 ]; then BACKUP_KEEP_DAYS=1; fi
if [ "$BACKUP_FILES_EVERY_DAYS" -lt 1 ]; then BACKUP_FILES_EVERY_DAYS=1; fi

size_of() { wc -c <"$1" | tr -d ' '; }

# The newest files-*.tar.gz (the stamp in the name sorts by time), or nothing.
newest_files() {
    find "$BACKUP_DIR" -maxdepth 1 -type f -name 'files-*.tar.gz' 2>/dev/null | sort | tail -n 1
}

# True when the files tarball is due: always with BACKUP_FILES_EVERY_DAYS=1 or when there is none;
# otherwise when the newest one is at least that many days old (one hour of slack, so the backup at
# the same hour a week later is not pushed to the next day by a few seconds of drift).
files_due() {
    [ "$BACKUP_FILES_EVERY_DAYS" -le 1 ] && return 0
    newest=$(newest_files)
    [ -z "$newest" ] && return 0
    recent=$(find "$newest" -mmin -$((BACKUP_FILES_EVERY_DAYS * 1440 - 60)) 2>/dev/null)
    [ -z "$recent" ]
}

# backup_once [all|scheduled]: "all" (default) always writes the files tarball; "scheduled" only
# when files_due (the daemon).
backup_once() {
    mode="${1:-all}"
    stamp=$(date -u +%Y%m%d-%H%M%S)
    db_file="$BACKUP_DIR/db-$stamp.dump"
    files_file="$BACKUP_DIR/files-$stamp.tar.gz"
    mkdir -p "$BACKUP_DIR"
    # Backups hold the knowledge base and conversations (text): owner root, group 10001 (the
    # backend's group, so a restore can read the files tarball), nobody else.
    umask 027

    log "database -> $(basename "$db_file")"
    if ! pg_dump -Fc -f "$db_file.part"; then
        rm -f "$db_file.part"
        log "FAILED: pg_dump (database $PGDATABASE on $PGHOST)"
        return 1
    fi
    chgrp "$APP_GID" "$db_file.part" 2>/dev/null || true
    mv -f "$db_file.part" "$db_file"

    if [ "$mode" = all ] || files_due; then
        log "media + tts_cache -> $(basename "$files_file")"
        set --
        for d in media tts_cache; do
            if [ -d "$DATA_DIR/$d" ]; then set -- "$@" "$d"; fi
        done
        if ! tar -czf "$files_file.part" -C "$DATA_DIR" "$@"; then
            rm -f "$files_file.part"
            log "FAILED: tar of $DATA_DIR ($*)"
            return 1
        fi
        chgrp "$APP_GID" "$files_file.part" 2>/dev/null || true
        mv -f "$files_file.part" "$files_file"
    else
        log "media + tts_cache: not due (every $BACKUP_FILES_EVERY_DAYS days; newest: $(basename "$(newest_files)"))"
    fi

    db_bytes=$(size_of "$db_file")
    last_files=$(newest_files)
    files_bytes=0
    files_stamp=""
    if [ -n "$last_files" ]; then
        files_bytes=$(size_of "$last_files")
        files_stamp=$(basename "$last_files" .tar.gz)
        files_stamp=${files_stamp#files-}
    fi
    # last-backup.json is read by the backend (admin "Server" card, uid 10001): world-readable.
    printf '{"time": "%s", "stamp": "%s", "db_bytes": %s, "files_bytes": %s, "files_stamp": "%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$stamp" "$db_bytes" "$files_bytes" "$files_stamp" >"$BACKUP_DIR/last-backup.json.part"
    chmod 644 "$BACKUP_DIR/last-backup.json.part"
    mv -f "$BACKUP_DIR/last-backup.json.part" "$BACKUP_DIR/last-backup.json"
    log "done: db $db_bytes bytes, files $files_bytes bytes"

    # Retention: -mtime +N matches files at least N+1 whole days old, so keep_days-1 keeps
    # BACKUP_KEEP_DAYS days of backups. Only our own file names; never the bundles or anything else.
    # The newest files tarball always stays (with BACKUP_FILES_EVERY_DAYS > 1 it may be the only one,
    # and the speech cache costs money to make again).
    old=$((BACKUP_KEEP_DAYS - 1))
    find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'db-*.dump' -o -name 'files-*.tar.gz' \) \
        -mtime +"$old" -print | while IFS= read -r f; do
        if [ "$f" = "$last_files" ]; then continue; fi
        log "deleting old $(basename "$f")"
        rm -f "$f"
    done
    return 0
}

# Seconds from now until the next BACKUP_HOUR:00:00 (UTC), always 1..86400.
seconds_until_next() {
    h=$(num "$(date -u +%H)")
    m=$(num "$(date -u +%M)")
    s=$(num "$(date -u +%S)")
    wait_s=$((BACKUP_HOUR * 3600 - (h * 3600 + m * 60 + s)))
    if [ "$wait_s" -le 0 ]; then wait_s=$((wait_s + 86400)); fi
    printf '%s' "$wait_s"
}

daemon() {
    # Stop promptly on `docker compose stop` (sh as PID 1 ignores SIGTERM without a trap).
    trap 'log "stopping"; exit 0' TERM INT
    log "daemon: daily backup at $(printf '%02d' "$BACKUP_HOUR"):00 UTC, files every $BACKUP_FILES_EVERY_DAYS day(s), keeping $BACKUP_KEEP_DAYS days"
    while :; do
        wait_s=$(seconds_until_next)
        log "next backup in $wait_s s"
        sleep "$wait_s" &
        wait $!
        backup_once scheduled || log "this backup failed; trying again tomorrow"
    done
}

case "${1:-}" in
    daemon) daemon ;;
    once) backup_once all ;;
    scheduled) backup_once scheduled ;;
    *)
        echo "usage: sh $0 daemon|once|scheduled" >&2
        exit 2
        ;;
esac
