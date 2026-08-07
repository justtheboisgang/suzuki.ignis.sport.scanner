"""Opportunity Score (0-100) for an active Ignis Sport listing.

Every point is explainable — the result carries a breakdown so the dashboard can
answer "Why 87/100?". Factors: price vs market, mileage, condition/risk signals,
match confidence, seller trust, rarity, listing age, LHD preference, location.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..models.listing import Listing

# Rough market reference for a clean Ignis Sport (EUR). Used only for relative
# scoring, never presented as an appraisal.
_MARKET_MEDIAN_EUR = 5500.0
_TYPICAL_MILEAGE = 130_000


@dataclass
class OpportunityResult:
    score: int
    breakdown: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"score": self.score, "breakdown": self.breakdown}


def compute_opportunity(listing: Listing, ai_analysis: dict | None = None) -> OpportunityResult:
    breakdown: dict[str, float] = {}
    score = 50.0
    breakdown["base"] = 50.0

    # --- Match confidence: the anchor. -----------------------------------
    conf_pts = (listing.vehicle_match_confidence - 60) * 0.3  # centred at 60
    score += conf_pts
    breakdown["match_confidence"] = round(conf_pts, 1)

    # --- Price vs market. ------------------------------------------------
    if listing.price_eur:
        ratio = listing.price_eur / _MARKET_MEDIAN_EUR
        # Cheaper than market → up to +18; pricey → down to -12.
        price_pts = max(-12.0, min(18.0, (1.0 - ratio) * 30))
        score += price_pts
        breakdown["price_vs_market"] = round(price_pts, 1)
    else:
        breakdown["price_vs_market"] = 0.0

    # --- Mileage. --------------------------------------------------------
    if listing.mileage_km is not None:
        mil_pts = max(-10.0, min(12.0, (_TYPICAL_MILEAGE - listing.mileage_km) / 12000))
        score += mil_pts
        breakdown["mileage"] = round(mil_pts, 1)

    # --- Condition / risk from AI (or deterministic) analysis. -----------
    if ai_analysis:
        risk = 0.0
        if ai_analysis.get("rust_mentioned"):
            risk -= 5
        if ai_analysis.get("accident_mentioned"):
            risk -= 8
        if ai_analysis.get("engine_issues"):
            risk -= 10
        if ai_analysis.get("transmission_issues"):
            risk -= 8
        if ai_analysis.get("service_history_present"):
            risk += 6
        if ai_analysis.get("modifications"):
            risk -= 3
        score += risk
        breakdown["condition_risk"] = round(risk, 1)

    # --- Seller trust. ---------------------------------------------------
    seller_pts = 0.0
    if listing.seller_type == "dealer":
        seller_pts += 3
    if listing.seller_website:
        seller_pts += 2  # found on the dealer's own site = fresh, direct
    score += seller_pts
    breakdown["seller_trust"] = round(seller_pts, 1)

    # --- LHD preference. -------------------------------------------------
    lhd_pts = 0.0
    if listing.lhd_rhd == "LHD":
        lhd_pts += 5
    elif listing.lhd_rhd == "RHD":
        lhd_pts -= 5
    score += lhd_pts
    breakdown["lhd_preference"] = round(lhd_pts, 1)

    # --- Freshness: newer listings are more actionable. ------------------
    if listing.first_seen_at:
        first = listing.first_seen_at
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - first).days
        fresh_pts = 6.0 if age_days <= 2 else (3.0 if age_days <= 7 else 0.0)
        score += fresh_pts
        breakdown["freshness"] = fresh_pts

    # --- Rarity / long-tail source bonus. --------------------------------
    # A find on a small dealer/enthusiast source is worth more than an
    # aggregator everyone watches; approximated via source_confidence being low
    # reach but high value is handled at pipeline level; here we add a small
    # rarity constant for genuine Sports.
    if listing.vehicle_match_confidence >= 80:
        score += 4
        breakdown["rarity"] = 4.0

    final = int(max(0, min(100, round(score))))
    return OpportunityResult(final, breakdown)
