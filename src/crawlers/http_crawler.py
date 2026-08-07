"""Per-source crawl orchestration.

Given a `Source`, choose the cheapest viable strategy and return candidate
`RawListing`s plus how many pages were touched:

    feed  →  search_url / listing page HTML  →  sitemap deep-crawl

Browser automation (Playwright) is intentionally out of the hot path; sources
that truly require JS are flagged `requires_browser` and can be handled by an
optional browser worker (see docs). We never bypass protection: robots and
rate limits are enforced by the fetcher.
"""

from __future__ import annotations

from ..models.source import Source
from ..parsers.html_generic import RawListing, extract_listings_from_html
from ..utils.hashing import domain_of
from ..utils.logging import get_logger
from .base import CrawlContext
from .feed_crawler import crawl_feed
from .sitemap_crawler import discover_sitemap_urls

log = get_logger("crawler.http")


def crawl_source(ctx: CrawlContext, source: Source) -> tuple[list[RawListing], int]:
    """Return (candidate_listings, pages_checked_delta)."""
    listings: list[RawListing] = []
    pages = 0
    domain = source.domain
    country = source.country

    # 1) Feed sources (parser_type == 'feed' or a search_url ending in xml/rss).
    if source.parser_type == "feed" or (source.search_url and
                                        source.search_url.rstrip("/").endswith(
                                            (".xml", ".rss", "/feed", "/rss"))):
        listings += crawl_feed(ctx.fetcher, source.search_url or source.base_url, country)
        pages += 1
        if listings:
            return listings, pages

    # 2) Direct listing/search page HTML.
    target_url = source.search_url or source.base_url
    if target_url:
        res = ctx.fetcher.fetch(target_url)
        pages += 1
        if res.blocked_by_robots:
            ctx.errors.append(f"{domain}: blocked by robots.txt")
        elif res.from_cache:
            log.info("%s unchanged (304)", domain)
        elif res.ok:
            listings += extract_listings_from_html(res.text, res.url, domain, country)
        else:
            ctx.errors.append(f"{domain}: {res.error}")

    # 3) Sitemap deep-crawl for small/long-tail dealer sites when the search
    #    page yielded little and we're allowed more pages.
    remaining = ctx.max_pages - pages
    if (source.source_type in {"dealer", "suzuki_dealer", "garage",
                               "youngtimer_dealer", "enthusiast_dealer",
                               "private_site"}
            and len(listings) < 3 and remaining > 1 and source.base_url):
        urls = discover_sitemap_urls(ctx.fetcher, source.base_url,
                                     limit=min(remaining, 30))
        for u in urls[: min(remaining, ctx.max_pages)]:
            res = ctx.fetcher.fetch(u)
            pages += 1
            if res.ok:
                listings += extract_listings_from_html(res.text, res.url, domain, country)
            if pages >= ctx.max_pages:
                break

    log.info("source %s -> %d candidates over %d pages", domain, len(listings), pages)
    return listings, pages
