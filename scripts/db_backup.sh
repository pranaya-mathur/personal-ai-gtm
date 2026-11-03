#!/usr/bin/env bash
# Verified logical backup of the LIVE database (gtm) to ./backups (git-ignored). Keeps newest 14.
# Verifies: server identity, dump integrity (gzip + trailer), and writes a sha256 + manifest of
# critical-table fingerprints that scripts/db_restore_check.sh compares after a restore.
# Usage: scripts/db_backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
psql_() { docker compose exec -T db psql -U gtm -v ON_ERROR_STOP=1 -tA "$@"; }
[ "$(psql_ -d gtm -c 'select current_database()')" = "gtm" ] || { echo "not connected to gtm" >&2; exit 1; }
f="backups/gtm-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T db pg_dump -U gtm -d gtm --no-owner --clean --if-exists | gzip > "$f"
test -s "$f" || { echo "backup is empty" >&2; rm -f "$f"; exit 1; }
gzip -t "$f" || { echo "backup is corrupt" >&2; exit 1; }
gunzip -c "$f" | tail -n 5 | grep -q "PostgreSQL database dump complete" || { echo "dump trailer missing (truncated?)" >&2; rm -f "$f"; exit 1; }
shasum -a 256 "$f" > "$f.sha256"
scripts/db_fingerprint.sh gtm > "$f.manifest"
ls -1t backups/gtm-*.sql.gz | tail -n +15 | while read -r old; do rm -f "$old" "$old.sha256" "$old.manifest"; done
echo "$f"
