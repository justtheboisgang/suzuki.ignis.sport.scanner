"""Deterministic classification helpers used by the search-hit pipeline and the
source registry:

  * ``domain_category``  — is a domain a MONITORABLE vehicle source, a
    DISCOVERY_ONLY site (parts/tuning/insurer/magazine/specs/ecommerce), or
    IRRELEVANT?
  * ``classify_url_type`` — what is a specific fetched URL (LISTING /
    DEALER_INVENTORY / HOMEPAGE / ARTICLE / PARTS_PAGE / FORUM_POST / …)?
  * ``detect_country``   — the site's real country from the page + ccTLD, not the
    query country.

All cheap and testable; the AI layer only refines, never replaces, these.
"""

from __future__ import annotations

import re

from ..config.countries import COUNTRIES
from ..utils.hashing import domain_of
from .queries import BIG_PLATFORMS

# --- Categories ------------------------------------------------------------
MONITORABLE = "MONITORABLE_VEHICLE_SOURCE"
DISCOVERY_ONLY = "DISCOVERY_ONLY_SOURCE"
IRRELEVANT = "IRRELEVANT"

# Known giant aggregators/socials — recorded but never a high-value long-tail
# find. Defined here (neutral module) so engine + hit pipeline share it without
# an import cycle.
_KNOWN_BIG = {
    "autoscout24.de", "autoscout24.com", "mobile.de", "theparking.eu",
    "autouncle.com", "ebay.com", "ebay.de", "facebook.com", "google.com",
    "youtube.com", "wikipedia.org", "leboncoin.fr", "marktplaats.nl",
    "autotrader.co.uk", "instagram.com", "pinterest.com", "x.com", "twitter.com",
    "reddit.com", "amazon.com", "gumtree.com",
}


def is_aggregator_domain(dom: str) -> bool:
    return any(b in dom for b in BIG_PLATFORMS) or dom in _KNOWN_BIG

# Site-type words that mean "NOT a place that sells whole used cars" even if the
# page mentions Suzuki/Ignis. Multilingual, matched against domain + text.
_DISCOVERY_ONLY_TERMS = (
    # parts / accessories
    "ersatzteil", "ersatzteile", "teile", "autoteile", "alkatresz", "alkatreszek",
    "parts", "spare", "ricambi", "recambios", "pièces", "onderdelen", "części",
    "zubehör", "accessor", "accessoire", "tuning", "tuner", "fahrwerk", "felgen",
    "wheels", "auspuff", "exhaust", "carnoisseur",
    # insurance / finance
    "versicher", "insurance", "assicurazion", "seguro", "assurance", "leasing",
    "versicherer",
    # media / reference
    "magazin", "magazine", "zeitschrift", "auto-motor", "motor-und-sport",
    "technische-daten", "technical", "specs", "datenblatt", "datenbank",
    "wiki", "enzyklop", "test", "review", "ratgeber", "news", "blog",
    # generic ecommerce / marketplaces of goods (not cars)
    "shop", "store", "amazon", "ebay-kleinanzeigen-shop",
)

# Strong signals that a domain IS a monitorable vehicle source.
_MONITORABLE_TERMS = (
    "autohaus", "gebrauchtwagen", "occasion", "fahrzeuge", "vehicules", "veicoli",
    "vehiculos", "used-cars", "usedcars", "usati", "ocasion", "segunda-mano",
    "annonces", "annunci", "anuncios", "kleinanzeigen", "marktplaats", "otomoto",
    "autoscout", "mobile", "dealer", "motors", "autos", "cars", "garage",
    "autobazar", "autobazar", "bazar", "auto24", "autoplius", "autovit",
    "carsales", "carmarket", "automarkt", "autoborse", "autobörse",
)

# Words hinting the URL points at an individual vehicle listing.
_LISTING_URL_HINTS = ("ignis", "ht81s", "/inserat", "/angebot", "/annonce",
                      "/annuncio", "/anuncio", "/vehicle", "/fahrzeug",
                      "/voiture", "/detail", "/listing", "/ad/", "/oferta",
                      "/occasion/", "/used/", "/gebrauchtwagen/")
_INVENTORY_URL_HINTS = ("/fahrzeuge", "/gebrauchtwagen", "/occasions", "/stock",
                        "/inventory", "/bestand", "/lager", "/angebote",
                        "/vehicules", "/veicoli", "/usati", "/coches",
                        "/aanbod", "/search", "/suche", "/lst/")
_ARTICLE_MARKERS = ("<article", "datePublished", "article:published_time",
                    "/news/", "/blog/", "/ratgeber", "/test/", "/review")
_FORUM_MARKERS = ("viewtopic", "/thread", "/forum", "/showthread", "/topic",
                  "phpbb", "vbulletin")


def domain_category(domain: str, text: str = "") -> dict:
    """Return {category, relevance, monitorable, reasons} deterministically."""
    dom = domain_of(domain) or domain
    blob = f" {dom} {text} ".lower()
    reasons: list[str] = []

    disc_hits = [t for t in _DISCOVERY_ONLY_TERMS if t in blob]
    mon_hits = [t for t in _MONITORABLE_TERMS if t in blob]

    # A parts/tuning/insurer/magazine site is discovery-only unless it *also*
    # clearly runs a used-car marketplace/inventory.
    if disc_hits and not mon_hits:
        reasons.append(f"discovery-only signals: {', '.join(disc_hits[:4])}")
        return {"category": DISCOVERY_ONLY, "relevance": 30, "monitorable": False,
                "reasons": reasons}

    if mon_hits:
        reasons.append(f"vehicle-marketplace signals: {', '.join(mon_hits[:4])}")
        # If it also smells of parts/tuning, be cautious but keep monitorable.
        rel = 70 if not disc_hits else 55
        return {"category": MONITORABLE, "relevance": rel, "monitorable": True,
                "reasons": reasons}

    if disc_hits:
        reasons.append(f"mixed discovery-only signals: {', '.join(disc_hits[:3])}")
        return {"category": DISCOVERY_ONLY, "relevance": 35, "monitorable": False,
                "reasons": reasons}

    reasons.append("no strong vehicle-marketplace signal")
    return {"category": DISCOVERY_ONLY, "relevance": 40, "monitorable": False,
            "reasons": reasons}


_PAGE_TYPE_TO_HIT = {
    "INDIVIDUAL_LISTING": "LISTING",
    "SEARCH_RESULTS": "DEALER_INVENTORY",   # per-card ingest, page never a listing
    "DEALER_INVENTORY": "DEALER_INVENTORY",
    "HOMEPAGE": "HOMEPAGE",
    "ARTICLE": "ARTICLE",
    "OTHER": "IRRELEVANT",
}


def classify_url_type(url: str, html: str, domain: str | None = None) -> dict:
    """Classify a fetched URL using the card-isolated extractor. Returns
    {type, page_type, listings, reason}. A search/inventory page is NEVER
    reported as a single LISTING — only its per-card candidates are returned."""
    from ..parsers.html_generic import extract_page
    dom = domain or domain_of(url)
    low_html = (html or "").lower()

    listings, page_type = extract_page(html or "", url, dom)
    hit_type = _PAGE_TYPE_TO_HIT.get(page_type, "IRRELEVANT")

    # Parts/tuning domains are discovery-only regardless of stray car cards.
    cat = domain_category(dom, low_html[:4000])
    if hit_type in ("IRRELEVANT", "HOMEPAGE") and cat["category"] == DISCOVERY_ONLY \
            and any(t in dom for t in ("teil", "parts", "tuning", "fahrwerk",
                                       "alkatresz", "zubehor", "zubehör")):
        return {"type": "PARTS_PAGE", "page_type": page_type, "listings": [],
                "reason": "parts/tuning domain"}

    return {"type": hit_type, "page_type": page_type, "listings": listings,
            "reason": f"page_type={page_type}, {len(listings)} card(s)"}


# --- Country detection -----------------------------------------------------
_CCTLD = {c.tld.lstrip("."): c.code for c in COUNTRIES.values()}
_PHONE_CC = {"+49": "DE", "+43": "AT", "+41": "CH", "+33": "FR", "+32": "BE",
             "+31": "NL", "+352": "LU", "+39": "IT", "+34": "ES", "+351": "PT",
             "+48": "PL", "+420": "CZ", "+421": "SK", "+36": "HU", "+386": "SI",
             "+385": "HR", "+40": "RO", "+359": "BG", "+30": "GR", "+45": "DK",
             "+46": "SE", "+47": "NO", "+358": "FI", "+372": "EE", "+371": "LV",
             "+370": "LT", "+353": "IE", "+44": "GB"}
_ADDR_HINT = {
    "DE": ("deutschland", "germany"), "AT": ("österreich", "austria", "wien"),
    "CH": ("schweiz", "switzerland", "suisse"), "FR": ("france",),
    "IT": ("italia", "italy"), "ES": ("españa", "spain"), "NL": ("nederland",),
    "BE": ("belgië", "belgique", "belgium"), "PL": ("polska", "poland"),
    "PT": ("portugal",), "GB": ("united kingdom", "england"), "IE": ("ireland",),
}


def detect_country(url: str, html: str = "") -> dict:
    """Best-effort real country of a site. Returns {country, confidence, signal}.
    ccTLD is only a weak signal; address/phone in the page are stronger."""
    dom = domain_of(url)
    low = (html or "").lower()

    # Phone country code in the page — strong.
    for cc, code in _PHONE_CC.items():
        if cc in (html or ""):
            return {"country": code, "confidence": 80, "signal": f"phone {cc}"}
    # Explicit country name in an address-ish context — medium/strong.
    for code, hints in _ADDR_HINT.items():
        if any(h in low for h in hints):
            return {"country": code, "confidence": 70, "signal": "address text"}
    # ccTLD — weak signal.
    tld = dom.rsplit(".", 1)[-1] if "." in dom else ""
    if tld in _CCTLD:
        return {"country": _CCTLD[tld], "confidence": 45, "signal": f".{tld} ccTLD"}
    return {"country": None, "confidence": 0, "signal": "unknown"}
