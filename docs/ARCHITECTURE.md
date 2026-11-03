# Architecture

## Overview
```
Next.js dashboard ──HTTP──▶ FastAPI ──▶ PostgreSQL ◀── Worker (polls workflow_runs)
                                                           │
                                                  LangGraph workflow
   opportunity_intelligence → agency_partnership_intelligence → research_evidence
        → qualification → outreach_intelligence → pipeline_analytics
                  │
         DiscoveryConnector (pluggable) ── mock now; Apify / MCP / official APIs later
```

## Agents (M1 status)
| Agent | Responsibility | M1 behaviour |
|---|---|---|
| Opportunity Intelligence | Find stated project demand and indirect buyer signals | Runs connectors with `project_request`/`company_signal` kinds |
| Agency Partnership Intelligence | Find agency/white-label/subcontract activity | Runs connectors with `partner_activity` kind |
| Research & Evidence | Audit evidence completeness and freshness | Deterministic checks; adds no facts |
| Qualification | Score 0-100 with a transparent rubric | Rubric over category, confidence, freshness, sourcing, known contact |
| Outreach Intelligence | Prepare drafts for approval | Template using evidence only; unknowns left as `[PLACEHOLDER]` |
| Pipeline & GTM Analytics | Funnel and spend metrics | Counts by category/status, pending approvals, spend |

No agent calls an LLM in M1; `agents/llm.py` defines the boundary for OpenAI.

## Evidence classes (never merged)
- **confirmed_demand**: a buyer publicly stated a project need (source URL + original text required).
- **verified_partnership**: evidenced agency/partner activity.
- **speculative_prospect**: inferred fit only. Anything lacking proof is demoted here, never promoted.
Classification lives in `services/ingest.classify`. `verification_status` is separate and only a
human action (`POST /opportunities/{id}/verify`) sets `verified`.

## Persistent job state
`workflow_runs` is both state store and queue. Workers claim rows with `FOR UPDATE SKIP LOCKED`,
state is committed after every graph node, nodes already in `completed_nodes` are skipped on retry,
runs are retried up to `WORKER_MAX_ATTEMPTS`, and `running` rows with a stale lock (>10 min) are re-queued.

## Data model
`workflow_runs`, `evidence`, `organizations`, `contacts`, `opportunities`, `opportunity_evidence`,
`qualifications`, `outreach_drafts`, `deals`, `interactions`, `provider_spend`.
Integrity rules are DB constraints: confidence in [0,1], enum checks, unique `dedupe_key` and
`evidence.content_hash`, and approved/sent drafts require `approved_by` + `approved_at`.

## Dedupe and freshness
Opportunity key = sha256(category | org domain/name | normalised title). Evidence hash =
sha256(provider | url | text). Sources older than `max_age_days` (default 90) or undated are `stale`
and auto-disqualified; stale items cannot be verified.

## Spend control
`SpendGuard.check(estimate)` runs before each provider call against per-run, daily and monthly limits;
actual cost is recorded in `provider_spend`.

## Safety
No sender exists. Approval API only records approval. Approval is refused while a draft still contains
`[` placeholders, so unfilled templates cannot be approved.
