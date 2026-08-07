"""The target-vehicle profile: everything the system knows about the
Suzuki Ignis Sport (1st generation, chassis code HT81S).

This is the single source of truth for the deterministic pre-filter and the
prompts handed to the AI layer. Keep it factual — every technical figure here
is used to decide whether an ambiguous "Suzuki Ignis" listing is really a Sport.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IgnisSportProfile:
    make: str = "Suzuki"
    model: str = "Ignis"
    variant: str = "Sport"
    chassis_code: str = "HT81S"

    # Technical fingerprint of the HT81S Sport (used to unmask mislabelled cars).
    displacement_cc_min: int = 1450
    displacement_cc_max: int = 1500
    power_hp_min: int = 100          # ~109 PS, allow a margin for rounding/dyno
    power_hp_max: int = 118
    power_kw_min: int = 74           # ~80 kW
    power_kw_max: int = 86
    production_year_min: int = 2003
    production_year_max: int = 2008

    # Explicit model/variant name strings across languages & spellings.
    strong_name_terms: tuple[str, ...] = (
        "ignis sport",
        "ignis 1.5 sport",
        "ignis 1,5 sport",
        "ignis 1.5 vvt sport",
        "sport ignis",
        "ignis 1.5 4x2 sport",
        "ht81s",
    )

    # Chassis code — an almost-certain giveaway wherever it appears.
    chassis_terms: tuple[str, ...] = ("ht81s", "ht81", "ht-81s")

    # Weaker terms: a bare Ignis listing that must be inspected further.
    base_model_terms: tuple[str, ...] = ("suzuki ignis", "ignis")

    # Technical hints that push a bare-Ignis listing toward "Sport".
    tech_hint_terms: tuple[str, ...] = (
        "1.5",
        "1,5",
        "1500",
        "1490",
        "1490cc",
        "1.5 vvt",
        "1,5 vvt",
        "m15a",       # the 1.5 engine code
        "80 kw",
        "80kw",
        "109 ps",
        "109ps",
        "109 hp",
        "109 cv",
        "109 pk",
    )

    # Sport-specific equipment / body cues (multilingual, loose matching).
    equipment_terms: tuple[str, ...] = (
        "sportsitze", "sport seats", "sports seats", "recaro",
        "spoiler", "heckspoiler", "dachspoiler", "aleron", "becquet",
        "seitenschweller", "side skirts", "bodykit", "body kit", "kit carrocería",
        "sport stoßfänger", "sport bumper", "front bumper",
        "sportfelgen", "sport wheels", "leichtmetallfelgen 15",
        "sportfahrwerk", "sport suspension",
        "sport auspuff", "sport exhaust",
    )

    # Related vehicles used to *discover dealers* (not to match cars). A dealer
    # who sells these is a good candidate to one day list an Ignis Sport.
    related_vehicles: tuple[str, ...] = (
        "suzuki swift sport",
        "suzuki jimny",
        "suzuki cappuccino",
        "suzuki samurai",
        "suzuki sx4",
        "suzuki wagon r",
        "daihatsu yrv turbo",
        "daihatsu copen",
        "toyota yaris ts",
        "toyota yaris t sport",
        "honda jazz",
        "japanese hot hatch",
        "youngtimer japan",
        "jdm kei car",
    )

    negative_terms: tuple[str, ...] = field(
        default_factory=lambda: (
            # These signal the *new-generation* Ignis (2016+), not our HT81S.
            "shvs",
            "allgrip",
            "1.2 dualjet",
            "1.2 hybrid",
            "mild hybrid",
            "2017", "2018", "2019", "2020", "2021", "2022", "2023",
        )
    )

    def year_plausible(self, year: int | None) -> bool:
        if year is None:
            return True  # unknown ≠ disqualifying
        return self.production_year_min - 1 <= year <= self.production_year_max + 1

    def power_hp_plausible(self, hp: int | None) -> bool:
        if hp is None:
            return True
        return self.power_hp_min - 8 <= hp <= self.power_hp_max + 8

    def power_kw_plausible(self, kw: int | None) -> bool:
        if kw is None:
            return True
        return self.power_kw_min - 6 <= kw <= self.power_kw_max + 6

    def displacement_plausible(self, cc: int | None) -> bool:
        if cc is None:
            return True
        return self.displacement_cc_min - 60 <= cc <= self.displacement_cc_max + 60


TARGET = IgnisSportProfile()
