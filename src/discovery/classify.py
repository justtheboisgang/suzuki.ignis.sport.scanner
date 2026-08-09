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
from ..parsers.html_generic import extract_listings_from_html
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


def classify_url_type(url: str, html: str, domain: str | None = None) -> dict:
    """Classify a fetched URL. Returns {type, listings, reason}. `listings` is
    the extracted RawListing candidates (used when type == LISTING)."""
    dom = domain or domain_of(url)
    low_html = (html or "").lower()
    low_url = (url or "").lower()

    listings = extract_listings_from_html(html or "", url, dom) if html else []
    ignis_listings = [rl for rl in listings
                      if "ignis" in (rl.title or "").lower()
                      or "ignis" in (rl.raw_text or "").lower()
                      or "ht81s" in (rl.combined_text() or "").lower()]

    # A single vehicle detail page: URL looks like a listing AND we extracted a
    # concrete Ignis candidate with a price or specs.
    strong_listing = any(h in low_url for h in _LISTING_URL_HINTS) or bool(ignis_listings)
    concrete = [rl for rl in ignis_listings
                if rl.price or rl.mileage_km or rl.year or rl.power_kw]

    if concrete and (strong_listing or len(concrete) == 1):
        return {"type": "LISTING", "listings": concrete,
                "reason": "Ignis candidate with price/specs on a detail-like URL"}

    if any(h in low_url for h in _INVENTORY_URL_HINTS) or len(listings) >= 3:
        return {"type": "DEALER_INVENTORY", "listings": listings,
                "reason": "inventory/search-style URL or many listings"}

    if any(m in low_html for m in _FORUM_MARKERS) or any(
            m in low_url for m in _FORUM_MARKERS):
        return {"type": "FORUM_POST", "listings": ignis_listings, "reason": "forum markers"}

    if any(m in low_html or m in low_url for m in _ARTICLE_MARKERS):
        return {"type": "ARTICLE", "listings": [], "reason": "article markers"}

    cat = domain_category(dom, low_html[:4000])
    if cat["category"] == DISCOVERY_ONLY and any(
            t in f"{dom}" for t in ("teil", "parts", "tuning", "fahrwerk",
                                    "alkatresz", "zubehor", "zubehör")):
        return {"type": "PARTS_PAGE", "listings": [], "reason": "parts/tuning domain"}

    if low_url.rstrip("/").endswith(dom.rstrip("/")) or low_url.count("/") <= 3:
        return {"type": "HOMEPAGE", "listings": listings, "reason": "root/homepage URL"}

    return {"type": "IRRELEVANT" if not ignis_listings else "DEALER_INVENTORY",
            "listings": listings, "reason": "no listing/inventory signals"}


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
