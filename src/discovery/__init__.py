"""Source Discovery Engine — continuously finds NEW places an Ignis Sport might
be sold. Not a static portal list."""

from .queries import generate_queries, GeneratedQuery, BIG_PLATFORMS
from .providers import (
    get_search_provider,
    get_multi_provider,
    build_providers,
    MultiProvider,
    SearchProvider,
    SearchHit,
)
from .seed_sources import SEED_SOURCES, seed_sources_into_db
from .engine import DiscoveryEngine, compute_source_discovery_value, is_aggregator_domain
from . import budget

__all__ = [
    "generate_queries",
    "GeneratedQuery",
    "BIG_PLATFORMS",
    "get_search_provider",
    "get_multi_provider",
    "build_providers",
    "MultiProvider",
    "SearchProvider",
    "SearchHit",
    "SEED_SOURCES",
    "seed_sources_into_db",
    "DiscoveryEngine",
    "compute_source_discovery_value",
    "is_aggregator_domain",
    "budget",
]
