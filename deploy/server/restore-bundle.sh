#!/usr/bin/env bash
# Restores the data bundle from the laptop (scripts/export-bundle.ps1) into the production stack:
#
#     sudo bash deploy/server/restore-bundle.sh smart-campus-bundle-YYYYmmdd-HHMMSS.tar.gz
#     sudo bash deploy/server/restore-bundle.sh <bundle.tar.gz> --replace [--yes]
#
# 1. unpacks the bundle in a temporary folder and checks the SHA-256 of every file against
#    manifest.json (and that no file is missing or extra);
# 2. refuses a bundle whose Alembic revision this code does not know (newer code is needed:
#    update.sh first);
# 3. refuses a database that already holds knowledge (any row in "sources") unless --replace;
#    with --replace it asks for YES (or --yes) and makes a backup first (backup-now.sh);
# 4. stops the backend, recreates the database from db.dump, replaces the media and speech cache
#    volumes with media/ and tts_cache/ of the bundle, starts the backend (which migrates);
# 5. compares the row count of every table with manifest.json and prints OK / DIFF per table.
#    Exit code 1 on any difference.
#
# Admin logins are not in the bundle: create one afterwards with create-admin.sh.
# Environment: SCR_PROJECT (default smart-campus-prod), SCR_ENV_FILE (default deploy/server/.env).

set -euo pipefail
# shellcheck source=deploy/server/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BUNDLE=""
REPLACE=0
YES=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --replace) REPLACE=1; shift ;;
        --yes) YES=1; shift ;;
        -h | --help) usage; exit 0 ;;
        -*) usage >&2; die "unknown option: $1" ;;
        *)
            [[ -z "$BUNDLE" ]] || die "only one bundle, got '$BUNDLE' and '$1'"
            BUNDLE="$1"; shift ;;
    esac
done
[[ -n "$BUNDLE" ]] || { usage >&2; die "the bundle file is missing"; }
[[ -f "$BUNDLE" ]] || die "file not found: $BUNDLE"
command -v jq >/dev/null || die "jq is needed (apt-get install jq; install.sh installs it)"
command -v sha256sum >/dev/null || die "sha256sum is needed (coreutils)"
require_env_file

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

info "1/6 unpack and check $(basename "$BUNDLE")"
# no absolute paths, no "..", no links: the bundle is data
if tar -tvzf "$BUNDLE" | awk '{print substr($1,1,1)}' | grep -qv '^[-d]$'; then
    die "the bundle holds links or special files: refused"
fi
if tar -tzf "$BUNDLE" | grep -qE '(^/|(^|/)\.\.(/|$))'; then
    die "the bundle holds absolute paths or '..': refused"
fi
tar -xzf "$BUNDLE" -C "$TMP" --no-same-owner
[[ -f "$TMP/manifest.json" ]] || die "manifest.json is missing in the bundle"
[[ -f "$TMP/db.dump" ]] || die "db.dump is missing in the bundle"
M="$TMP/manifest.json"
FORMAT="$(jq -r '.format // empty' "$M")"
[[ "$FORMAT" == 1 ]] || die "unknown bundle format '$FORMAT' (this script reads format 1)"
note "created $(jq -r '.created_at' "$M"), git $(jq -r '.git_commit // "?" | .[0:10]' "$M"), app $(jq -r '.app_version // "?"' "$M"), Postgres $(jq -r '.postgres_server_version // "?"' "$M")"
note "conversations included: $(jq -r '.include_conversations' "$M"), tables without data: $(jq -r '(.excluded_table_data // []) | join(", ")' "$M")"
note "simulated robot runs (SIMULATED, labelled as such): $(jq -r '.simulated_robot_runs // 0' "$M")"

# every listed file: sha256 matches
jq -r '.files | to_entries[] | "\(.value)  \(.key)"' "$M" >"$TMP/.sums"
n_listed="$(wc -l <"$TMP/.sums")"
[[ "$n_listed" -gt 0 ]] || die "manifest.json lists no files"
if ! (cd "$TMP" && sha256sum --check --quiet --strict .sums); then
    die "SHA-256 check FAILED (see above): the bundle is damaged or was changed; nothing was restored"
fi
# no extra file that the manifest does not list
unlisted="$(cd "$TMP" && find . -type f ! -name manifest.json ! -name .sums | sed 's|^\./||' | sort |
    comm -23 - <(jq -r '.files | keys[]' "$M" | sort))"
[[ -z "$unlisted" ]] || die "files in the bundle that manifest.json does not list: ${unlisted//$'\n'/, }"
note "SHA-256 OK for $n_listed files"

info "2/6 Alembic revision"
BUNDLE_REV="$(jq -r '.alembic_revision // empty' "$M")"
[[ -n "$BUNDLE_REV" ]] || die "manifest.json has no alembic_revision"
# The revisions this checkout knows (the image is built from it; update.sh keeps them in step).
mapfile -t CODE_REVS < <(sed -nE 's/^revision(: *str)? *= *"([^"]+)".*/\2/p' "$REPO"/backend/migrations/versions/*.py | sort)
mapfile -t DOWN_REVS < <(sed -nE 's/^down_revision[^=]*= *"([^"]+)".*/\1/p' "$REPO"/backend/migrations/versions/*.py | sort -u)
[[ ${#CODE_REVS[@]} -gt 0 ]] || die "no migrations found in $REPO/backend/migrations/versions"
CODE_HEAD="$(comm -23 <(printf '%s\n' "${CODE_REVS[@]}") <(printf '%s\n' "${DOWN_REVS[@]}") | tr '\n' ' ')"
if ! printf '%s\n' "${CODE_REVS[@]}" | grep -qxF "$BUNDLE_REV"; then
    die "the bundle is at revision $BUNDLE_REV, which this code does not know (code head: ${CODE_HEAD% }). Update the code first (update.sh), then restore."
fi
note "bundle $BUNDLE_REV, code head ${CODE_HEAD% }: OK (the backend migrates up on start if needed)"

info "3/6 is the database empty?"
dc up -d db >/dev/null
for _ in $(seq 1 30); do
    # shellcheck disable=SC2016
    if dc exec -T db sh -c 'pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' 2>/dev/null; then break; fi
    sleep 2
done
has_sources="$(psql_db "SELECT to_regclass('public.sources') IS NOT NULL")" || die "cannot reach the database"
n_sources=0
if [[ "$has_sources" == t ]]; then n_sources="$(psql_db 'SELECT count(*) FROM sources')"; fi
if [[ "$n_sources" -gt 0 ]]; then
    if [[ $REPLACE -ne 1 ]]; then
        die "the database already holds $n_sources sources: refused. Add --replace to overwrite it (a backup is made first)."
    fi
    note "the database holds $n_sources sources: --replace REPLACES all of it with the bundle."
    if [[ $YES -ne 1 ]]; then
        [[ -t 0 ]] || die "no terminal to confirm: add --yes"
        read -r -p "   Type YES to continue: " answer
        [[ "$answer" == YES ]] || die "cancelled, nothing was changed"
    fi
    info "   backup of the current state first"
    bash "$SCRIPTS/backup-now.sh"
else
    note "empty (no knowledge sources): restoring"
fi

info "4/6 restore the database"
dc stop backend >/dev/null 2>&1 || true
copy_stdin_to_db_container /tmp/restore.dump <"$TMP/db.dump"
restore_dump_in_db || die "pg_restore failed (see above). The database may be empty now; with --replace the backup made before is the way back (restore-backup.sh --list)."
note "database restored"

info "5/6 media and speech cache"
dirs=()
for d in media tts_cache; do [[ -d "$TMP/$d" ]] && dirs+=("$d"); done
if [[ ${#dirs[@]} -gt 0 ]]; then
    tar -C "$TMP" -cf - "${dirs[@]}" | replace_files_from_tar "" "${dirs[@]}"
else
    note "no media/ or tts_cache/ in the bundle"
fi

info "6/6 start the backend (it migrates to the code's head) and compare the counts"
dc up -d backend
wait_healthy 240 || die "the backend is not healthy after the restore (see the log above)"

mapfile -t TABLES < <(jq -r '.tables | keys[]' "$M")
# table names go into SQL: only plain lower-case identifiers; a table the database does not have
# (renamed by a migration) is reported as "missing"
[[ ${#TABLES[@]} -gt 0 ]] || die "manifest.json lists no tables"
arr=""
for t in "${TABLES[@]}"; do
    [[ "$t" =~ ^[a-z_][a-z0-9_]*$ ]] || die "odd table name in manifest.json: $t"
    arr+="${arr:+,}'$t'"
done
mapfile -t missing < <(psql_db "SELECT t FROM unnest(ARRAY[$arr]) AS t WHERE to_regclass('public.' || t) IS NULL")
sql=""
for t in "${TABLES[@]}"; do
    if [[ " ${missing[*]} " == *" $t "* ]]; then continue; fi
    sql+="${sql:+ UNION ALL }SELECT '$t', count(*) FROM $t"
done
declare -A NOW=()
if [[ -n "$sql" ]]; then
    while IFS='|' read -r t c; do NOW["$t"]="$c"; done < <(psql_db "$sql")
fi

bad=0
printf '   %-34s %10s %10s  %s\n' table bundle restored result
for t in "${TABLES[@]}"; do
    want="$(jq -r --arg t "$t" '.tables[$t]' "$M")"
    got="${NOW[$t]:-missing}"
    if [[ "$got" == "$want" ]]; then res=OK; else res=DIFF; bad=1; fi
    printf '   %-34s %10s %10s  %s\n' "$t" "$want" "$got" "$res"
done
if [[ $bad -ne 0 ]]; then
    warn "some counts differ (DIFF above). If the bundle's revision ($BUNDLE_REV) is older than the"
    warn "code head, a migration may have added rows; otherwise check the restore output above."
    exit 1
fi
info "bundle restored, all ${#TABLES[@]} table counts match. Next: create-admin.sh NAME"
