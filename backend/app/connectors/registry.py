from collections.abc import Callable

from app.connectors.agencies import (
    AnnouncementFeedConnector,
    PublicPartnerPageConnector,
    WikidataAgencyConnector,
)
from app.connectors.apify import ApifyActorConnector
from app.connectors.apify_specs import SPECS
from app.connectors.base import DiscoveryConnector
from app.connectors.mock import MockAgencyConnector, MockOpportunityConnector
from app.core.config import Settings, get_settings

Factory = Callable[[Settings], DiscoveryConnector]

_FACTORIES: dict[str, Factory] = {
    "mock_opportunities": lambda s: MockOpportunityConnector(),
    "mock_agencies": lambda s: MockAgencyConnector(),
    "public_partner_pages": lambda s: PublicPartnerPageConnector(s),
    "public_announcement_feeds": lambda s: AnnouncementFeedConnector(s),
    "wikidata_agencies": lambda s: WikidataAgencyConnector(s),
}
for _name, _spec in SPECS.items():
    _FACTORIES[_name] = lambda s, _spec=_spec: ApifyActorConnector(_spec, s)  # type: ignore[misc]


def register_connector(name: str, factory: Factory) -> None:
    """Add a provider without touching business logic."""
    _FACTORIES[name] = factory


def known_connectors() -> list[str]:
    return sorted(_FACTORIES)


def build_connectors(
    names: list[str], settings: Settings | None = None
) -> list[DiscoveryConnector]:
    settings = settings or get_settings()
    unknown = [n for n in names if n not in _FACTORIES]
    if unknown:
        raise ValueError(f"Unknown connectors configured: {unknown}. Known: {known_connectors()}")
    return [_FACTORIES[n](settings) for n in names]
