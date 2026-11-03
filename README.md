# Personal AI GTM Command Center

An AI-assisted client-acquisition system for finding, qualifying and engaging paid AI
implementation projects and delivery partners worldwide. Every finding keeps its original
evidence, and **nothing is ever sent without explicit human approval**.

> Status: **Milestone 2 (real discovery, built but not yet run against paid providers)**.
> Defaults to mock providers: no paid APIs, no credentials, no outreach. Apify connectors exist
> but cannot spend until you set a token *and* `APIFY_PAID_EXECUTION_APPROVED=true`.
> See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) and [docs/APIFY_ACTORS.md](docs/APIFY_ACTORS.md).

## Stack
Python 3.12 · FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL 16 · LangGraph · Next.js 16 (TypeScript) · Docker Compose.
AI provider: **OpenAI** (default) behind a provider-neutral `LLMClient`, using the Responses API with
Structured Outputs; offline mock mode is the default. Claude Code is the development tool only.
Data acquisition: Apify Actors behind `DiscoveryConnector` (MCP/official APIs are planned).

## Key guarantees
- Three strictly separate buckets: `confirmed_demand`, `verified_partnership`, `speculative_prospect`.
- Evidence (source URL, verbatim text, dates, confidence, verification status) is stored for every finding.
- Contacts exist only if the source contained them. Nothing is inferred or invented.
- Outreach drafts start as `pending_approval`; a DB constraint blocks `approved`/`sent` without an approver.
- **AI claims must be source-backed:** every AI-generated claim cites an evidence id and quotes it verbatim; outputs that don't (or that invent URLs or amounts in outreach) are rejected, re-asked once, then discarded and audited. AI can refine qualification and draft outreach, never classify evidence or send anything.
- Every LLM attempt is metered (tokens, cost) in `llm_usage`, counted against per-run/daily/monthly limits, retried with backoff on transient errors, and refused outright if live prices are not configured.
- Provider spend limits (per-run / daily / monthly) are checked *before* any provider call; Apify runs also carry a hard `maxTotalChargeUsd`.
- Original-source verification (optional) fetches the cited page, respects robots.txt, blocks private addresses, and records `source_check_status` separately from human verification.
- Duplicates collapse across providers (canonical URL + normalised text) and near-identical titles merge.
- Stale or undated sources are flagged and cannot be verified.

## Quick start (Docker)
```bash
cp .env.example .env
docker compose up --build
```
Dashboard http://localhost:3000 · API docs http://localhost:8000/docs

If a host port is taken (3000 for the dashboard, 5432 for Postgres), set `FRONTEND_PORT` / `DB_PORT` in `.env`.

## Quick start (local, no Docker)
Requires Python 3.12, [uv](https://docs.astral.sh/uv/), Node 20+, and a PostgreSQL 16 server.
```bash
cp .env.example .env            # point DATABASE_URL at your Postgres
make migrate                    # alembic upgrade head
make api                        # terminal 1 - http://localhost:8000
make worker                     # terminal 2 - background workflow worker
make web                        # terminal 3 - http://localhost:3000 (run `npm install` in frontend/ first)
```
### Turning on live OpenAI (needs your approval)
Set in `.env`: `LLM_MOCK_MODE=false`, `LLM_PAID_CALLS_ENABLED=true`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INPUT_USD_PER_MTOK`, `OPENAI_OUTPUT_USD_PER_MTOK` (prices from OpenAI's pricing page; none are assumed). `GET /ai/status` and the dashboard show whether paid calls are active.

Click **Run discovery (mock)** in the dashboard, or `curl -XPOST localhost:8000/runs -H 'content-type: application/json' -d '{}'`.

## Tests and checks
```bash
make test     # backend pytest + frontend vitest
make lint     # ruff, mypy --strict, tsc
```
Integration tests need a database: `TEST_DATABASE_URL=postgresql+psycopg://user@host:5432/gtm_test make test`
(they drop and recreate the `public` schema of that database - use a throwaway one). Without the variable they are skipped.

## Layout
```
backend/app/{core,db,models,connectors,agents,orchestrator,services,api,worker}
backend/alembic/       migrations      backend/tests/{unit,integration}
frontend/              Next.js dashboard
docs/                  ARCHITECTURE, IMPLEMENTATION_PLAN, DECISIONS
```

## Adding a real provider
Implement `DiscoveryConnector` (`backend/app/connectors/base.py`), declare the `SignalKind`s it can
produce and an honest `estimate_cost`, then `register_connector(...)` and add its name to
`ENABLED_CONNECTORS`. Verify the provider's real API contract from its own documentation first.
Do not guess field names.

## Secrets
Only `.env.example` is committed. `.env` is git-ignored. Keys use `SecretStr` and are never logged.
