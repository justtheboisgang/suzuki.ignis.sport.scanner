"""Search-provider bookkeeping.

`ProviderUsage` logs every search API request so monthly request budgets can be
enforced. `DomainDiscovery` records which provider surfaced which domain (and at
what rank/page/query), enabling the provider-comparison report (Brave-unique vs
SerpApi-unique vs overlap).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_column


class ProviderUsage(Base):
    __tablename__ = "provider_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = created_column()
    provider: Mapped[str] = mapped_column(String(24), index=True)
    query: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(4))
    page: Mapped[int] = mapped_column(Integer, default=1)
    results_returned: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None] = mapped_column(Text)


class DomainDiscovery(Base):
    __tablename__ = "domain_discoveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = created_column()
    domain: Mapped[str] = mapped_column(String(255), index=True)
    provider: Mapped[str] = mapped_column(String(24), index=True)
    query: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(4))
    rank: Mapped[int | None] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text)
    was_new: Mapped[bool] = mapped_column(default=False)
