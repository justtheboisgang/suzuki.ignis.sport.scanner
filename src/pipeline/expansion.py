"""Seller → Source expansion and reverse source discovery.

A listing is never the end of the trail. When we ingest one — even if it was
found on AutoScout/mobile/TheParking — we try to identify the *seller's own
dealer domain*, register that domain as a new source (so we can monitor its full
inventory directly), and record the whole provenance chain on the listing:

    discovery_source → aggregator → original_marketplace → seller → dealer_domain
                                                                → canonical_listing
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..discovery.engine import is_aggregator_domain
from ..models.listing import Listing
from ..models.source import Source
from ..utils.hashing import domain_of
from ..utils.logging import get_logger

log = get_logger("pipeline.expansion")

_LONG_TAIL_TYPES = {"dealer", "suzuki_dealer", "garage", "youngtimer_dealer",
                    "enthusiast_dealer", "private_site", "forum", "club",
                    "community", "local_marketplace"}
_MARKETPLACE_TYPES = {"major_marketplace", "classifieds", "aggregator"}

# A URL somewhere in the text that could be the dealer's own website.
_URL_RE = re.compile(r"https?://([a-z0-9.-]+\.[a-z]{2,})(/[^\s\"'<>]*)?", re.I)


def is_long_tail_source(source: Source | None) -> bool:
    if source is None:
        return False
    if is_aggregator_domain(source.domain):
        return False
    return (source.source_type in _LONG_TAIL_TYPES
            or (source.discovery_value or 0) >= 60)


def _candidate_dealer_domain(listing: Listing, source: Source | None) -> str | None:
    """Find the seller's own domain from explicit fields or the description."""
    # 1) Explicit seller website (best).
    if listing.seller_website:
        dom = domain_of(listing.seller_website)
        if dom and not is_aggregator_domain(dom):
            return dom
    # 2) If the listing itself is already on a dealer's own site, that's it.
    if source and source.source_type in _LONG_TAIL_TYPES and not is_aggregator_domain(source.domain):
        return source.domain
    # 3) A dealer URL mentioned in the description.
    for text in (listing.description_original or "", listing.title or ""):
        for m in _URL_RE.finditer(text):
            dom = m.group(1).lower()
            if dom.startswith("www."):
                dom = dom[4:]
            if dom and not is_aggregator_domain(dom) and "suzuki" not in dom[:0]:
                # Skip obvious non-dealer hosts.
                if not any(b in dom for b in ("google", "facebook", "youtube",
                                              "instagram", "wikipedia")):
                    return dom
    return None


def set_provenance(listing: Listing, source: Source | None) -> None:
    """Record where this listing came from, independent of expansion."""
    if source is not None:
        listing.discovery_source = source.discovery_method or "seed"
        listing.discovered_by_provider = source.first_provider
        if source.is_aggregator or source.source_type == "aggregator":
            listing.aggregator = source.domain
        if source.source_type in _MARKETPLACE_TYPES:
            listing.original_marketplace = source.domain
    listing.is_long_tail = is_long_tail_source(source)
    if not listing.canonical_listing_url:
        listing.canonical_listing_url = (listing.original_listing_url
                                         or listing.listing_url)


def expand_seller_to_source(session: Session, listing: Listing,
                            source: Source | None) -> str | None:
    """Register the seller's dealer domain as a monitorable source (if new) and
    link it to the listing. Returns the dealer domain, or None."""
    dealer = _candidate_dealer_domain(listing, source)
    if not dealer:
        return None

    listing.dealer_domain = dealer
    # Prefer the dealer's own page as canonical when the listing lives there.
    if source and source.domain == dealer:
        listing.canonical_listing_url = listing.original_listing_url or listing.listing_url

    existing = session.query(Source).filter(Source.domain == dealer).first()
    if existing is None:
        session.add(Source(
            domain=dealer,
            name=(listing.seller_name or dealer)[:120],
            country=listing.country,
            source_type="dealer",
            base_url=f"https://{dealer}/",
            discovery_method="seller_expansion",
            discovery_value=70,          # own-inventory dealer = high value
            priority=65,
            parser_type="html_generic",
            seller_derived=True,
            notes=f"Discovered from listing {listing.internal_id}",
        ))
        log.info("Seller→source expansion: registered dealer domain %s", dealer)
    return dealer
