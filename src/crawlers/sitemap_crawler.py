"""Sitemap discovery. A cheap way to find deep, badly-linked vehicle pages on
small dealer sites — exactly the long-tail we care about. We read
/robots.txt Sitemap: directives and /sitemap.xml, following sitemap indexes."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..utils.logging import get_logger
from .base import HttpFetcher

log = get_logger("crawler.sitemap")

_INTEREST = re.compile(r"(ignis|suzuki|ht81s|fahrzeug|vehicle|voiture|auto|occasion|gebraucht)", re.I)


def _sitemap_candidates(fetcher: HttpFetcher, base_url: str) -> list[str]:
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [urljoin(root, "/sitemap.xml"), urljoin(root, "/sitemap_index.xml")]
    # robots.txt Sitemap: lines
    robots = fetcher.fetch(urljoin(root, "/robots.txt"))
    if robots.ok:
        for line in robots.text.splitlines():
            if line.lower().startswith("sitemap:"):
                candidates.append(line.split(":", 1)[1].strip())
    # de-dup, keep order
    seen: set[str] = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]


def _extract_locs(xml: str) -> list[str]:
    soup = BeautifulSoup(xml, "xml")
    return [loc.get_text(strip=True) for loc in soup.find_all("loc")]


def discover_sitemap_urls(fetcher: HttpFetcher, base_url: str,
                          limit: int = 200, follow_index: bool = True) -> list[str]:
    """Return interesting URLs found via sitemaps (filtered to vehicle-ish paths
    when possible, otherwise the first `limit` URLs)."""
    found: list[str] = []
    for sm in _sitemap_candidates(fetcher, base_url):
        res = fetcher.fetch(sm)
        if not res.ok or "<" not in res.text:
            continue
        locs = _extract_locs(res.text)
        # Sitemap index → recurse one level.
        if follow_index and locs and all(l.endswith(".xml") for l in locs[:3]):
            for sub in locs[:10]:
                sub_res = fetcher.fetch(sub)
                if sub_res.ok:
                    found.extend(_extract_locs(sub_res.text))
        else:
            found.extend(locs)
        if len(found) >= limit * 3:
            break

    interesting = [u for u in found if _INTEREST.search(u)]
    result = interesting or found
    # de-dup preserve order
    seen: set[str] = set()
    deduped = [u for u in result if not (u in seen or seen.add(u))]
    log.info("sitemap %s -> %d urls (%d interesting)", base_url, len(deduped),
             len(interesting))
    return deduped[:limit]
