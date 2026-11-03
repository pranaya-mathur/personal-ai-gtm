from collections.abc import Callable

from app.connectors.base import DiscoveryConnector
from app.connectors.mock import MockAgencyConnector, MockOpportunityConnector

_FACTORIES: dict[str, Callable[[], DiscoveryConnector]] = {
    "mock_opportunities": MockOpportunityConnector,
    "mock_agencies": MockAgencyConnector,
}


def register_connector(name: str, factory: Callable[[], DiscoveryConnector]) -> None:
    """Add a provider (e.g. an Apify-backed connector) without touching business logic."""
    _FACTORIES[name] = factory


def build_connectors(names: list[str]) -> list[DiscoveryConnector]:
    unknown = [n for n in names if n not in _FACTORIES]
    if unknown:
        raise ValueError(f"Unknown connectors configured: {unknown}. Known: {sorted(_FACTORIES)}")
    return [_FACTORIES[n]() for n in names]
