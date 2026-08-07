"""Crawler framework. Preference order per source: API → RSS → XML → JSON feed
→ JSON-LD → HTML → (optional) browser automation. Everything here respects
robots.txt, rate limits and never circumvents access protection."""

from .base import CrawlContext, FetchResult, HttpFetcher, RobotsCache
from .http_crawler import crawl_source
from .feed_crawler import crawl_feed
from .sitemap_crawler import discover_sitemap_urls

__all__ = [
    "CrawlContext",
    "FetchResult",
    "HttpFetcher",
    "RobotsCache",
    "crawl_source",
    "crawl_feed",
    "discover_sitemap_urls",
]
