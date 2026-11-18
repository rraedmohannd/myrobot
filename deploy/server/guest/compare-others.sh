#!/usr/bin/env bash
# Checks that nothing of the OTHER project restarted or stopped (read only):
#
#     bash .../compare-others.sh                  the baseline     vs  a new one taken now
#     bash .../compare-others.sh BEFORE.txt       BEFORE           vs  a new one taken now
#     bash .../compare-others.sh BEFORE AFTER     two snapshot files (snapshot-others.sh)
#
# For every container in BEFORE it compares status, start time and restart count with AFTER and
# SHOUTS when a container stopped or went away, restarted (restart count went up) or has another
# start time. Exit code 0 = all unchanged; 1 = something changed: tell the server owner at once.
# Containers that are new in AFTER are listed, but are not an alarm.
# The baseline is /srv/guest/state/others-baseline.txt (snapshot-others.sh --baseline), or the newest
# snapshot when there is no baseline.

set -euo pipefail
# shellcheck source=deploy/server/guest/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

files=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) usage; exit 0 ;;
        -*) usage >&2; die "unknown option: $1" ;;
        *) files+=("$1"); shift ;;
    esac
done
[[ ${#files[@]} -le 2 ]] || die "at most two snapshot files"

if [[ ${#files[@]} -eq 0 && -f "$STATE/others-baseline.txt" ]]; then
    files=("$STATE/others-baseline.txt")
elif [[ ${#files[@]} -eq 0 ]]; then
    newest="$(find "$STATE" -maxdepth 1 -name 'others-*.txt' ! -name others-baseline.txt -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -n 1 | cut -d' ' -f2-)"
    [[ -n "$newest" ]] || die "no snapshot yet in $STATE: run snapshot-others.sh before the change"
    files=("$newest")
fi
if [[ ${#files[@]} -eq 1 ]]; then
    files+=("$(bash "$SCRIPT_DIR/snapshot-others.sh" | tail -n 1)")
fi
BEFORE="${files[0]}"
AFTER="${files[1]}"
[[ -f "$BEFORE" ]] || die "not found: $BEFORE"
[[ -f "$AFTER" ]] || die "not found: $AFTER"
info "before: $BEFORE"
info "after:  $AFTER"

declare -A A_STATUS=() A_START=() A_RC=()
while read -r name status started rc; do
    [[ -n "$name" ]] || continue
    A_STATUS["$name"]="$status"
    A_START["$name"]="$started"
    A_RC["$name"]="$rc"
done <"$AFTER"

alarm=0
shout() {
    printf '!!!!! %s\n' "$*" >&2
    alarm=1
}
declare -A SEEN=()
n=0
while read -r name status started rc; do
    [[ -n "$name" ]] || continue
    n=$((n + 1))
    SEEN["$name"]=1
    if [[ -z "${A_STATUS[$name]+x}" ]]; then
        shout "$name: NOT RUNNING ANY MORE (stopped or removed); it was $status since $started"
        continue
    fi
    if [[ "${A_STATUS[$name]}" != "$status" ]]; then
        shout "$name: status changed $status -> ${A_STATUS[$name]}"
    fi
    if [[ "${A_RC[$name]}" != "$rc" ]]; then
        shout "$name: RESTARTED (restart count $rc -> ${A_RC[$name]})"
    fi
    if [[ "${A_START[$name]}" != "$started" ]]; then
        shout "$name: START TIME CHANGED $started -> ${A_START[$name]} (it was restarted)"
    fi
done <"$BEFORE"

for name in "${!A_STATUS[@]}"; do
    [[ -n "${SEEN[$name]+x}" ]] || note "new since before (not an alarm): $name ${A_STATUS[$name]} ${A_START[$name]}"
done

if [[ $alarm -ne 0 ]]; then
    printf '!!!!! SOMETHING OF THE OTHER PROJECT CHANGED (above). Stop and tell the server owner now.\n' >&2
    exit 1
fi
info "all $n container(s) of the other project unchanged (same status, start time and restart count)"
