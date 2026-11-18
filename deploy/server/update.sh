#!/usr/bin/env bash
# Updates the production stack to new code, with a health check and an automatic rollback:
#
#     sudo bash deploy/server/update.sh                  pull the current branch (fast-forward only)
#     sudo bash deploy/server/update.sh --ref v1.2.0     a tag, a branch or a commit
#     sudo bash deploy/server/update.sh --ref v1.2.0 --image ghcr.io/rraedmohannd/smart-campus-backend:v1.2.0
#
# Steps: 1. backup (backup-now.sh)  2. keep the running backend image as smart-campus-backend:rollback
# 3. git fetch + checkout/pull  4. build the backend image here, or with --image pull a ready-made
# image (built by GitHub Actions; "docker login ghcr.io" first if it is private) and write it as
# BACKEND_IMAGE into the settings file, so later restarts keep using it  5. docker compose up -d
# (the backend migrates the database on start)  6. health check.
# If the health check fails: the previous image, the previous commit and the previous BACKEND_IMAGE
# are put back and started again. Database migrations are NOT reversed: if the new code migrated
# the database, the backup from step 1 is the way back (restore-backup.sh STAMP, printed below).
#
# Options:
#   --ref REF                  tag, branch or commit (default: pull the current branch)
#   --image IMAGE              use this backend image instead of building on the server
#   --simulate-failed-health   TEST ONLY: the health check asks for a URL that does not exist, so it
#                              fails on purpose and the rollback runs (rehearsal, docs/SPRINT_6.md P0.6)
# Environment: SCR_PROJECT (default smart-campus-prod), SCR_ENV_FILE (default deploy/server/.env).

set -euo pipefail
# shellcheck source=deploy/server/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

REF=""
IMAGE=""
SIMULATE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ref) [[ $# -ge 2 && -n "$2" ]] || die "--ref needs a value"; REF="$2"; shift 2 ;;
        --image) [[ $# -ge 2 && -n "$2" ]] || die "--image needs a value"; IMAGE="$2"; shift 2 ;;
        --simulate-failed-health) SIMULATE=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done
[[ -z "$REF" || "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || die "odd --ref: $REF"
[[ -z "$IMAGE" || "$IMAGE" =~ ^[A-Za-z0-9._/:@-]+$ ]] || die "odd --image: $IMAGE"
require_env_file
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "$REPO is not a git checkout"
if [[ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]]; then
    git -C "$REPO" status --short --untracked-files=no >&2
    die "the checkout has local changes (above): commit them elsewhere or undo them (git -C $REPO checkout -- <file>)"
fi
[[ $SIMULATE -eq 0 ]] || warn "TEST ONLY: --simulate-failed-health: the health check will fail on purpose"

OLD_COMMIT="$(git -C "$REPO" rev-parse HEAD)"
OLD_BRANCH="$(git -C "$REPO" symbolic-ref -q --short HEAD || true)"
OLD_IMAGE_NAME="$(backend_image)"
OLD_IMAGE_SETTING="$(env_get BACKEND_IMAGE)"
had_image_setting=0
if env_has BACKEND_IMAGE; then had_image_setting=1; fi

info "1/6 backup"
bash "$SCRIPTS/backup-now.sh" || die "the backup failed: nothing was updated"
BACKUP_STAMP="$(last_backup_json | jq -r '.stamp // empty' 2>/dev/null || true)"
note "backup stamp: ${BACKUP_STAMP:-unknown}"

info "2/6 keep the running image ($OLD_IMAGE_NAME) as $ROLLBACK_TAG"
OLD_IMAGE_ID="$(docker image inspect -f '{{.Id}}' "$OLD_IMAGE_NAME" 2>/dev/null)" ||
    die "image $OLD_IMAGE_NAME not found: start the stack once (install.sh) before updating"
docker tag "$OLD_IMAGE_ID" "$ROLLBACK_TAG"
note "${OLD_IMAGE_ID:0:19} tagged $ROLLBACK_TAG; code at ${OLD_COMMIT:0:10}${OLD_BRANCH:+ (branch $OLD_BRANCH)}"

# puts the checkout back where it was (the tree was clean, checked at the start)
restore_code() {
    if [[ -n "$OLD_BRANCH" ]]; then
        git -C "$REPO" checkout -q "$OLD_BRANCH"
        git -C "$REPO" reset -q --hard "$OLD_COMMIT"
    else
        git -C "$REPO" checkout -q --detach "$OLD_COMMIT"
    fi
}

info "3/6 code"
git -C "$REPO" fetch --prune --tags origin || die "git fetch failed: nothing was updated"
code_ok=1
if [[ -z "$REF" ]]; then
    [[ -n "$OLD_BRANCH" ]] || die "the checkout is not on a branch (a tag?): pass --ref REF"
    git -C "$REPO" merge -q --ff-only "origin/$OLD_BRANCH" || code_ok=0
elif git -C "$REPO" show-ref --verify -q "refs/remotes/origin/$REF"; then
    { git -C "$REPO" checkout -q "$REF" && git -C "$REPO" merge -q --ff-only "origin/$REF"; } || code_ok=0
elif git -C "$REPO" rev-parse -q --verify "$REF^{commit}" >/dev/null; then
    git -C "$REPO" checkout -q --detach "$REF" || code_ok=0
else
    die "--ref $REF: no such tag, branch or commit on origin: nothing was updated"
fi
if [[ $code_ok -ne 1 ]]; then
    restore_code
    die "the code could not be updated (not a fast-forward?): checkout put back, nothing was changed"
fi
NEW_COMMIT="$(git -C "$REPO" rev-parse HEAD)"
note "now at $(git -C "$REPO" log -1 --format='%h %cs %s')"

rollback() {
    warn "ROLLBACK: putting back commit ${OLD_COMMIT:0:10}, image ${OLD_IMAGE_ID:0:19} as $OLD_IMAGE_NAME"
    restore_code
    if [[ $had_image_setting -eq 1 ]]; then
        env_set BACKEND_IMAGE "$OLD_IMAGE_SETTING"
    elif env_has BACKEND_IMAGE; then
        env_set BACKEND_IMAGE "$OLD_IMAGE_NAME"
    fi
    docker tag "$ROLLBACK_TAG" "$OLD_IMAGE_NAME"
    dc up -d --no-build
    if wait_healthy 240; then
        warn "the previous version runs again and is healthy."
    else
        warn "the previous version is NOT healthy either."
    fi
    warn "Database migrations made by the new version are NOT reversed. If the new version migrated"
    warn "the database (or the old one does not start), restore the backup made in step 1:"
    warn "  sudo bash $SCRIPTS/restore-backup.sh ${BACKUP_STAMP:-<stamp, see --list>}"
    exit 1
}

info "4/6 backend image"
if [[ -n "$IMAGE" ]]; then
    docker pull "$IMAGE" || { warn "docker pull $IMAGE failed (docker login ghcr.io for a private image?)"; rollback; }
    env_set BACKEND_IMAGE "$IMAGE"
    note "BACKEND_IMAGE=$IMAGE written to $ENV_FILE"
else
    dc build || { warn "the build failed"; rollback; }
fi

info "5/6 start (the backend migrates the database to the new head)"
dc up -d || { warn "docker compose up failed"; rollback; }

info "6/6 health check"
if [[ $SIMULATE -eq 1 ]]; then
    wait_healthy 15 /health-simulated-failure-test-only || rollback
else
    wait_healthy 240 || rollback
fi
if [[ "$NEW_COMMIT" == "$OLD_COMMIT" && -z "$IMAGE" ]]; then
    note "the code did not change (rebuilt and restarted anyway)"
fi
info "updated to $(git -C "$REPO" log -1 --format='%h %s') and healthy. Rollback image kept as $ROLLBACK_TAG."
