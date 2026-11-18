#!/usr/bin/env bash
# Makes a backup of the production stack now (the same as the nightly one):
#
#     sudo bash deploy/server/backup-now.sh
#
# Runs "backup.sh once" in the backup service: the database (db-<stamp>.dump, pg_dump custom
# format) and the media + speech cache files (files-<stamp>.tar.gz) in the backups volume, then
# prints last-backup.json and the newest files. Restore one with restore-backup.sh <stamp>.
#
# Environment: SCR_PROJECT (default smart-campus-prod), SCR_ENV_FILE (default deploy/server/.env).

set -euo pipefail
# shellcheck source=deploy/server/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done
require_env_file

info "backup now (project $SCR_PROJECT)"
dc exec -T backup sh /scr/backup.sh once || die "the backup failed (see above); nothing was deleted"
note "last-backup.json: $(last_backup_json | tr -d '\n')"
note "newest files in the backups volume:"
dc exec -T backup sh -c 'ls -lt /backups | head -n 7' | sed 's/^/     /'
