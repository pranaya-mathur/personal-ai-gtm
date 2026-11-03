# Decision log

1. **Sync SQLAlchemy 2 + psycopg3.** LangGraph runs and the worker are synchronous; avoids async/sync bridging. Revisit if API load requires it.
2. **Postgres table as job queue** (`workflow_runs`, `SKIP LOCKED`) rather than Redis/Celery. One fewer service, persistent state for free. Revisit at higher throughput.
3. **Python 3.12.** Pinned `>=3.12,<3.14`; the machine default (3.15) is too new for the dependency set.
4. **Deterministic agents.** No LLM calls until evidence schemas and evals exist; prevents invented requirements. (AI stage added in 19.)
5. **Verification is human-only.** Automated steps may mark `stale` or `unverified`, never `verified`.
6. **Category by source assertion, demoted on missing proof.** Prevents speculative signals leaking into confirmed demand.
7. **JSON/JSONB variant type.** Unit tests run on SQLite; integration tests and production use PostgreSQL.
8. **No real provider contracts yet.** Apify/MCP connectors need documented, verified schemas first. Mock data uses `.example` domains and a `[MOCK FIXTURE]` tag.
9. **Dependency advisories (resolved 2026-11-03).** Upgraded Next 15.3 -> 16.4.4 (clears next/postcss/sharp) and vitest 3 -> 4.1.12 (clears vitest/tinypool). `npm audit`: 0 vulnerabilities. Python lockfile checked against OSV: 0 advisories (pip-audit crashed on this machine, so OSV was queried directly).
10. **Uncommitted/unpushed.** Per instruction, nothing is pushed until approved.
11. **OpenAI is the primary AI provider (final).** History: planned OpenAI -> briefly switched to Claude -> reverted on your instruction; Claude Code is the development tool only. The Anthropic client and its dependency were removed rather than kept as unverified code. `app/llm/` keeps a provider-neutral `LLMClient` (structured output + token usage), a free `MockLLM` (default) and `OpenAILLM`.
12. **Apify behaviour lives in `ActorSpec`s.** Only `connectors/apify_specs.py` knows Actor field names, taken from schemas read read-only on 2026-11-03. Three gates guard spend: spec `reviewed`, `APIFY_TOKEN`, `APIFY_PAID_EXECUTION_APPROVED`; plus the pre-flight spend guard and Apify's `maxTotalChargeUsd`.
13. **Demand sources are official procurement notices** (EU TED, World Bank). Directory listings are never promoted above `speculative_prospect`: a listing proves an agency exists, not partner activity.
14. **Clutch Actor registered but blocked.** It needs residential proxies to get past blocking and Clutch's terms are unreviewed. Agency-partnership discovery needs a permitted source (see APIFY_ACTORS.md).
15. **Evidence hash is provider-independent** (canonical URL + whitespace-normalised text) so providers that return the same page collapse to one record.
16. **Source check is separate from verification.** `source_check_status` says the cited page contains the claim; only a human sets `verified`.
17. **Fixtures are synthetic.** Apify test fixtures follow the validated schemas but are invented; real recordings come after the first approved paid run.
18. **Compose ports are configurable** (`FRONTEND_PORT`, `DB_PORT`): this machine has other services on 3000 and 5432.
19. **OpenAI via the Responses API with Structured Outputs.** Contract verified from OpenAI's docs and the installed SDK's types (not assumed): `responses.parse(model, instructions, input, text_format=<Pydantic>, max_output_tokens, store=False)`, `output_parsed`, `usage.input_tokens/output_tokens`, refusal as a `ResponseOutputRefusal` item, incomplete status. SDK-level retries are disabled so the gateway meters every attempt.
20. **No assumed model or prices.** `OPENAI_MODEL` and both per-1M-token prices must be set by you; live calls are refused otherwise, so cost tracking and limits can never silently be zero.
21. **Live OpenAI needs five switches:** `LLM_MOCK_MODE=false`, `LLM_PAID_CALLS_ENABLED=true`, key, model, prices. Mock mode is the default and runs the full AI pipeline offline.
22. **Claims are verified in code, not trusted.** Each AI claim = text + evidence_id + verbatim quote (>= 8 chars) checked against that evidence; scores range-checked; outreach bodies may not contain URLs or money amounts absent from evidence. Failure => one metered repair attempt => discard and audit (`ai_analyses.validated=false`). Strict schemas use only required fields (no constraint keywords) so they are Structured-Outputs compatible; ranges are enforced after parsing.
23. **AI is advisory.** Evidence category stays deterministic. AI qualification blends 40% into the rubric score only when validated; an AI "unsupported" verdict blocks `qualified`. AI outreach drafts keep `[YOUR RELEVANT EXPERIENCE]` / `[PROPOSED NEXT STEP]` placeholders (the sender's experience is unknown), so the approval gate still forces a human edit.
24. **Evidence is untrusted input.** Prompts fence it in `<evidence>` tags and tell the model to treat it as data; the claim guard is the real defence against prompt injection.
25. **Cost bounds.** Pre-call worst-case estimate (chars/3 input + max output tokens) must fit the per-run LLM limit and global limits; at most `AI_MAX_OPPORTUNITIES_PER_RUN` opportunities (default 10) get AI; exhausted budget degrades to deterministic output instead of failing the run.

26. **Per-task models** (`OPENAI_*_MODEL`) fall back to `OPENAI_MODEL`; a failing model is never silently replaced.
27. **Test budgets** (`*_TEST_BUDGET_USD`) are lifetime caps per provider family plus a total, checked pre-call on the shared `provider_spend` ledger.
28. **Cancellation/timeouts**: `cancelled` run status (migration 0004), checked between graph nodes; a run exceeding `RUN_TIMEOUT_SECONDS` fails terminally.
29. **Mock isolation**: records whose evidence is all from `mock*` providers are flagged `is_mock` and never counted as leads in the dashboard.

30. **Two-stage qualification is deterministic** (`services/triage.py`): Stage A (technical relevance from the source text; case-sensitive short acronyms; AI words in a list only reach "unclear") and Stage B (external requirement, active, scope, delivery model, realistic size, contact route, budget; each pass/fail/unknown). `actionable` needs relevant + open + external + contact route. The AI dossier may only refine Stage A and never creates `actionable`. Category and human verification are never changed by triage.
31. **Rejected is explicit**: `triage=rejected` with `rejection_reason` (`not_relevant`, `closed`, `awarded`), status `disqualified`; evidence is kept. Closed/awarded notices are ingested and flagged rather than dropped.
32. **One AI call per record** (`dossier`) replaces five; deterministic filters run first; results are cached by a hash of task+model+prompt; per-record, per-task and repair limits are enforced; a bad response is audited and never retries the workflow.
33. **Agency discovery** uses permitted sources only: listed partner pages (robots.txt + SSRF-safe), listed RSS/Atom feeds, and Wikidata (potential partners only). A page that states nothing yields no record. Three classes: `active_requirement` (-> confirmed demand, still triaged), `verified_model` (-> verified partnership), `potential` (-> speculative).
34. **Audit** (`python -m app.cli.audit`) is dry-run by default, appends a Qualification row with the previous state, and can backfill structured fields from existing Apify run datasets via free read-only GETs.
