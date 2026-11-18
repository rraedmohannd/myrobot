#!/usr/bin/env bash
# Creates an admin login for the admin panel (or sets a new password for an existing one):
#
#     sudo bash deploy/server/create-admin.sh NAME
#     printf '%s\n' "$PASSWORD" | sudo bash deploy/server/create-admin.sh NAME --password-stdin
#
# Wraps "python -m app.ingestion.cli create-admin NAME" in the running backend container. The
# password is typed (hidden) in the terminal, so the first form needs a real terminal (TTY); it is
# never an argument and never ends up in the shell history.
#
# Environment: SCR_PROJECT (default smart-campus-prod), SCR_ENV_FILE (default deploy/server/.env).

set -euo pipefail
# shellcheck source=deploy/server/lib.sh
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
require_env_file

if [[ $STDIN -eq 1 ]]; then
    dc exec -T backend python -m app.ingestion.cli create-admin "$NAME" --password-stdin
else
    [[ -t 0 && -t 1 ]] || die "no terminal: run it in an interactive SSH session, or use --password-stdin"
    dc exec backend python -m app.ingestion.cli create-admin "$NAME"
fi
