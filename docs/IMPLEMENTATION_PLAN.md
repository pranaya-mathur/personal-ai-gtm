# Implementation plan

## M1 - Foundation (done)
Repo structure, FastAPI + Postgres + Alembic, entities, LangGraph pipeline with persistent state,
connector interface + mocks, spend guard, dedupe/freshness, approval gate, dashboard shell, Docker, tests, docs.

## M2 - Real discovery
- Verify and implement first Apify-backed connector against the Actor's documented input/output schema (budget-capped, behind explicit opt-in).
- Official-API and permitted public-source connectors (RSS/job boards with terms allowing access); at least two independent providers per signal kind.
- Source freshness re-checks and scheduled re-discovery.

## M3 - Intelligence
- `OpenAILLMClient` behind `LLMClient`; structured extraction with citations to evidence spans; eval set to catch invented requirements.
- Research agent: second-source corroboration, decision-maker discovery from permitted sources.
- Qualification v2 (ICP fit configuration, weighting UI).

## M4 - Engagement and pipeline
- Draft editor + approval UI; deals/interactions CRUD; follow-up reminders; email sending only after approval, with audit log.
- Auth for the dashboard/API.

## M5 - Analytics and hardening
- Funnel/source-ROI analytics, cron-style scheduled runs, metrics/tracing, CI pipeline, backups, rate limits.
