"""Time-series tables: full snapshots, price history and status history. The
system never loses history — a listing that disappears and reappears keeps its
whole timeline."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_column


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    captured_at: Mapped[datetime] = created_column()
    price_eur: Mapped[float | None] = mapped_column(Float)
    mileage_km: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(20))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    raw_title: Mapped[str | None] = mapped_column(Text)

    listing = relationship("Listing", back_populates="snapshots")


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    changed_at: Mapped[datetime] = created_column()
    old_price_eur: Mapped[float | None] = mapped_column(Float)
    new_price_eur: Mapped[float | None] = mapped_column(Float)
    delta_eur: Mapped[float | None] = mapped_column(Float)

    listing = relationship("Listing", back_populates="price_history")


class StatusHistory(Base):
    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    changed_at: Mapped[datetime] = created_column()
    old_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)

    listing = relationship("Listing", back_populates="status_history")
