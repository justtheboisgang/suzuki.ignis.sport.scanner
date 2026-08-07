"""AI Module 3 — Listing Analyst.

For a serious Ignis Sport hit, extract condition/risk information and produce a
short English summary (translating foreign descriptions internally). Only runs
on genuine hits, and only when the description content hash has changed.
"""

from __future__ import annotations

from ..parsers.normalize import detect_language
from .client import get_client
from .schemas import ListingAnalysis

_SYSTEM = """You analyse a used-car listing for a Suzuki Ignis Sport buyer.
Read the (possibly non-English) description and extract condition, maintenance,
damage, rust, accident hints, engine/gearbox issues, modifications, import
status, service history, inspection/MOT/TÜV validity, owner count and seller
type. Translate the essence into concise English. Report ONLY what the text
supports — do not speculate beyond it. Fill unknowns with null/false rather than
guessing."""


def analyze_listing(title: str, description: str, country: str | None = None,
                    seller_type: str = "unknown", target_id: str | None = None,
                    ) -> ListingAnalysis:
    client = get_client()
    lang = detect_language(description) or "unknown"
    user = (f"Country: {country or 'unknown'} | Detected language: {lang} | "
            f"Seller type hint: {seller_type}\n"
            f"Title: {title}\n\nDescription:\n{description[:3000] or '(none)'}")
    result = client.structured(
        module="listing_analyst",
        system=_SYSTEM,
        user=user,
        schema=ListingAnalysis,
        target_type="listing",
        target_id=target_id,
        max_tokens=1200,
    )
    if result is not None:
        return result

    # Deterministic fallback: keyword scan, no translation.
    blob = f"{title} {description}".lower()
    def has(*words):
        return any(w in blob for w in words)
    return ListingAnalysis(
        condition_summary="AI unavailable — keyword-only summary.",
        translated_summary=(description or "")[:280],
        rust_mentioned=has("rost", "rust", "rouille", "ruggine", "óxido"),
        accident_mentioned=has("unfall", "accident", "incidente", "accidente", "schaden"),
        engine_issues=has("motorschaden", "engine problem", "motor kaputt"),
        transmission_issues=has("getriebe", "gearbox", "transmission problem"),
        modifications=has("tuning", "umbau", "modified", "getunt"),
        imported=has("import", "importiert", "importe"),
        service_history_present=has("scheckheft", "service history", "carnet",
                                    "tagliandi", "historique"),
        seller_type=seller_type,
        is_fallback=True,
    )
