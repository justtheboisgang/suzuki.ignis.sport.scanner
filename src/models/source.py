"""The `sources` table — the system's permanent memory of *where* to look."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_column, updated_column


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(4), index=True)
    language: Mapped[str | None] = mapped_column(String(8))
    source_type: Mapped[str] = mapped_column(String(40), index=True)

    base_url: Mapped[str | None] = mapped_column(String(500))
    search_url: Mapped[str | None] = mapped_column(String(1000))
    discovery_method: Mapped[str | None] = mapped_column(String(60))

    first_discovered_at: Mapped[datetime] = created_column()
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    check_frequency: Mapped[str] = mapped_column(String(20), default="6h")

    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=50)          # 0-100
    discovery_value: Mapped[int] = mapped_column(Integer, default=50)   # 0-100

    vehicles_found_total: Mapped[int] = mapped_column(Integer, default=0)
    ignis_found_total: Mapped[int] = mapped_column(Integer, default=0)
    ignis_sport_found_total: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)

    parser_type: Mapped[str] = mapped_column(String(40), default="html_generic")
    requires_browser: Mapped[bool] = mapped_column(Boolean, default=False)
    robots_allowed: Mapped[bool] = mapped_column(Boolean, default=True)

    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = updated_column()

    # Health signal: typical result count, so a sudden drop to 0 is detectable.
    typical_result_count: Mapped[int] = mapped_column(Integer, default=0)
    last_result_count: Mapped[int | None] = mapped_column(Integer)
    health: Mapped[str] = mapped_column(String(20), default="unknown")  # healthy/degraded/failed

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Source {self.domain} [{self.source_type}] {self.country}>"
