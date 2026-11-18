#!/usr/bin/env bash
# Loads a backend image that was built on the PC (never built on the server: that would load its
# 2 CPUs for minutes and slow down the other project):
#
#     bash /srv/guest/app/deploy/server/guest/load-image.sh /srv/guest/images/guest-backend-0.6.0.tar.gz
#
# On the PC first (Git Bash; the PowerShell version is in docs/DEPLOY_GUEST.md):
#     docker build --build-context shared=shared --build-arg EXTRAS= --platform linux/amd64 -t guest-backend:0.6.0 backend
#     docker save guest-backend:0.6.0 | gzip > guest-backend-0.6.0.tar.gz
#     sha256sum guest-backend-0.6.0.tar.gz > guest-backend-0.6.0.tar.gz.sha256
#     scp guest-backend-0.6.0.tar.gz guest-backend-0.6.0.tar.gz.sha256 <ssh-user>@<server-ip>:/srv/guest/images/
#
# Checks: the SHA-256 in FILE.sha256 (first word of the file; sha256sum or PowerShell Get-FileHash
# output) matches; the archive holds only images named guest-backend:<version>. Then docker load.
# The last line on standard output is the loaded tag (guest-backend:<version>).

set -euo pipefail
# shellcheck source=deploy/server/guest/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FILE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) usage; exit 0 ;;
        -*) usage >&2; die "unknown option: $1" ;;
        *)
            [[ -z "$FILE" ]] || die "only one file, got '$FILE' and '$1'"
            FILE="$1"; shift ;;
    esac
done
[[ -n "$FILE" ]] || { usage >&2; die "the image file is missing"; }
[[ -f "$FILE" ]] || die "file not found: $FILE"
[[ -f "$FILE.sha256" ]] || die "$FILE.sha256 not found: copy it next to the image (see --help)"
require_docker

info "1/3 SHA-256"
want="$(tr -d '\r' <"$FILE.sha256" | awk 'NF { print tolower($1); exit }')"
[[ "$want" =~ ^[0-9a-f]{64}$ ]] || die "$FILE.sha256 holds no SHA-256 (64 hex characters)"
got="$(sha256sum "$FILE" | awk '{ print $1 }')"
[[ "$got" == "$want" ]] || die "SHA-256 MISMATCH: the file is damaged or incomplete (copy it again); nothing was loaded"
note "OK ${got:0:16}..."

info "2/3 what is inside"
case "$FILE" in
    *.gz) tags="$(tar -xzOf "$FILE" manifest.json 2>/dev/null || true)" ;;
    *) tags="$(tar -xOf "$FILE" manifest.json 2>/dev/null || true)" ;;
esac
[[ -n "$tags" ]] || die "no manifest.json in $FILE: not an image made by docker save"
mapfile -t TAGS < <(printf '%s' "$tags" | grep -o '"RepoTags":\[[^]]*\]' |
    sed -e 's/^"RepoTags":\[//' -e 's/\]$//' | tr ',' '\n' | tr -d '" ' | sed '/^$/d' || true)
[[ ${#TAGS[@]} -gt 0 ]] || die "the archive holds an image without a name: build it with -t guest-backend:<version>"
for t in "${TAGS[@]}"; do
    [[ "$t" =~ ^guest-backend:[A-Za-z0-9._-]+$ ]] ||
        die "the archive holds '$t': only guest-backend:<version> images are loaded here"
done
note "images: ${TAGS[*]}"
free_kb="$(df -Pk "$GUEST_ROOT" | awk 'NR == 2 { print $4 }')"
if [[ "$free_kb" -lt $((3 * 1024 * 1024)) ]]; then
    warn "less than 3 GB free disk: check with status-guest.sh before loading more images"
fi

info "3/3 docker load"
docker load -i "$FILE"
printf '%s\n' "${TAGS[-1]}"
