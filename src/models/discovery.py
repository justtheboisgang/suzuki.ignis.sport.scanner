"""`discovery_queries` — the memory of what the system searched for, so it can
learn which queries actually yield new sources and listings."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_column


class DiscoveryQuery(Base):
    __tablename__ = "discovery_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(Text, index=True)
    country: Mapped[str | None] = mapped_column(String(4), index=True)
    language: Mapped[str | None] = mapped_column(String(8))
    provider: Mapped[str] = mapped_column(String(40), default="none")
    experimental: Mapped[bool] = mapped_column(default=False)

    first_run_at: Mapped[datetime] = created_column()
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_count: Mapped[int] = mapped_column(Integer, default=0)

    result_count: Mapped[int] = mapped_column(Integer, default=0)
    new_domains_found: Mapped[int] = mapped_column(Integer, default=0)
    new_listings_found: Mapped[int] = mapped_column(Integer, default=0)
    effectiveness_score: Mapped[float] = mapped_column(Float, default=0.0)
