# Decision log

1. **Sync SQLAlchemy 2 + psycopg3.** LangGraph runs and the worker are synchronous; avoids async/sync bridging. Revisit if API load requires it.
2. **Postgres table as job queue** (`workflow_runs`, `SKIP LOCKED`) rather than Redis/Celery. One fewer service, persistent state for free. Revisit at higher throughput.
3. **Python 3.12.** Pinned `>=3.12,<3.14`; the machine default (3.15) is too new for the dependency set.
4. **Deterministic agents.** No LLM calls until evidence schemas and evals exist; prevents invented requirements. (Supersedes the original OpenAI plan: see 11.)
5. **Verification is human-only.** Automated steps may mark `stale` or `unverified`, never `verified`.
6. **Category by source assertion, demoted on missing proof.** Prevents speculative signals leaking into confirmed demand.
7. **JSON/JSONB variant type.** Unit tests run on SQLite; integration tests and production use PostgreSQL.
8. **No real provider contracts yet.** Apify/MCP connectors need documented, verified schemas first. Mock data uses `.example` domains and a `[MOCK FIXTURE]` tag.
9. **Dependency advisories (resolved 2026-11-03).** Upgraded Next 15.3 -> 16.4.4 (clears next/postcss/sharp) and vitest 3 -> 4.1.12 (clears vitest/tinypool). `npm audit`: 0 vulnerabilities. Python lockfile checked against OSV: 0 advisories (pip-audit crashed on this machine, so OSV was queried directly).
10. **Uncommitted/unpushed.** Per instruction, nothing is pushed until approved.
11. **Claude is the primary AI provider, not OpenAI.** `app/llm/` holds a provider-neutral `LLMClient`, a free `MockLLM` (default) and an `AnthropicLLM` that raises `LLMDisabledError` unless `LLM_PAID_CALLS_ENABLED=true`, an API key and an explicit `ANTHROPIC_MODEL` are all present. No model default is guessed. The `anthropic` SDK is an optional group. A Claude Pro/Max or Claude Code login is not API access for a deployed app.
12. **Apify behaviour lives in `ActorSpec`s.** Only `connectors/apify_specs.py` knows Actor field names, taken from schemas read read-only on 2026-11-03. Three gates guard spend: spec `reviewed`, `APIFY_TOKEN`, `APIFY_PAID_EXECUTION_APPROVED`; plus the pre-flight spend guard and Apify's `maxTotalChargeUsd`.
13. **Demand sources are official procurement notices** (EU TED, World Bank). Directory listings are never promoted above `speculative_prospect`: a listing proves an agency exists, not partner activity.
14. **Clutch Actor registered but blocked.** It needs residential proxies to get past blocking and Clutch's terms are unreviewed. Agency-partnership discovery needs a permitted source (see APIFY_ACTORS.md).
15. **Evidence hash is provider-independent** (canonical URL + whitespace-normalised text) so providers that return the same page collapse to one record.
16. **Source check is separate from verification.** `source_check_status` says the cited page contains the claim; only a human sets `verified`.
17. **Fixtures are synthetic.** Apify test fixtures follow the validated schemas but are invented; real recordings come after the first approved paid run.
18. **Compose ports are configurable** (`FRONTEND_PORT`, `DB_PORT`): this machine has other services on 3000 and 5432.
