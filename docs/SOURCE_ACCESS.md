# Source access and commercial-use permissions

Public visibility does **not** grant automated commercial collection rights. Connectors are only enabled when a permitted access route exists.

| Source | Automated collection | Status | Notes |
|---|---|---|---|
| EU TED v3 API | Yes (official) | Enabled when listed | Free, keyless |
| World Bank procnotices | Yes (official) | Enabled when listed | Free, keyless |
| UK Contracts Finder / Find a Tender | Yes (official OCDS) | Enabled when listed | Free, keyless |
| CanadaBuys open data CSV | Yes (official) | Enabled when listed | Free, keyless |
| SAM.gov Opportunities API | Yes (official) | Disabled until `SAMGOV_API_KEY` | Free public key; rate-limited |
| Hacker News Algolia API | Yes (public API) | Enabled when listed | "SEEKING FREELANCER" comments only |
| HN "Who is hiring?" (triggers) | Yes (public API) | Enabled when listed | Pipeline C: a hiring post is a TRIGGER only; kept only with a contract/new-AI-initiative/services-firm signal |
| TED prior-information notices (pin-only) | Yes (official) | Enabled when listed | Pipeline C: planned procurement / market consultation = trigger, not an open call |
| Wikidata SPARQL | Yes (CC0) | Discoverer; candidates only | Existence ≠ demand |
| GitHub Organizations API | Yes (official) | Discoverer; candidates only | Org blog URL only |
| Agency pages / RSS you seed | Yes (robots.txt + SSRF-safe) | Needs seed URLs | Evergreen partner pages = model, not active request |
| Brave Search API | Yes (paid API) | Off until approved | Hits are URLs to read, never evidence |
| Apify SERP Actors | Yes (paid) | Off until approved | Same evidence rules; no duplicate re-submit |
| LinkedIn | No permitted route | **Disabled** | ToS prohibit automated commercial scraping; use Manual intake |
| Reddit | No commercial API arrangement | **Disabled** | Manual intake of posts you may copy |
| Facebook / Meta | No permitted route | **Disabled** | Manual intake only |
| Upwork / Fiverr / etc. | Excluded | **Blocked** | Marketplace exclusion policy |
| Clutch directories | Blocked | **Blocked** | Residential-proxy evasion + unreviewed ToS |

## Manual intake

`POST /intake` and the dashboard **Manual intake** tab accept a URL + verbatim excerpt the operator is permitted to copy. Nothing is fetched. Records are tagged `origin=manual` / provider `manual_intake` and start unverified.
