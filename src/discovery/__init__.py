"""Source Discovery Engine — continuously finds NEW places an Ignis Sport might
be sold. Not a static portal list."""

from .queries import generate_queries, GeneratedQuery
from .providers import get_search_provider, SearchProvider, SearchHit
from .seed_sources import SEED_SOURCES, seed_sources_into_db
from .engine import DiscoveryEngine

__all__ = [
    "generate_queries",
    "GeneratedQuery",
    "get_search_provider",
    "SearchProvider",
    "SearchHit",
    "SEED_SOURCES",
    "seed_sources_into_db",
    "DiscoveryEngine",
]
