"""Source-backed claim enforcement. Nothing AI-generated is accepted on trust."""

import re

from pydantic import BaseModel

from app.ai.schemas import (
    Claim,
    EvidenceEvaluation,
    OpportunityDossier,
    OutreachDraftAI,
    QualificationAssessment,
)

MIN_QUOTE_CHARS = 8
_URL = re.compile(r"https?://[^\s)>\]\"']+")
_MONEY = re.compile(r"[$€£]\s?\d[\d,.]*(?:\s?[kKmM])?|\d[\d,.]*\s?(?:USD|EUR|GBP)\b")


_STOP = frozenset(
    "that this with from have been will they their there which about into than then also such "
    "source states stated says said what when where while would could should shall these those "
    "being does done only some more most other over under both each very just your ours".split()
)
_NUM = re.compile(r"\d[\d.,]*\d|\d")
_NEG = re.compile(
    r"\b(not|no|never|without|cannot|can't|won't|neither|nor|no longer|prohibited|excluded)\b", re.I
)
_STRONG = re.compile(r"\b(must|required|requires?|mandatory|shall|obligatory|only)\b", re.I)
_WEAK = re.compile(r"\b(may|might|optional|could|possibly|desirable|desired|nice to have)\b", re.I)


def _stem(w: str) -> str:
    for suf in ("ations", "ation", "ing", "ies", "ed", "es", "s", "ly"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _stems(text: str) -> set[str]:
    return {
        _stem(w) for w in norm(text).split() if len(w) >= 4 and w not in _STOP and not w.isdigit()
    }


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "").rstrip(".") for m in _NUM.findall(text)}


SUPPORTED, AMBIGUOUS, UNSUPPORTED = "supported", "ambiguous", "unsupported"


def claim_support(claim_text: str, quote: str) -> tuple[str, str]:
    """Deterministic check that the QUOTE actually backs the CLAIM (not merely that it exists).

    unsupported: the claim states a number the quote lacks, or shares almost no content with it.
    ambiguous:   reversed polarity (negation), stronger obligation than the quote, or thin overlap.
    A paraphrase that keeps the quote's key terms and numbers is supported."""
    missing = sorted(n for n in _numbers(claim_text) - _numbers(quote) if len(n) > 1 or n.isdigit())
    if missing:
        return UNSUPPORTED, f"claim states {missing[0]} which the quote does not contain"
    claim_stems, quote_stems = _stems(claim_text), _stems(quote)
    coverage = len(claim_stems & quote_stems) / len(claim_stems) if claim_stems else 1.0
    if claim_stems and coverage < 0.15:
        return UNSUPPORTED, f"quote shares almost nothing with the claim (overlap {coverage:.0%})"
    reasons: list[str] = []
    if bool(_NEG.search(claim_text)) != bool(_NEG.search(quote)):
        reasons.append("negation differs between claim and quote")
    if _STRONG.search(claim_text) and not _STRONG.search(quote) and _WEAK.search(quote):
        reasons.append("claim states an obligation the quote only makes optional")
    if coverage < 0.4:
        reasons.append(f"thin overlap between claim and quote ({coverage:.0%})")
    if reasons:
        return AMBIGUOUS, "; ".join(reasons)
    return SUPPORTED, ""


def support_report(output: object, evidence: dict[str, str]) -> list[tuple[Claim, str, str]]:
    """(claim, label, reason) for every claim whose evidence exists."""
    out: list[tuple[Claim, str, str]] = []
    for c in collect_claims(output):
        if c.evidence_id in evidence and len(norm(c.quote)) >= MIN_QUOTE_CHARS:
            label, why = claim_support(c.text, c.quote)
            out.append((c, label, why))
    return out


class ClaimValidationError(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w$€£%.@:/-]+", " ", text.lower()).split())


def collect_claims(obj: object) -> list[Claim]:
    if isinstance(obj, Claim):
        return [obj]
    found: list[Claim] = []
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            found += collect_claims(getattr(obj, name))
    elif isinstance(obj, list):
        for item in obj:
            found += collect_claims(item)
    return found


def validate_output(
    output: BaseModel,
    evidence: dict[str, str],
    min_claims: int = 0,
    evidence_urls: set[str] | None = None,
) -> list[str]:
    """Raises ClaimValidationError listing every problem. `evidence` maps id -> original text.
    Returns review notes for claims whose quote exists but only ambiguously supports them."""
    problems: list[str] = []
    review: list[str] = []
    claims = collect_claims(output)
    if len(claims) < min_claims:
        problems.append(f"needs at least {min_claims} source-backed claim(s), got {len(claims)}")
    normalized = {k: norm(v) for k, v in evidence.items()}
    for c in claims:
        if not c.text.strip():
            problems.append("claim with empty text")
        if c.evidence_id not in normalized:
            problems.append(f"unknown evidence_id {c.evidence_id!r}")
            continue
        q = norm(c.quote)
        if len(q) < MIN_QUOTE_CHARS:
            problems.append(f"quote too short to support claim {c.text[:40]!r}")
        elif q not in normalized[c.evidence_id]:
            problems.append(f"quote not found verbatim in evidence for claim {c.text[:40]!r}")
        else:
            label, why = claim_support(c.text, c.quote)
            if label == UNSUPPORTED:
                problems.append(f"quote does not support claim {c.text[:40]!r}: {why}")
            elif label == AMBIGUOUS:
                review.append(f"{c.text[:60]!r}: {why}")

    if isinstance(output, EvidenceEvaluation) and not 0 <= output.reliability <= 1:
        problems.append("reliability outside 0..1")
    if isinstance(output, OpportunityDossier):
        if not 0 <= output.reliability <= 1:
            problems.append("reliability outside 0..1")
        if not 0 <= output.fit_score <= 100:
            problems.append("fit_score outside 0..100")
        if output.ai_project == "explicit" and output.ai_project_support is None:
            problems.append("ai_project 'explicit' requires ai_project_support quote")
    if isinstance(output, QualificationAssessment):
        if not 0 <= output.fit_score <= 100:
            problems.append("fit_score outside 0..100")
        problems += [
            f"criterion {c.name!r} score outside 0..100"
            for c in output.criteria
            if not 0 <= c.score <= 100
        ]
    if isinstance(output, OutreachDraftAI):
        corpus = " ".join(evidence.values())
        allowed = {u.rstrip("/").lower() for u in (evidence_urls or set())}
        for url in _URL.findall(output.body):
            if url.rstrip("/.,").lower() not in allowed:
                problems.append(f"body contains a URL not present in evidence: {url[:60]}")
        corpus_n = norm(corpus)
        for money in _MONEY.findall(output.body):
            if norm(money) not in corpus_n:
                problems.append(f"body states an amount not present in evidence: {money!r}")
    if problems:
        raise ClaimValidationError(problems)
    return review
