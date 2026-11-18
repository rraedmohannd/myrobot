#!/usr/bin/env bash
# Tests for check-forbidden.sh (run by CI; may run anywhere, it only writes a temporary folder):
#
#     bash deploy/server/tests/test-guest-check-forbidden.sh
#
# Every forbidden form is written into its own fixture folder and must make the check FAIL; the
# allowed forms must PASS. This file lives OUTSIDE deploy/server/guest/ on purpose: it holds the
# forbidden lines as test data, and nothing of it is ever run on the server. (Some words are built
# from pieces, "pr""une", only so that a grep over the repository finds the real uses quickly.)

set -euo pipefail

CHECK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../guest" && pwd)/check-forbidden.sh"
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT

DC="docker com""pose"
P="pr""une"
fails=0
n=0

# expect RESULT NAME LINE: RESULT is fail or pass; LINE goes into a .sh fixture
expect() {
    local want="$1" name="$2" line="$3" d rc
    n=$((n + 1))
    d="$WORK/case$n"
    mkdir -p "$d"
    printf '#!/usr/bin/env bash\n%s\n' "$line" >"$d/fixture.sh"
    if bash "$CHECK" "$d" >"$d/out.txt" 2>&1; then rc=pass; else rc=fail; fi
    if [[ "$rc" == "$want" ]]; then
        printf 'ok   %-4s %s\n' "$want" "$name"
    else
        printf 'FAIL expected %s, got %s: %s\n' "$want" "$rc" "$name"
        sed 's/^/     /' "$d/out.txt"
        fails=$((fails + 1))
    fi
}

# --- must fail ---
expect fail "system $P" "docker system $P -af"
expect fail "volume $P" "docker volume $P"
expect fail "image $P" "docker image $P -a"
expect fail "git fetch --$P" "git fetch --$P origin"
expect fail "systemctl" "sys""temctl restart docker"
expect fail "apt" "a""pt upgrade -y"
expect fail "apt-get" "a""pt-get install jq"
expect fail "ufw" "u""fw allow 8100"
expect fail "iptables" "ip""tables -L"
expect fail "reboot" "sleep 1; re""boot"
expect fail "shutdown" "shut""down -h now"
expect fail "daemon.json" "cat /etc/docker/dae""mon.json"
expect fail "crontab" "cron""tab -l"
expect fail "compose without -p" "$DC down"
expect fail "compose with another project" "$DC -p other down"
expect fail "compose -p after the command" "$DC down -p guest"
expect fail "two compose calls, one without -p" "$DC -p guest ps && $DC up -d"
expect fail "old v1 compose" "docker-com""pose -p guest up -d"
expect fail "rm -rf outside" "r""m -rf /var/lib/something"
expect fail "rm -rf variable" "r""m -rf \"\$TMP\""
expect fail "rm -r -f outside" "r""m -r -f /etc/x"
expect fail "rm --recursive outside" "r""m --recursive --force /opt/x"
expect fail "rm -rf with .." "r""m -rf /srv/guest/../etc"
expect fail "rm -Rf second path outside" "r""m -Rf /srv/guest/a /tmp/b"
expect fail "docker logs" "docker lo""gs some-container"
expect fail "docker exec" "docker ex""ec -it some-container sh"
expect fail "docker stop all" "docker st""op \$(docker ps -q)"
expect fail "docker restart" "docker res""tart x"
expect fail "docker rm -f" "docker r""m -f x"
expect fail "full inspect" "docker insp""ect x"
expect fail "other inspect format" "docker insp""ect -f '{{.Config.Env}}' x"

# --- must pass ---
expect pass "comment with forbidden words" "# never $P, never sys""temctl, never $DC down"
expect pass "compose with -p guest" "$DC -p guest --env-file /srv/guest/.env -f x.yml ps"
expect pass "dc helper" "dc --profile tunnel up -d cloudflared"
expect pass "rm -rf under /srv/guest" "r""m -rf -- /srv/guest/state/tmp/x"
expect pass "rm -rf under GUEST_ROOT" "r""m -rf -- \"\$GUEST_ROOT/state/tmp/\$name\""
expect pass "rm -f (not recursive)" "r""m -f -- \"\$tmp\""
expect pass "allowed inspect" "docker insp""ect --format '{{.Name}} {{.State.Status}} {{.State.StartedAt}} {{.RestartCount}}' \"\$name\""
expect pass "image inspect" "docker image insp""ect guest-backend:0.6.0"
expect pass "file name docker-compose.guest.yml" "f=/srv/guest/app/docker-com""pose.guest.yml"
expect pass "docker ps and image rm by tag" "docker ps -q; docker image rm guest-backend:0.5.0"

# --- explanations (.md, .example) are not scanned ---
n=$((n + 1))
mkdir -p "$WORK/case$n"
printf 'Never run docker system %s or %s down.\n' "$P" "$DC" >"$WORK/case$n/README.md"
printf '# a comment\nX=1\n' >"$WORK/case$n/guest.env.example"
if bash "$CHECK" "$WORK/case$n" >/dev/null 2>&1; then
    printf 'ok   pass .md and .example are not scanned\n'
else
    printf 'FAIL .md / .example were scanned\n'
    fails=$((fails + 1))
fi

# --- the real folder is clean ---
n=$((n + 1))
if bash "$CHECK" >/dev/null 2>&1; then
    printf 'ok   pass deploy/server/guest itself\n'
else
    bash "$CHECK" || true
    printf 'FAIL deploy/server/guest itself\n'
    fails=$((fails + 1))
fi

if [[ $fails -ne 0 ]]; then
    printf '%d of %d cases FAILED\n' "$fails" "$n" >&2
    exit 1
fi
printf 'all %d cases OK\n' "$n"
