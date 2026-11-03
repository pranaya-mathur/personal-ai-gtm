# Personal AI GTM — Product Status

Last audited: 2026-11-03 against local HEAD `f0a9dd7` plus uncommitted WIP (Apify reconcile, manual intake, triage budget parsing).

Paid providers remain **disabled by default**. No paid experiment was executed for this status update.

## Live baseline (real DB, not fixtures)

| Metric | Value |
|---|---|
| Opportunities | 142 |
| Actionable (direct buyer) | 5 |
| Needs verification | 13 |
| Rejected | 124 |
| Agency verified partnership | 2 (needs verification) |
| Deals / interactions | 0 / 0 |
| Source candidates | 54 (1 signal, 34 no_signal) |
| Apify intent spend | ~$0.07 → 1 stored record |
| OpenAI spend | ~$0.24 |

Dominant providers by volume: World Bank, TED, UK Find a Tender. High retrieval, high reject rate (not_relevant / closed / awarded).

## Capability inventory

| Capability | Class | Notes |
|---|---|---|
| FastAPI + bearer auth/roles | Production-capable | Loopback ports; dashboard proxy |
| PostgreSQL schema + Alembic | Production-capable | Through 0007; engagement tables exist |
| LangGraph orchestration + resume | Production-capable | Node skip + lock recovery |
| Mock discovery + isolation | Production-capable | `is_mock` excluded from lead counts |
| Official procurement APIs (TED, WB, UK, Canada) | Implemented, insufficiently commercially validated | Technically sound; yield mostly large/irrelevant notices |
| HN freelancer connector | Implemented, insufficiently validated | Small but relevant channel |
| Apify Actor connectors (TED/WB) | Implemented, gated | Schemas validated; paid off by default |
| Apify SERP intent search | Implemented, gated; low commercial yield | Duplicate-run fix at `f0a9dd7`; reconcile CLI in WIP |
| Brave intent search | Implemented, gated | Off unless `SEARCH_DISCOVERY_ENABLED` |
| Wikidata/GitHub discoverers | Partially implemented | Disabled when `ENABLED_DISCOVERERS` empty; mostly no_signal |
| Agency seed/page/feed connectors | Partially implemented | Needs non-empty `AGENCY_SEED_URLS` / feeds |
| Deterministic triage (Stage A/B) | Production-capable (rules) | Keyword alone cannot make actionable |
| Commercial-intent 6-class mapping | Added this cycle | Deterministic over category+triage+channel |
| Adaptive query strategy + effectiveness | Added this cycle | Free; no auto paid fallback |
| Source performance dashboard | Implemented | Cost-per-usable from ledger |
| SAM.gov US connector | Added this cycle | Free official API; needs `SAMGOV_API_KEY`; off by default |
| OpenAI gateway + structured outputs | Production-capable (gated) | Mock default; claim validation |
| Qualification ranking | Improved this cycle | Separate buyer vs agency rubrics |
| Buyer dossiers (AI) | Implemented, live lightly used | Cache + spend caps |
| Contact routes | Production-capable | Page URL alone is not a route |
| Manual human intake | Added this cycle | Permitted copy-paste for Reddit/LinkedIn/etc. |
| Outreach drafts + approval gate | Production-capable | No auto-send |
| Deals / interactions / pipeline stages | Partially → completed this cycle | Schema existed; API + dashboard added |
| Worker scheduling / cron rediscovery | Partially implemented | Polling worker; no cron scheduler yet |
| Paid provider auto-fallback | Intentionally missing | Must never silent-spend |

## Why lead yield is low (root causes)

1. **Channel mismatch.** Preferred deals ($3k–$10k) rarely appear as EU/WB tenders; those sources skew large, framework, or non-software.
2. **Static intent queries.** A handful of SERP strings hit listicles, vendor offers, and hiring posts; snippets are not evidence.
3. **Agency seeds empty.** Partnership connectors cannot invent domains; discoverers find orgs but pages seldom state subcontract demand.
4. **Social platforms excluded by design.** Reddit/LinkedIn/Facebook have no permitted automated commercial collection route → manual intake is the legitimate path.
5. **Engagement unused.** Actionable records never progressed into deals (0 rows), so conversion learning never started.

## Acceptance criteria tracker

| # | Criterion | Status |
|---|---|---|
| 1 | Find unknown orgs without seed domains | Partial — Wikidata/GitHub when enabled; mostly no demand signal |
| 2 | Discover commercial requirements via permitted sources | Partial — procurement + HN + intake; SERP gated |
| 3 | Distinguish real buyer intent | Implemented (deterministic + optional AI) |
| 4 | Active agency demand vs partnership model | Implemented |
| 5 | Preserve evidence; flag unsupported claims | Implemented |
| 6 | Exclude irrelevant/expired/dupes from actionable | Implemented (triage); live reject rate high |
| 7 | Legitimate contact routes | Implemented when printed |
| 8 | Qualify vs AI services / delivery | Implemented (triage + deal bands) |
| 9 | Evidence-grounded outreach for approval | Implemented |
| 10 | Track contact → response → meeting → proposal → outcome | Implemented this cycle (API + UI) |
| 11 | Recover without duplicate paid calls | Implemented (`f0a9dd7` + reconcile) |
| 12 | Security / privacy / spend constraints | Implemented; paid flags off |
| 13 | Operational metrics in dashboard | Implemented; engagement + query stats added |

## Source access limitations (summary)

See `docs/SOURCE_ACCESS.md`. LinkedIn, Reddit, Facebook automated collection: **disabled**. Upwork/freelance marketplaces: **excluded**. Clutch Actor: **blocked**.

## Next operational steps (no paid spend unless you approve)

1. Rebuild/restart containers so API picks up new code; run Alembic `0008`.
2. Add a few real leads via **Manual intake** (community posts you are allowed to copy).
3. Open an actionable opportunity → create a deal → log interactions → set follow-up.
4. Optionally set `ENABLED_DISCOVERERS=wikidata,github` for free candidate discovery.
5. Optionally obtain a free SAM.gov public API key and set `SAMGOV_API_KEY` + add `sam_gov` to `ENABLED_CONNECTORS`.
6. Do **not** re-enable Apify/Brave until a written experiment plan with budget cap is approved.
