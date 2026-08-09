"""Dedicated Marktplaats (marktplaats.nl / 2dehands.be) adapter.

Marktplaats search/browse pages ("/l/…#q:…") list many result cards; each card
links to an individual ad at "/v/…". The generic extractor already treats "/v/"
as a detail URL, but Marktplaats is important and JS-heavy enough to warrant an
explicit, isolated per-card parser so we never store the search page itself or
leak neighbouring cards' text.

Returns None for non-Marktplaats pages (so the generic extractor runs instead).
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .urltype import EXACT_DETAIL, url_quality

_MARKTPLAATS_DOMAINS = ("marktplaats.nl", "2dehands.be", "2ememain.be")


def _is_marktplaats(domain: str) -> bool:
    return any(d in (domain or "") for d in _MARKTPLAATS_DOMAINS)


def maybe_extract_marktplaats(html: str, base_url: str, source_domain: str,
                              country: str | None):
    if not _is_marktplaats(source_domain):
        return None
    from .html_generic import RawListing, _enrich_from_text, DEALER_INVENTORY, \
        SEARCH_RESULTS, INDIVIDUAL_LISTING

    soup = BeautifulSoup(html or "", "lxml")
    # Individual ad page: URL is /v/… → let the generic individual path handle
    # it (return None so the generic extractor builds one clean candidate).
    if "/v/" in base_url and url_quality(base_url) == EXACT_DETAIL:
        return None

    out: list[RawListing] = []
    seen: set[str] = set()
    # Each ad links to /v/… ; iterate those anchors and isolate their card.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/v/" not in href:
            continue
        abs_url = urljoin(base_url, href)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        # Title: the card heading, else the anchor text (NOT the page title).
        card = a.find_parent(["li", "article", "div"]) or a
        heading = card.find(["h1", "h2", "h3"])
        title = ""
        if heading:
            title = " ".join(heading.get_text(" ", strip=True).split())
        if len(title) < 3:
            title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 3:
            continue
        # Card-local text only (this card's own container).
        card_text = " ".join(card.get_text(" ", strip=True).split())[:500]
        img = card.find("img")
        images = []
        if img:
            src = img.get("src") or img.get("data-src")
            if src:
                images = [urljoin(base_url, src)]
        rl = RawListing(
            title=title[:200], url=abs_url, source_domain=source_domain,
            raw_text=card_text, images=images, country=country,
            extraction_method="marktplaats_card", listing_url_quality=EXACT_DETAIL,
            card_href_found=True, page_type=SEARCH_RESULTS,
            discovered_from_url=base_url)
        _enrich_from_text(rl)
        out.append(rl)

    # If it's clearly a Marktplaats results page but we found no cards, return an
    # empty list (PARSER_UNRESOLVED) rather than falling back to a page-listing.
    if not out and ("/l/" in base_url or "#q" in base_url):
        return []
    return out or None
