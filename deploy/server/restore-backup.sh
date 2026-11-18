#!/usr/bin/env bash
# Restores a backup made by the backup service (nightly or backup-now.sh) into the live stack:
#
#     sudo bash deploy/server/restore-backup.sh --list
#     sudo bash deploy/server/restore-backup.sh STAMP [--yes]       (STAMP like 20270110-030000)
#
# REPLACES the live database with backups/db-STAMP.dump and the media + speech cache with
# backups/files-STAMP.tar.gz (if that file exists). Everything changed after that backup is lost.
# It asks you to type YES (or --yes), and makes a fresh backup first (backup-now.sh), so the
# restore itself can be undone. The backend is stopped during the restore, started afterwards
# (it migrates the database to the code's revision) and health-checked.
#
# Environment: SCR_PROJECT (default smart-campus-prod), SCR_ENV_FILE (default deploy/server/.env).

set -euo pipefail
# shellcheck source=deploy/server/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

STAMP=""
YES=0
LIST=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes) YES=1; shift ;;
        --list) LIST=1; shift ;;
        -h | --help) usage; exit 0 ;;
        -*) usage >&2; die "unknown option: $1" ;;
        *)
            [[ -z "$STAMP" ]] || die "only one STAMP, got '$STAMP' and '$1'"
            STAMP="$1"; shift ;;
    esac
done
require_env_file

if [[ $LIST -eq 1 ]]; then
    info "backups in the backups volume (project $SCR_PROJECT)"
    dc exec -T backup sh -c 'ls -l /backups' | sed 's/^/   /'
    exit 0
fi
[[ -n "$STAMP" ]] || { usage >&2; die "STAMP is missing (see --list)"; }
[[ "$STAMP" =~ ^[0-9]{8}-[0-9]{6}$ ]] || die "STAMP must look like 20270110-030000 (see --list)"

DB_FILE="/backups/db-$STAMP.dump"
FILES_FILE="/backups/files-$STAMP.tar.gz"
dc exec -T backup test -s "$DB_FILE" || die "$DB_FILE not found in the backups volume (see --list)"
HAVE_FILES=0
if dc exec -T backup test -s "$FILES_FILE"; then HAVE_FILES=1; fi

info "restore backup $STAMP into project $SCR_PROJECT"
note "database: $DB_FILE"
if [[ $HAVE_FILES -eq 1 ]]; then note "files:    $FILES_FILE"; else note "files:    none for this stamp (media and speech cache are left as they are)"; fi
note "This REPLACES the live database. Everything changed after this backup is lost."
if [[ $YES -ne 1 ]]; then
    [[ -t 0 ]] || die "no terminal to confirm: add --yes"
    read -r -p "   Type YES to continue: " answer
    [[ "$answer" == YES ]] || die "cancelled, nothing was changed"
fi

info "1/5 fresh backup of the current state first"
bash "$SCRIPTS/backup-now.sh"

info "2/5 stop the backend"
dc stop backend

info "3/5 restore the database"
dc exec -T backup cat "$DB_FILE" | copy_stdin_to_db_container /tmp/restore.dump
if ! restore_dump_in_db; then
    warn "pg_restore failed: the database may be empty now. The backup made in step 1 is the way back:"
    warn "  bash $SCRIPTS/restore-backup.sh --list   (the newest stamp), then restore it"
    exit 1
fi

info "4/5 media and speech cache"
if [[ $HAVE_FILES -eq 1 ]]; then
    # the top folders in the archive (media and/or tts_cache); others are not restored
    mapfile -t dirs < <(dc exec -T backup tar -tzf "$FILES_FILE" | sed 's|^\./||' | cut -d/ -f1 |
        grep -xE 'media|tts_cache' | sort -u)
    if [[ ${#dirs[@]} -gt 0 ]]; then
        dc exec -T backup cat "$FILES_FILE" | replace_files_from_tar z "${dirs[@]}"
    else
        note "files-$STAMP.tar.gz holds neither media/ nor tts_cache/: left as they are"
    fi
else
    note "skipped (no files-$STAMP.tar.gz)"
fi

info "5/5 start the backend (it migrates the database to the code's revision)"
dc up -d backend
wait_healthy 240 || die "the backend is not healthy after the restore (see the log above)"
note "sources: $(psql_db 'SELECT count(*) FROM sources'), chunks: $(psql_db 'SELECT count(*) FROM chunks')"
info "restored backup $STAMP"
