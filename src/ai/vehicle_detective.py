"""AI Module 1 — Vehicle Detective.

Receives ONLY ambiguous candidates (pre-filter bucket == NEEDS_AI). Decides
whether a bare / mislabelled "Suzuki Ignis" listing is really an Ignis Sport,
citing concrete signals and refusing to invent facts.
"""

from __future__ import annotations

from ..classification.confidence import ConfidenceResult
from ..classification.prefilter import PreFilterResult
from ..config.target import TARGET
from .client import get_client
from .schemas import VehicleVerdict

_SYSTEM = f"""You are an expert on the first-generation Suzuki Ignis Sport
(chassis code {TARGET.chassis_code}). Facts about the target car:
- 1.5 litre M15A engine, ~{TARGET.power_kw_min}-{TARGET.power_kw_max} kW
  (~109 PS/hp), produced roughly {TARGET.production_year_min}-{TARGET.production_year_max}.
- Sport-only cues: sport seats, front/rear spoiler, side skirts, sport bumpers,
  15" sport alloys, body kit. The base Ignis has a 1.3 engine and none of these.
- The 2016+ Ignis is a DIFFERENT car (mild hybrid / AllGrip) and is NOT a match.

You judge whether an AMBIGUOUS listing is really an Ignis Sport. Use ONLY the
evidence given. Never invent specs. If information is missing, say so in
`uncertainties`. Prefer a moderate confidence when evidence is thin."""


def analyze_candidate(
    title: str,
    description: str,
    pf: PreFilterResult,
    baseline: ConfidenceResult,
    year: int | None = None,
    power_kw: int | None = None,
    power_hp: int | None = None,
    displacement_cc: int | None = None,
    target_id: str | None = None,
) -> VehicleVerdict:
    """Return a VehicleVerdict. Falls back to the deterministic baseline when
    AI is unavailable."""
    client = get_client()
    facts = [
        f"Title: {title}",
        f"Description: {description[:1500] or '(none)'}",
        f"Parsed year: {year}",
        f"Parsed power: {power_kw} kW / {power_hp} hp",
        f"Parsed displacement: {displacement_cc} cc",
        f"Deterministic positive signals: {pf.positive_signals}",
        f"Deterministic technical hints: {pf.tech_hints}",
        f"Deterministic negative signals: {pf.negative_signals}",
        f"Deterministic baseline confidence: {baseline.confidence}",
    ]
    user = ("Assess whether this is a Suzuki Ignis Sport (HT81S).\n\n"
            + "\n".join(facts))

    result = client.structured(
        module="vehicle_detective",
        system=_SYSTEM,
        user=user,
        schema=VehicleVerdict,
        target_type="listing",
        target_id=target_id,
    )
    if result is not None:
        return result

    # Deterministic fallback mirrors the confidence scorer.
    return VehicleVerdict(
        vehicle_match_confidence=baseline.confidence,
        classification=baseline.classification,
        positive_signals=[s for s in pf.positive_signals],
        negative_signals=[s for s in pf.negative_signals],
        uncertainties=["AI layer unavailable — deterministic estimate only"],
        reasoning_summary=f"Deterministic baseline ({baseline.band}): "
                          + "; ".join(baseline.reasons[:4]),
        is_fallback=True,
    )
