#!/usr/bin/env bash
# Shared helpers for the guest-mode scripts in deploy/server/guest/ (sourced, never run directly).
#
# Guest mode = our stack beside another live project on a shared server (docs/DEPLOY_GUEST.md).
# Everything of ours lives in one folder, GUEST_ROOT = /srv/guest:
#   /srv/guest/app/          a copy of the repository (docker-compose.guest.yml, deploy/)
#   /srv/guest/.env          the settings (mode 600, made by install-guest.sh)
#   /srv/guest/state/        snapshots of the other containers, temporary folders
#   /srv/guest/bundles/      data bundles from the PC
#   /srv/guest/images/       backend images from the PC (guest-backend-<v>.tar.gz + .sha256)
# and every Docker command names the Compose project "guest" (the dc helper below).
#
# The scripts refuse to run unless they are themselves in /srv/guest/app/deploy/server/guest.
# GUEST_TEST_ROOT (TEST ONLY): a throw-away folder used instead of /srv/guest by the script tests;
# its last path part must start with "guest", it must be at least two levels deep and it may not be
# a system folder. Never set it on the real server.
# shellcheck shell=bash

info() { printf '== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die() {
    printf '!! %s\n' "$*" >&2
    exit 1
}

# Prints the comment block at the top of the calling script (line 2 up to the first blank line).
usage() { sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# --- where are we? ---------------------------------------------------------------------------
_guest_test_root_ok() {
    local p="$1" base
    [[ "$p" == /* ]] || return 1
    [[ "$p" != *..* ]] || return 1
    base="${p##*/}"
    [[ "$base" =~ ^guest[A-Za-z0-9._-]*$ ]] || return 1
    # at least two levels deep (/a/guest-x), never a system folder or something inside one
    [[ "$p" =~ ^/[^/]+/.+ ]] || return 1
    case "$p" in
        /srv/guest | /srv/guest/*) return 1 ;; # that is the real one: no override needed
        /bin/* | /boot/* | /dev/* | /etc/* | /lib/* | /lib32/* | /lib64/* | /libx32/* | /proc/* | \
            /run/* | /sbin/* | /sys/* | /usr/* | /var/* | /snap/* | /opt/* | /srv/*) return 1 ;;
    esac
    return 0
}

if [[ -n "${GUEST_TEST_ROOT:-}" ]]; then
    _guest_test_root_ok "$GUEST_TEST_ROOT" ||
        die "GUEST_TEST_ROOT=$GUEST_TEST_ROOT refused (test only: an absolute folder named guest*, not a system folder)"
    [[ -d "$GUEST_TEST_ROOT" ]] || die "GUEST_TEST_ROOT=$GUEST_TEST_ROOT does not exist"
    GUEST_ROOT="$(cd "$GUEST_TEST_ROOT" && pwd -P)"
    _guest_test_root_ok "$GUEST_ROOT" || die "GUEST_TEST_ROOT resolves to $GUEST_ROOT: refused"
    warn "TEST MODE: GUEST_TEST_ROOT=$GUEST_ROOT is used instead of /srv/guest (never on the real server)"
else
    GUEST_ROOT=/srv/guest
fi
[[ "$SCRIPT_DIR" == "$GUEST_ROOT/app/deploy/server/guest" ]] ||
    die "this script must run from $GUEST_ROOT/app/deploy/server/guest (it is in $SCRIPT_DIR): refused. See docs/DEPLOY_GUEST.md."

GUEST_ENV="$GUEST_ROOT/.env"
GUEST_COMPOSE="$GUEST_ROOT/app/docker-compose.guest.yml"
# shellcheck disable=SC2034  # used by the scripts that source this file
STATE="$GUEST_ROOT/state"
# shellcheck disable=SC2034
BUNDLES="$GUEST_ROOT/bundles"
# shellcheck disable=SC2034
IMAGES="$GUEST_ROOT/images"
GUEST_IMAGE_DEFAULT="guest-backend:0.6.0"
# docker-compose.guest.yml reads the same file through env_file: ${GUEST_ENV_FILE:-...}
export GUEST_ENV_FILE="$GUEST_ENV"

# The only docker compose call of guest mode: always project "guest", always our env and compose file.
dc() { docker compose -p guest --env-file "$GUEST_ENV" -f "$GUEST_ROOT/app/docker-compose.guest.yml" "$@"; }

require_docker() {
    command -v docker >/dev/null || die "docker is not on this machine (we never install it: ask the server owner)"
    docker version --format '{{.Server.Version}}' >/dev/null 2>&1 ||
        die "cannot talk to Docker: this user must be in the docker group (ask the server owner; never sudo)"
    docker compose -p guest version >/dev/null 2>&1 || die "the Compose plugin of Docker is missing (ask the server owner; we never install it)"
}

require_env_file() {
    [[ -f "$GUEST_ENV" ]] || die "settings file $GUEST_ENV not found: run install-guest.sh first"
    [[ -f "$GUEST_COMPOSE" ]] || die "$GUEST_COMPOSE not found: copy the app folder first (docs/DEPLOY_GUEST.md)"
}

# --- settings file (data, never sourced; values are never printed) ------------------------------
env_get() {
    local key="$1" file="${2:-$GUEST_ENV}" line
    [[ -f "$file" ]] || return 0
    line="$(grep -E "^${key}=" "$file" | tail -n 1 || true)"
    line="${line#*=}"
    line="${line%$'\r'}"
    if [[ ${#line} -ge 2 && ( ( "${line:0:1}" == '"' && "${line: -1}" == '"' ) || ( "${line:0:1}" == "'" && "${line: -1}" == "'" ) ) ]]; then
        line="${line:1:${#line}-2}"
    fi
    printf '%s' "$line"
}

env_has() {
    local file="${2:-$GUEST_ENV}"
    [[ -f "$file" ]] && grep -qE "^${1}=" "$file"
}

# env_set KEY VALUE [FILE]: replaces every KEY=... line (or appends one); the value goes through the
# environment, never a command line. Keeps mode 600.
env_set() {
    local key="$1" file="${3:-$GUEST_ENV}" tmp
    tmp="$(mktemp "$(dirname "$file")/.guest.env.XXXXXX")"
    chmod 600 "$tmp"
    SCR_KEY="$key" SCR_VAL="$2" awk '
        BEGIN { k = ENVIRON["SCR_KEY"]; v = ENVIRON["SCR_VAL"]; done = 0 }
        index($0, k "=") == 1 { if (!done) { print k "=" v; done = 1 } next }
        { print }
        END { if (!done) print k "=" v }
    ' "$file" >"$tmp"
    mv -f "$tmp" "$file"
}

guest_image() {
    local v
    v="$(env_get GUEST_IMAGE)"
    printf '%s' "${v:-$GUEST_IMAGE_DEFAULT}"
}

# Secrets: letters and digits only (safe in the database URL and in shell scripts).
gen_secret() { head -c 4096 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-48; }
# Access code xxxx-xxxx-xxxx (no 0/o/1/l/i, easy to read out).
gen_code() {
    local c
    c="$(head -c 4096 /dev/urandom | LC_ALL=C tr -dc 'abcdefghjkmnpqrstuvwxyz23456789' | cut -c1-12)"
    printf '%s-%s-%s' "${c:0:4}" "${c:4:4}" "${c:8:4}"
}

# A temporary folder inside $GUEST_ROOT/state/tmp; prints its NAME (not the path). Remove it with
# rm_tmp NAME: removal only ever happens below $GUEST_ROOT.
make_tmp() {
    mkdir -p "$GUEST_ROOT/state/tmp"
    chmod 700 "$GUEST_ROOT/state/tmp"
    basename "$(mktemp -d "$GUEST_ROOT/state/tmp/t.XXXXXX")"
}
rm_tmp() {
    local name="$1"
    [[ "$name" =~ ^t\.[A-Za-z0-9]{6}$ ]] || return 0
    rm -rf -- "$GUEST_ROOT/state/tmp/$name"
}

# --- containers of our project -------------------------------------------------------------------
# true when our quick-tunnel container (service cloudflared, profile tunnel) runs. A plain "dc up -d"
# (without --profile tunnel) never touches it, so its public address stays the same.
tunnel_running() {
    [[ -n "$(docker ps -q --filter label=com.docker.compose.project=guest --filter label=com.docker.compose.service=cloudflared)" ]]
}

# run_tunnel_url [ARGS]: tunnel-url.sh (agent F1: reads the address from the cloudflared log,
# writes PUBLIC_BASE_URL into the env file, re-creates only the backend), with our paths
run_tunnel_url() {
    GUEST_ENV_FILE="$GUEST_ENV" GUEST_COMPOSE_FILE="$GUEST_COMPOSE" bash "$SCRIPT_DIR/tunnel-url.sh" "$@"
}

health_once() {
    local path="${1:-/health}"
    dc exec -T backend python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000$path', timeout=5)" \
        >/dev/null 2>&1
}

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

# python3 on the host if there is one, else the backend image's Python (no install ever needed).
# Usage: py_run CODE [ARGS...] <stdin
py_run() {
    local code="$1"
    shift
    if command -v python3 >/dev/null; then
        python3 -I -c "$code" "$@"
    else
        dc run --rm --no-deps -T --entrypoint python backend -I -c "$code" "$@"
    fi
}

# --- database and files (same logic as deploy/server/lib.sh, project guest) -----------------------
psql_db() {
    # shellcheck disable=SC2016  # expanded by sh inside the db container
    dc exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"' sh "$1"
}

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

copy_stdin_to_db_container() {
    # shellcheck disable=SC2016
    dc exec -T db sh -c 'cat >"$1"' sh "$1"
}

# replace_files_from_tar Z DIR...: see deploy/server/lib.sh (unpacks straight into the volumes, as uid 10001)
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

last_backup_json() {
    dc exec -T backup cat /backups/last-backup.json 2>/dev/null || true
}

# backup_now: one database + files backup by our backup service (deploy/backup/backup.sh once)
backup_now() {
    dc exec -T backup sh /scr/backup.sh once
}
