"""`search_hits` — a first-class record of every relevant search-engine result.

The exact result URL must NEVER be lost: a Brave hit like
``https://dealer.it/cars/suzuki-ignis-sport-2004`` is stored here verbatim and
investigated directly (it may itself be the car), rather than being collapsed to
just the domain homepage.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_column


class SearchHit(Base):
    __tablename__ = "search_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exact_url: Mapped[str] = mapped_column(String(1000), index=True)
    normalized_url: Mapped[str] = mapped_column(String(1000), index=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    query: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(24), index=True)
    rank: Mapped[int | None] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    country: Mapped[str | None] = mapped_column(String(4))
    discovered_at: Mapped[datetime] = created_column()

    # LISTING / DEALER_INVENTORY / SEARCH_RESULT_PAGE / HOMEPAGE / ARTICLE /
    # PARTS_PAGE / FORUM_POST / IRRELEVANT / UNKNOWN
    hit_type: Mapped[str] = mapped_column(String(30), default="UNKNOWN", index=True)
    # PENDING / FETCHED / INGESTED / SOURCE_REGISTERED / SKIPPED / BLOCKED / ERROR
    processing_status: Mapped[str] = mapped_column(String(24), default="PENDING",
                                                   index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    listing_internal_id: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
