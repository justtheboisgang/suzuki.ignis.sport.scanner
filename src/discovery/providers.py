"""Pluggable, multi-provider web search.

We use search providers for their RAW web results (title / url / snippet /
rank), never for generated answers. Results feed our own source-discovery
pipeline. Brave AND SerpApi can be enabled simultaneously; a `MultiProvider`
fans a query out to all enabled providers, records per-provider provenance, and
de-duplicates URLs across providers.

Every request is budget-guarded and logged (see budget.py) so search spend stays
bounded and separate from the always-on monitoring of known sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ..config.settings import Settings, get_settings
from ..utils.hashing import domain_of, normalize_url
from ..utils.logging import get_logger
from . import budget

log = get_logger("discovery.provider")


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    domain: str = ""
    rank: int = 0          # 1-based rank within the provider's results
    page: int = 1
    provider: str = ""
    query: str = ""
    country: str | None = None
    language: str | None = None

    def __post_init__(self):
        if not self.domain:
            self.domain = domain_of(self.url)


class SearchProvider:
    name = "base"

    @property
    def available(self) -> bool:
        return False

    def _search_page(self, query: str, page: int, country: str | None
                     ) -> list[SearchHit]:  # pragma: no cover - abstract
        raise NotImplementedError

    def search(self, query: str, pages: int = 1, country: str | None = None,
               language: str | None = None) -> list[SearchHit]:
        """Fetch up to `pages` result pages, honouring the monthly budget."""
        if not self.available:
            return []
        out: list[SearchHit] = []
        for page in range(1, pages + 1):
            if not budget.can_request(self.name, 1):
                log.warning("%s budget exhausted — stopping pagination at page %d",
                            self.name, page)
                break
            try:
                hits = self._search_page(query, page, country)
                budget.record_request(self.name, query, country, page, len(hits),
                                      ok=True)
            except Exception as exc:  # network/API error is non-fatal
                budget.record_request(self.name, query, country, page, 0,
                                      ok=False, error=str(exc)[:300])
                log.warning("%s search error p%d '%s': %s", self.name, page,
                            query[:40], exc)
                break
            for h in hits:
                h.query = query
                h.country = country
                h.language = language
                h.provider = self.name
            out.extend(hits)
            if not hits:
                break  # no point paginating further
        return out


class BraveProvider(SearchProvider):
    """Brave Search API — https://api.search.brave.com (Web Search endpoint)."""

    name = "brave"
    PAGE_SIZE = 20

    def __init__(self, key: str):
        self.key = key

    @property
    def available(self):
        return bool(self.key)

    def _search_page(self, query, page, country):
        headers = {"X-Subscription-Token": self.key, "Accept": "application/json"}
        params = {"q": query, "count": self.PAGE_SIZE,
                  "offset": page - 1}  # Brave offset is in pages of `count`
        if country:
            params["country"] = country.upper()
        r = httpx.get("https://api.search.brave.com/res/v1/web/search",
                      headers=headers, params=params, timeout=25)
        if r.status_code == 429:
            raise RuntimeError("brave rate limited (429)")
        r.raise_for_status()
        data = r.json()
        results = (data.get("web", {}) or {}).get("results", []) or []
        base_rank = (page - 1) * self.PAGE_SIZE
        hits = []
        for i, item in enumerate(results, start=1):
            hits.append(SearchHit(
                title=item.get("title", ""), url=item.get("url", ""),
                snippet=item.get("description", ""), rank=base_rank + i, page=page))
        return hits


class SerpApiProvider(SearchProvider):
    """SerpApi (Google results) — https://serpapi.com/search."""

    name = "serpapi"
    PAGE_SIZE = 20

    def __init__(self, key: str):
        self.key = key

    @property
    def available(self):
        return bool(self.key)

    def _search_page(self, query, page, country):
        params = {"engine": "google", "q": query, "num": self.PAGE_SIZE,
                  "start": (page - 1) * self.PAGE_SIZE, "api_key": self.key}
        if country:
            params["gl"] = country.lower()
        r = httpx.get("https://serpapi.com/search.json", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        results = data.get("organic_results", []) or []
        base_rank = (page - 1) * self.PAGE_SIZE
        hits = []
        for i, item in enumerate(results, start=1):
            hits.append(SearchHit(
                title=item.get("title", ""), url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                rank=item.get("position", base_rank + i), page=page))
        return hits


class GoogleCSEProvider(SearchProvider):
    name = "google_cse"
    PAGE_SIZE = 10

    def __init__(self, cse_id: str, key: str):
        self.cse_id = cse_id
        self.key = key

    @property
    def available(self):
        return bool(self.cse_id and self.key)

    def _search_page(self, query, page, country):
        params = {"key": self.key, "cx": self.cse_id, "q": query,
                  "num": self.PAGE_SIZE, "start": (page - 1) * self.PAGE_SIZE + 1}
        r = httpx.get("https://www.googleapis.com/customsearch/v1", params=params,
                      timeout=25)
        r.raise_for_status()
        data = r.json()
        base_rank = (page - 1) * self.PAGE_SIZE
        return [SearchHit(title=i.get("title", ""), url=i.get("link", ""),
                          snippet=i.get("snippet", ""), rank=base_rank + n, page=page)
                for n, i in enumerate(data.get("items", []), start=1)]


@dataclass
class MultiSearchResult:
    """Deduplicated hits plus per-provider provenance for the same URL."""

    hits: list[SearchHit] = field(default_factory=list)
    # url -> set of providers that returned it
    providers_by_url: dict = field(default_factory=dict)
    per_provider_counts: dict = field(default_factory=dict)


class MultiProvider:
    """Fans a query out to all enabled providers and de-duplicates by URL,
    remembering which providers surfaced each URL (for the comparison report)."""

    def __init__(self, providers: list[SearchProvider]):
        self.providers = [p for p in providers if p.available]

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.providers]

    @property
    def available(self) -> bool:
        return bool(self.providers)

    def search(self, query: str, pages: int = 1, country: str | None = None,
               language: str | None = None) -> MultiSearchResult:
        result = MultiSearchResult()
        seen: dict[str, SearchHit] = {}
        for provider in self.providers:
            hits = provider.search(query, pages=pages, country=country,
                                   language=language)
            result.per_provider_counts[provider.name] = \
                result.per_provider_counts.get(provider.name, 0) + len(hits)
            for h in hits:
                key = normalize_url(h.url)
                result.providers_by_url.setdefault(key, set()).add(provider.name)
                if key not in seen:
                    seen[key] = h  # keep first (best-ranked) occurrence
        result.hits = list(seen.values())
        return result


def build_providers(settings: Settings | None = None) -> list[SearchProvider]:
    s = settings or get_settings()
    providers: list[SearchProvider] = []
    enabled = s.enabled_providers()
    if "brave" in enabled:
        providers.append(BraveProvider(s.brave_key))
    if "serpapi" in enabled:
        providers.append(SerpApiProvider(s.serpapi_key_resolved))
    if "google_cse" in enabled:
        providers.append(GoogleCSEProvider(s.google_cse_id, s.google_cse_key))
    return providers


def get_multi_provider(settings: Settings | None = None) -> MultiProvider:
    return MultiProvider(build_providers(settings))


# Backward-compatible single-provider accessor (used by older code/tests).
def get_search_provider(settings: Settings | None = None) -> SearchProvider:
    providers = build_providers(settings)
    if providers:
        return providers[0]

    class _Null(SearchProvider):
        name = "none"

        @property
        def available(self):
            return True

        def _search_page(self, query, page, country):
            return []

        def search(self, query, pages=1, count=20, country=None, language=None):
            return []

    return _Null()
