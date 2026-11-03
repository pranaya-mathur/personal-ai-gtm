# Implementation plan

## M1 - Foundation (done)
Repo structure, FastAPI + Postgres + Alembic, entities, LangGraph pipeline with persistent state,
connector interface + mocks, spend guard, dedupe/freshness, approval gate, dashboard shell, Docker, tests, docs.

## M2 - Real discovery (built; paid execution awaiting approval)
Done: Actor-spec connector framework, TED + World Bank specs (schemas validated read-only), search profiles, provider-independent dedupe + near-duplicate merge, source verification step, per-run spend caps, dashboard views, tests on synthetic fixtures.
Blocked / pending (see docs/APIFY_ACTORS.md): approval + token for the first paid run; a permitted agency-partnership source; recorded real responses to replace synthetic fixtures; scheduled re-discovery.

## M3 - Intelligence
- Enable `AnthropicLLM` (needs a console.anthropic.com API key, explicit model, `LLM_PAID_CALLS_ENABLED=true`) behind `LLMClient`; structured extraction with citations to evidence spans; eval set to catch invented requirements.
- Research agent: second-source corroboration, decision-maker discovery from permitted sources.
- Qualification v2 (ICP fit configuration, weighting UI).

## M4 - Engagement and pipeline
- Draft editor + approval UI; deals/interactions CRUD; follow-up reminders; email sending only after approval, with audit log.
- Auth for the dashboard/API.

## M5 - Analytics and hardening
- Funnel/source-ROI analytics, cron-style scheduled runs, metrics/tracing, CI pipeline, backups, rate limits.
