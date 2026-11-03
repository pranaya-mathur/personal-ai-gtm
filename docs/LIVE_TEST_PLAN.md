# First controlled live test — plan awaiting approval

Nothing below has been run. Billable calls need your explicit go-ahead.

## Verified without spending (2026-11-03)
- `gpt-5.4-mini` exists (docs: developers.openai.com/api/docs/models/gpt-5.4-mini): Responses API and
  Structured Outputs supported; $0.75 in / $0.075 cached in / $4.50 out per 1M tokens; 400k context.
- `GET /v1/models/gpt-5.4-mini` with the configured key returned 200 (free metadata call): the model is
  available to this account. A billable probe has not been made.
- Apify token valid (`/v2/users/me/limits`, free). Account monthly cap $29, month-to-date ≈ $0.0024.
- Actor schemas validated read-only earlier (see APIFY_ACTORS.md): `foxlabs/ted-tenders`,
  `foxlabs/worldbank-tenders`.

## Enforced limits
OPENAI_TEST_BUDGET_USD=0.50, APIFY_TEST_BUDGET_USD=0.25, TOTAL_TEST_BUDGET_USD=0.75 are checked against
the spend ledger BEFORE each call using a conservative estimate. Apify runs also pass
`maxTotalChargeUsd`. Per-run LLM cap 0.50, AI limited to 10 records.

## Proposed execution (needs approval)
1. `.env`: `ENABLED_CONNECTORS=apify_ted_tenders,apify_worldbank_tenders`,
   `APIFY_PAID_EXECUTION_APPROVED=true`, `LLM_MOCK_MODE=false`, `LLM_PAID_CALLS_ENABLED=true`,
   `AI_MAX_OPPORTUNITIES_PER_RUN=10`, `SOURCE_VERIFICATION_ENABLED=true`.
2. One search profile: AI keywords, `max_results` 40 (TED worst case ≈ $0.16) and ≤ 20 for World Bank
   (≈ $0.05): worst case Apify ≈ $0.21 < $0.25.
3. One run; then review in the dashboard. No outreach, applications or contact purchases.

## Limits you should know
- **Agency partnership discovery is NOT covered**: the only registered Actor (Clutch) is blocked
  (residential proxies / ToS). Pipeline B needs a permitted source first.
- Caps are checked on estimates before each call; an in-flight call can overshoot by its own cost and
  provider billing lags. Cached-input discounts are not applied (costs are over-estimated).
- Real Apify output shapes are unverified until the first run (fixtures are synthetic).
