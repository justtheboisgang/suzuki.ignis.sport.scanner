"""Thin, safe wrapper around the Anthropic Messages API.

Responsibilities:
  * Load the key from the environment ONLY (never hardcoded, never logged).
  * Enforce a monthly USD budget guard.
  * Cache identical requests (same module + model + prompt hash) so unchanged
    listings/sources are never re-billed.
  * Log every call to the `ai_usage` table with token counts and an estimated
    cost.
  * Return validated Pydantic objects via a small "return_json" tool contract,
    with bounded retries.
  * Degrade gracefully: if the SDK/key is missing or the budget is exhausted,
    `structured()` returns None and callers fall back to deterministic logic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from ..config.settings import CACHE_DIR, get_settings
from ..database.base import session_scope
from ..models.ai_usage import AIUsage
from ..utils.hashing import content_hash
from ..utils.logging import get_logger

log = get_logger("ai.client")

T = TypeVar("T", bound=BaseModel)

# Rough public per-MTok pricing (USD) for cost *estimation* only. Kept as a
# lookup so it can be tuned without touching logic. Unknown models use a
# conservative default.
_PRICING = {
    "opus": (15.0, 75.0),
    "sonnet": (3.0, 15.0),
    "haiku": (0.80, 4.0),
    "fable": (3.0, 15.0),
}
_DEFAULT_PRICE = (5.0, 15.0)


def _price_for(model: str) -> tuple[float, float]:
    m = model.lower()
    for key, price in _PRICING.items():
        if key in m:
            return price
    return _DEFAULT_PRICE


def _estimate_cost(model: str, in_tok: int, out_tok: int) -> float:
    pin, pout = _price_for(model)
    return round(in_tok / 1_000_000 * pin + out_tok / 1_000_000 * pout, 6)


class ClaudeClient:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._client = None
        self._cache_dir: Path = CACHE_DIR / "ai"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        if self.settings.ai_enabled:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
            except Exception as exc:  # pragma: no cover
                log.warning("Anthropic SDK unavailable, AI disabled: %s", type(exc).__name__)
                self._client = None

    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _month_spend(self) -> float:
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0,
                                                    second=0, microsecond=0)
        with session_scope() as s:
            rows = s.query(AIUsage.estimated_cost_usd).filter(
                AIUsage.created_at >= start).all()
        return round(sum(r[0] or 0.0 for r in rows), 4)

    def _budget_ok(self) -> bool:
        budget = self.settings.ai_monthly_budget_usd
        if budget <= 0:
            return True
        spend = self._month_spend()
        if spend >= budget:
            log.warning("AI monthly budget reached (%.2f/%.2f USD) — using fallback",
                        spend, budget)
            return False
        return True

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _cache_get(self, key: str) -> dict | None:
        p = self._cache_path(key)
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def _cache_put(self, key: str, data: dict) -> None:
        try:
            self._cache_path(key).write_text(json.dumps(data), "utf-8")
        except OSError:
            pass

    def _log_usage(self, module, model, in_tok, out_tok, cost, cache_hit,
                   target_type, target_id, ok=True, error=None):
        # Telemetry only: serialised + retrying write that never raises, so a
        # locked DB can never abort an AI-assisted discovery/scan step.
        from ..database.writer import run_write
        run_write(
            lambda s: s.add(AIUsage(
                module=module, model=model, input_tokens=in_tok,
                output_tokens=out_tok, estimated_cost_usd=cost,
                cache_hit=cache_hit, target_type=target_type,
                target_id=str(target_id) if target_id is not None else None,
                ok=ok, error=error)),
            swallow=True, label="ai_usage")

    # ------------------------------------------------------------------ #
    def structured(
        self,
        *,
        module: str,
        system: str,
        user: str,
        schema: Type[T],
        target_type: str | None = None,
        target_id: str | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        images: list[dict] | None = None,
        retries: int = 2,
    ) -> T | None:
        """Call Claude and return a validated `schema` instance, or None to
        signal the caller to use its deterministic fallback."""
        if not self.enabled:
            return None

        model = model or self.settings.anthropic_model
        cache_key = content_hash(module, model, system, user,
                                 json.dumps(images or [], sort_keys=True))

        cached = self._cache_get(cache_key)
        if cached is not None:
            self._log_usage(module, model, 0, 0, 0.0, True, target_type, target_id)
            try:
                return schema.model_validate(cached)
            except ValidationError:
                pass  # fall through and recompute

        if not self._budget_ok():
            return None

        # Enforce JSON via a single tool the model must call.
        tool = {
            "name": "return_result",
            "description": f"Return the structured result for {module}.",
            "input_schema": schema.model_json_schema(),
        }
        content: list[dict] = []
        for img in images or []:
            content.append(img)
        content.append({"type": "text", "text": user})

        last_err = None
        for attempt in range(retries + 1):
            try:
                resp = self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": "return_result"},
                    messages=[{"role": "user", "content": content}],
                )
            except Exception as exc:  # network / API error
                last_err = str(exc)
                log.warning("AI call failed (%s) attempt %d: %s",
                            module, attempt, type(exc).__name__)
                continue

            in_tok = getattr(resp.usage, "input_tokens", 0)
            out_tok = getattr(resp.usage, "output_tokens", 0)
            cost = _estimate_cost(model, in_tok, out_tok)

            payload = None
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    payload = block.input
                    break
            if payload is None:
                last_err = "no tool_use block"
                continue
            try:
                obj = schema.model_validate(payload)
            except ValidationError as ve:
                last_err = f"validation: {ve}"
                self._log_usage(module, model, in_tok, out_tok, cost, False,
                                target_type, target_id, ok=False, error=last_err)
                continue

            self._cache_put(cache_key, payload)
            self._log_usage(module, model, in_tok, out_tok, cost, False,
                            target_type, target_id, ok=True)
            return obj

        self._log_usage(module, model, 0, 0, 0.0, False, target_type, target_id,
                        ok=False, error=last_err)
        return None


@lru_cache(maxsize=1)
def get_client() -> ClaudeClient:
    return ClaudeClient()
