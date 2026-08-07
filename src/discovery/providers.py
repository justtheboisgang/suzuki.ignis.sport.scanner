"""Pluggable search providers.

We prefer official/permitted APIs over scraping search engines. Providers are
selected by the SEARCH_PROVIDER env var. When set to "none" (the default, no
credentials required), discovery still works via seed sources + sitemap crawling
— the search layer just returns nothing rather than breaking.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config.settings import Settings, get_settings
from ..utils.logging import get_logger

log = get_logger("discovery.provider")


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""


class SearchProvider:
    name = "none"

    def search(self, query: str, count: int = 20, country: str | None = None
               ) -> list[SearchHit]:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        return True


class NullProvider(SearchProvider):
    """No external search configured. Returns nothing (system still works via
    seeds + sitemaps). This keeps the pipeline credential-free by default."""

    name = "none"

    def search(self, query, count=20, country=None):
        return []


class SerpApiProvider(SearchProvider):
    name = "serpapi"

    def __init__(self, key: str):
        self.key = key

    @property
    def available(self):
        return bool(self.key)

    def search(self, query, count=20, country=None):
        if not self.key:
            return []
        params = {"engine": "google", "q": query, "num": min(count, 100),
                  "api_key": self.key}
        if country:
            params["gl"] = country.lower()
        try:
            r = httpx.get("https://serpapi.com/search.json", params=params, timeout=25)
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("serpapi error: %s", exc)
            return []
        hits = []
        for item in data.get("organic_results", []):
            hits.append(SearchHit(item.get("title", ""), item.get("link", ""),
                                  item.get("snippet", "")))
        return hits


class BraveProvider(SearchProvider):
    name = "brave"

    def __init__(self, key: str):
        self.key = key

    @property
    def available(self):
        return bool(self.key)

    def search(self, query, count=20, country=None):
        if not self.key:
            return []
        headers = {"X-Subscription-Token": self.key, "Accept": "application/json"}
        params = {"q": query, "count": min(count, 20)}
        if country:
            params["country"] = country.upper()
        try:
            r = httpx.get("https://api.search.brave.com/res/v1/web/search",
                          headers=headers, params=params, timeout=25)
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("brave error: %s", exc)
            return []
        hits = []
        for item in (data.get("web", {}) or {}).get("results", []):
            hits.append(SearchHit(item.get("title", ""), item.get("url", ""),
                                  item.get("description", "")))
        return hits


class GoogleCSEProvider(SearchProvider):
    name = "google_cse"

    def __init__(self, cse_id: str, key: str):
        self.cse_id = cse_id
        self.key = key

    @property
    def available(self):
        return bool(self.cse_id and self.key)

    def search(self, query, count=20, country=None):
        if not self.available:
            return []
        params = {"key": self.key, "cx": self.cse_id, "q": query,
                  "num": min(count, 10)}
        try:
            r = httpx.get("https://www.googleapis.com/customsearch/v1",
                          params=params, timeout=25)
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("google cse error: %s", exc)
            return []
        return [SearchHit(i.get("title", ""), i.get("link", ""), i.get("snippet", ""))
                for i in data.get("items", [])]


def get_search_provider(settings: Settings | None = None) -> SearchProvider:
    s = settings or get_settings()
    provider = (s.search_provider or "none").lower()
    if provider == "serpapi" and s.serpapi_key:
        return SerpApiProvider(s.serpapi_key)
    if provider == "brave" and s.brave_api_key:
        return BraveProvider(s.brave_api_key)
    if provider == "google_cse" and s.google_cse_id and s.google_cse_key:
        return GoogleCSEProvider(s.google_cse_id, s.google_cse_key)
    if provider != "none":
        log.info("SEARCH_PROVIDER=%s but no credentials — falling back to none",
                 provider)
    return NullProvider()
