"""Card-isolated HTML extraction.

Hard rule (fixes the "Suzuki Bus search page stored as a listing" bug): a
search-/inventory page is NEVER turned into a single vehicle listing. Instead
every result CARD is extracted independently, each with:
  * its own title (from the card, never the page <title> or the search query),
  * its own EXACT detail href (absolute), and
  * its own price/mileage/year/text — no other card's text leaks in.

If the page is a search/inventory page but no per-card detail links can be
found, we return ZERO candidates (PARSER_UNRESOLVED) rather than fabricating
listings. Precision over recall on unstructured pages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .jsonld import extract_jsonld_vehicles
from .normalize import (
    parse_displacement,
    parse_mileage,
    parse_power,
    parse_price,
    parse_year,
)
from .urltype import (
    EXACT_DETAIL,
    HOMEPAGE,
    LIKELY_DETAIL,
    SEARCH_PAGE,
    UNKNOWN,
    url_quality,
)
from ..utils.hashing import normalize_url

# Page-type constants.
INDIVIDUAL_LISTING = "INDIVIDUAL_LISTING"
SEARCH_RESULTS = "SEARCH_RESULTS"
DEALER_INVENTORY = "DEALER_INVENTORY"
PAGE_HOMEPAGE = "HOMEPAGE"
ARTICLE = "ARTICLE"
OTHER = "OTHER"

_ARTICLE_MARKERS = ("article:published_time", "datepublished", "/news/", "/blog/")


@dataclass
class RawListing:
    """One candidate vehicle, isolated to a single result card / detail page."""

    title: str
    url: str
    source_domain: str
    description: str | None = None
    price: float | None = None
    currency: str | None = None
    mileage_km: int | None = None
    year: int | None = None
    power_kw: int | None = None
    power_hp: int | None = None
    displacement_cc: int | None = None
    vin: str | None = None
    color: str | None = None
    fuel: str | None = None
    transmission: str | None = None
    images: list[str] = field(default_factory=list)
    country: str | None = None
    extraction_method: str = "html_generic"
    raw_text: str = ""
    # Provenance / quality of THIS candidate's detail link.
    listing_url_quality: str = UNKNOWN
    card_href_found: bool = False
    page_type: str = OTHER
    discovered_from_url: str | None = None

    def combined_text(self) -> str:
        return " ".join(filter(None, [self.title, self.description, self.raw_text]))


def _enrich_from_text(rl: RawListing) -> None:
    """Fill missing numeric fields from THIS card's own text only."""
    text = rl.combined_text()
    if rl.price is None:
        rl.price, cur = parse_price(text, rl.currency)
        rl.currency = rl.currency or cur
    if rl.mileage_km is None:
        rl.mileage_km = parse_mileage(text)
    if rl.year is None:
        rl.year = parse_year(text)
    if rl.power_kw is None and rl.power_hp is None:
        rl.power_kw, rl.power_hp = parse_power(text)
    if rl.displacement_cc is None:
        rl.displacement_cc = parse_displacement(text)


# --------------------------------------------------------------------------- #
def _meta_url(soup: BeautifulSoup, base_url: str) -> str | None:
    link = soup.find("link", rel=lambda v: v and "canonical" in v)
    if link and link.get("href"):
        return urljoin(base_url, link["href"])
    og = soup.find("meta", property="og:url")
    if og and og.get("content"):
        return urljoin(base_url, og["content"])
    return None


def _coerce_km(value) -> int | None:
    if value is None:
        return None
    s = str(value)
    km = parse_mileage(s)
    if km is not None:
        return km
    digits = re.sub(r"[^\d]", "", s)
    if digits:
        n = int(digits)
        if 0 <= n <= 1_000_000:
            return n
    return None


def _isolated_card_text(anchor, base_url: str) -> str:
    """Text of the smallest ancestor that still contains ONLY this one detail
    anchor — so a neighbouring Jimny/Bus card can never leak into this card."""
    best = anchor
    node = anchor
    for _ in range(5):
        parent = node.parent
        if parent is None or parent.name in ("body", "html"):
            break
        detail_anchors = 0
        for x in parent.find_all("a", href=True):
            if url_quality(urljoin(base_url, x["href"])) in (EXACT_DETAIL, LIKELY_DETAIL):
                detail_anchors += 1
                if detail_anchors > 1:
                    break
        if detail_anchors > 1:
            break
        best = parent
        node = parent
    return " ".join(best.get_text(" ", strip=True).split())[:500]


def _card_title(anchor, card_text: str) -> str:
    title = " ".join(anchor.get_text(" ", strip=True).split())
    if len(title) < 3:
        title = " ".join((anchor.get("title") or anchor.get("aria-label") or "").split())
    if len(title) < 3:
        card = anchor.find_parent(["li", "article", "div"])
        if card:
            h = card.find(["h1", "h2", "h3", "h4"])
            if h:
                title = " ".join(h.get_text(" ", strip=True).split())
    return title[:200]


def _card_image(anchor) -> list[str]:
    card = anchor.find_parent(["li", "article", "div"]) or anchor
    img = card.find("img")
    if img:
        src = img.get("src") or img.get("data-src") or img.get("data-lazy")
        if src and src.startswith(("http", "//", "/")):
            return [src]
    return []


def _card_candidates(soup: BeautifulSoup, base_url: str, domain: str,
                     country: str | None) -> list[RawListing]:
    seen: dict[str, RawListing] = {}
    page_key = normalize_url(base_url)
    for a in soup.find_all("a", href=True):
        abs_url = urljoin(base_url, a["href"])
        q = url_quality(abs_url)
        if q not in (EXACT_DETAIL, LIKELY_DETAIL):
            continue
        key = normalize_url(abs_url)
        if key == page_key or key in seen:
            continue
        card_text = _isolated_card_text(a, base_url)
        title = _card_title(a, card_text)
        if not title or len(title) < 3:
            continue
        images = _card_image(a)
        rl = RawListing(
            title=title, url=abs_url, source_domain=domain, raw_text=card_text,
            images=[urljoin(base_url, i) for i in images], country=country,
            extraction_method="card_anchor", listing_url_quality=q,
            card_href_found=True, discovered_from_url=base_url)
        _enrich_from_text(rl)
        seen[key] = rl
    return list(seen.values())


def _candidate_from_jsonld(node: dict, base_url: str, domain: str,
                           country: str | None) -> RawListing | None:
    url = node.get("url")
    if not url:
        return None
    abs_url = urljoin(base_url, url)
    q = url_quality(abs_url)
    if q not in (EXACT_DETAIL, LIKELY_DETAIL):
        return None
    kw, hp = parse_power(str(node.get("power_raw") or ""))
    rl = RawListing(
        title=(node.get("title") or node.get("model") or "").strip() or abs_url,
        url=abs_url, source_domain=domain, description=node.get("description"),
        price=node.get("price"), currency=node.get("currency"),
        mileage_km=_coerce_km(node.get("mileage")),
        year=parse_year(str(node.get("year") or "")), power_kw=kw, power_hp=hp,
        displacement_cc=parse_displacement(str(node.get("displacement_raw") or "")),
        vin=node.get("vin"), color=node.get("color"), fuel=node.get("fuel"),
        transmission=node.get("transmission"),
        images=[urljoin(base_url, i) for i in (node.get("images") or []) if i],
        country=country, extraction_method="jsonld", listing_url_quality=q,
        card_href_found=True, discovered_from_url=base_url)
    _enrich_from_text(rl)
    return rl


def _individual_candidate(soup: BeautifulSoup, jl: list[dict], detail_url: str,
                          domain: str, country: str | None) -> RawListing | None:
    """Build the single candidate for an INDIVIDUAL detail page. Title comes
    from JSON-LD / og:title / <h1> — never a search query."""
    node = jl[0] if jl else {}
    title = (node.get("title") or "").strip()
    if not title:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = " ".join(og["content"].split())
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = " ".join(h1.get_text(" ", strip=True).split())
    if not title:
        return None
    kw, hp = parse_power(str(node.get("power_raw") or ""))
    body_text = " ".join(soup.get_text(" ", strip=True).split())[:1500]
    rl = RawListing(
        title=title[:200], url=detail_url, source_domain=domain,
        description=node.get("description"),
        price=node.get("price"), currency=node.get("currency"),
        mileage_km=_coerce_km(node.get("mileage")),
        year=parse_year(str(node.get("year") or "")), power_kw=kw, power_hp=hp,
        displacement_cc=parse_displacement(str(node.get("displacement_raw") or "")),
        vin=node.get("vin"), color=node.get("color"),
        images=[urljoin(detail_url, i) for i in (node.get("images") or []) if i],
        country=country, extraction_method="individual",
        listing_url_quality=url_quality(detail_url), card_href_found=True,
        raw_text=body_text, page_type=INDIVIDUAL_LISTING,
        discovered_from_url=detail_url)
    _enrich_from_text(rl)
    return rl


def extract_page(html: str, base_url: str, source_domain: str,
                 country: str | None = None) -> tuple[list[RawListing], str]:
    """Return (candidates, page_type). Every candidate has an EXACT/LIKELY
    detail URL; search/inventory pages yield per-card candidates only."""
    if not html:
        return [], OTHER
    soup = BeautifulSoup(html, "lxml")
    canonical = _meta_url(soup, base_url)
    page_url = canonical or base_url
    page_q = url_quality(page_url)

    jl = []
    for node in extract_jsonld_vehicles(html):
        u = node.get("url")
        if u:
            node["url"] = urljoin(base_url, u)
        jl.append(node)

    cards = _card_candidates(soup, base_url, source_domain, country)

    # A URL that itself looks like a detail page.
    if page_q in (EXACT_DETAIL, LIKELY_DETAIL):
        distinct = {normalize_url(c.url) for c in cards}
        distinct.discard(normalize_url(page_url))
        if len(distinct) >= 2:
            page_type = DEALER_INVENTORY
            return _finalize(cards, page_type, page_url), page_type
        cand = _individual_candidate(soup, jl, page_url, source_domain, country)
        if cand:
            return [cand], INDIVIDUAL_LISTING
        # detail-ish URL but nothing parseable
        return [], OTHER

    # Search / inventory / homepage.
    if cards:
        page_type = (SEARCH_RESULTS if page_q == SEARCH_PAGE else DEALER_INVENTORY)
        return _finalize(cards, page_type, page_url), page_type

    # No DOM cards — try JSON-LD product/vehicle nodes with specific URLs.
    jl_cands = [c for c in (_candidate_from_jsonld(n, base_url, source_domain, country)
                            for n in jl) if c]
    if jl_cands:
        page_type = SEARCH_RESULTS if len(jl_cands) > 1 else OTHER
        return _finalize(jl_cands, page_type, page_url), page_type

    low = html.lower()
    if any(m in low for m in _ARTICLE_MARKERS):
        return [], ARTICLE
    if page_q == HOMEPAGE:
        return [], PAGE_HOMEPAGE
    if page_q == SEARCH_PAGE:
        return [], SEARCH_RESULTS   # search page we could not parse → 0 candidates
    return [], OTHER


def _finalize(cards: list[RawListing], page_type: str, page_url: str
              ) -> list[RawListing]:
    page_key = normalize_url(page_url)
    out = []
    for c in cards:
        if normalize_url(c.url) == page_key:
            continue  # never the page itself
        if c.listing_url_quality not in (EXACT_DETAIL, LIKELY_DETAIL):
            continue
        c.page_type = page_type
        out.append(c)
    return out


def extract_listings_from_html(html: str, base_url: str, source_domain: str,
                               country: str | None = None) -> list[RawListing]:
    """Back-compatible entry point: returns just the candidate list."""
    # Marktplaats (and similar) get a dedicated adapter first.
    from .marktplaats import maybe_extract_marktplaats
    special = maybe_extract_marktplaats(html, base_url, source_domain, country)
    if special is not None:
        return special
    candidates, _ = extract_page(html, base_url, source_domain, country)
    return candidates
