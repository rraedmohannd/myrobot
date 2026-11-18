#!/usr/bin/env bash
# Short status of the guest stack (read only, prints no secret):
#
#     bash /srv/guest/app/deploy/server/guest/status-guest.sh
#
# Backend health, the public address (quick tunnel), today's paid OpenAI calls against the daily
# cap, memory per container (docker stats, guest containers only), disk use and the last backup.

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
require_env_file

info "guest stack: image $(guest_image), port $(env_get GUEST_BIND):$(env_get GUEST_PORT)"
dc --profile tunnel ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}' || warn "compose ps failed"

mapfile -t IDS < <(docker ps -q --filter label=com.docker.compose.project=guest)
if [[ ${#IDS[@]} -gt 0 ]]; then
    info "memory (limit: all of ours together at most 1 GB)"
    docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}' "${IDS[@]}" | sed 's/^/   /'
fi

info "backend health"
# shellcheck disable=SC2016
health="$(dc exec -T backend python -c '
import json, urllib.request, urllib.error
try:
    r = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5)
    code, body = r.status, r.read()
except urllib.error.HTTPError as e:
    code, body = e.code, e.read()
d = json.loads(body)
keys = ("status", "version", "database", "pgvector", "migration")
print("HTTP %s: " % code + " ".join("%s=%s" % (k, d[k]) for k in keys if k in d))
' 2>/dev/null)" || health="no answer (is the backend running?)"
note "$health"

info "public address"
if tunnel_running; then
    url="$(run_tunnel_url --print --wait 5 2>/dev/null | tail -n 1 || true)"
    note "quick tunnel running: ${url:-no address yet (tunnel.sh url)}"
else
    note "quick tunnel NOT running (tunnel.sh up). PUBLIC_BASE_URL=$(env_get PUBLIC_BASE_URL)"
fi

info "disk"
df -h "$GUEST_ROOT" | sed 's/^/   /'
du -sh "$GUEST_ROOT/images" "$GUEST_ROOT/bundles" "$GUEST_ROOT/state" 2>/dev/null | sed 's/^/   /' || true
# shellcheck disable=SC2016
dc exec -T backend sh -c 'du -sh /app/data/media /app/data/tts_cache /backups 2>/dev/null' | sed 's/^/   /' || true
db_size="$(psql_db 'SELECT pg_size_pretty(pg_database_size(current_database()))' 2>/dev/null || true)"
note "database: ${db_size:-?}"
docker image ls guest-backend --format 'image {{.Repository}}:{{.Tag}} {{.Size}}' | sed 's/^/   /' || true

info "last backup"
last="$(last_backup_json | tr -d '\n')"
note "${last:-none yet (the first one runs at BACKUP_HOUR UTC)}"

info "paid OpenAI calls today (cap: DAILY_PAID_CALL_LIMIT=$(env_get DAILY_PAID_CALL_LIMIT))"
usage_line="$(dc exec -T backend python -m app.ingestion.cli usage-today 2>/dev/null | tail -n 1)" ||
    usage_line="not available (backend not running?)"
note "$usage_line"
