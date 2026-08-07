"""Generic HTML listing extraction.

Two responsibilities:
  1. Turn a fetched page into candidate `RawListing` objects (JSON-LD first,
     then anchor/heading heuristics) — deliberately source-agnostic so a brand
     new dealer site yields *something* without a bespoke parser.
  2. Provide the `RawListing` dataclass that the rest of the pipeline consumes.
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

# Words that, in an anchor/heading, suggest a car listing worth inspecting.
_INTEREST = re.compile(r"\b(ignis|suzuki|ht81s)\b", re.I)


def _coerce_km(value) -> int | None:
    """Coerce a JSON-LD odometer value (which may be a bare number, a numeric
    string, or "118000 km") into an integer kilometre reading."""
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


@dataclass
class RawListing:
    """Everything a crawler managed to extract for one candidate vehicle,
    before classification / normalisation into the ORM model."""

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

    def combined_text(self) -> str:
        return " ".join(filter(None, [self.title, self.description, self.raw_text]))


def _enrich_from_text(rl: RawListing) -> None:
    """Fill missing numeric fields from the combined text, deterministically."""
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


def extract_listings_from_html(
    html: str, base_url: str, source_domain: str, country: str | None = None
) -> list[RawListing]:
    """Best-effort extraction. Returns candidate listings; the pre-filter later
    decides which are actually Ignis-related."""
    results: list[RawListing] = []
    seen_urls: set[str] = set()

    # 1) Structured JSON-LD (highest quality).
    for node in extract_jsonld_vehicles(html):
        url = node.get("url") or base_url
        url = urljoin(base_url, url)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        kw, hp = parse_power(str(node.get("power_raw") or ""))
        rl = RawListing(
            title=(node.get("title") or node.get("model") or "").strip() or base_url,
            url=url,
            source_domain=source_domain,
            description=node.get("description"),
            price=node.get("price"),
            currency=node.get("currency"),
            mileage_km=_coerce_km(node.get("mileage")),
            year=parse_year(str(node.get("year") or "")),
            power_kw=kw,
            power_hp=hp,
            displacement_cc=parse_displacement(str(node.get("displacement_raw") or "")),
            vin=node.get("vin"),
            color=node.get("color"),
            fuel=node.get("fuel"),
            transmission=node.get("transmission"),
            images=[urljoin(base_url, i) for i in (node.get("images") or []) if i],
            country=country,
            extraction_method="jsonld",
        )
        _enrich_from_text(rl)
        results.append(rl)

    # 2) Heuristic anchor scan for pages without structured data.
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text(" ", strip=True).split())
        if not text or not _INTEREST.search(text):
            continue
        url = urljoin(base_url, a["href"])
        if url in seen_urls:
            continue
        seen_urls.add(url)
        # Pull a little context from the surrounding block.
        context = ""
        parent = a.find_parent(["li", "div", "article", "tr"])
        if parent:
            context = " ".join(parent.get_text(" ", strip=True).split())[:600]
        rl = RawListing(
            title=text[:200],
            url=url,
            source_domain=source_domain,
            raw_text=context,
            country=country,
            extraction_method="anchor_heuristic",
        )
        _enrich_from_text(rl)
        results.append(rl)

    return results
