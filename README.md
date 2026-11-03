# Personal AI GTM Command Center

An AI-assisted client-acquisition system for finding, qualifying and engaging paid AI
implementation projects and delivery partners worldwide. Every finding keeps its original
evidence, and **nothing is ever sent without explicit human approval**.

> Status: **Milestone 1 (foundation)**. Runs entirely on mock providers: no paid APIs, no
> credentials, no outreach. See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Stack
Python 3.12 · FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL 16 · LangGraph · Next.js 15 (TypeScript) · Docker Compose.
OpenAI, Apify and MCP are planned integrations behind interfaces (not wired in M1).

## Key guarantees
- Three strictly separate buckets: `confirmed_demand`, `verified_partnership`, `speculative_prospect`.
- Evidence (source URL, verbatim text, dates, confidence, verification status) is stored for every finding.
- Contacts exist only if the source contained them. Nothing is inferred or invented.
- Outreach drafts start as `pending_approval`; a DB constraint blocks `approved`/`sent` without an approver.
- Provider spend limits (per-run / daily / monthly) are checked *before* any provider call.
- Stale or undated sources are flagged and cannot be verified.

## Quick start (Docker)
```bash
cp .env.example .env
docker compose up --build
```
Dashboard http://localhost:3000 · API docs http://localhost:8000/docs

## Quick start (local, no Docker)
Requires Python 3.12, [uv](https://docs.astral.sh/uv/), Node 20+, and a PostgreSQL 16 server.
```bash
cp .env.example .env            # point DATABASE_URL at your Postgres
make migrate                    # alembic upgrade head
make api                        # terminal 1 - http://localhost:8000
make worker                     # terminal 2 - background workflow worker
make web                        # terminal 3 - http://localhost:3000 (run `npm install` in frontend/ first)
```
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
