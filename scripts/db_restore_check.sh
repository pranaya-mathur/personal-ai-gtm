#!/usr/bin/env bash
# Proves a backup restores: loads it into a THROWAWAY database (gtm_restore_check), compares row
# counts with the live database, then drops the throwaway. Never touches the live database.
# Usage: scripts/db_restore_check.sh [backups/gtm-YYYYmmdd-HHMMSS.sql.gz]
set -euo pipefail
cd "$(dirname "$0")/.."
f="${1:-$(ls -1t backups/gtm-*.sql.gz | head -n1)}"
psql_() { docker compose exec -T db psql -U gtm -v ON_ERROR_STOP=1 "$@"; }
psql_ -d postgres -qc "DROP DATABASE IF EXISTS gtm_restore_check" -c "CREATE DATABASE gtm_restore_check"
gunzip -c "$f" | psql_ -d gtm_restore_check -q >/dev/null
tables=$(psql_ -d gtm -tAc "select tablename from pg_tables where schemaname='public' order by 1")
status=0
for t in $tables; do
  a=$(psql_ -d gtm -tAc "select count(*) from \"$t\"")
  b=$(psql_ -d gtm_restore_check -tAc "select count(*) from \"$t\"")
  if [ "$a" != "$b" ]; then echo "MISMATCH $t live=$a restored=$b"; status=1; fi
done
psql_ -d postgres -qc "DROP DATABASE gtm_restore_check"
[ $status -eq 0 ] && echo "restore check OK: $f ($(echo "$tables" | wc -w | tr -d ' ') tables, all row counts equal)"
exit $status
