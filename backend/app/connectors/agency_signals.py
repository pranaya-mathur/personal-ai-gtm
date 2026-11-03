"""Is a 'seeking help' post written by an agency (channel A) or a direct buyer (channel B)?
Deterministic wording check only; the organisation is never assumed from the sender."""

import re

_AGENCY = re.compile(
    r"\b(we(?:'re| are) an? (?:\w+ ){0,3}(?:agency|consultancy|studio|software house|dev shop)|"
    r"our (?:agency|consultancy|studio)|white-?label|for our clients|our client projects|"
    r"overflow work|sub-?contract\w*)\b",
    re.I,
)


def agency_post_class(text: str) -> bool:
    return bool(_AGENCY.search(text))
