"""Human feedback on listings and a durable notification log (so the console/DB
notification channel works with zero external credentials)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_column


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), index=True)
    seller_name: Mapped[str | None] = mapped_column(String(160))
    label: Mapped[str] = mapped_column(String(30), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_column()


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = created_column()
    priority: Mapped[str] = mapped_column(String(20), default="normal", index=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    payload: Mapped[dict | None] = mapped_column(JSON)
    delivered_channels: Mapped[list | None] = mapped_column(JSON, default=list)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
