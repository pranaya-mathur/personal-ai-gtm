# Apify Actors: validation and approval checklist

Schemas were read from the Apify Store on **2026-11-03** (read-only; **no Actor was executed, nothing was spent**).
Re-validate before enabling if an Actor has been updated.

| Connector | Actor | Signal | Pricing (FREE tier) | Status |
|---|---|---|---|---|
| `apify_ted_tenders` | `foxlabs/ted-tenders` (official EU TED API) | confirmed demand | $0.00005 start + $0.004/result | Ready, needs approval |
| `apify_worldbank_tenders` | `foxlabs/worldbank-tenders` (official World Bank API) | confirmed demand | $0.00005 start + $0.0025/result | Ready, needs approval |
| `apify_clutch_agencies` | `danthedataman/clutch-agency-directory` | company signal only | $0.00005 start + $0.00085/result | **Blocked** |

## Request shape used
`POST /v2/acts/{username~name}/runs?maxTotalChargeUsd=<cap>` with the Actor input as JSON body, poll `GET /v2/actor-runs/{id}`,
then `GET /v2/datasets/{defaultDatasetId}/items`. Authorization is a Bearer token from `APIFY_TOKEN`.

## Worst-case cost per run (defaults: max 100 results per Actor)
TED about $0.40, World Bank about $0.25. The default per-run limit is $1.00 and the daily limit $5.00, so one TED + one World Bank run fits.

## Why Clutch is blocked
Its own input schema defaults to RESIDENTIAL proxies and per-request IP rotation because Clutch blocks scraping; that is evasion of a site's access controls, and Clutch's terms have not been reviewed. A directory listing would also only prove an agency exists (it maps to `speculative_prospect`), not partner activity.
Options for agency-partnership discovery that need your decision: (a) official partner directories with permissive terms/APIs, (b) agency websites' own public partner/white-label pages via a general crawler, (c) another source you name.

## To enable the first paid run (needs your approval)
1. Create an Apify token; put it in `.env` as `APIFY_TOKEN` (never commit).
2. Set `ENABLED_CONNECTORS=apify_ted_tenders,apify_worldbank_tenders` and `APIFY_PAID_EXECUTION_APPROVED=true`.
3. Optionally lower `SPEND_LIMIT_PER_RUN_USD`. Create a search profile, then run it from the dashboard.
4. Save the first real responses as fixtures (replace the synthetic ones in `backend/tests/fixtures/apify`).

Unverified assumptions to confirm on the first real run: exact `noticeType` values and date formats in real TED output (the date parser returns "undated" rather than guess, and undated items are treated as stale), and whether `usageTotalUsd` is populated at run end (the connector falls back to the `maxTotalChargeUsd` cap for the ledger).
