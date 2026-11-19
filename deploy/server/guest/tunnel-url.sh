#!/usr/bin/env bash
# Route 2 (Cloudflare quick tunnel): makes the backend use the tunnel's current public address.
#
#     bash /srv/guest/app/deploy/server/guest/tunnel-url.sh             # read, write, re-create, print
#     bash /srv/guest/app/deploy/server/guest/tunnel-url.sh --print     # only print the address
#     bash /srv/guest/app/deploy/server/guest/tunnel-url.sh --wait 120  # wait up to 120 s for it (default 90)
#
# 1. reads the newest https://<words>.trycloudflare.com from the log of the cloudflared service;
# 2. writes it to PUBLIC_BASE_URL in the env file (only when it changed; the file keeps its mode 600);
# 3. re-creates ONLY the backend (up -d --no-deps --force-recreate backend), never cloudflared: the
#    address changes whenever cloudflared is re-created;
# 4. waits for the backend health check and prints the address and the links.
#
# The address is random and changes whenever the cloudflared container is re-created (not on a plain
# restart of the same container). Run this script after every start of the stack with --profile tunnel.
#
# Environment (defaults = the server layout):
#   GUEST_ENV_FILE       /srv/guest/.env
#   GUEST_COMPOSE_FILE   /srv/guest/app/docker-compose.guest.yml
#   GUEST_DC             the whole Docker Compose command, split on spaces, instead of
#                        "docker compose -p guest --env-file $GUEST_ENV_FILE -f $GUEST_COMPOSE_FILE";
#                        it must name its project with -p (the PC rehearsal uses -p guest-rehearsal).
# Prints no secret. Touches only the env file's PUBLIC_BASE_URL line and the backend container.

set -euo pipefail

usage() { sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; }

WAIT=90
PRINT_ONLY=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --wait)
            [[ "${2:-}" =~ ^[0-9]+$ ]] || { usage >&2; exit 2; }
            WAIT="$2"
            shift 2
            ;;
        --print) PRINT_ONLY=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

ENV_FILE="${GUEST_ENV_FILE:-/srv/guest/.env}"
# GUEST_ENV_FILE is also read by docker-compose.guest.yml (env_file:)
export GUEST_ENV_FILE="$ENV_FILE"
if [[ -n "${GUEST_DC:-}" ]]; then
    read -r -a DC <<<"$GUEST_DC"
    [[ " ${DC[*]} " == *" -p "* ]] || { echo "!! GUEST_DC must name its project with -p" >&2; exit 2; }
else
    DC=(docker compose -p guest --env-file "$ENV_FILE" -f "${GUEST_COMPOSE_FILE:-/srv/guest/app/docker-compose.guest.yml}")
fi
dc() { "${DC[@]}" --profile tunnel "$@"; }

[[ -f "$ENV_FILE" ]] || { echo "!! env file $ENV_FILE not found (GUEST_ENV_FILE)" >&2; exit 1; }

# The newest https://<name>.trycloudflare.com in the cloudflared log (api.trycloudflare.com is the
# service cloudflared asks for a tunnel and appears in error lines: not ours).
current_url() {
    dc logs --no-color --no-log-prefix cloudflared 2>&1 |
        grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | grep -v '^https://api\.' | tail -n 1 || true
}

running() {
    [[ -n "$(dc ps --status running -q cloudflared 2>/dev/null)" ]]
}

env_get() {
    local line
    line="$(grep -E "^$1=" "$ENV_FILE" | tail -n 1 || true)"
    line="${line#*=}"
    printf '%s' "${line%$'\r'}"
}

# Replaces every KEY=... line (or appends one), keeping the file's mode. The value is not secret.
env_set() {
    local tmp
    tmp="$(mktemp "$(dirname "$ENV_FILE")/.env.XXXXXX")"
    chmod 600 "$tmp"
    KEY="$1" VAL="$2" awk '
        BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VAL"]; done = 0 }
        index($0, k "=") == 1 { if (!done) { print k "=" v; done = 1 } next }
        { print }
        END { if (!done) print k "=" v }
    ' "$ENV_FILE" >"$tmp"
    mv -f "$tmp" "$ENV_FILE"
}

backend_healthy() {
    dc exec -T backend python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)" \
        >/dev/null 2>&1
}

# 1. the address
url=""
start=$SECONDS
while :; do
    if running; then url="$(current_url)"; fi
    [[ -n "$url" ]] && break
    if ((SECONDS - start >= WAIT)); then
        if running; then
            echo "!! cloudflared runs but has no address yet (logs: ${DC[*]} --profile tunnel logs cloudflared)" >&2
        else
            echo "!! cloudflared is not running (start: ${DC[*]} --profile tunnel up -d)" >&2
        fi
        exit 1
    fi
    sleep 2
done

if [[ $PRINT_ONLY -eq 1 ]]; then
    printf '%s\n' "$url"
    exit 0
fi

# 2 + 3. PUBLIC_BASE_URL, then re-create only the backend
if [[ "$(env_get PUBLIC_BASE_URL)" == "$url" ]]; then
    echo "== PUBLIC_BASE_URL is already $url (backend not re-created)"
else
    env_set PUBLIC_BASE_URL "$url"
    echo "== PUBLIC_BASE_URL=$url written; re-creating only the backend"
    dc up -d --no-deps --force-recreate backend
fi

# 4. health
printf '   waiting for the backend health check '
start=$SECONDS
until backend_healthy; do
    if ((SECONDS - start >= 240)); then
        printf ' TIMEOUT\n'
        dc logs --no-color --tail 30 backend >&2 || true
        exit 1
    fi
    printf '.'
    sleep 3
done
printf ' ok\n'

cat <<EOF

  Public address (changes whenever cloudflared is re-created):
    $url
    Robot screen : $url/robot
    Voice test   : $url/voice
    Admin        : $url/admin
  Visitors type the access code (SITE_ACCESS_CODE in $ENV_FILE) once on /access.

EOF
