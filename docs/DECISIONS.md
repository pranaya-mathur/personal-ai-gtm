# Decision log

1. **Sync SQLAlchemy 2 + psycopg3.** LangGraph runs and the worker are synchronous; avoids async/sync bridging. Revisit if API load requires it.
2. **Postgres table as job queue** (`workflow_runs`, `SKIP LOCKED`) rather than Redis/Celery. One fewer service, persistent state for free. Revisit at higher throughput.
3. **Python 3.12.** Pinned `>=3.12,<3.14`; the machine default (3.15) is too new for the dependency set.
4. **Deterministic agents in M1.** No LLM calls until evidence schemas and evals exist; prevents invented requirements.
5. **Verification is human-only.** Automated steps may mark `stale` or `unverified`, never `verified`.
6. **Category by source assertion, demoted on missing proof.** Prevents speculative signals leaking into confirmed demand.
7. **JSON/JSONB variant type.** Unit tests run on SQLite; integration tests and production use PostgreSQL.
8. **No real provider contracts yet.** Apify/MCP connectors need documented, verified schemas first. Mock data uses `.example` domains and a `[MOCK FIXTURE]` tag.
9. **Known audit items.** `npm audit` reports transitive `postcss`/`sharp` advisories inside Next.js; `next/image` is unused. Re-check when Next ships a fix; do not `--force`.
10. **Uncommitted/unpushed.** Per instruction, nothing is pushed until approved.
