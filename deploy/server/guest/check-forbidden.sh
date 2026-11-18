#!/usr/bin/env bash
# Fails when a guest-mode file holds a command that is forbidden on the shared server
# (docs/SPRINT_6_GUEST.md G4). Run by CI and by hand:
#
#     bash deploy/server/guest/check-forbidden.sh [DIR]      (default: this script's folder)
#
# Checked: every file under DIR except *.md and *.example (explanations), every line that is not a
# comment (a line whose first non-blank character is #). Forbidden:
#   - any "prune" (docker system/volume/image/network/container prune ...);
#   - host-wide commands: systemctl, apt / apt-get, ufw, iptables / ip6tables, nft, reboot, shutdown,
#     poweroff; the files daemon.json and crontab (or /etc/cron);
#   - a docker compose command that does not start with "docker compose -p guest" (the dc helper in
#     lib.sh is the one place that runs it); the old docker-compose command;
#   - rm -r / -rf / --recursive on a path that does not start with /srv/guest or "$GUEST_ROOT";
#   - docker commands that act on containers directly (logs, exec, stop, restart, kill, rm, start,
#     run, update, system, container, volume, network, ...): ours go through dc (project guest);
#   - docker inspect in any form other than the one the server owner allows:
#     docker inspect --format '{{.Name}} {{.State.Status}} {{.State.StartedAt}} {{.RestartCount}}'
# This file writes the patterns with [x] brackets so that it passes its own check. Tests:
# deploy/server/tests/test-guest-check-forbidden.sh (outside this folder: it holds forbidden lines).
# Exit code 0 = clean, 1 = something forbidden (listed), 2 = usage error.
# Unlike the other scripts, this one may run anywhere (it only reads files).

set -euo pipefail

usage() { sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; }

DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) usage; exit 0 ;;
        -*) usage >&2; exit 2 ;;
        *) [[ -z "$DIR" ]] || { usage >&2; exit 2; }; DIR="$1"; shift ;;
    esac
done
DIR="${DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
[[ -d "$DIR" ]] || { echo "!! not a folder: $DIR" >&2; exit 2; }

B='(^|[^A-Za-z0-9_])'
E='([^A-Za-z0-9_]|$)'
R_CLEAN='p[r]une'
R_HOST="${B}(s[y]stemctl|a[p]t|a[p]t-get|u[f]w|i[p]tables|ip6t[a]bles|n[f]t|r[e]boot|s[h]utdown|p[o]weroff)${E}"
R_FILES='d[a]emon\.json|c[r]ontab|/etc/c[r]on'
R_COMPOSE_ANY='d[o]cker[[:space:]]+c[o]mpose([[:space:]]|$)'
R_COMPOSE_OK='d[o]cker[[:space:]]+c[o]mpose[[:space:]]+-p[[:space:]]+guest([[:space:]]|$)'
R_COMPOSE_V1="${B}d[o]cker-c[o]mpose([[:space:]]|$)"
R_DOCKER_DIRECT="${B}d[o]cker[[:space:]]+(l[o]gs|e[x]ec|s[t]op|r[e]start|k[i]ll|r[m]|r[m]i|s[t]art|r[u]n|c[r]eate|p[a]use|u[n]pause|u[p]date|r[e]name|a[t]tach|c[p]|c[o]mmit|e[x]port|s[y]stem|s[w]arm|c[o]ntainer|v[o]lume|n[e]twork|p[l]ugin|c[o]ntext)${E}"
R_INSPECT="${B}d[o]cker[[:space:]]+i[n]spect${E}"
INSPECT_OK="--format '{{.Name}} {{.State.Status}} {{.State.StartedAt}} {{.RestartCount}}'"
R_RM="${B}r[m][[:space:]]+(.*)$"

found=0
checked_files=0
checked_lines=0
hit() {
    printf '%s:%s: %s\n      %s\n' "$1" "$2" "$3" "$4"
    found=$((found + 1))
}

# rm_ok ARGS: true unless it is a recursive rm with a path outside /srv/guest / "$GUEST_ROOT"
rm_ok() {
    local rest="$1" recursive=0 w
    local -a words paths=()
    read -r -a words <<<"$rest"
    local opts_done=0
    for w in "${words[@]}"; do
        case "$w" in
            ';' | '|' | '||' | '&&' | '&' | ')' | '}' | '>'* | '2>'*) break ;;
        esac
        if [[ $opts_done -eq 0 && "$w" == -- ]]; then opts_done=1; continue; fi
        if [[ $opts_done -eq 0 && "$w" == --* ]]; then
            [[ "$w" == --recursive ]] && recursive=1
            continue
        fi
        if [[ $opts_done -eq 0 && "$w" == -* ]]; then
            [[ "$w" == *[rR]* ]] && recursive=1
            continue
        fi
        opts_done=1
        paths+=("${w%;}")
        [[ "$w" == *';' ]] && break
    done
    [[ $recursive -eq 1 ]] || return 0
    [[ ${#paths[@]} -gt 0 ]] || return 1
    local p
    for p in "${paths[@]}"; do
        [[ "$p" != *..* ]] || return 1
        if [[ "$p" =~ ^\"?/srv/guest(/|\"|$) || "$p" =~ ^\"?\$\{?GUEST_ROOT\}?(/|\"|$) ]]; then
            continue
        fi
        return 1
    done
    return 0
}

while IFS= read -r -d '' f; do
    case "$f" in
        *.md | *.example) continue ;;
    esac
    checked_files=$((checked_files + 1))
    while IFS= read -r numbered; do
        n="${numbered%%:*}"
        line="${numbered#*:}"
        line="${line%$'\r'}"
        checked_lines=$((checked_lines + 1))
        low="${line,,}"
        if [[ "$low" =~ $R_CLEAN ]]; then hit "$f" "$n" "forbidden: a bulk clean-up command (never, of any kind)" "$line"; fi
        if [[ "$low" =~ $R_HOST ]]; then hit "$f" "$n" "forbidden: host-wide command '${BASH_REMATCH[2]}'" "$line"; fi
        if [[ "$low" =~ $R_FILES ]]; then hit "$f" "$n" "forbidden: host-wide file or scheduler '${BASH_REMATCH[0]}'" "$line"; fi
        if [[ "$line" =~ $R_COMPOSE_V1 ]]; then hit "$f" "$n" "forbidden: the old v1 compose command (use dc)" "$line"; fi
        if [[ "$line" =~ $R_COMPOSE_ANY ]]; then
            all="$(grep -oE "$R_COMPOSE_ANY" <<<"$line" | wc -l)"
            ok="$(grep -oE "$R_COMPOSE_OK" <<<"$line" | wc -l)"
            if [[ "$ok" -lt "$all" ]]; then hit "$f" "$n" "forbidden: a compose command without '-p guest' right after it" "$line"; fi
        fi
        if [[ "$line" =~ $R_DOCKER_DIRECT ]]; then hit "$f" "$n" "forbidden: docker ${BASH_REMATCH[2]} directly (ours go through dc, project guest)" "$line"; fi
        if [[ "$line" =~ $R_INSPECT && "$line" != *"$INSPECT_OK"* ]]; then
            hit "$f" "$n" "forbidden: an inspect other than the allowed --format (a full inspect prints secrets)" "$line"
        fi
        rest="$line"
        while [[ "$rest" =~ $R_RM ]]; do
            rest="${BASH_REMATCH[2]}"
            if ! rm_ok "$rest"; then
                hit "$f" "$n" "forbidden: recursive rm outside /srv/guest or \"\$GUEST_ROOT\"" "$line"
                break
            fi
        done
    done < <(grep -nvE '^[[:space:]]*(#|$)' "$f" || true)
done < <(find "$DIR" -type f -print0 | sort -z)

if [[ $found -gt 0 ]]; then
    printf '!! %d forbidden line(s) in %s (above)\n' "$found" "$DIR" >&2
    exit 1
fi
printf 'OK: %d files, %d non-comment lines, nothing forbidden in %s\n' "$checked_files" "$checked_lines" "$DIR"
