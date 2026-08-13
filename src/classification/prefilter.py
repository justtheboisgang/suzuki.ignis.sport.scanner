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
NON_LISTING = "NON_LISTING"          # info/spec/tax/parts/tuning/wanted page
NORMAL_IGNIS = "NORMAL_IGNIS"
POSSIBLE_IGNIS_SPORT = "POSSIBLE_IGNIS_SPORT"
CLEAR_IGNIS_SPORT = "CLEAR_IGNIS_SPORT"
UNKNOWN = "UNKNOWN"

_CHASSIS = ("ht81s", "ht-81s", "ht 81s", "ht81")

# --- Non-listing detection (a page ABOUT the car, or a part FOR it) ---------
# Info-database / calculator pages (AutoUncle-style) whose title is a topic word
# followed by the model, e.g. "Kfz-Steuer Suzuki Ignis 1.5 Sport".
_NONLISTING_PREFIX = re.compile(
    r"^\s*(reifen|verbrauch|wertverlust|finanzierung|versicherung|"
    r"kfz[\s-]?steuer|kfz[\s-]?versicherung|leasing)\b", re.I)
# Parts / tuning / spec / wanted signals anywhere in the title.
_NONLISTING_ANY = (
    "für suzuki", "für ignis", "for suzuki ignis", "voor de suzuki", "per suzuki",
    "tieferlegung", "tieferlegungsfeder", "federsatz", "sportfeder",
    "sportfedersatz", "stoßdämpfer", "stossdämpfer", "vogtland", "eibach",
    "bilstein", "ersatzteil", "moteur ", "technische daten", "datenblatt",
    "fiche technique", "prova su strada",
)
_WANTED = re.compile(r"\b(suche|gesucht|ankauf|wtb)\b", re.I)


def is_non_listing_title(title: str) -> str | None:
    """Return a reason if the TITLE is an info/spec/tax/parts/tuning/wanted page
    rather than an individual vehicle for sale, else None."""
    raw = normalize_text(title)
    low = f" {raw.lower()} "
    if _NONLISTING_PREFIX.search(raw):
        return "info/calculator page"
    for term in _NONLISTING_ANY:
        if term in low:
            return f"part/spec page ({term.strip()})"
    if _WANTED.search(low) and ("suzuki" in low or "ignis" in low):
        return "wanted ad"
    return None


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
        return self.bucket not in (OTHER_MODEL, IRRELEVANT, NON_LISTING)


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


def prefilter(title: str, description: str = "", *,
              year: int | None = None, power_kw: int | None = None,
              power_hp: int | None = None,
              displacement_cc: int | None = None) -> PreFilterResult:
    """Classify a candidate with a TITLE-FIRST hard model gate.

    Vehicle IDENTITY (is it an Ignis? is it another model?) is decided from the
    TITLE alone — a (possibly contaminated) description can never establish Ignis
    identity nor upgrade another model. Only once the title confirms an Ignis do
    the description + technical data help decide Sport vs. base.
    """
    # ---- GATE 0: is this an individual vehicle at all, or a page ABOUT it /
    #      a part FOR it (tax/insurance/consumption/specs/tuning/wanted)? -----
    non_listing = is_non_listing_title(title)
    if non_listing:
        return PreFilterResult(
            bucket=NON_LISTING, is_ignis=False,
            negative_signals=[f"non_listing:{non_listing}"],
            reason=f"not an individual vehicle for sale — {non_listing}")

    t = f" {normalize_text(title).lower()} "
    d = f" {normalize_text(description).lower()} "
    full = t + d

    other_t = _match_terms(t, TARGET.other_models)     # other model IN TITLE
    ignis_t = _word(t, "ignis")                         # 'ignis' IN TITLE
    chassis_t = any(c in t for c in _CHASSIS)           # HT81S IN TITLE

    # Technical fingerprint present in the TITLE (used only for the ambiguous,
    # model-less path — never from the description, to avoid contamination).
    title_tech = any(x in t for x in (" 1.5", " 1,5", "1500", "1490", "80 kw",
                                      "80kw", "109 ps", "109ps", "109 hp",
                                      "109 cv", "109 pk", "m15a"))

    # ---- GATE 1: explicit OTHER Suzuki model in the TITLE ----------------
    if other_t and not ignis_t and not chassis_t:
        return PreFilterResult(
            bucket=OTHER_MODEL, is_ignis=False, other_model=other_t,
            negative_signals=[f"other_model:{other_t}"],
            reason=f"title names another Suzuki model: {other_t}")

    # ---- Chassis code in the TITLE = definitive Ignis Sport --------------
    if chassis_t:
        tech = _hits(full, TARGET.tech_hint_terms)
        return PreFilterResult(
            bucket=CLEAR_IGNIS_SPORT, is_ignis=True,
            positive_signals=["chassis:ht81s"], tech_hints=[f"tech:{x}" for x in tech],
            reason="HT81S chassis code in title")

    # ---- Stage A: is the TITLE an Ignis? --------------------------------
    if not ignis_t:
        # The title does not name Ignis. Identity must NOT come from the
        # description. Only a model-less title with strong tech IN THE TITLE
        # defers to AI; otherwise it is not our car.
        if not other_t and title_tech and TARGET.year_plausible(year):
            return PreFilterResult(
                bucket=UNKNOWN, is_ignis=False, needs_ai=True,
                reason="title model unclear but title tech may indicate Ignis")
        return PreFilterResult(
            bucket=IRRELEVANT, is_ignis=False, other_model=other_t,
            reason="title not identifiable as a Suzuki Ignis")

    # ---- Stage B: title IS an Ignis — Sport or base? --------------------
    # Now (and only now) the description may corroborate Sport signals.
    negatives = _hits(full, TARGET.negative_terms)
    tech = _hits(full, TARGET.tech_hint_terms)
    equip = _hits(full, TARGET.equipment_terms)
    chassis_ctx = any(c in d for c in _CHASSIS)
    strong_tech = any(x in full for x in (" 1.5", " 1,5", "1500", "1490",
                                          "80 kw", "80kw", "109 ps", "109ps",
                                          "109 hp", "109 cv", "109 pk", "m15a"))

    positive = ["name:ignis"]
    strong_name = _match_terms(t, TARGET.strong_name_terms) or \
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
