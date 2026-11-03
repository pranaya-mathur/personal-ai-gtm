# Operating guide (client acquisition)

Status: the code is verified; **commercial discovery effectiveness is unproven**. The system helps you
verify, qualify, draft, approve and track. It does not autonomously acquire clients, and it never sends anything.

## Minimum configuration (`.env`, never committed)
- `API_TOKENS` with `name:role:token` entries (viewer/operator/admin); `GTM_API_TOKEN` = an operator token.
- `DASHBOARD_PASSWORD` (operator login). For approvals also: `DASHBOARD_ADMIN_USER`, `DASHBOARD_ADMIN_PASSWORD`
  (different password), `GTM_ADMIN_API_TOKEN` = an admin token from `API_TOKENS`.
- Leave all paid flags off (`APIFY_PAID_EXECUTION_APPROVED=false`, `SEARCH_DISCOVERY_ENABLED=false`, `LLM_MOCK_MODE=true`).
- Optional: free `SAMGOV_API_KEY`; `SCHEDULER_ENABLED=true` for free scheduled discovery.

## 1. Start
`docker compose up -d --build` (migrations run automatically), open http://localhost:3100, sign in.
Before any migration or data change run `scripts/db_backup.sh` then `scripts/db_restore_check.sh`.
The header shows whether you are signed in as operator or admin.

## 2. Review discovered opportunities (works)
Tabs: *Review queue* (new, unreviewed), *Relevant, needs verification*, *Proactive buyers*, *Contact routes*.
Mock fixtures are labelled and excluded from real counts. "Pursuable" is rare by design; proactive prospects are never demand.

## 3. Add a genuine requirement or agency need (manual effort)
*Manual intake*: paste the original post URL and a verbatim excerpt you are allowed to copy, set channel
(direct buyer / agency), status, deadline, budget if stated, and the published contact route.
Use this for communities, founder posts, your own network and agency contacts: no permitted automated route exists there.

## 4. Qualify (works, needs your judgment)
Open the record, read the original source yourself, then in *Review queue* tick only what the source itself establishes
(supplier eligibility, delivery model) and press "I verified the source". The assessment is recomputed; a closed,
expired or out-of-scope record stays rejected. Unknown budget is not treated as suitability: ask before quoting.

## 5. Outreach (works; sending is manual)
Research detail -> *Prepare outreach draft*. In *Outreach drafts* edit and fill every `[PLACEHOLDER]` (no invented
experience; add verifiable references to the capability profile first). An **admin** login approves. Send it yourself
from your own mail, then press "I sent this myself — record it".

## 6. Track replies, meetings, proposals, onboarding (works; you enter the facts)
*Engagement pipeline*: create the deal, log each real reply/call/meeting, set the stage. *Proposals*: prepare, edit scope,
price and timeline, admin-approve, send yourself, record it. When a deal is won, *Onboarding* offers the checklist.
Never record a reply, signature or payment that did not happen.

## What is automatic vs manual
- Automatic (free): TED, UK, CanadaBuys, World Bank, Hacker News feeds; triage; audit trail; spend guards; optional schedules.
- Manual: finding real demand outside those sources, verification, contact choice, drafting, approval, sending, all follow-up.
