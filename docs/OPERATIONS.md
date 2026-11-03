# Operations: backups, restore, test isolation

## Incident (2026-11-03)
The integration tests drop and recreate the `public` schema. They were once pointed at the live
`gtm` database and wiped it (the provider-spend ledger, run history, candidates and manual
rejections were lost; opportunities were repopulated by a free rediscovery). Controls added:

* `tests/conftest.py::assert_safe_test_db` refuses any `TEST_DATABASE_URL` whose database name does
  not contain `test`, for **every** test that receives the URL (unit-tested in `test_db_safety.py`).
* Use a dedicated database: `TEST_DATABASE_URL=postgresql+psycopg://gtm:gtm@localhost:5433/gtm_test`.

## Backup and restore
```bash
make backup          # ./scripts/db_backup.sh -> backups/gtm-YYYYmmdd-HHMMSS.sql.gz (newest 14 kept, git-ignored)
make restore-check   # restores the newest backup into a THROWAWAY database and compares every table's
                     # row count with the live database; never touches the live database
```
Real restore of the live database (deliberate, manual): stop the api/worker, then
`gunzip -c backups/<file> | docker compose exec -T db psql -U gtm -d gtm`.
Backups contain contact data: keep them local. Schedule `make backup` (cron/launchd) daily.

## Spend ledger
After the wipe the ledger was re-entered as two rows labelled `reconstructed` (Apify from the
provider's own run records, OpenAI from the pre-wipe ledger). Verify OpenAI in its console.
Apify run costs are now recorded as the larger of the provider figure and the published price x
returned items, because `usageTotalUsd` was observed to be not yet finalised at run end.
