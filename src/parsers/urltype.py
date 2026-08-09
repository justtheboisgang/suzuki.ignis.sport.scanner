"""URL classification — is a URL a concrete vehicle detail page, a search-results
page, a dealer-inventory page or a homepage?

Neutral module (no project imports beyond stdlib) so both the HTML extractor and
the discovery classifier can use it without an import cycle.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

# URL-quality buckets.
EXACT_DETAIL = "EXACT_DETAIL"
LIKELY_DETAIL = "LIKELY_DETAIL"
SEARCH_PAGE = "SEARCH_PAGE"
HOMEPAGE = "HOMEPAGE"
UNKNOWN = "UNKNOWN"

# Query keys that indicate a search / results page.
_SEARCH_QUERY_KEYS = ("q", "query", "search", "keywords", "zoekwoord", "text",
                      "searchterm", "suchbegriff", "k")
# Path fragments that indicate search / browse / inventory *root* (not a car).
_SEARCH_PATH = ("/search", "/zoeken", "/suche", "/suchen", "/recherche",
                "/ricerca", "/buscar", "/q/", "/l/", "/lst", "/results",
                "/resultaten", "/anzeigen", "/inventory", "/aanbod",
                "/gebrauchtwagen$", "/fahrzeuge$", "/occasions$", "/stock$",
                "/cars$", "/autos$")
# Path fragments that indicate an individual listing.
_DETAIL_SEGMENTS = ("/v/", "/listing/", "/inserat", "/angebot/", "/annonce",
                    "/annuncio", "/anuncio", "/vehicle/", "/fahrzeug/", "/ad/",
                    "/item/", "/a/", "/detail", "/oferta", "/occasion/",
                    "/used-car", "/voiture/", "/coche/", "/auto/", "/pkw/",
                    "/gebrauchtwagen/", "/fahrzeuge/")


def _norm_path(url: str):
    try:
        p = urlparse(url if "//" in url else f"https://{url}")
    except ValueError:
        return None
    return p


def is_homepage_url(url: str | None) -> bool:
    p = _norm_path(url or "")
    if p is None:
        return False
    return not (p.path or "").strip("/") and not p.query


def is_search_url(url: str | None) -> bool:
    return url_quality(url) == SEARCH_PAGE


def looks_like_detail_url(url: str | None) -> bool:
    return url_quality(url) in (EXACT_DETAIL, LIKELY_DETAIL)


def url_quality(url: str | None) -> str:
    """Classify a URL's specificity. Precision-focused: anything that looks like
    a search/results/inventory-root page is SEARCH_PAGE, never a detail link."""
    if not url:
        return UNKNOWN
    p = _norm_path(url)
    if p is None:
        return UNKNOWN
    path = (p.path or "").rstrip("/")
    frag = (p.fragment or "").lower()
    query = parse_qs(p.query)

    if not path and not p.query:
        return HOMEPAGE

    # Search markers in query / fragment (e.g. Marktplaats "#q:ignis+sport").
    if any(k.lower() in {kk.lower() for kk in query} for k in _SEARCH_QUERY_KEYS):
        return SEARCH_PAGE
    if frag.startswith("q:") or "#q:" in (url.lower()):
        return SEARCH_PAGE

    plow = "/" + path.lower().strip("/")
    for marker in _SEARCH_PATH:
        if marker.endswith("$"):
            if plow == marker[:-1]:
                return SEARCH_PAGE
        elif marker in plow + "/":
            # /l/ , /q/ , /search etc.
            return SEARCH_PAGE

    # Individual-listing signals.
    if any(seg in plow + "/" for seg in _DETAIL_SEGMENTS):
        # But a bare inventory root like "/fahrzeuge" is handled above; here we
        # have "/fahrzeuge/<something>".
        if re.search(r"/(fahrzeuge|gebrauchtwagen|auto)/$", plow + "/"):
            return SEARCH_PAGE
        return EXACT_DETAIL
    if re.search(r"\d{5,}", path):
        return EXACT_DETAIL
    # A slug with several segments is probably a detail page.
    segs = [s for s in path.split("/") if s]
    if len(segs) >= 2 and any("-" in s or len(s) > 12 for s in segs[-1:]):
        return LIKELY_DETAIL
    if len(segs) >= 3:
        return LIKELY_DETAIL
    return UNKNOWN
