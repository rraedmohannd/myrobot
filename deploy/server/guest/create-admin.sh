#!/usr/bin/env bash
# Creates an admin login for the admin panel of the guest stack (or sets a new password):
#
#     bash /srv/guest/app/deploy/server/guest/create-admin.sh NAME
#     printf '%s\n' "$PASSWORD" | bash .../create-admin.sh NAME --password-stdin
#
# Wraps "python -m app.ingestion.cli create-admin NAME" in the running backend container. Mohannd
# types the password himself (hidden), so the first form needs a real terminal (an interactive SSH
# session); it is never an argument and never ends up in the shell history.
# Several accounts are fine: one for Mohannd and one for the supervisor, e.g.
#     bash .../create-admin.sh mohannd
#     bash .../create-admin.sh supervisor     (send him the password privately, not with the link)
# The same NAME again sets a new password for that account.

set -euo pipefail
# shellcheck source=deploy/server/guest/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

NAME=""
STDIN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --password-stdin) STDIN=1; shift ;;
        -h | --help) usage; exit 0 ;;
        -*) usage >&2; die "unknown option: $1" ;;
        *)
            [[ -z "$NAME" ]] || die "only one NAME, got '$NAME' and '$1'"
            NAME="$1"; shift ;;
    esac
done
[[ -n "$NAME" ]] || { usage >&2; die "NAME is missing"; }
[[ "$NAME" =~ ^[A-Za-z0-9._-]{2,64}$ ]] || die "NAME: 2-64 letters, digits, '.', '_' or '-'"
require_docker
require_env_file

if [[ $STDIN -eq 1 ]]; then
    dc exec -T backend python -m app.ingestion.cli create-admin "$NAME" --password-stdin
else
    [[ -t 0 && -t 1 ]] || die "no terminal: run it in an interactive SSH session, or use --password-stdin"
    dc exec backend python -m app.ingestion.cli create-admin "$NAME"
fi
