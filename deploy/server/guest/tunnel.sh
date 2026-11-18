#!/usr/bin/env bash
# Starts, shows or stops the Cloudflare quick tunnel (service cloudflared, profile tunnel), the
# public HTTPS address of the guest stack (no account, no port opened, nothing asked of the owner):
#
#     bash /srv/guest/app/deploy/server/guest/tunnel.sh url       the current address (tunnel-url.sh)
#     bash /srv/guest/app/deploy/server/guest/tunnel.sh up        start it if it is not running, then url
#     bash /srv/guest/app/deploy/server/guest/tunnel.sh restart   re-create it: the address CHANGES
#     bash /srv/guest/app/deploy/server/guest/tunnel.sh down      stop it (the backend keeps running)
#
# A thin wrapper: the address is read, written into PUBLIC_BASE_URL and applied (backend only) by
# tunnel-url.sh. Good to know:
#  - The address is random and NEW whenever the cloudflared container is re-created. Nothing here
#    re-creates it except "restart" (and "down" + "up"); send the new address after that.
#  - Quick tunnels come with no uptime guarantee; Cloudflare describes them as meant for testing.
#    Fine for a supervisor demo; a stable address is a later step.
#  - Anyone with the address reaches the access page; the pages still need the access code, and
#    paid calls are capped per day by DAILY_PAID_CALL_LIMIT.

set -euo pipefail
# shellcheck source=deploy/server/guest/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CMD=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        up | down | url | restart) [[ -z "$CMD" ]] || die "one command only"; CMD="$1"; shift ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown argument: $1" ;;
    esac
done
[[ -n "$CMD" ]] || { usage >&2; die "say url, up, restart or down"; }
[[ -f "$SCRIPT_DIR/tunnel-url.sh" ]] || die "$SCRIPT_DIR/tunnel-url.sh is missing: copy the app folder again"
require_docker
require_env_file

case "$CMD" in
    url)
        run_tunnel_url --print --wait 10
        ;;
    up)
        if tunnel_running; then
            note "the tunnel is already running (not re-created: the address stays the same)"
        else
            info "starting the quick tunnel (a NEW address)"
            dc --profile tunnel up -d --no-recreate cloudflared
        fi
        run_tunnel_url
        ;;
    restart)
        warn "re-creating the tunnel: the public address CHANGES; send the new one to everyone who uses it"
        dc --profile tunnel up -d --force-recreate --no-deps cloudflared
        run_tunnel_url
        ;;
    down)
        dc --profile tunnel rm -s -f cloudflared
        info "quick tunnel stopped (its address no longer works). The backend keeps running."
        ;;
esac
