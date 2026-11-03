# Source acquisition strategy (gap assessment)

Three commercial classes are kept strictly apart end to end (ingest → triage → board → pipeline):

| Class | Meaning | System label |
|---|---|---|
| Confirmed commercial demand | A buyer/agency publicly asked for external implementation capacity | `confirmed_active_ai_request`, `active_agency_delivery_partner_need` |
| Verified partner model | An agency demonstrably works with external delivery partners; no current project | `verified_agency_partnership_model` |
| Proactive prospect | A business trigger exists; nobody asked for services | `potential_without_current_demand` (never counted as demand) |

"Pursuable" (`assessment.pursuability == "pursuable"`) additionally requires: open and not past deadline,
in-scope AI work, delivery model established, supplier eligibility established (formal procurement),
a usable route, and size not failed. Unknown budget is allowed but never counts as suitability.

## Per-source assessment

Historical yield is from the live database before the hardening run (records → pursuable after the stricter gates).

| Source | Demand type | Access | Small ($3–10k) projects? | Early enough? | Buyer identifiable / route | Realistic for a solo specialist? | Repeatable without manual seeds | Yield (records → pursuable) | Cost |
|---|---|---|---|---|---|---|---|---|---|
| TED API (EU) | A | official, free | rare (above-threshold notices) | yes, open notices | yes / notice contact | seldom: eligibility, lots, language | yes | 29 → 0 (14 relevant, need verification) | $0 |
| UK Contracts Finder | A | official, free | some sub-threshold | yes | yes | sometimes | yes | 10 → 0 | $0 |
| UK Find a Tender | A | official, free | no (above threshold) | yes | yes | seldom | yes | 22 → 0 | $0 |
| World Bank notices | A | official, free | no (mostly individual/on-site consulting) | yes | yes | no | yes | 54 → 0 | $0 |
| CanadaBuys | A | official, free | some | yes | yes | often supply-arrangement only | yes | 3 → 0 | $0 |
| SAM.gov | A | official, **needs free API key (not configured)** | RFQs/micro-purchases exist | yes | yes | needs SAM registration | yes | not run | $0 |
| HN "Seeking freelancer" | A | public API | yes (this is the right size) | yes | poster handle/email | yes | yes | 2 → 0 (very low volume) | $0 |
| Partner pages / feeds / Wikidata / GitHub candidates | B | permitted, robots-respecting | n/a | models, not requests | agency yes | yes | candidates yes; pages bounded | 2 verified models, 0 active needs | $0 |
| HN "Who is hiring" triggers | C | public API | n/a | trigger only | company | yes, as prospects | yes | 27 → 7 prospects, 0 demand | $0 |
| TED planning notices | C | official | n/a | before tender | yes | prospects | yes | 4 prospects | $0 |
| Brave / Apify SERP | A/B | paid, **off** | n/a | n/a | n/a | n/a | yes | prior paid trials ≈ no usable records | per call |
| Reddit | A | no commercial API arrangement; manual intake only | yes | yes | handle | yes | **no** | manual | $0 |
| LinkedIn / Facebook | A/B | no permitted automated route; manual intake only | yes | yes | person | yes | **no** | manual | $0 |
| Upwork / Fiverr / Clutch etc. | — | excluded | — | — | — | — | — | — | — |

Terms for Reddit/LinkedIn/Facebook are as understood at time of writing; re-check before relying on them.

## Gap conclusions

1. Free official procurement APIs are repeatable but structurally mismatched: they surface large, eligibility-gated,
   often closed notices. They will rarely yield a $3–10k project; they remain useful as a free baseline and for Channel C.
2. The sizes that fit (small, remote, informal) live where there is no API: communities, founder posts, agency
   subcontracting requests. Permitted access there is **human-in-the-loop** (Manual intake) until a licensed
   data route exists. This is the main unsolved product blocker.
3. Candidate sources worth assessing next (not built, no spend): sub-threshold national portals with open data
   (France BOAMP, Australia AusTender OCDS, Spain PLACSP), SAM.gov RFQs (needs the free key), and vendor partner
   programmes that publish agency-partner requests. Each needs a terms check and a measured yield before enabling.
4. Query-effectiveness feedback only orders paid/search queries; no self-learning claim is made until repeated
   live executions show the measured ordering improving usable-record ratio.
