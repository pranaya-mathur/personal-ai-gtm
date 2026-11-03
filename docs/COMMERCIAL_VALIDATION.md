# Commercial source assessment and bounded validation (2026-11-03)

Everything below is free, permitted access. No paid provider ran; nothing was sent. Web evidence was
gathered with search on the same date; claims about third-party terms need re-checking before reliance.

## 1. What the evidence says about each channel

### A. Buyers actively seeking external AI implementation
| Candidate source | Concrete recent demand seen | Eligibility / contact | Access | Cost | Automatable | Fit to $3–10k |
|---|---|---|---|---|---|---|
| HN monthly freelance thread | Nov/Oct/Sep 2026 threads: 8/21/24 comments, **all** "SEEKING WORK" (supply side); 0 buyers | direct, open | public API (free) | $0 | yes | right size, but no demand now |
| EU/UK/CA/World Bank tenders (existing) | 9 open relevant notices, e.g. TED 723838-2026 (chatbot, FR, est. €316k), 674672-2026 (€4bn gigafactory) | formal eligibility, lots, language | official, free | $0 | yes | none fit: values 50×–400,000× too large, or frameworks/defence/R&D |
| SAM.gov | search found only a closed GSA AI contact-centre **RFI** (small-business set-aside, due 2026-01-20); API key not configured | US registration needed to be awarded | official, free key | $0 | yes once keyed | plausible for micro-purchases; unmeasured |
| Enterprise Europe Network partnering database | listings are mostly company offers and Horizon consortium searches (e.g. Greek AI agency seeking a Cluster 4 consortium, Feb 2026); no current outsourcing request found | free profile access; contact via local EEN office | public pages; no confirmed API | $0 | partial | weak; consortium/R&D oriented |
| Reddit / LinkedIn / Facebook groups, founder communities | cannot be measured by automation | n/a | no permitted automated route | $0 | **no** | likely best fit, human-only |

### B. Agencies with real subcontracting needs or documented external-specialist relationships
Searching "white-label / subcontractor AI partner" returns almost only **supply-side** vendors advertising
themselves to agencies (Alian Software partners page, Codewave, Cogniq AI, an Upwork "subcontracting partner" post by
Aivia Labs). Those are competitors, not demand, and Upwork is excluded. No agency stating a current external-delivery
requirement was found by any permitted automated route. The existing candidate/partner-page pipeline found **0 real**
verified partner models and 0 active agency needs (the "2 verified" in the earlier report were mock fixtures — corrected).
Realistic route: relationship-building with agencies you can name (your own network, conference/community contacts,
vendor partner ecosystems you join), logged through Manual intake.

### C. Credible proactive prospects (triggers, never demand)
HN "Who is hiring" (existing connector, works): 14 non-rejected prospects after the run. Useful as a *list to research*,
not demand: most are full-time AI hires at funded product companies (Juro, Hestus, Encube, Opsfleet). The better
prospects for contract help are those with a stated contract/services angle or no in-house team yet, e.g. a Saudi
engineering group building a new in-house AI unit. Public-sector PINs (TED) are triggers for large buyers only.

## 2. Recommendation
- **Automate (free):** TED/UK/CA/World Bank/HN-hiring as a *monitoring baseline* and Channel C prospect list; SAM.gov
  after you add the free key (measure first; expect micro-purchase RFQs).
- **Human-in-the-loop:** Channel A at your size and Channel B. Use Manual intake from posts you may legitimately copy,
  your own network, and partner programmes you join. The system's value there is verification, fit, drafting and tracking.
- **Do not build next:** more procurement APIs or keyword lists. Evidence says they surface large, gated notices.
- Re-evaluate continued use: World Bank (54+13 records, 0 usable), UK Find a Tender (22, 0), HN freelancer (0 buyers).
  Keep TED/Contracts Finder/CanadaBuys at low frequency for Channel C and monitoring.

## 3. Bounded validation result (live DB, same-day run)
Pursuable opportunities: **0**. Active agency delivery requirements: **0**. Real verified agency models: **0**.

### Relevant, open, contactable notices (needs verification; none fits $3–10k)
| Rank | Organisation / notice | Evidence (original) | Route | Fit | Next action |
|---|---|---|---|---|---|
| 1 | Action Logement (FR) – customer chatbot, deadline 2026-11-19 | https://ted.europa.eu/en/notice/723838-2026/html | achats.als@actionlogement.fr (procurement) | relevant scope; est. €316k, formal tender, delivery model unstated | read notice; only if you can bid at that scale |
| 2 | Tourismus Felsenland Südeifel (DE) – AI guest advice, lot 3, deadline 2026-11-13 | https://ted.europa.eu/en/notice/712759-2026/html | info@felsenland-suedeifel.de (generic) | relevant; value unstated; German-language; short deadline | read lot 3; likely too soon |
| 3 | Gemeente Den Haag – DAS innovation advisory, 2026-11-19 | https://ted.europa.eu/en/notice/697168-2026/html | [named individual - omitted] (named, role unverified) | advisory, €6m | skip unless consulting scale |
| – | EuroHPC (€4bn), Cyberagentur, Digifinland (DPS), ICT Skillnet (framework), C4ISR (defence) | in DB | procurement inboxes | not accessible / not small | reject for this engagement size |

### Proactive prospects (NOT demand; evidence = HN hiring post or TED planning notice)
| Rank | Organisation | Evidence | Route | Why it ranks | Next action |
|---|---|---|---|---|---|
| 1 | Opsfleet (cloud & AI services, remote EEA) | https://news.ycombinator.com/item?id=50349129 | hiring@opsfleet.com is a **recruiting** inbox; use company site | agency-like (Channel B candidate): could use external specialists, unproven | research partner page; find a delivery lead |
| 2 | Confidential Saudi engineering group (new in-house AI unit) | https://news.ycombinator.com/item?id=50339161 | via https://zalvi.app | building capability, may buy interim help | check site; low confidence |
| 3 | Juro (London, AI contract automation) | https://news.ycombinator.com/item?id=50339551 | careers page only | funded, active AI team; contractor angle unproven | research CTO via company site |
| 4 | Hestus, Encube, Vivral, United Risk | HN item ids 50342280, 50349751, 50344410, 50338822 | careers/forms only | full-time hiring, weak outside-help signal | low priority |
| – | Norwegian/Dutch/Finnish public bodies (PIN/RFI) | TED 728691, 721582, 683094, 647124, 596208 | procurement inboxes | large public buyers, future tenders | monitor only |

### Why each source did or did not work (this run)
| Source | Result | Reason |
|---|---|---|
| ted_api | ran, 33 records, 0 new | mostly previously seen; large/gated |
| worldbank_api | ran, 100 records, 13 new, all rejected | on-site/individual/large consulting |
| uk_contracts_finder / find_a_tender | ran, no new | closed/awarded |
| canadabuys | ran, 3 | supply-arrangement / defence |
| hn_freelancer_threads | ran, 2 old records | **bug**: connector read Oct 2025 threads (fixed this round); 2026 threads have 0 buyers |
| hn_hiring_triggers, ted_planning_notices | ran, 6 / 8 (3 new) | trigger data only |
| sam_gov | skipped | no API key |
| wikidata discoverer | **failed** (ReadTimeout 60s) | external service slow; retry later |
| github discoverer / partner pages | ran; 21 pages no signal, 2 blocked, 2 error | agencies rarely publish active needs |
