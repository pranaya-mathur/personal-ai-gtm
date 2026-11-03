import httpx
from sqlalchemy import func, select

from app.connectors.base import RawFinding, SignalKind
from app.core.config import Settings
from app.models import Evidence, Opportunity
from app.models.enums import SourceCheckStatus
from app.services.dedupe import canonical_url, evidence_hash, title_similarity
from app.services.ingest import ingest_finding
from app.services.source_check import SourceChecker

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def checker(page: str, robots: str = "", status: int = 200, resolver=PUBLIC):  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200 if robots else 404, text=robots)
        return httpx.Response(status, text=page)

    return SourceChecker(transport=httpx.MockTransport(handler), resolver=resolver)


def test_source_confirmed_when_title_present_in_visible_text() -> None:
    page = "<html><script>x</script><h1>AI  Knowledge &amp; Assistant</h1></html>"
    c = checker(page)
    assert (
        c.check("https://a.example/n/1", "AI knowledge & assistant\nbody")
        == SourceCheckStatus.CONFIRMED
    )


def test_source_mismatch_unreachable_and_robots() -> None:
    assert (
        checker("<p>other</p>").check("https://a.example/n", "Claimed title")
        == SourceCheckStatus.MISMATCH
    )
    assert (
        checker("x", status=404).check("https://a.example/n", "t") == SourceCheckStatus.UNREACHABLE
    )
    blocked = checker("Claimed title", robots="User-agent: *\nDisallow: /")
    assert blocked.check("https://a.example/n", "Claimed title") == SourceCheckStatus.BLOCKED


def test_ssrf_targets_are_blocked() -> None:
    private = checker("Claimed title", resolver=lambda h: ["127.0.0.1"])
    assert private.check("https://internal.example/n", "Claimed title") == SourceCheckStatus.BLOCKED
    assert checker("x").check("file:///etc/passwd", "x") == SourceCheckStatus.BLOCKED
    meta = checker("x", resolver=lambda h: ["169.254.169.254"])
    assert meta.check("http://metadata.example/", "x") == SourceCheckStatus.BLOCKED


def test_redirect_to_private_host_is_blocked() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://evil.internal/x"})

    c = SourceChecker(
        transport=httpx.MockTransport(handler),
        resolver=lambda h: ["10.0.0.5"] if "internal" in h else ["93.184.216.34"],
    )
    assert c.check("https://a.example/n", "t") == SourceCheckStatus.BLOCKED


def test_canonical_url_and_cross_provider_hash() -> None:
    a = canonical_url("HTTPS://Site.Example/notice/1/?utm_source=x&b=2&a=1#frag")
    assert a == "https://site.example/notice/1?a=1&b=2"
    assert evidence_hash("https://s.example/n/", "Some  text") == evidence_hash(
        "https://s.example/n?utm_campaign=z", "Some text"
    )


def test_title_similarity() -> None:
    assert (
        title_similarity("Build RAG assistant for support", "build a RAG assistant for support")
        > 0.8
    )
    assert title_similarity("RAG assistant", "Road resurfacing tender") < 0.2


def _f(title: str, url: str) -> RawFinding:
    return RawFinding(
        kind=SignalKind.PROJECT_REQUEST,
        title=title,
        source_url=url,
        original_text=title,
        organization_name="Org",
        organization_domain="org.example",
    )


def test_near_duplicate_titles_merge_but_keep_both_evidence(session) -> None:  # type: ignore[no-untyped-def]
    ingest_finding(
        session, _f("Build RAG assistant for support docs", "https://o.example/1"), "p1", None, 90
    )
    out = ingest_finding(
        session, _f("Build a RAG assistant for support docs", "https://o.example/2"), "p2", None, 90
    )
    assert out.created is False
    assert session.execute(select(func.count()).select_from(Opportunity)).scalar_one() == 1
    assert session.execute(select(func.count()).select_from(Evidence)).scalar_one() == 2


def test_distinct_titles_not_merged(session) -> None:  # type: ignore[no-untyped-def]
    ingest_finding(session, _f("Build RAG assistant", "https://o.example/1"), "p", None, 90)
    ingest_finding(
        session, _f("Fraud detection model for payments", "https://o.example/2"), "p", None, 90
    )
    assert session.execute(select(func.count()).select_from(Opportunity)).scalar_one() == 2


def test_secret_not_in_repr() -> None:
    s = Settings(openai_api_key="sk-secret-value", apify_token="apify-secret")  # type: ignore[arg-type]
    assert "sk-secret-value" not in repr(s) and "apify-secret" not in repr(s)
