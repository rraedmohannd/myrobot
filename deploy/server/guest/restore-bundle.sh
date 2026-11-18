#!/usr/bin/env bash
# Restores the data bundle from the PC (scripts/export-bundle.ps1) into the guest stack:
#
#     bash /srv/guest/app/deploy/server/guest/restore-bundle.sh /srv/guest/bundles/smart-campus-bundle-YYYYmmdd-HHMMSS.tar.gz
#     bash .../restore-bundle.sh <bundle.tar.gz> --replace [--yes]
#
# The same steps as deploy/server/restore-bundle.sh, for project "guest", without jq (Python reads
# manifest.json: the host's python3, or the backend image's when there is none):
# 1. unpacks the bundle in /srv/guest/state/tmp and checks the SHA-256 of every file against
#    manifest.json (and that no file is missing or extra);
# 2. refuses a bundle whose Alembic revision the loaded backend image does not know;
# 3. refuses a database that already holds knowledge (any row in "sources") unless --replace;
#    with --replace it asks for YES (or --yes) and makes a backup first;
# 4. stops the backend, recreates the database from db.dump, replaces the media and speech cache
#    volumes, starts the backend (which migrates);
# 5. compares the row count of every table with manifest.json (OK / DIFF). Exit code 1 on a DIFF.
#
# Admin logins are not in the bundle: create one afterwards with create-admin.sh.

set -euo pipefail
# shellcheck source=deploy/server/guest/lib.sh
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
command -v sha256sum >/dev/null || die "sha256sum is needed (coreutils)"
require_docker
require_env_file

TMPNAME="$(make_tmp)"
TMP="$GUEST_ROOT/state/tmp/$TMPNAME"
cleanup() { rm_tmp "$TMPNAME"; }
trap cleanup EXIT

info "1/6 unpack and check $(basename "$BUNDLE")"
if tar -tvzf "$BUNDLE" | awk '{print substr($1,1,1)}' | grep -qv '^[-d]$'; then
    die "the bundle holds links or special files: refused"
fi
if tar -tzf "$BUNDLE" | grep -qE '(^/|(^|/)\.\.(/|$))'; then
    die "the bundle holds absolute paths or '..': refused"
fi
tar -xzf "$BUNDLE" -C "$TMP" --no-same-owner
[[ -f "$TMP/manifest.json" ]] || die "manifest.json is missing in the bundle"
[[ -f "$TMP/db.dump" ]] || die "db.dump is missing in the bundle"

# manifest.json -> plain lines: "M<TAB>key<TAB>value", "F<TAB>sha256<TAB>path", "T<TAB>table<TAB>rows"
# shellcheck disable=SC2016
PY='
import json, sys
m = json.load(sys.stdin)
def out(*a):
    s = "\t".join(str(x) for x in a)
    if "\n" in s or "\r" in s:
        sys.exit("odd value in manifest.json")
    print(s)
out("M", "format", m.get("format", ""))
for k in ("created_at", "git_commit", "app_version", "postgres_server_version", "alembic_revision"):
    out("M", k, m.get(k) or "?")
out("M", "include_conversations", str(m.get("include_conversations")).lower())
out("M", "excluded", ", ".join(m.get("excluded_table_data") or []))
out("M", "simulated_robot_runs", m.get("simulated_robot_runs") or 0)
for p, h in (m.get("files") or {}).items():
    if "\t" in p:
        sys.exit("odd file name in manifest.json")
    out("F", h, p)
for t, c in (m.get("tables") or {}).items():
    out("T", t, c)
'
py_run "$PY" <"$TMP/manifest.json" >"$TMP/.manifest.tsv" || die "manifest.json cannot be read"
mget() { awk -F'\t' -v k="$1" '$1 == "M" && $2 == k { print $3; exit }' "$TMP/.manifest.tsv"; }
[[ "$(mget format)" == 1 ]] || die "unknown bundle format '$(mget format)' (this script reads format 1)"
note "created $(mget created_at), git $(mget git_commit | cut -c1-10), app $(mget app_version), Postgres $(mget postgres_server_version)"
note "conversations included: $(mget include_conversations), tables without data: $(mget excluded)"
note "simulated robot runs (SIMULATED, labelled as such): $(mget simulated_robot_runs)"

awk -F'\t' '$1 == "F" { print $2 "  " $3 }' "$TMP/.manifest.tsv" >"$TMP/.sums"
n_listed="$(wc -l <"$TMP/.sums")"
[[ "$n_listed" -gt 0 ]] || die "manifest.json lists no files"
if ! (cd "$TMP" && sha256sum --check --quiet --strict .sums); then
    die "SHA-256 check FAILED (see above): the bundle is damaged or was changed; nothing was restored"
fi
unlisted="$(cd "$TMP" && find . -type f ! -name manifest.json ! -name .sums ! -name .manifest.tsv | sed 's|^\./||' | sort |
    comm -23 - <(awk -F'\t' '$1 == "F" { print $3 }' "$TMP/.manifest.tsv" | sort))"
[[ -z "$unlisted" ]] || die "files in the bundle that manifest.json does not list: ${unlisted//$'\n'/, }"
note "SHA-256 OK for $n_listed files"

info "2/6 Alembic revision"
BUNDLE_REV="$(mget alembic_revision)"
[[ -n "$BUNDLE_REV" && "$BUNDLE_REV" != "?" ]] || die "manifest.json has no alembic_revision"
# the revisions the loaded backend image knows (that is the code that will run)
# shellcheck disable=SC2016
REVS="$(dc run --rm --no-deps -T --entrypoint sh backend -c '
    sed -nE "s/^revision(: *str)? *= *\"([^\"]+)\".*/R \2/p; s/^down_revision[^=]*= *\"([^\"]+)\".*/D \1/p" /app/migrations/versions/*.py
')" || die "cannot read the migrations of image $(guest_image)"
mapfile -t CODE_REVS < <(printf '%s\n' "$REVS" | awk '$1 == "R" { print $2 }' | sort)
mapfile -t DOWN_REVS < <(printf '%s\n' "$REVS" | awk '$1 == "D" { print $2 }' | sort -u)
[[ ${#CODE_REVS[@]} -gt 0 ]] || die "no migrations found in image $(guest_image)"
CODE_HEAD="$(comm -23 <(printf '%s\n' "${CODE_REVS[@]}") <(printf '%s\n' "${DOWN_REVS[@]}") | tr '\n' ' ')"
if ! printf '%s\n' "${CODE_REVS[@]}" | grep -qxF "$BUNDLE_REV"; then
    die "the bundle is at revision $BUNDLE_REV, which image $(guest_image) does not know (head: ${CODE_HEAD% }). Load a newer image first (update-guest.sh)."
fi
note "bundle $BUNDLE_REV, image head ${CODE_HEAD% }: OK (the backend migrates up on start if needed)"

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
    dc up -d backup >/dev/null
    backup_now || die "the backup failed: nothing was changed"
else
    note "empty (no knowledge sources): restoring"
fi

info "4/6 restore the database"
# only the backend stops; the quick tunnel (cloudflared) keeps running, so its address stays
dc stop backend >/dev/null 2>&1 || true
copy_stdin_to_db_container /tmp/restore.dump <"$TMP/db.dump"
restore_dump_in_db || die "pg_restore failed (see above). The database may be empty now; with --replace the backup made before is the way back."
note "database restored"

info "5/6 media and speech cache"
dirs=()
for d in media tts_cache; do [[ -d "$TMP/$d" ]] && dirs+=("$d"); done
if [[ ${#dirs[@]} -gt 0 ]]; then
    tar -C "$TMP" -cf - "${dirs[@]}" | replace_files_from_tar "" "${dirs[@]}"
else
    note "no media/ or tts_cache/ in the bundle"
fi

info "6/6 start the backend (it migrates to the image's head) and compare the counts"
dc up -d backend
wait_healthy 240 || die "the backend is not healthy after the restore (see the log above)"

mapfile -t TABLES < <(awk -F'\t' '$1 == "T" { print $2 }' "$TMP/.manifest.tsv")
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
    want="$(awk -F'\t' -v k="$t" '$1 == "T" && $2 == k { print $3; exit }' "$TMP/.manifest.tsv")"
    got="${NOW[$t]:-missing}"
    if [[ "$got" == "$want" ]]; then res=OK; else res=DIFF; bad=1; fi
    printf '   %-34s %10s %10s  %s\n' "$t" "$want" "$got" "$res"
done
if [[ $bad -ne 0 ]]; then
    warn "some counts differ (DIFF above). If the bundle's revision ($BUNDLE_REV) is older than the"
    warn "image's head, a migration may have added rows; otherwise check the restore output above."
    exit 1
fi
info "bundle restored, all ${#TABLES[@]} table counts match. Next: create-admin.sh NAME"
