"""`job_status` — one persistent row per job type (discovery / scan) so the
dashboard can show IDLE / RUNNING / FINISHED / FAILED with live progress."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, updated_column


class JobStatus(Base):
    __tablename__ = "job_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(20), default="IDLE")  # IDLE/RUNNING/FINISHED/FAILED
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = updated_column()

    queries_total: Mapped[int] = mapped_column(Integer, default=0)
    queries_done: Mapped[int] = mapped_column(Integer, default=0)
    new_domains: Mapped[int] = mapped_column(Integer, default=0)
    sources_added: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    # Job-type-specific counters (scan vs discovery need different fields).
    metrics: Mapped[dict | None] = mapped_column(JSON)
