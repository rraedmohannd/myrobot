#!/usr/bin/env bash
# Looks at the server before anything is installed or changed (read only, changes nothing):
#
#     bash /srv/guest/app/deploy/server/guest/preflight.sh
#
# Runs exactly: docker ps, docker compose ls (as -p guest ls), df -h, free -m, ss -tlnp. Then prints the free disk,
# the free memory and which of our ports 8100-8199 are free.
# Exit code 1 (stop, do not install) when less than 6 GB of disk or less than 1 GB of memory is
# free, or when our port (GUEST_PORT in /srv/guest/.env, default 8100) is taken by something
# that is not our own "guest" project.

set -euo pipefail
# shellcheck source=deploy/server/guest/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done
require_docker

MIN_DISK_KB=$((6 * 1024 * 1024))
MIN_MEM_MB=1024
PORT="$(env_get GUEST_PORT)"
PORT="${PORT:-8100}"
[[ "$PORT" =~ ^[0-9]+$ && "$PORT" -ge 8100 && "$PORT" -le 8199 ]] || die "GUEST_PORT=$PORT: only 8100-8199 are ours"
bad=0

info "docker ps"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
info "compose projects (docker compose -p guest ls)"
# -p does not filter "ls" (it lists every project); it is there because every compose command of
# guest mode names the guest project (check-forbidden.sh)
docker compose -p guest ls
info "df -h"
df -h
info "free -m"
free -m
info "ss -tlnp"
ss -tlnp

GUEST_RUNNING=0
if [[ -n "$(docker ps -q --filter label=com.docker.compose.project=guest)" ]]; then
    GUEST_RUNNING=1
fi

info "summary"
# free disk: the file system of /srv/guest (or the nearest existing parent) and of Docker's data
min_kb=""
for p in "$GUEST_ROOT" /var/lib/docker; do
    q="$p"
    while [[ ! -e "$q" && "$q" != / ]]; do q="$(dirname "$q")"; done
    kb="$(df -Pk "$q" | awk 'NR == 2 { print $4 }')"
    note "free disk at $q: $((kb / 1024 / 1024)) GB ($((kb / 1024)) MB)"
    if [[ -z "$min_kb" || "$kb" -lt "$min_kb" ]]; then min_kb="$kb"; fi
done
if [[ "$min_kb" -lt "$MIN_DISK_KB" ]]; then
    warn "STOP: less than 6 GB of free disk"
    bad=1
fi

avail_mb="$(free -m | awk '/^Mem:/ { print $7 }')"
note "available memory: $avail_mb MB (our stack needs at most 1024 MB)"
if [[ "$avail_mb" -lt "$MIN_MEM_MB" ]]; then
    if [[ $GUEST_RUNNING -eq 1 ]]; then
        note "(our guest project is already running and uses part of it)"
    else
        warn "STOP: less than 1 GB of memory available"
        bad=1
    fi
fi

# listening TCP ports (local address column, e.g. 127.0.0.1:8100, [::]:8100, *:8100)
mapfile -t LISTEN < <(ss -tlnH | awk '{ print $4 }' | sed -E 's/.*:([0-9]+)$/\1/' | sort -un)
free_ports=()
for p in $(seq 8100 8199); do
    if [[ " ${LISTEN[*]} " != *" $p "* ]]; then free_ports+=("$p"); fi
done
note "free ports in 8100-8199: ${#free_ports[@]} (first ones: ${free_ports[*]:0:5})"
if [[ " ${LISTEN[*]} " == *" $PORT "* ]]; then
    ours="$(docker ps --filter label=com.docker.compose.project=guest --format '{{.Ports}}' | grep -c ":$PORT->" || true)"
    if [[ "$ours" -gt 0 ]]; then
        note "port $PORT: used by our own guest project (fine)"
    else
        warn "STOP: port $PORT is taken by something else. Pick a free one (GUEST_PORT in $GUEST_ENV)."
        bad=1
    fi
else
    note "port $PORT: free"
fi

if [[ $bad -ne 0 ]]; then
    die "preflight FAILED (see STOP above). Nothing was changed. Ask before going on."
fi
info "preflight OK. Nothing was changed."
