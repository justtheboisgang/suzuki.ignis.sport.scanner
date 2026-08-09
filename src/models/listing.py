"""The `listings` table — one row per de-duplicated vehicle. Every field that
is not directly observed stays NULL/UNKNOWN rather than being guessed."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_column, updated_column
from .enums import ListingStatus, SellerType


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    internal_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # --- Vehicle identity ------------------------------------------------
    title: Mapped[str | None] = mapped_column(Text)
    make: Mapped[str | None] = mapped_column(String(60), default="Suzuki")
    model: Mapped[str | None] = mapped_column(String(60), default="Ignis")
    variant: Mapped[str | None] = mapped_column(String(60))
    production_year: Mapped[int | None] = mapped_column(Integer, index=True)
    registration_date: Mapped[str | None] = mapped_column(String(20))
    mileage_km: Mapped[int | None] = mapped_column(Integer, index=True)

    engine: Mapped[str | None] = mapped_column(String(60))
    displacement_cc: Mapped[int | None] = mapped_column(Integer)
    power_kw: Mapped[int | None] = mapped_column(Integer)
    power_hp: Mapped[int | None] = mapped_column(Integer)
    transmission: Mapped[str | None] = mapped_column(String(30))
    fuel: Mapped[str | None] = mapped_column(String(30))
    color: Mapped[str | None] = mapped_column(String(40))

    # --- Pricing ---------------------------------------------------------
    price_original: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    price_eur: Mapped[float | None] = mapped_column(Float, index=True)
    # OK / SUSPECT / UNKNOWN — guards against €1 placeholders & "price on request".
    price_parse_status: Mapped[str] = mapped_column(String(12), default="OK")

    # --- Location / seller ----------------------------------------------
    country: Mapped[str | None] = mapped_column(String(4), index=True)
    region: Mapped[str | None] = mapped_column(String(80))
    city: Mapped[str | None] = mapped_column(String(80))
    seller_type: Mapped[str] = mapped_column(String(20), default=SellerType.UNKNOWN.value)
    seller_name: Mapped[str | None] = mapped_column(String(160))
    seller_phone_public: Mapped[str | None] = mapped_column(String(60))
    seller_website: Mapped[str | None] = mapped_column(String(300))

    # --- Provenance / links ---------------------------------------------
    listing_url: Mapped[str] = mapped_column(String(1000), index=True)
    original_listing_url: Mapped[str | None] = mapped_column(String(1000))
    # All known URLs pointing at the same physical car (dedup).
    alternate_urls: Mapped[list | None] = mapped_column(JSON, default=list)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), index=True)

    # Full provenance chain (reverse source discovery):
    #   discovery_source → aggregator → original_marketplace → seller/dealer.
    discovery_source: Mapped[str | None] = mapped_column(String(80))   # e.g. brave/seed/manual
    discovered_by_provider: Mapped[str | None] = mapped_column(String(24))
    aggregator: Mapped[str | None] = mapped_column(String(120))
    original_marketplace: Mapped[str | None] = mapped_column(String(120))
    dealer_domain: Mapped[str | None] = mapped_column(String(160))
    canonical_listing_url: Mapped[str | None] = mapped_column(String(1000))
    is_long_tail: Mapped[bool] = mapped_column(default=False, index=True)

    # URL / extraction quality (fixes search-page-as-listing bug).
    # EXACT_DETAIL / LIKELY_DETAIL / SEARCH_PAGE / HOMEPAGE / UNKNOWN
    listing_url_quality: Mapped[str] = mapped_column(String(16), default="UNKNOWN",
                                                     index=True)
    page_type: Mapped[str | None] = mapped_column(String(24))
    extraction_method: Mapped[str | None] = mapped_column(String(30))
    card_href_found: Mapped[bool] = mapped_column(default=False)
    discovered_from_url: Mapped[str | None] = mapped_column(String(1000))

    first_seen_at: Mapped[datetime] = created_column()
    last_seen_at: Mapped[datetime] = updated_column()
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    listing_status: Mapped[str] = mapped_column(
        String(20), default=ListingStatus.ACTIVE.value, index=True
    )
    consecutive_misses: Mapped[int] = mapped_column(Integer, default=0)

    # --- Description -----------------------------------------------------
    description_original: Mapped[str | None] = mapped_column(Text)
    description_language: Mapped[str | None] = mapped_column(String(8))
    description_normalized: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    # --- Identifiers / condition ----------------------------------------
    vin_public: Mapped[str | None] = mapped_column(String(40))
    chassis_code: Mapped[str | None] = mapped_column(String(20))
    lhd_rhd: Mapped[str | None] = mapped_column(String(12))  # LHD/RHD/UNKNOWN
    lhd_rhd_confidence: Mapped[int | None] = mapped_column(Integer)
    inspection_info: Mapped[str | None] = mapped_column(Text)
    service_history: Mapped[str | None] = mapped_column(Text)
    damage_notes: Mapped[str | None] = mapped_column(Text)

    image_urls: Mapped[list | None] = mapped_column(JSON, default=list)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    image_hashes: Mapped[list | None] = mapped_column(JSON, default=list)

    # --- Scores ----------------------------------------------------------
    vehicle_match_confidence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    source_confidence: Mapped[int] = mapped_column(Integer, default=50)
    opportunity_score: Mapped[int | None] = mapped_column(Integer, index=True)
    classification: Mapped[str | None] = mapped_column(String(40), index=True)

    # Structured AI analysis (JSON blob from the listing analyst / detective).
    ai_analysis: Mapped[dict | None] = mapped_column(JSON)
    score_explanation: Mapped[dict | None] = mapped_column(JSON)

    # --- Claude Vision verification (only for ambiguous Ignis candidates) --
    vision_analyzed: Mapped[bool] = mapped_column(default=False)
    vision_model: Mapped[str | None] = mapped_column(String(80))
    vision_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vision_image_hash: Mapped[str | None] = mapped_column(String(64))  # cache key
    vehicle_identity_confidence: Mapped[int | None] = mapped_column(Integer)
    ignis_confidence: Mapped[int | None] = mapped_column(Integer)
    ignis_sport_visual_confidence: Mapped[int | None] = mapped_column(Integer)
    visible_positive_signals: Mapped[list | None] = mapped_column(JSON)
    visible_negative_signals: Mapped[list | None] = mapped_column(JSON)
    vision_uncertainties: Mapped[list | None] = mapped_column(JSON)
    visual_summary: Mapped[str | None] = mapped_column(Text)
    vision_conflict: Mapped[bool] = mapped_column(default=False)

    # Relationships
    snapshots = relationship("ListingSnapshot", back_populates="listing",
                             cascade="all, delete-orphan")
    price_history = relationship("PriceHistory", back_populates="listing",
                                 cascade="all, delete-orphan")
    status_history = relationship("StatusHistory", back_populates="listing",
                                  cascade="all, delete-orphan")

    @property
    def external_detail_url(self) -> str | None:
        """The best CONCRETE individual-listing URL, or None if we only know a
        search/inventory/homepage URL. The dashboard only shows an
        'Original-Anzeige' button when this is not None."""
        from ..parsers.urltype import url_quality
        for u in (self.canonical_listing_url, self.original_listing_url,
                  self.listing_url):
            if u and url_quality(u) in ("EXACT_DETAIL", "LIKELY_DETAIL"):
                return u
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Listing {self.internal_id} '{self.title}' "
            f"conf={self.vehicle_match_confidence} {self.listing_status}>"
        )
