"""Monthly request/cost budget guards for the external APIs.

Search APIs must NOT run in full on every listing scan. These helpers enforce
monthly request budgets per search provider (and expose the AI cost budget for
symmetry). At 80% we warn; at 100% non-critical calls stop — but monitoring of
known sources (which uses no search API) keeps running regardless.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..config.settings import get_settings
from ..database.base import session_scope
from ..database.writer import run_write
from ..models.ai_usage import AIUsage
from ..models.provider import ProviderUsage
from ..utils.logging import get_logger

log = get_logger("discovery.budget")


def _month_start() -> datetime:
    return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0,
                                              microsecond=0)


def provider_request_count(provider: str) -> int:
    with session_scope() as s:
        return (s.query(ProviderUsage)
                .filter(ProviderUsage.provider == provider,
                        ProviderUsage.created_at >= _month_start()).count())


def provider_budget(provider: str) -> int:
    s = get_settings()
    return {
        "brave": s.brave_monthly_request_budget,
        "serpapi": s.serpapi_monthly_request_budget,
    }.get(provider, 0)


def budget_state(provider: str) -> dict:
    budget = provider_budget(provider)
    used = provider_request_count(provider)
    if budget <= 0:
        return {"provider": provider, "used": used, "budget": budget,
                "remaining": None, "ok": True, "warn": False, "pct": 0.0}
    pct = used / budget
    return {"provider": provider, "used": used, "budget": budget,
            "remaining": max(0, budget - used), "ok": used < budget,
            "warn": pct >= 0.8, "pct": round(pct, 3)}


def can_request(provider: str, needed: int = 1) -> bool:
    """True if `needed` more requests fit within the monthly budget (0 = no
    limit). Emits an 80% warning."""
    st = budget_state(provider)
    if st["budget"] <= 0:
        return True
    if st["warn"] and st["ok"]:
        log.warning("%s search budget at %.0f%% (%d/%d)", provider,
                    st["pct"] * 100, st["used"], st["budget"])
    return st["used"] + needed <= st["budget"]


def record_request(provider: str, query: str, country: str | None, page: int,
                   results: int, ok: bool = True, error: str | None = None) -> None:
    """Log one search request. Telemetry only — serialised + retrying, and it
    NEVER raises (a failed usage INSERT must not abort discovery)."""
    run_write(
        lambda s: s.add(ProviderUsage(
            provider=provider, query=query[:2000], country=country, page=page,
            results_returned=results, ok=ok, error=error)),
        swallow=True, label="provider_usage")


def ai_cost_this_month() -> float:
    with session_scope() as s:
        rows = s.query(AIUsage.estimated_cost_usd).filter(
            AIUsage.created_at >= _month_start()).all()
    return round(sum(r[0] or 0.0 for r in rows), 4)


def all_budget_states() -> dict:
    s = get_settings()
    return {
        "brave": budget_state("brave"),
        "serpapi": budget_state("serpapi"),
        "anthropic": {"used_usd": ai_cost_this_month(),
                      "budget_usd": s.anthropic_budget},
    }
