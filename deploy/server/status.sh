#!/usr/bin/env bash
# Shows the state of the production stack (read only, prints no secret):
#
#     sudo bash deploy/server/status.sh
#
# Containers, backend health (status, version, migration), disk use, the last backup, today's
# paid OpenAI calls against the daily limit, and the HTTPS certificate's expiry date.
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

DOMAIN="$(env_get DOMAIN)"
info "project $SCR_PROJECT, $(git -C "$REPO" log -1 --format='code %h %cs %s' 2>/dev/null || echo 'code: not a git checkout')"
note "domain: ${DOMAIN:-<not set>}, Caddy: $(use_caddy && echo yes || echo 'no (behind an existing web server)'), backend image: $(backend_image)"

info "containers"
dc ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}' || warn "docker compose ps failed"

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
' 2>/dev/null)" || health="no answer (is the backend running? docker compose logs backend)"
note "$health"

info "disk"
df -h / | sed 's/^/   /'
docker system df 2>/dev/null | sed 's/^/   /' || true

info "last backup"
last="$(last_backup_json | tr -d '\n')"
note "${last:-none yet (the first nightly backup runs at BACKUP_HOUR, or run backup-now.sh)}"

info "paid OpenAI calls today"
usage_line="$(dc exec -T backend python -m app.ingestion.cli usage-today 2>/dev/null | tail -n 1)" ||
    usage_line="not available (backend not running, or the code has no usage-today command yet)"
note "$usage_line"

info "HTTPS certificate"
if use_caddy && [[ -n "$DOMAIN" ]]; then
    port="$(env_get HTTPS_PORT)"
    port="${port:-443}"
    end="$(timeout 15 openssl s_client -connect "127.0.0.1:$port" -servername "$DOMAIN" </dev/null 2>/dev/null |
        openssl x509 -noout -enddate -issuer 2>/dev/null || true)"
    if [[ -n "$end" ]]; then
        printf '%s\n' "$end" | sed 's/^/   /'
        note "(Caddy renews it by itself about 30 days before it expires)"
    else
        note "no certificate served yet on port $port. Caddy gets one when $DOMAIN points to this server"
        note "and ports 80 + 443 are open: dc logs caddy  (sudo docker compose -p $SCR_PROJECT logs caddy)"
    fi
else
    note "not managed here (USE_CADDY=false: the existing web server holds the certificate)"
fi
