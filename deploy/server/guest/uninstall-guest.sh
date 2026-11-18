#!/usr/bin/env bash
# Stops and removes the guest stack (only project "guest"; nothing else on the server is touched):
#
#     bash /srv/guest/app/deploy/server/guest/uninstall-guest.sh            stop + remove our containers
#     bash /srv/guest/app/deploy/server/guest/uninstall-guest.sh --purge    ... AND delete all our data
#
# Without --purge: docker compose -p guest down (the volumes with the database, media, speech cache
# and backups stay, and so does /srv/guest; install-guest.sh or "up" brings it back).
# With --purge (asks you to type PURGE GUEST): also our volumes (guest_*), our guest-backend images
# (each by exact tag) and the folder /srv/guest with .env, bundles and images. This cannot be
# undone: copy a backup to the PC first if the data matters.
#
# Options:
#   --purge              delete the data too (see above)
#   --confirm TEXT       non-interactive confirmation; TEXT must be exactly "PURGE GUEST"

set -euo pipefail
# shellcheck source=deploy/server/guest/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PURGE=0
CONFIRM=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge) PURGE=1; shift ;;
        --confirm) [[ $# -ge 2 ]] || die "--confirm needs a value"; CONFIRM="$2"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done
require_docker
require_env_file

if [[ $PURGE -eq 0 ]]; then
    info "docker compose -p guest down (volumes and $GUEST_ROOT are kept)"
    dc --profile tunnel down --remove-orphans
    info "stopped. Data kept. Back: bash $SCRIPT_DIR/install-guest.sh"
    exit 0
fi

info "PURGE: this deletes ALL data of the guest stack:"
note "containers and volumes of Compose project guest (database, media, speech cache, backups)"
docker image ls guest-backend --format 'image {{.Repository}}:{{.Tag}}' | sed 's/^/   /' || true
note "the folder $GUEST_ROOT (.env, bundles, images, snapshots, the app copy)"
if [[ -z "$CONFIRM" ]]; then
    [[ -t 0 ]] || die "no terminal to confirm: add --confirm \"PURGE GUEST\""
    read -r -p "   Type PURGE GUEST to delete it all: " CONFIRM
fi
[[ "$CONFIRM" == "PURGE GUEST" ]] || die "not confirmed, nothing was deleted"

info "1/3 containers and volumes (project guest only)"
dc --profile tunnel down --volumes --remove-orphans

info "2/3 guest-backend images (by exact tag)"
mapfile -t ALL < <(docker image ls guest-backend --format '{{.Repository}}:{{.Tag}}' | grep -E '^guest-backend:[A-Za-z0-9._-]+$' | sort -u || true)
for img in "${ALL[@]}"; do
    if docker image rm "$img" >/dev/null; then note "removed $img"; else warn "could not remove $img"; fi
done

info "3/3 the folder $GUEST_ROOT"
# GUEST_ROOT is /srv/guest, or a validated test folder (lib.sh); checked once more right here
[[ "$GUEST_ROOT" == /srv/guest || -n "${GUEST_TEST_ROOT:-}" ]] || die "unexpected GUEST_ROOT $GUEST_ROOT"
[[ "$GUEST_ROOT" =~ ^/[^/]+/guest[A-Za-z0-9._-]*$|^/[^/]+/.+/guest[A-Za-z0-9._-]*$ ]] || die "unexpected GUEST_ROOT $GUEST_ROOT"
cd /
rm -rf -- "$GUEST_ROOT"
info "purged. Nothing of the guest stack is left on this server."
