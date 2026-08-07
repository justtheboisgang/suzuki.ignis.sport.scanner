"""`scan_runs` — one row per scan cycle, for logging and health monitoring."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_column


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(30), default="scan")  # scan/discovery/manual
    start_time: Mapped[datetime] = created_column()
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sources_attempted: Mapped[int] = mapped_column(Integer, default=0)
    sources_successful: Mapped[int] = mapped_column(Integer, default=0)
    sources_failed: Mapped[int] = mapped_column(Integer, default=0)
    pages_checked: Mapped[int] = mapped_column(Integer, default=0)
    listings_seen: Mapped[int] = mapped_column(Integer, default=0)
    new_listings: Mapped[int] = mapped_column(Integer, default=0)
    changed_listings: Mapped[int] = mapped_column(Integer, default=0)
    removed_listings: Mapped[int] = mapped_column(Integer, default=0)
    ai_calls: Mapped[int] = mapped_column(Integer, default=0)
    new_domains: Mapped[int] = mapped_column(Integer, default=0)

    errors: Mapped[list | None] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="running")
