# Implementation plan

## M1 - Foundation (done)
Repo structure, FastAPI + Postgres + Alembic, entities, LangGraph pipeline with persistent state,
connector interface + mocks, spend guard, dedupe/freshness, approval gate, dashboard shell, Docker, tests, docs.

## M2 - Real discovery (built; paid execution awaiting approval)
Done: Actor-spec connector framework, TED + World Bank specs (schemas validated read-only), search profiles, provider-independent dedupe + near-duplicate merge, source verification step, per-run spend caps, dashboard views, tests on synthetic fixtures.
Blocked / pending (see docs/APIFY_ACTORS.md): approval + token for the first paid run; a permitted agency-partnership source; recorded real responses to replace synthetic fixtures; scheduled re-discovery.

## M3 - Intelligence (partly built)
Done (mock-verified, live untested): OpenAI gateway (Structured Outputs, retries, token/cost ledger, limits), six AI capabilities with claim validation, AI-blended qualification, AI outreach drafts.
Remaining: first approved live OpenAI run and a real-output eval set (to measure rejection rate and quality); second-source corroboration and decision-maker discovery from permitted sources; ICP configuration UI; prompt/schema versioning.

## M4 - Engagement and pipeline
- Draft editor + approval UI; deals/interactions CRUD; follow-up reminders; email sending only after approval, with audit log.
- Auth for the dashboard/API.

## M5 - Analytics and hardening
- Funnel/source-ROI analytics, cron-style scheduled runs, metrics/tracing, CI pipeline, backups, rate limits.
