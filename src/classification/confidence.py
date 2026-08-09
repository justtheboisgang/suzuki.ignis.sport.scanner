"""Vehicle Match Confidence (0-100) plus classification band.

A vehicle that failed the Model Identity Gate (is_ignis == False) always scores
0 / NOT_IGNIS — an explicit other Suzuki model can never be an Ignis-Sport
candidate. An Ignis that is clearly not a Sport maps to NORMAL_IGNIS, never to a
Sport classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config.target import TARGET
from ..models.enums import Classification
from .prefilter import PreFilterResult


@dataclass
class ConfidenceResult:
    confidence: int
    classification: str
    band: str
    reasons: list[str] = field(default_factory=list)


def _band(conf: int) -> str:
    if conf >= 95:
        return "practically certain"
    if conf >= 80:
        return "very likely"
    if conf >= 60:
        return "interesting candidate"
    if conf >= 40:
        return "uncertain"
    return "probably not a Sport"


def score_confidence(pf: PreFilterResult, *, year: int | None = None,
                     power_kw: int | None = None, power_hp: int | None = None,
                     displacement_cc: int | None = None) -> ConfidenceResult:
    # Hard gate: not an Ignis → 0, NOT_IGNIS. (Other model / irrelevant / the
    # UNKNOWN bucket where the model wasn't confirmed.)
    if not pf.is_ignis:
        cls = (Classification.NOT_IGNIS.value if pf.bucket in ("OTHER_MODEL",
               "IRRELEVANT") else Classification.UNCERTAIN.value)
        reason = pf.reason or "not a Suzuki Ignis"
        # UNKNOWN (model unclear, tech suggests Ignis) gets a small, sub-threshold
        # score so it surfaces for AI but never as a confident hit.
        conf = 30 if pf.bucket == "UNKNOWN" else 0
        return ConfidenceResult(conf, cls, _band(conf), [reason])

    conf = 20
    reasons = ["Base: identified as a Suzuki Ignis (+20)"]

    chassis = any(s.startswith("chassis:") for s in pf.positive_signals)
    strong_name = any(s.startswith("name:") and s != "name:ignis"
                      for s in pf.positive_signals)
    equip = [s for s in pf.positive_signals if s.startswith("equip:")]

    if chassis:
        conf += 65
        reasons.append("HT81S chassis code present (+65)")
    if strong_name:
        conf += 60
        reasons.append("Explicit 'Ignis Sport' naming (+60)")
    if equip:
        add = min(20, 6 * len(equip))
        conf += add
        reasons.append(f"Sport equipment cues {', '.join(e[6:] for e in equip)} (+{add})")

    if power_kw is not None or power_hp is not None:
        if TARGET.power_kw_plausible(power_kw) and TARGET.power_hp_plausible(power_hp):
            conf += 12
            reasons.append("Power ~80 kW / ~109 hp matches (+12)")
        else:
            conf -= 25
            reasons.append("Power inconsistent with Sport (-25)")
    if displacement_cc is not None:
        if displacement_cc >= 1450 and TARGET.displacement_plausible(displacement_cc):
            conf += 10
            reasons.append("1.5 l displacement matches (+10)")
        elif displacement_cc < 1300:
            conf -= 20
            reasons.append("Small engine (<1.3 l) — likely base Ignis (-20)")

    if pf.tech_hints and not chassis and not strong_name:
        conf += 8
        reasons.append("Technical hints suggest Sport spec (+8)")

    if pf.negative_signals:
        conf -= 30
        reasons.append(f"Negative signals: {', '.join(pf.negative_signals)} (-30)")
    if year is not None and year >= 2015:
        conf -= 30
        reasons.append("Model year ≥ 2015 → new-generation Ignis (-30)")
    if year is not None and not TARGET.year_plausible(year):
        conf -= 10
        reasons.append("Year outside HT81S production window (-10)")

    conf = max(0, min(100, conf))

    if conf >= 95:
        cls = Classification.CONFIRMED_IGNIS_SPORT
    elif conf >= 80:
        cls = Classification.LIKELY_IGNIS_SPORT
    elif conf >= 60:
        cls = Classification.POSSIBLE_IGNIS_SPORT
    elif conf >= 40:
        cls = Classification.UNCERTAIN
    else:
        # It IS an Ignis, just not a Sport.
        cls = Classification.NORMAL_IGNIS

    return ConfidenceResult(conf, cls.value, _band(conf), reasons)
