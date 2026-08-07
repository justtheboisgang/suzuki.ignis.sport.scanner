"""The cheap, deterministic gate that decides what happens to each candidate.

Buckets (mirrors the funnel in the spec):
  * IRRELEVANT          — not even an Ignis; drop.
  * CLEAR_SPORT         — explicit Sport / HT81S naming; no AI needed.
  * CLEAR_BASE          — plainly a non-Sport Ignis; store but low priority.
  * NEEDS_AI            — a bare / mislabelled Ignis with Sport-ish signals;
                          THIS is the only bucket forwarded to Claude.

Finding a Sport that was mislabelled as a plain "Suzuki Ignis 1.5" is the whole
point, so the NEEDS_AI trigger is deliberately generous about technical hints
while staying cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config.target import TARGET
from ..parsers.normalize import normalize_text


@dataclass
class PreFilterResult:
    bucket: str                       # IRRELEVANT / CLEAR_SPORT / CLEAR_BASE / NEEDS_AI
    is_ignis: bool
    positive_signals: list[str] = field(default_factory=list)
    negative_signals: list[str] = field(default_factory=list)
    tech_hints: list[str] = field(default_factory=list)
    needs_ai: bool = False

    @property
    def relevant(self) -> bool:
        return self.bucket != "IRRELEVANT"


def _hits(text: str, terms) -> list[str]:
    return [t for t in terms if t in text]


def prefilter(
    text: str,
    year: int | None = None,
    power_kw: int | None = None,
    power_hp: int | None = None,
    displacement_cc: int | None = None,
) -> PreFilterResult:
    """Classify a candidate purely from text + parsed numeric fields."""
    t = f" {normalize_text(text).lower()} "

    chassis = _hits(t, [f" {c} " for c in TARGET.chassis_terms]) or _hits(
        t, list(TARGET.chassis_terms)
    )
    strong = _hits(t, TARGET.strong_name_terms)
    base = _hits(t, TARGET.base_model_terms)
    equipment = _hits(t, TARGET.equipment_terms)
    tech = _hits(t, TARGET.tech_hint_terms)
    negatives = _hits(t, TARGET.negative_terms)

    positive: list[str] = []
    positive += [f"chassis:{c.strip()}" for c in chassis]
    positive += [f"name:{s}" for s in strong]
    positive += [f"equip:{e}" for e in equipment]
    tech_hints = [f"tech:{x}" for x in tech]

    is_ignis = bool(base or strong or chassis)

    # Not an Ignis at all → drop early (unless a chassis code slipped through).
    if not is_ignis and not chassis:
        return PreFilterResult(
            bucket="IRRELEVANT", is_ignis=False,
            positive_signals=positive, negative_signals=list(negatives),
            tech_hints=tech_hints,
        )

    # Numeric plausibility against the HT81S fingerprint.
    year_ok = TARGET.year_plausible(year)
    power_ok = TARGET.power_kw_plausible(power_kw) and TARGET.power_hp_plausible(power_hp)
    disp_ok = TARGET.displacement_plausible(displacement_cc)
    has_power = power_kw is not None or power_hp is not None

    # The new-gen Ignis (2016+) or explicit hybrid tags disqualify a Sport.
    new_gen = bool(negatives) or (year is not None and year >= 2015)

    # Explicit Sport / chassis naming → we're confident without AI.
    if (strong or chassis) and not new_gen:
        return PreFilterResult(
            bucket="CLEAR_SPORT", is_ignis=True,
            positive_signals=positive, negative_signals=list(negatives),
            tech_hints=tech_hints,
        )

    # From here we have a bare "Ignis" listing. Do the technical data hint Sport?
    sport_signal_strength = 0
    if equipment:
        sport_signal_strength += len(equipment)
    # 1.5 engine / ~80 kW / ~109 hp are the strongest unmaskers.
    strong_tech = any(x in t for x in (" 1.5", " 1,5", "1500", "1490",
                                       "80 kw", "80kw", "109 ps", "109ps",
                                       "109 hp", "109 cv", "109 pk", "m15a"))
    if strong_tech:
        sport_signal_strength += 2
    if has_power and power_ok and not new_gen:
        sport_signal_strength += 1
    if disp_ok and displacement_cc and displacement_cc >= 1450:
        sport_signal_strength += 1

    # New-gen / clearly-not-Sport bare Ignis: keep as base, low value.
    if new_gen and sport_signal_strength == 0:
        return PreFilterResult(
            bucket="CLEAR_BASE", is_ignis=True,
            positive_signals=positive, negative_signals=list(negatives),
            tech_hints=tech_hints,
        )

    # Ambiguous but with Sport-ish signals → hand to the AI detective.
    if sport_signal_strength >= 1 and year_ok:
        return PreFilterResult(
            bucket="NEEDS_AI", is_ignis=True, needs_ai=True,
            positive_signals=positive, negative_signals=list(negatives),
            tech_hints=tech_hints,
        )

    # A plain old-gen Ignis with nothing pointing to Sport.
    return PreFilterResult(
        bucket="CLEAR_BASE", is_ignis=True,
        positive_signals=positive, negative_signals=list(negatives),
        tech_hints=tech_hints,
    )
