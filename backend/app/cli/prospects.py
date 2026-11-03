"""Report-only review of discovered agency candidates (reads each site's homepage and contact
page once, robots.txt respected). It RANKS potential partners by complementary services and
published contact route. It never creates records and never claims demand: a ranked prospect
is "potential, no evidence of external-delivery demand" unless a page states a model/request.

    python -m app.cli.prospects [--limit 60]
"""

import argparse
import re
from dataclasses import dataclass, field

from sqlalchemy import select

from app.connectors.agencies import CONTACT_PATHS
from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models import SourceCandidate
from app.models.enums import SourceCheckStatus
from app.services.partnership import classify_partner_text, page_title, visible_text
from app.services.routes import routes_from_html
from app.services.source_check import SourceChecker
from app.services.triage import find_terms

COMPLEMENT = {
    "web/app development": r"\b(web|website|mobile|app|software|full-?stack|custom software)\b.{0,25}\b(development|developers?|engineering)\b",
    "enterprise/ERP/CRM": r"\b(ERP|CRM|SAP|Salesforce|Odoo|Dynamics|enterprise software|system integrat\w+)\b",
    "cloud/DevOps": r"\b(cloud|AWS|Azure|DevOps|infrastructure|kubernetes)\b",
    "data/analytics": r"\b(data engineering|analytics|business intelligence|data warehouse|BI)\b",
    "digital transformation": r"\b(digital transformation|IT consulting|IT services|managed services)\b",
    "product/UX design": r"\b(UX|UI design|product design|branding|digital agency)\b",
}
OFFERS_WHITELABEL = re.compile(
    r"white-?label|reseller|outsourc\w+ (?:partner|team)|dedicated (?:team|developers)", re.I
)


@dataclass
class Prospect:
    domain: str
    name: str | None
    country: str | None
    origin: str
    services: list[str] = field(default_factory=list)
    ai_depth: str = "none"
    model_statement: str | None = None
    vendor_of_whitelabel: bool = False
    emails: list[str] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)
    reached: bool = False

    @property
    def score(self) -> int:
        s = 3 * len(self.services) + (4 if self.emails else 0) + (2 if self.forms else 0)
        s += 1 if self.ai_depth == "light" else 0  # complements AI delivery; heavy AI = competitor
        s -= 5 if self.ai_depth == "heavy" else 0
        s -= 6 if self.vendor_of_whitelabel else 0  # sells capacity itself: competitor, not buyer
        s += 10 if self.model_statement else 0
        return s if self.reached else -99


def review(limit: int = 60) -> list[Prospect]:
    s = get_settings()
    checker = SourceChecker()
    out: list[Prospect] = []
    with get_session_factory()() as db:
        cands = (
            db.execute(select(SourceCandidate).order_by(SourceCandidate.created_at).limit(limit))
            .scalars()
            .all()
        )
    for c in cands:
        p = Prospect(c.domain, c.name, c.country, c.origin)
        status, body = checker.fetch_page(f"https://{c.domain}/")
        if status is not SourceCheckStatus.CONFIRMED:
            out.append(p)
            continue
        p.reached = True
        text = visible_text(body)
        p.services = [k for k, rx in COMPLEMENT.items() if re.search(rx, text, re.I)]
        ai_hits = len(find_terms(text, s.topic_term_list))
        p.ai_depth = "heavy" if ai_hits >= 4 else "light" if ai_hits else "none"
        p.vendor_of_whitelabel = bool(OFFERS_WHITELABEL.search(text))
        sig = classify_partner_text(text, s.topic_term_list)
        if sig is not None:
            p.model_statement = sig.sentences[0][:200]
        routes = routes_from_html(body, f"https://{c.domain}/", text)
        for path in CONTACT_PATHS:
            st, b2 = checker.fetch_page(f"https://{c.domain}{path}")
            if st is SourceCheckStatus.CONFIRMED:
                routes += routes_from_html(b2, f"https://{c.domain}{path}", visible_text(b2))
                break
        p.emails = sorted({r.value for r in routes if r.kind == "email"})[:2]
        p.forms = sorted({r.value for r in routes if r.kind == "inquiry_form"})[:1]
        p.name = p.name or page_title(body)
        out.append(p)
    return sorted(out, key=lambda x: -x.score)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()
    ps = review(args.limit)
    reached = [p for p in ps if p.reached]
    print(
        f"candidates reviewed: {len(ps)} | reachable: {len(reached)} | stating a sourcing model: {sum(bool(p.model_statement) for p in ps)}"
    )
    for p in ps[:15]:
        if p.score < 0:
            continue
        print(
            f"{p.score:>3} {p.domain} ({p.country or '?'}, {p.origin}) services={p.services} ai={p.ai_depth} "
            f"whitelabel_vendor={p.vendor_of_whitelabel} email={p.emails} form={bool(p.forms)}"
            + (f"\n     MODEL: {p.model_statement}" if p.model_statement else "")
        )


if __name__ == "__main__":
    main()
