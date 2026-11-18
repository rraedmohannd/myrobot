#!/usr/bin/env bash
# Writes the start times of the OTHER project's containers to a snapshot file (read only):
#
#     bash /srv/guest/app/deploy/server/guest/snapshot-others.sh
#
# For every running container whose Compose project label is not "guest", it runs only this exact
# command (the one form the server owner allows; a full inspect would print their secret keys):
#
#     docker inspect --format '{{.Name}} {{.State.Status}} {{.State.StartedAt}} {{.RestartCount}}' <name>
#
# and writes the lines to /srv/guest/state/others-<YYYYmmdd-HHMMSS>.txt. The last line on standard
# output is the path of that file (messages go to standard error). Compare two snapshots with
# compare-others.sh.
#
# --baseline: also keep this snapshot as /srv/guest/state/others-baseline.txt, the default "before"
# of compare-others.sh (install-guest.sh makes one on the first install).

set -euo pipefail
# shellcheck source=deploy/server/guest/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BASELINE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --baseline) BASELINE=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done
require_docker

mkdir -p "$STATE"
chmod 700 "$STATE"
stamp="$(date -u +%Y%m%d-%H%M%S)"
out="$STATE/others-$stamp.txt"
n=1
while [[ -e "$out" ]]; do out="$STATE/others-$stamp-$n.txt"; n=$((n + 1)); done

tmp="$(mktemp "$STATE/.others.XXXXXX")"
count=0
while IFS=$'\t' read -r name project; do
    [[ -n "$name" ]] || continue
    [[ "$project" == guest ]] && continue
    if docker inspect --format '{{.Name}} {{.State.Status}} {{.State.StartedAt}} {{.RestartCount}}' "$name" >>"$tmp" 2>/dev/null; then
        count=$((count + 1))
    else
        warn "could not read the start time of $name (it went away just now?)"
    fi
done < <(docker ps --format '{{.Names}}\t{{.Label "com.docker.compose.project"}}')
sort -o "$tmp" "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$out"
if [[ $BASELINE -eq 1 ]]; then
    cp -f "$out" "$STATE/others-baseline.txt"
    info "kept as the baseline: $STATE/others-baseline.txt" >&2
fi
info "snapshot of $count other running container(s): $out" >&2
sed 's/^/   /' "$out" >&2
printf '%s\n' "$out"
