#!/usr/bin/env bash
# Proves a backup restores and is semantically identical: loads it into a THROWAWAY database
# (gtm_restore_check), then compares the full fingerprint (schema, constraints, indexes, per-table
# content digests, spend total, orphan checks) with the backup's manifest taken at dump time.
# Never writes to the live database. Usage: scripts/db_restore_check.sh [backups/gtm-*.sql.gz]
set -euo pipefail
cd "$(dirname "$0")/.."
f="${1:-$(ls -1t backups/gtm-*.sql.gz | head -n1)}"
[ -f "$f.sha256" ] && shasum -a 256 -c "$f.sha256" >/dev/null || { echo "checksum missing/mismatch for $f" >&2; exit 1; }
[ -f "$f.manifest" ] || { echo "no manifest for $f (taken before manifests existed); use scripts/db_restore_check.sh --live-compare" >&2; exit 1; }
psql_() { docker compose exec -T db psql -U gtm -v ON_ERROR_STOP=1 "$@"; }
[ "$(psql_ -d postgres -tAc "select count(*) from pg_database where datname='gtm_restore_check'")" = "0" ] || psql_ -d postgres -qc "DROP DATABASE gtm_restore_check"
psql_ -d postgres -qc "CREATE DATABASE gtm_restore_check"
trap 'psql_ -d postgres -qc "DROP DATABASE IF EXISTS gtm_restore_check" >/dev/null' EXIT
gunzip -c "$f" | psql_ -d gtm_restore_check -q >/dev/null
scripts/db_fingerprint.sh gtm_restore_check > /tmp/gtm_restore.fp
if diff <(grep -v '^unvalidated' "$f.manifest") <(grep -v '^unvalidated' /tmp/gtm_restore.fp); then
  [ "$(grep '^unvalidated_constraints=' /tmp/gtm_restore.fp | cut -d= -f2)" = "0" ] || { echo "unvalidated constraints after restore" >&2; exit 1; }
  echo "restore check OK: $f (schema, constraints, indexes, per-table content digests, spend total all identical)"
else
  echo "RESTORE MISMATCH (diff above: < backup-time manifest, > restored)" >&2; exit 1
fi
