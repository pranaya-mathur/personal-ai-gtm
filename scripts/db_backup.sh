#!/usr/bin/env bash
# Logical backup of the LIVE database (gtm) to ./backups (git-ignored). Keeps the newest 14.
# Usage: scripts/db_backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
f="backups/gtm-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T db pg_dump -U gtm -d gtm --no-owner --clean --if-exists | gzip > "$f"
test -s "$f" || { echo "backup is empty" >&2; rm -f "$f"; exit 1; }
ls -1t backups/gtm-*.sql.gz | tail -n +15 | xargs -I{} rm -f {}
echo "$f"
