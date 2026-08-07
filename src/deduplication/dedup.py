"""Duplicate detection and merging.

A candidate is considered the SAME car as an existing listing when strong
identity signals agree:
  * identical public VIN, or
  * same seller phone + same price band + same mileage band, or
  * perceptually similar images, or
  * same normalised URL (trivially the same listing).

When merged, the surviving listing keeps every known URL and prefers a dealer's
own site as the canonical `original_listing_url`.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..models.listing import Listing
from ..utils.hashing import normalize_url
from .imagehashing import hashes_similar


def _norm_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits[-9:] if len(digits) >= 9 else (digits or None)


def _band(value, size):
    if value is None:
        return None
    return round(value / size)


def dedup_key_signals(listing: Listing) -> dict:
    return {
        "vin": (listing.vin_public or "").strip().upper() or None,
        "phone": _norm_phone(listing.seller_phone_public),
        "price_band": _band(listing.price_eur, 300),
        "mileage_band": _band(listing.mileage_km, 5000),
        "url": normalize_url(listing.listing_url),
    }


def find_duplicate(session: Session, candidate: Listing) -> Listing | None:
    """Return an existing Listing that is the same physical car, or None."""
    cand = dedup_key_signals(candidate)

    # 1) Exact normalised URL (same listing re-seen).
    if cand["url"]:
        existing = session.query(Listing).all()
        for other in existing:
            if other.id == candidate.id:
                continue
            o = dedup_key_signals(other)
            # Strong: same VIN.
            if cand["vin"] and cand["vin"] == o["vin"]:
                return other
            # URL identity.
            if cand["url"] and cand["url"] == o["url"]:
                return other
            # Phone + price/mileage band agreement.
            if (cand["phone"] and cand["phone"] == o["phone"]
                    and cand["price_band"] == o["price_band"]
                    and cand["mileage_band"] == o["mileage_band"]):
                return other
            # Perceptual image match (same car, different platform).
            if hashes_similar(candidate.image_hashes or [], other.image_hashes or []):
                return other
    return None


def _prefer_dealer_url(existing: Listing, new_url: str, new_domain_is_dealer: bool):
    """Prefer a dealer's own homepage listing as the canonical original."""
    if new_domain_is_dealer and not existing.original_listing_url:
        existing.original_listing_url = new_url


def merge_into(existing: Listing, candidate: Listing,
               candidate_is_dealer: bool = False) -> Listing:
    """Fold candidate's new information into the existing listing, keeping all
    known URLs and the most complete field values."""
    urls = set(existing.alternate_urls or [])
    urls.add(existing.listing_url)
    urls.add(candidate.listing_url)
    if candidate.original_listing_url:
        urls.add(candidate.original_listing_url)
    urls.discard(existing.listing_url)
    existing.alternate_urls = sorted(urls)

    _prefer_dealer_url(existing, candidate.listing_url, candidate_is_dealer)

    # Fill any gaps in the surviving record (never overwrite a known fact with
    # an unknown one).
    for field in ("price_original", "currency", "price_eur", "mileage_km",
                  "production_year", "power_kw", "power_hp", "displacement_cc",
                  "vin_public", "chassis_code", "color", "fuel", "transmission",
                  "seller_name", "seller_phone_public", "seller_website",
                  "city", "region", "country", "description_original"):
        if getattr(existing, field) in (None, "") and getattr(candidate, field):
            setattr(existing, field, getattr(candidate, field))

    # Keep the highest confidence and merge image hashes.
    if candidate.vehicle_match_confidence > existing.vehicle_match_confidence:
        existing.vehicle_match_confidence = candidate.vehicle_match_confidence
        existing.classification = candidate.classification
    img = set(existing.image_hashes or []) | set(candidate.image_hashes or [])
    existing.image_hashes = sorted(img)
    if candidate.image_urls:
        merged_imgs = list(dict.fromkeys((existing.image_urls or []) + candidate.image_urls))
        existing.image_urls = merged_imgs
        existing.image_count = len(merged_imgs)
    return existing
