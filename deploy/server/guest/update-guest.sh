#!/usr/bin/env bash
# Updates the guest stack to a new backend image, with a health check and an automatic rollback:
#
#     bash .../update-guest.sh /srv/guest/images/guest-backend-0.6.1.tar.gz   load the file, then switch
#     bash .../update-guest.sh --tag guest-backend:0.6.1                       switch to a loaded tag
#     bash .../update-guest.sh --tag guest-backend:0.6.0                       manual rollback to the old one
#     bash .../update-guest.sh --restart                                       apply edited /srv/guest/.env
#
# Steps: 1. load the image (load-image.sh: SHA-256 check)  2. backup (database + files)
# 3. GUEST_IMAGE=<new tag> in /srv/guest/.env  4. docker compose -p guest up -d (the backend migrates the
# database)  5. health check. If it fails: GUEST_IMAGE goes back to the previous tag and the stack is
# started again. Database migrations are NOT reversed: the backup of step 2 is the way back.
# 6. on success, old guest-backend images are removed one by one by exact tag (never a bulk clean-up):
# the new tag and the previous one (for a manual rollback) are kept.
# A new docker-compose.guest.yml / scripts: copy the new app folder first (docs/DEPLOY_GUEST.md).
# Plain "up -d" without the tunnel profile: the quick tunnel is not re-created, its address stays.
#
# Options:
#   --tag IMAGE                use an image that is already loaded (guest-backend:<version>)
#   --restart                  keep the image, recreate what changed in /srv/guest/.env, health check
#   --simulate-failed-health   TEST ONLY: the health check asks for a URL that does not exist, so
#                              the rollback runs on purpose

set -euo pipefail
# shellcheck source=deploy/server/guest/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FILE=""
TAG=""
RESTART=0
SIMULATE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag) [[ $# -ge 2 && -n "$2" ]] || die "--tag needs a value"; TAG="$2"; shift 2 ;;
        --restart) RESTART=1; shift ;;
        --simulate-failed-health) SIMULATE=1; shift ;;
        -h | --help) usage; exit 0 ;;
        -*) usage >&2; die "unknown option: $1" ;;
        *) [[ -z "$FILE" ]] || die "only one image file"; FILE="$1"; shift ;;
    esac
done
n_modes=0
[[ -n "$FILE" ]] && n_modes=$((n_modes + 1))
[[ -n "$TAG" ]] && n_modes=$((n_modes + 1))
[[ $RESTART -eq 1 ]] && n_modes=$((n_modes + 1))
[[ $n_modes -eq 1 ]] || { usage >&2; die "give exactly one of: an image file, --tag IMAGE, --restart"; }
require_docker
require_env_file
[[ $SIMULATE -eq 0 ]] || warn "TEST ONLY: --simulate-failed-health: the health check will fail on purpose"

OLD="$(guest_image)"
docker image inspect "$OLD" >/dev/null 2>&1 || warn "the current image $OLD is not on this machine: a rollback would fail"

info "1/6 image"
if [[ -n "$FILE" ]]; then
    TAG="$(bash "$SCRIPT_DIR/load-image.sh" "$FILE" | tail -n 1)"
elif [[ $RESTART -eq 1 ]]; then
    TAG="$OLD"
fi
[[ "$TAG" =~ ^guest-backend:[A-Za-z0-9._-]+$ ]] || die "only guest-backend:<version> images: got '$TAG'"
docker image inspect "$TAG" >/dev/null 2>&1 || die "image $TAG is not loaded: load-image.sh FILE first"
note "now $OLD, new $TAG"


info "2/6 backup"
if [[ $RESTART -eq 1 ]]; then
    note "skipped (--restart: same image, no migration)"
else
    backup_now || die "the backup failed: nothing was updated"
    note "backup: $(last_backup_json | tr -d '\n')"
fi

rollback() {
    warn "ROLLBACK: GUEST_IMAGE back to $OLD"
    env_set GUEST_IMAGE "$OLD"
    dc up -d || true
    if wait_healthy 240; then
        warn "the previous version runs again and is healthy."
    else
        warn "the previous version is NOT healthy either: status-guest.sh, and tell Mohannd."
    fi
    warn "Database migrations made by the new version are NOT reversed. If it migrated the database"
    warn "and the old version misbehaves, the backup from step 2 is the way back (deploy/backup)."
    exit 1
}

info "3/6 GUEST_IMAGE=$TAG"
printf '%s\n' "$OLD" >"$STATE/previous-image"
env_set GUEST_IMAGE "$TAG"

info "4/6 start (docker compose -p guest up -d)"
dc up -d || { warn "compose up failed"; rollback; }

info "5/6 health check"
if [[ $SIMULATE -eq 1 ]]; then
    wait_healthy 15 /health-simulated-failure-test-only || rollback
else
    wait_healthy 240 || rollback
fi

info "6/6 old guest-backend images (removed by exact tag; $TAG and $OLD are kept)"
mapfile -t ALL < <(docker image ls guest-backend --format '{{.Repository}}:{{.Tag}}' | grep -E '^guest-backend:[A-Za-z0-9._-]+$' | sort -u || true)
removed=0
for img in "${ALL[@]}"; do
    [[ "$img" == "$TAG" || "$img" == "$OLD" ]] && continue
    if docker image rm "$img" >/dev/null; then
        note "removed $img"
        removed=$((removed + 1))
    else
        warn "could not remove $img (still used?)"
    fi
done
[[ $removed -gt 0 ]] || note "nothing to remove"

if tunnel_running; then note "quick tunnel untouched (same address): $(env_get PUBLIC_BASE_URL)"; fi
info "updated to $TAG and healthy. Manual rollback: update-guest.sh --tag $OLD"
