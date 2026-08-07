"""AI Module 5 — Source / Parser Diagnostic.

When a source that normally returns N listings suddenly returns 0, this suggests
a probable cause (block, layout change, dead page) and a repair action. Claude
NEVER edits production code directly — it only proposes; a human/test gate
applies any change.
"""

from __future__ import annotations

from .client import get_client
from .schemas import ParserDiagnosis

_SYSTEM = """You diagnose why a web scraper that previously extracted car
listings from a page suddenly extracts none. Given the HTTP status, a snippet of
the returned HTML, and the typical vs current result counts, identify the most
likely cause (access block / rate limit, page structure change, empty inventory,
wrong URL) and suggest a concrete next action. You only DIAGNOSE and PROPOSE —
you do not modify code."""


def _heuristic(status: int, html: str, typical: int, current: int) -> ParserDiagnosis:
    low = html.lower()
    blocked = status in (401, 403, 429) or any(
        k in low for k in ("captcha", "access denied", "are you a robot",
                           "cloudflare", "rate limit"))
    empty = any(k in low for k in ("keine ergebnisse", "no results",
                                   "0 fahrzeuge", "aucun résultat"))
    if blocked:
        cause = "access_block_or_rate_limit"
        action = "Back off, increase crawl delay, respect robots; do not bypass."
    elif empty:
        cause = "empty_inventory"
        action = "Likely genuinely no matching stock; keep source, recheck later."
    elif current == 0 and typical > 0:
        cause = "possible_structure_change"
        action = "Inspect selectors; the page layout may have changed."
    else:
        cause = "unknown"
        action = "Manual review recommended."
    return ParserDiagnosis(
        likely_cause=cause, is_blocked=blocked,
        structure_changed=(cause == "possible_structure_change"),
        suggested_action=action, confidence=55, is_fallback=True,
    )


def diagnose_parser(status: int, html: str, typical: int, current: int,
                    domain: str | None = None) -> ParserDiagnosis:
    client = get_client()
    user = (f"Domain: {domain}\nHTTP status: {status}\n"
            f"Typical result count: {typical}\nCurrent result count: {current}\n"
            f"HTML snippet:\n{html[:2500]}")
    result = client.structured(
        module="parser_diagnostic",
        system=_SYSTEM,
        user=user,
        schema=ParserDiagnosis,
        target_type="source",
        target_id=domain,
        model=client.settings.anthropic_model_fast,
    )
    return result or _heuristic(status, html, typical, current)
