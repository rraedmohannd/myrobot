#!/usr/bin/env bash
# Export the laptop's data as one bundle for the server (docs/SPRINT_6.md P0.5, P1).
# Linux / macOS / Git Bash version of scripts/export-bundle.ps1; same output.
#
#   bash scripts/export-bundle.sh
#   bash scripts/export-bundle.sh --include-conversations
#
# Writes backups/smart-campus-bundle-YYYYmmdd-HHMMSS.tar.gz (backups/ is git-ignored) holding:
#   manifest.json  versions, Alembic revision, git commit, rows per table, SHA-256 of every file
#   db.dump        pg_dump -Fc --no-owner --no-privileges of the running dev database
#   media/         backend/data/media
#   tts_cache/     backend/data/tts_cache
# Restore it on the server with deploy/server/restore-bundle.sh.
#
# Privacy: the DATA of sessions and turns (visitors' questions) stays on the laptop unless
# --include-conversations is given. admin_users and admin_sessions data never travels (the
# server makes its own admin with create-admin). Their table definitions are in the dump.
#
# No .env and no keys go into the bundle. Before the archive is written, every staged file
# and the text form of the dump are searched for the secret values of the local .env (keys
# named *PASSWORD*, *SECRET*, *TOKEN*, *KEY*, *ACCESS_CODE*) and for "sk-..." API keys; on a
# hit the script stops and writes nothing. Secret values are never printed.
#
# The database is only read: pg_dump and SELECT. It must be running (docker compose up -d).
# Needs: docker, git, tar, sha256sum or shasum. No python or jq.

set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash: keep container paths such as /tmp/x as they are
export COPYFILE_DISABLE=1   # macOS tar: no ._ resource-fork files

include_conversations=false
for arg in "$@"; do
    case "$arg" in
        --include-conversations) include_conversations=true ;;
        -h | --help)
            sed -n '2,25p' "$0"
            exit 0
            ;;
        *)
            echo "unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

cd "$(dirname "$0")/.."

die() {
    echo "ERROR: $*" >&2
    exit 1
}

stamp=$(date +%Y%m%d-%H%M%S)
name="smart-campus-bundle-$stamp.tar.gz"
out="backups/$name"
stage="backups/.bundle-staging-$stamp"
work="backups/.bundle-work-$stamp" # scan files and secret patterns; never part of the bundle
ctr_dump="/tmp/scr-bundle-$stamp.dump"
ctr_sql="/tmp/scr-bundle-$stamp.sql"

if [ "$include_conversations" = true ]; then
    excluded=(admin_sessions admin_users)
else
    excluded=(admin_sessions admin_users sessions turns)
fi

is_excluded() {
    local t
    for t in "${excluded[@]}"; do [ "$t" = "$1" ] && return 0; done
    return 1
}

# psql inside the db container; SQL on stdin
run_psql() {
    docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -X -q -At -v ON_ERROR_STOP=1' |
        tr -d '\r' | sed '/^[[:space:]]*$/d'
}

table_counts() {
    run_psql <<'SQL' | LC_ALL=C sort
SELECT string_agg(format('SELECT %L, count(*) FROM public.%I', tablename, tablename), ' UNION ALL ' ORDER BY tablename)
FROM pg_tables WHERE schemaname = 'public' \gexec
SQL
}

sha256() {
    local h
    if command -v sha256sum >/dev/null 2>&1; then
        h=$(sha256sum -b "$1")
    else
        h=$(shasum -a 256 -b "$1")
    fi
    h=${h%% *}
    printf '%s' "${h#\\}"
}

json_str() {
    local s=$1
    [[ $s =~ [[:cntrl:]] ]] && die "control character in a value or file name: refusing to build the manifest"
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    printf '"%s"' "$s"
}

container=$(docker compose ps -q db | tr -d '\r')
[ -n "$container" ] || die "the db container is not running (docker compose up -d)"

cleanup() {
    docker compose exec -T db rm -f "$ctr_dump" "$ctr_sql" >/dev/null 2>&1 || true
    rm -rf "$stage" "$work"
}
trap cleanup EXIT

mkdir -p backups "$stage/media" "$stage/tts_cache" "$work"
chmod 700 "$work"

# 1. Foreign keys from a dumped table into a table whose data is left out. pg_restore would fail
#    to create such a constraint if a row points at a missing row. Checked at every run.
arr="ARRAY["
sep=""
for t in "${excluded[@]}"; do
    arr+="$sep'$t'"
    sep=","
done
arr+="]::text[]"
while IFS='|' read -r fk n; do
    [ -n "$fk" ] || continue
    if [ "$n" -gt 0 ]; then
        die "foreign key $fk: $n rows point into a table whose data is excluded. Use --include-conversations or clear those references first."
    fi
done < <(run_psql <<SQL
SELECT format('SELECT %L, count(*) FROM %s WHERE %s', c.conname || ' on ' || c.conrelid::regclass::text,
              c.conrelid::regclass, string_agg(format('%I IS NOT NULL', a.attname), ' AND '))
FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
WHERE c.contype = 'f' AND c.confrelid::regclass::text = ANY ($arr)
  AND NOT (c.conrelid::regclass::text = ANY ($arr))
GROUP BY c.conname, c.conrelid \gexec
SQL
)

# 2. Counts before, dump, counts after (the backend may be writing; the dump is one snapshot)
table_counts >"$work/before.txt"
ex_args=""
for t in "${excluded[@]}"; do ex_args+=" --exclude-table-data=public.$t"; done
docker compose exec -T db sh -c "pg_dump -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Fc --no-owner --no-privileges$ex_args -f $ctr_dump" ||
    die "pg_dump failed"
table_counts >"$work/after.txt"
cmp -s "$work/before.txt" "$work/after.txt" || die "a table changed while dumping. Run the script again."
[ -s "$work/after.txt" ] || die "no tables found"
docker cp "$container:$ctr_dump" "$stage/db.dump" >/dev/null || die "could not copy the dump out of the container"

# text form of the dump, only for the secret scan below (never part of the bundle)
docker compose exec -T db pg_restore -f "$ctr_sql" "$ctr_dump" || die "pg_restore (text form for the secret scan) failed"
docker cp "$container:$ctr_sql" "$work/dump.sql" >/dev/null || die "could not copy the text dump out of the container"

alembic=$(echo "SELECT version_num FROM alembic_version;" | run_psql | head -n 1)
pg_version=$(echo "SHOW server_version;" | run_psql | head -n 1)
simulated=$(echo "SELECT count(*) FROM robot_runs WHERE is_simulated;" | run_psql | head -n 1)

# 3. Files
for d in media tts_cache; do
    if [ -d "backend/data/$d" ]; then cp -R "backend/data/$d/." "$stage/$d/"; fi
done
if [ -n "$(find "$stage" -name '.env*' -print -quit)" ]; then
    die "refusing: a .env file is among the files to export"
fi

# 4. Manifest
commit=$(git rev-parse HEAD 2>/dev/null || echo unknown)
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then dirty=true; else dirty=false; fi
app_version=$(sed -n "s/^__version__[[:space:]]*=[[:space:]]*[\"']\([^\"']*\)[\"'].*/\1/p" backend/app/__init__.py | head -n 1)
[ -n "$app_version" ] || app_version=unknown
created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

manifest="$stage/manifest.json"
{
    printf '{\n'
    printf '  "format": 1,\n'
    printf '  "created_at": %s,\n' "$(json_str "$created_at")"
    printf '  "git_commit": %s,\n' "$(json_str "$commit")"
    printf '  "git_dirty": %s,\n' "$dirty"
    printf '  "app_version": %s,\n' "$(json_str "$app_version")"
    printf '  "alembic_revision": %s,\n' "$(json_str "$alembic")"
    printf '  "postgres_server_version": %s,\n' "$(json_str "$pg_version")"
    printf '  "include_conversations": %s,\n' "$include_conversations"
    printf '  "excluded_table_data": ['
    sep=""
    for t in "${excluded[@]}"; do
        printf '%s%s' "$sep" "$(json_str "$t")"
        sep=", "
    done
    printf '],\n'
    printf '  "tables": {\n'
    sep=""
    while IFS='|' read -r t n; do
        is_excluded "$t" && n=0
        printf '%s    %s: %s' "$sep" "$(json_str "$t")" "$n"
        sep=$',\n'
    done <"$work/after.txt"
    printf '\n  },\n'
    printf '  "simulated_robot_runs": %s,\n' "$simulated"
    printf '  "files": {\n'
    printf '    "db.dump": "%s"' "$(sha256 "$stage/db.dump")"
    while IFS= read -r -d '' rel; do
        printf ',\n    %s: "%s"' "$(json_str "$rel")" "$(sha256 "$stage/$rel")"
    done < <(cd "$stage" && find media tts_cache -type f -print0 | LC_ALL=C sort -z)
    printf '\n  }\n}\n'
} >"$manifest"

# 5. Secret scan: every staged file, the manifest and the text form of the dump
nsecrets=0
hits=0
if [ -f .env ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line%$'\r'}
        line="${line#"${line%%[![:space:]]*}"}"
        case "$line" in '' | '#'*) continue ;; esac
        line=${line#export }
        case "$line" in *=*) ;; *) continue ;; esac
        key=${line%%=*}
        key="${key%"${key##*[![:space:]]}"}"
        val=${line#*=}
        val="${val#"${val%%[![:space:]]*}"}"
        val="${val%"${val##*[![:space:]]}"}"
        if [ ${#val} -ge 2 ]; then
            case "$val" in \"*\" | \'*\') val=${val:1:${#val}-2} ;; esac
        fi
        [[ $key =~ PASSWORD|SECRET|TOKEN|KEY|ACCESS_CODE ]] || continue
        [ ${#val} -ge 6 ] || continue # empty or placeholder values cannot be told apart from normal text
        printf '%s\n' "$val" >"$work/needle"
        nsecrets=$((nsecrets + 1))
        while IFS= read -r f; do
            echo "SECRET FOUND: $f contains the value of $key from .env" >&2
            hits=$((hits + 1))
        done < <(grep -rlaF -f "$work/needle" "$stage" "$work/dump.sql" || true)
    done <.env
    rm -f "$work/needle"
fi
while IFS= read -r f; do
    echo "SECRET FOUND: $f contains an sk-... API key pattern" >&2
    hits=$((hits + 1))
done < <(grep -rlaE 'sk-[A-Za-z0-9_-]{20,}' "$stage" "$work/dump.sql" || true)
[ "$hits" -eq 0 ] || die "refusing to write the bundle: secrets found (see above). Nothing was written."
nscan=$(($(find "$stage" -type f | wc -l) + 1))
echo "Secret scan: $nscan files clean ($nsecrets .env secret values and the sk- pattern checked)"

# 6. Archive (entries are relative, manifest.json first)
tar_owner=()
if tar --version 2>/dev/null | grep -q 'GNU tar'; then tar_owner=(--owner=0 --group=0 --numeric-owner); fi
tar ${tar_owner[@]+"${tar_owner[@]}"} -czf "$out" -C "$stage" manifest.json db.dump media tts_cache || die "tar failed"

nfiles=$(($(find "$stage" -type f | wc -l) - 1))
echo
echo "Rows per table in the bundle:"
while IFS='|' read -r t n; do
    if is_excluded "$t"; then
        printf '  %-22s %8s  (data excluded, %s rows stay on the laptop)\n' "$t" 0 "$n"
    else
        printf '  %-22s %8s\n' "$t" "$n"
    fi
done <"$work/after.txt"
echo "Simulated robot runs (labelled): $simulated"
echo "Files: $nfiles (db.dump + media + tts_cache)"
if [ "$dirty" = true ]; then dirty_note=" (dirty tree)"; else dirty_note=""; fi
echo "Alembic $alembic, app $app_version, commit $commit$dirty_note"
echo
size=$(wc -c <"$out" | tr -d ' ')
echo "Bundle written: $out ($(awk -v b="$size" 'BEGIN { printf "%.2f", b / 1048576 }') MB)"
if [ "$include_conversations" = true ]; then
    echo "It contains knowledge and robot data AND visitors' conversations. Keep it private."
else
    echo "It contains knowledge and robot data. Keep it private."
fi
