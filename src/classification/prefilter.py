"""Deterministic classification gate.

Two hard rules fixed here (Phase 17c):
  1. MODEL IDENTITY GATE — if the vehicle's OWN identity text (title/description)
     names another Suzuki model (Swift, Jimny, Vitara, Wagon R, …) and is not an
     Ignis, it is rejected outright (confidence 0, NOT_IGNIS). The word "Sport"
     alone never produces an Ignis-Sport score.
  2. IGNIS FIRST, SPORT SECOND — Stage A decides "is this an Ignis at all?";
     only then does Stage B decide "is it the Sport / HT81S?".

Crucially, model identity is judged on the LISTING'S OWN text, never on
surrounding page context (which caused false positives like "Suzuki Wagon R"
scoring 80 because an Ignis Sport was listed elsewhere on the same page).

Buckets: OTHER_MODEL / IRRELEVANT / NORMAL_IGNIS / POSSIBLE_IGNIS_SPORT /
CLEAR_IGNIS_SPORT / UNKNOWN.  Only POSSIBLE_IGNIS_SPORT and UNKNOWN go to AI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config.target import TARGET
from ..parsers.normalize import normalize_text

# Bucket constants
OTHER_MODEL = "OTHER_MODEL"
IRRELEVANT = "IRRELEVANT"
NORMAL_IGNIS = "NORMAL_IGNIS"
POSSIBLE_IGNIS_SPORT = "POSSIBLE_IGNIS_SPORT"
CLEAR_IGNIS_SPORT = "CLEAR_IGNIS_SPORT"
UNKNOWN = "UNKNOWN"

_CHASSIS = ("ht81s", "ht-81s", "ht 81s", "ht81")


@dataclass
class PreFilterResult:
    bucket: str
    is_ignis: bool
    other_model: str | None = None
    positive_signals: list[str] = field(default_factory=list)
    negative_signals: list[str] = field(default_factory=list)
    tech_hints: list[str] = field(default_factory=list)
    needs_ai: bool = False
    reason: str = ""

    @property
    def relevant(self) -> bool:
        return self.bucket not in (OTHER_MODEL, IRRELEVANT)


def _word(text: str, term: str) -> bool:
    """Whole-word / code match (handles multi-word terms and codes like sx4)."""
    pat = r"(?<![a-z0-9])" + re.escape(term).replace(r"\ ", r"[\s-]?") + r"(?![a-z0-9])"
    return re.search(pat, text) is not None


def _match_terms(text: str, terms) -> str | None:
    for t in terms:
        if _word(text, t):
            return t
    return None


def _hits(text: str, terms) -> list[str]:
    return [t for t in terms if t in text]


def prefilter(identity_text: str, context_text: str = "", *,
              year: int | None = None, power_kw: int | None = None,
              power_hp: int | None = None,
              displacement_cc: int | None = None) -> PreFilterResult:
    """Classify a candidate. `identity_text` is the vehicle's own title(+desc);
    `context_text` is surrounding page context used only for weak corroboration.
    """
    identity = f" {normalize_text(identity_text).lower()} "
    context = f" {normalize_text(context_text).lower()} "
    full = identity + context

    other = _match_terms(identity, TARGET.other_models)
    ignis_id = _word(identity, "ignis")
    chassis_id = any(c in identity for c in _CHASSIS)
    chassis_ctx = any(c in context for c in _CHASSIS)

    negatives = _hits(full, TARGET.negative_terms)
    tech = _hits(full, TARGET.tech_hint_terms)
    equip = _hits(full, TARGET.equipment_terms)

    # ---- GATE 1: explicit OTHER Suzuki model that is not an Ignis --------
    if other and not ignis_id and not chassis_id:
        return PreFilterResult(
            bucket=OTHER_MODEL, is_ignis=False, other_model=other,
            negative_signals=[f"other_model:{other}"],
            reason=f"identity names another Suzuki model: {other}")

    # ---- Chassis code in the identity = definitive Ignis Sport -----------
    if chassis_id:
        return PreFilterResult(
            bucket=CLEAR_IGNIS_SPORT, is_ignis=True,
            positive_signals=["chassis:ht81s"], tech_hints=[f"tech:{t}" for t in tech],
            reason="HT81S chassis code in identity")

    # ---- GATE 2 (Stage A): is this an Ignis at all? ----------------------
    strong_tech = any(x in full for x in (" 1.5", " 1,5", "1500", "1490",
                                          "80 kw", "80kw", "109 ps", "109ps",
                                          "109 hp", "109 cv", "109 pk", "m15a"))
    if not ignis_id:
        # Model not named in identity. Only if NO other model is named AND
        # strong technical evidence suggests an Ignis do we defer to AI.
        if not other and strong_tech and TARGET.year_plausible(year):
            return PreFilterResult(
                bucket=UNKNOWN, is_ignis=False, needs_ai=True,
                tech_hints=[f"tech:{t}" for t in tech],
                reason="model unclear but technical data may indicate Ignis")
        return PreFilterResult(
            bucket=IRRELEVANT, is_ignis=False,
            other_model=other,
            reason="not identifiable as a Suzuki Ignis")

    # ---- Stage B: it IS an Ignis — is it the Sport? ----------------------
    positive = ["name:ignis"]
    strong_name = _match_terms(identity, TARGET.strong_name_terms) or \
        _match_terms(full, ("ignis sport", "ignis 1.5 sport", "ignis 1,5 sport",
                            "ignis 1.5 vvt sport", "sport ignis"))
    new_gen = bool(negatives) or (year is not None and year >= 2015)

    if strong_name and not new_gen:
        positive.append(f"name:{strong_name}")
        return PreFilterResult(
            bucket=CLEAR_IGNIS_SPORT, is_ignis=True, positive_signals=positive,
            tech_hints=[f"tech:{t}" for t in tech], reason="explicit Ignis Sport naming")

    # Sport signal strength from equipment + technical fingerprint.
    strength = len(equip)
    if strong_tech:
        strength += 2
    if (power_kw is not None or power_hp is not None) and \
            TARGET.power_kw_plausible(power_kw) and TARGET.power_hp_plausible(power_hp) \
            and not new_gen:
        strength += 1
    if displacement_cc and displacement_cc >= 1450 and TARGET.displacement_plausible(displacement_cc):
        strength += 1
    if chassis_ctx:
        strength += 1  # weak corroboration from page context

    positive += [f"equip:{e}" for e in equip]
    tech_hints = [f"tech:{t}" for t in tech]

    if new_gen and strength == 0:
        return PreFilterResult(
            bucket=NORMAL_IGNIS, is_ignis=True, positive_signals=positive,
            negative_signals=[f"neg:{n}" for n in negatives], tech_hints=tech_hints,
            reason="new-generation / non-Sport Ignis")

    if strength >= 1 and TARGET.year_plausible(year):
        return PreFilterResult(
            bucket=POSSIBLE_IGNIS_SPORT, is_ignis=True, needs_ai=True,
            positive_signals=positive, negative_signals=[f"neg:{n}" for n in negatives],
            tech_hints=tech_hints, reason="Ignis with Sport-ish signals — needs AI")

    return PreFilterResult(
        bucket=NORMAL_IGNIS, is_ignis=True, positive_signals=positive,
        negative_signals=[f"neg:{n}" for n in negatives], tech_hints=tech_hints,
        reason="plain Ignis, no Sport signals")
