#!/usr/bin/env bash
# First installation of the guest stack in /srv/guest (never install.sh: that one installs packages
# and changes the firewall, which is forbidden on a shared server). No sudo: the SSH user is in the
# docker group and owns /srv/guest.
#
#     bash /srv/guest/app/deploy/server/guest/install-guest.sh
#
# Before: /srv/guest exists and is ours, the app folder is in /srv/guest/app, the backend image is
# loaded (load-image.sh). Steps: 1. folders /srv/guest/{state,bundles,images}  2. preflight.sh
# (stops on any problem)  3. /srv/guest/.env from guest.env.example with new secrets (mode 600,
# never printed; only the visitor access code is shown once), asking for the OpenAI key with hidden
# input. It must be a SEPARATE key for this server with a MONTHLY SPENDING LIMIT set in the OpenAI
# dashboard. An existing /srv/guest/.env is kept as it is.  4. snapshot-others.sh
# 5. docker compose -p guest --profile tunnel up -d (db, backend, backup, cloudflared)  6. health
# 7. tunnel-url.sh: the public https address -> PUBLIC_BASE_URL (backend re-created)  8. compare-others.sh
#
# Options:
#   --no-tunnel         without the quick tunnel (reachable only through an SSH tunnel to 127.0.0.1)
#   --public-url URL    PUBLIC_BASE_URL to start with (https://...); the tunnel replaces it
#   --openai-key-stdin  read the key from standard input instead of the hidden prompt
#   --fake-providers    TEST ONLY: FAKE_PROVIDERS=true, no key (answers are SIMULATED). Never on the server.

set -euo pipefail
# shellcheck source=deploy/server/guest/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TUNNEL=1
PUBLIC_URL=""
KEY_STDIN=0
FAKE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-tunnel) TUNNEL=0; shift ;;
        --public-url) [[ $# -ge 2 && -n "$2" ]] || die "--public-url needs a value"; PUBLIC_URL="$2"; shift 2 ;;
        --openai-key-stdin) KEY_STDIN=1; shift ;;
        --fake-providers) FAKE=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done
[[ -z "$PUBLIC_URL" || "$PUBLIC_URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?/?$ ]] || die "--public-url must be https://<name>"
[[ -d "$GUEST_ROOT" && -w "$GUEST_ROOT" ]] ||
    die "$GUEST_ROOT is missing or not writable for $(id -un): ask the server owner to create it owned by this user (we never use sudo)"
[[ -f "$GUEST_COMPOSE" ]] || die "$GUEST_COMPOSE not found: copy the app folder first (docs/DEPLOY_GUEST.md)"
[[ -f "$SCRIPT_DIR/guest.env.example" ]] || die "$SCRIPT_DIR/guest.env.example not found"
require_docker

info "1/8 folders in $GUEST_ROOT"
mkdir -p "$GUEST_ROOT/state" "$GUEST_ROOT/bundles" "$GUEST_ROOT/images"
chmod 750 "$GUEST_ROOT"
chmod 700 "$GUEST_ROOT/state"
note "state/ bundles/ images/ ready"

info "2/8 preflight (read only)"
bash "$SCRIPT_DIR/preflight.sh" >"$GUEST_ROOT/state/preflight-last.txt" 2>&1 || {
    cat "$GUEST_ROOT/state/preflight-last.txt" >&2
    die "preflight failed (above): nothing was installed"
}
grep -E '^(==|   |!!)' "$GUEST_ROOT/state/preflight-last.txt" | sed -n '/== summary/,$p'

info "3/8 settings file $GUEST_ENV"
if [[ -f "$GUEST_ENV" ]]; then
    note "exists: kept as it is (secrets unchanged). Delete it only with uninstall-guest.sh --purge."
else
    # a placeholder until tunnel-url.sh writes the real address (production needs an https:// value)
    PUBLIC_URL="${PUBLIC_URL:-https://pending.invalid}"
    PUBLIC_URL="${PUBLIC_URL%/}"
    KEY=""
    if [[ $FAKE -eq 1 ]]; then
        warn "TEST ONLY: --fake-providers: FAKE_PROVIDERS=true, no OpenAI key, every answer is SIMULATED"
    elif [[ $KEY_STDIN -eq 1 ]]; then
        IFS= read -r KEY || true
    else
        [[ -t 0 ]] || die "no terminal for the hidden key prompt: run it in an interactive SSH session (or --openai-key-stdin)"
        note "Paste the OpenAI key for THIS SERVER: a separate key, not the PC's, with a monthly"
        note "spending limit set in the OpenAI dashboard. Input is hidden."
        IFS= read -r -s -p "   OpenAI key: " KEY
        printf '\n'
    fi
    KEY="${KEY//[[:space:]]/}"
    if [[ $FAKE -eq 0 ]]; then
        [[ "$KEY" =~ ^sk-[A-Za-z0-9_-]{20,}$ ]] || die "that does not look like an OpenAI key (sk-...): nothing was written"
    fi
    CODE="$(gen_code)"
    tmp="$(mktemp "$GUEST_ROOT/.guest.env.XXXXXX")"
    chmod 600 "$tmp"
    # each __GENERATE__ gets its own secret; values go through the environment, never a command line
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" == *__GENERATE__* ]]; then line="${line//__GENERATE__/"$(gen_secret)"}"; fi
        line="${line//__ACCESS_CODE__/"$CODE"}"
        line="${line//__PUBLIC_BASE_URL__/"$PUBLIC_URL"}"
        line="${line//__OPENAI_API_KEY__/"$KEY"}"
        if [[ $FAKE -eq 1 && "$line" == FAKE_PROVIDERS=* ]]; then line="FAKE_PROVIDERS=true"; fi
        printf '%s\n' "$line"
    done <"$SCRIPT_DIR/guest.env.example" >"$tmp"
    unset KEY
    if grep -qE '__(GENERATE|ACCESS_CODE|PUBLIC_BASE_URL|OPENAI_API_KEY)__' "$tmp"; then
        rm -f -- "$tmp"
        die "a placeholder was not replaced: nothing was written"
    fi
    mv -f "$tmp" "$GUEST_ENV"
    chmod 600 "$GUEST_ENV"
    note "written (mode 600). PUBLIC_BASE_URL=$PUBLIC_URL"
    note "Visitor access code (shown only now; send it by private message only): $CODE"
    unset CODE
fi
IMG="$(guest_image)"
docker image inspect "$IMG" >/dev/null 2>&1 || die "image $IMG is not loaded: run load-image.sh first"
note "backend image: $IMG"

info "4/8 snapshot of the other project's containers (before)"
if [[ -f "$STATE/others-baseline.txt" ]]; then
    BEFORE="$(bash "$SCRIPT_DIR/snapshot-others.sh" | tail -n 1)"
else
    BEFORE="$(bash "$SCRIPT_DIR/snapshot-others.sh" --baseline | tail -n 1)"
fi

info "5/8 start (docker compose -p guest up -d)"
if [[ $TUNNEL -eq 1 ]]; then
    dc --profile tunnel up -d
else
    dc up -d
fi

info "6/8 health"
wait_healthy 240 || die "the backend is not healthy (log above). Nothing of the other project was touched; compare-others.sh $BEFORE"

info "7/8 public address"
if [[ $TUNNEL -eq 1 ]]; then
    if run_tunnel_url --wait 120; then
        wait_healthy 240 || die "the backend is not healthy after tunnel-url.sh (log above)"
    else
        warn "no tunnel address yet: run tunnel.sh url in a minute"
    fi
else
    note "--no-tunnel: no public address (SSH tunnel to 127.0.0.1:$(env_get GUEST_PORT) only)"
fi

info "8/8 the other project unchanged?"
bash "$SCRIPT_DIR/compare-others.sh" "$BEFORE" || die "SOMETHING OF THE OTHER PROJECT CHANGED: stop and tell the server owner"

info "installed. Next: restore-bundle.sh, then create-admin.sh NAME (docs/DEPLOY_GUEST.md)"
