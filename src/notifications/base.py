"""Notification abstractions and the standard alert message format."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Priority(str, Enum):
    HOT = "hot"        # SEVERE: strong match + strong opportunity
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class Notification:
    title: str
    body: str
    priority: Priority = Priority.NORMAL
    listing_id: int | None = None
    payload: dict = field(default_factory=dict)


def format_hit(payload: dict) -> str:
    """Render the canonical "IGNIS SPORT FOUND" message from a payload dict."""
    g = payload.get
    price = g("price_eur")
    price_str = f"€{price:,.0f}" if price else "?"
    mileage = g("mileage_km")
    mileage_str = f"{mileage:,} km" if mileage else "?"
    lines = [
        "🚨 SUZUKI IGNIS SPORT FOUND",
        f"Country:          {g('country') or '?'}",
        f"City:             {g('city') or '?'}",
        f"Price:            {price_str}",
        f"Mileage:          {mileage_str}",
        f"Year:             {g('year') or '?'}",
        f"Seller:           {g('seller_name') or '?'}",
        f"Seller type:      {g('seller_type') or '?'}",
        f"Listing age:      {g('listing_age') or 'new'}",
        f"Match confidence: {g('match_confidence')}/100",
        f"Opportunity:      {g('opportunity_score')}/100",
        f"Source:           {g('source') or '?'}",
        f"Original URL:     {g('url') or '?'}",
        "",
        "WHY INTERESTING:",
        g("why") or "-",
        "",
        "RISKS:",
        g("risks") or "-",
    ]
    return "\n".join(lines)


class NotificationProvider:
    name = "base"

    @property
    def enabled(self) -> bool:
        return True

    def send(self, notification: Notification) -> bool:  # pragma: no cover
        raise NotImplementedError
