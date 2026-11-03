#!/usr/bin/env bash
# One-time, operator-run: creates the isolated sandbox database used by destructive tests and
# marks it. Refuses to touch anything but the named sandbox. Usage: scripts/create_test_db.sh [name]
set -euo pipefail
cd "$(dirname "$0")/.."
name="${1:-gtm_test}"
case "$name" in *test*) ;; *) echo "sandbox name must contain 'test'" >&2; exit 1;; esac
case "$name" in gtm|postgres|template0|template1) echo "refusing $name" >&2; exit 1;; esac
psql_() { docker compose exec -T db psql -U gtm -v ON_ERROR_STOP=1 -d postgres "$@"; }
[ "$(psql_ -tAc "select count(*) from pg_database where datname='$name'")" = "1" ] || psql_ -qc "CREATE DATABASE \"$name\""
psql_ -qc "COMMENT ON DATABASE \"$name\" IS 'gtm-test-sandbox'"
echo "sandbox $name ready (marked). Use TEST_DATABASE_URL=postgresql+psycopg://gtm:gtm@localhost:5433/$name"
