#!/usr/bin/env bash
# Prints a deterministic fingerprint of a database: schema signature, constraint validity,
# alembic revision, per-table row count + content digest, and the spend total.
# Usage: scripts/db_fingerprint.sh <database>      (read-only)
set -euo pipefail
cd "$(dirname "$0")/.."
db="$1"
q() { docker compose exec -T db psql -U gtm -d "$db" -v ON_ERROR_STOP=1 -tA -c "$1"; }
emit() { local v; v="$(q "$2")" || { echo "fingerprint query failed: $1" >&2; exit 1; }; echo "$1=$v"; }
emit alembic 'select version_num from alembic_version'
emit schema "select md5(string_agg(table_name||'.'||column_name||':'||data_type||':'||is_nullable, ',' order by table_name, column_name)) from information_schema.columns where table_schema='public'"
emit constraints "select md5(string_agg(conname||':'||contype::text, ',' order by conname)) from pg_constraint c join pg_namespace n on n.oid=c.connamespace where n.nspname='public'"
emit unvalidated_constraints "select count(*) from pg_constraint c join pg_namespace n on n.oid=c.connamespace where n.nspname='public' and not convalidated"
emit indexes "select md5(string_agg(indexname, ',' order by indexname)) from pg_indexes where schemaname='public'"
for t in $(q "select tablename from pg_tables where schemaname='public' order by 1"); do
  if [ "$(q "select count(*) from information_schema.columns where table_schema='public' and table_name='$t' and column_name='id'")" = "1" ]; then
    emit "table.$t" "select count(*)||':'||coalesce(md5(string_agg(t::text, ',' order by id)),'-') from \"$t\" t"
  else
    emit "table.$t" "select count(*) from \"$t\""
  fi
done
emit spend_total 'select coalesce(sum(amount_usd),0) from provider_spend'
# orphaned foreign keys would mean a restore silently dropped parents
emit orphan_opportunity_orgs 'select count(*) from opportunities o where o.organization_id is not null and not exists (select 1 from organizations g where g.id=o.organization_id)'
emit orphan_evidence_links 'select count(*) from opportunity_evidence l where not exists (select 1 from evidence e where e.id=l.evidence_id) or not exists (select 1 from opportunities o where o.id=l.opportunity_id)'
