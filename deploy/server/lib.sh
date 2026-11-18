#!/usr/bin/env bash
# Shared helpers for the server scripts in deploy/server/ (sourced, never run directly).
#
#   REPO        the checkout (this file's directory/../..)
#   ENV_FILE    ${SCR_ENV_FILE:-$REPO/deploy/server/.env}
#   dc ...      docker compose for the production stack (project ${SCR_PROJECT:-smart-campus-prod})
#
# Rehearsals on the laptop set SCR_PROJECT=scr-rehearsal SCR_ENV_FILE=... so they never touch the
# developer stack (project smart-campus-robot) or a real production stack.
# shellcheck shell=bash

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${SCR_ENV_FILE:-$REPO/deploy/server/.env}"
# docker-compose.prod.yml reads the same file through env_file: ${SCR_ENV_FILE:-...}
export SCR_ENV_FILE="$ENV_FILE"
SCR_PROJECT="${SCR_PROJECT:-smart-campus-prod}"
# shellcheck disable=SC2034  # used by the scripts that source this file
SCRIPTS="$REPO/deploy/server"
# shellcheck disable=SC2034
ROLLBACK_TAG="smart-campus-backend:rollback"

info() { printf '== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die() {
    printf '!! %s\n' "$*" >&2
    exit 1
}

# Prints the comment block at the top of the calling script (line 2 up to the first blank line).
usage() { sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; }

require_root() {
    [[ $EUID -eq 0 ]] || die "run this as root (sudo bash $0 ...)"
}

require_env_file() {
    [[ -f "$ENV_FILE" ]] || die "settings file $ENV_FILE not found: run deploy/server/install.sh first"
}

# env_get KEY [FILE]: the value of the last KEY=value line, surrounding quotes removed. The file is
# never sourced (it is data, not code). Callers must not print secret values.
env_get() {
    local key="$1" file="${2:-$ENV_FILE}" line
    [[ -f "$file" ]] || return 0
    line="$(grep -E "^${key}=" "$file" | tail -n 1 || true)"
    line="${line#*=}"
    line="${line%$'\r'}"
    if [[ ${#line} -ge 2 && ( ( "${line:0:1}" == '"' && "${line: -1}" == '"' ) || ( "${line:0:1}" == "'" && "${line: -1}" == "'" ) ) ]]; then
        line="${line:1:${#line}-2}"
    fi
    printf '%s' "$line"
}

# env_has KEY [FILE]: true when a KEY=... line exists (even with an empty value).
env_has() {
    local file="${2:-$ENV_FILE}"
    [[ -f "$file" ]] && grep -qE "^${1}=" "$file"
}

# env_set KEY VALUE [FILE]: replaces every KEY=... line (or appends one). The value is passed through
# the environment, never on a command line, and is not printed. Keeps the file mode (600).
env_set() {
    local key="$1" file="${3:-$ENV_FILE}" tmp
    tmp="$(mktemp "$(dirname "$file")/.env.XXXXXX")"
    chmod 600 "$tmp"
    SCR_KEY="$key" SCR_VAL="$2" awk '
        BEGIN { k = ENVIRON["SCR_KEY"]; v = ENVIRON["SCR_VAL"]; done = 0 }
        index($0, k "=") == 1 { if (!done) { print k "=" v; done = 1 } next }
        { print }
        END { if (!done) print k "=" v }
    ' "$file" >"$tmp"
    mv -f "$tmp" "$file"
}

use_caddy() {
    local v
    v="$(env_get USE_CADDY | tr '[:upper:]' '[:lower:]')"
    [[ -z "$v" || "$v" == true || "$v" == 1 || "$v" == yes ]]
}

backend_image() {
    local v
    v="$(env_get BACKEND_IMAGE)"
    printf '%s' "${v:-smart-campus-backend:local}"
}

# docker compose for the production stack (docs: CONTRACT / docs/DEPLOY.md "Server mode")
dc() {
    require_env_file
    local -a extra=()
    if use_caddy; then
        extra=(--profile caddy)
    else
        # NGINX MODE (USE_CADDY=false, behind an existing web server): the override file from agent B
        # that publishes the backend on BACKEND_BIND. Change this one line if that mechanism changes.
        extra=(-f "$REPO/docker-compose.prod.nginx.yml")
    fi
    docker compose -p "$SCR_PROJECT" --env-file "$ENV_FILE" -f "$REPO/docker-compose.prod.yml" \
        "${extra[@]}" "$@"
}

# health_once [PATH]: true when the backend answers HTTP 200 on PATH (default /health) from inside
# its own container (so it works with and without Caddy).
health_once() {
    local path="${1:-/health}"
    dc exec -T backend python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000$path', timeout=5)" \
        >/dev/null 2>&1
}

# wait_healthy [SECONDS] [PATH]: polls the health URL until HTTP 200 or the timeout (default 240 s).
wait_healthy() {
    local timeout="${1:-240}" path="${2:-/health}" start
    start=$SECONDS
    printf '   waiting for the backend health check (up to %s s) ' "$timeout"
    while ((SECONDS - start < timeout)); do
        if health_once "$path"; then
            printf ' ok (%s s)\n' "$((SECONDS - start))"
            return 0
        fi
        printf '.'
        sleep 3
    done
    printf ' TIMEOUT\n'
    warn "the backend did not become healthy in $timeout s; its last log lines:"
    dc logs --no-color --tail 30 backend >&2 || true
    return 1
}

# psql_db SQL: runs SQL in the production database (credentials from the db container's own
# environment, so no password passes through this script). Prints unaligned, tuples only.
psql_db() {
    # shellcheck disable=SC2016  # expanded by sh inside the db container
    dc exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"' sh "$1"
}

# restore_dump_in_db: replaces the production database with /tmp/restore.dump inside the db
# container (drop + create + CREATE EXTENSION vector + pg_restore in one transaction).
# The backend must be stopped first. The dump file is removed afterwards.
restore_dump_in_db() {
    # shellcheck disable=SC2016  # expanded by sh inside the db container
    dc exec -T db sh -euc '
        dropdb -U "$POSTGRES_USER" --if-exists --force "$POSTGRES_DB"
        createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"
        psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS vector"
        pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges \
            --exit-on-error --single-transaction /tmp/restore.dump
        rm -f /tmp/restore.dump
    '
}

# copy_stdin_to_db_container FILE: streams standard input to FILE inside the db container.
copy_stdin_to_db_container() {
    # shellcheck disable=SC2016
    dc exec -T db sh -c 'cat >"$1"' sh "$1"
}

# replace_files_from_tar Z DIR...: reads a tar archive on standard input (gzip when Z is "z", plain
# when Z is "") whose members are DIR/... (DIR = media and/or tts_cache, as made by backup.sh and by
# restore-bundle.sh) and REPLACES the content of those volumes with it, as the backend user 10001.
# The archive is unpacked straight into the volumes: the backend container's root file system is
# read-only and its /tmp is a small tmpfs, so no staging copy is made.
replace_files_from_tar() {
    local z="$1"
    shift
    local d
    for d in "$@"; do
        [[ "$d" == media || "$d" == tts_cache ]] || die "replace_files_from_tar: unexpected folder $d"
    done
    # shellcheck disable=SC2016  # expanded by sh inside the container
    dc run --rm --no-deps -T --user 10001:10001 --entrypoint sh backend -euc '
        z="$1"; shift
        for d in "$@"; do find "/app/data/$d" -mindepth 1 -delete; done
        tar -x"${z}"f - -C /app/data --no-same-owner "$@"
        for d in "$@"; do echo "   $d: $(find "/app/data/$d" -type f | wc -l) files restored"; done
    ' sh "$z" "$@"
}

# last_backup_json: the backup service's /backups/last-backup.json (empty if none yet).
last_backup_json() {
    dc exec -T backup cat /backups/last-backup.json 2>/dev/null || true
}
