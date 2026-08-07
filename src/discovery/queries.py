"""Multilingual, family-based search-query generation.

Seven query families deliberately attack the problem from different angles — the
mislabelled-vehicle and chassis-code families are the highest-value long-tail
weapons. Queries are emitted per country in the local language (+ English +
language-independent chassis code). High-value families are marked so the
engine can paginate deeper for them while spending less on generic ones.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..config.countries import COUNTRIES, Country
from ..config.target import TARGET

# Big platforms we deliberately exclude in FAMILY D to surface small originals.
BIG_PLATFORMS = ("autoscout24", "mobile", "theparking", "autouncle",
                 "leboncoin", "marktplaats")

_MODEL_PHRASES = (
    "Suzuki Ignis Sport",
    "Suzuki Ignis 1.5 Sport",
    "Suzuki Ignis 1.5 VVT Sport",
    "Suzuki Ignis 80 kW",
    "Suzuki Ignis 109 PS",
)
_CHASSIS = ("HT81S", "Suzuki Ignis HT81S")

# FAMILY B — the mislabelled-Sport hunt. Power expressed the local way.
_POWER_TOKENS = ("80 kW", "109 PS", "109 cv", "109 ch", "109 pk", "109 hp", "109 cavalli")
_MISLABEL_BASES = ("Suzuki Ignis 1.5", "Suzuki Ignis 1,5", "Ignis 1.5 VVT")

# FAMILY C — dealer inventory vocabulary (multilingual, loose).
_DEALER_WORDS = ("dealer", "stock", "garage", "concessionnaire", "Händler",
                 "autosalone", "concesionario", "komis", "autobazar")

# FAMILY F — file / feed discovery.
_FILE_WORDS = ("pdf", "xml", "rss", "inventory", "stock", "occasion", "used cars",
               "fahrzeugbestand")

# FAMILY G — enthusiast community.
_COMMUNITY_TEMPLATES = ("Ignis Sport forum", "HT81S forum", "Ignis Sport club",
                        "Ignis Sport marketplace", "Suzuki owners club Ignis Sport")


@dataclass
class GeneratedQuery:
    query: str
    country: str
    language: str
    family: str            # A..G
    high_value: bool = False
    experimental: bool = False


def _fam_A(c: Country) -> list[str]:
    out = []
    for phrase in _MODEL_PHRASES:
        out.append(f"{phrase} {c.for_sale[0]}")
        out.append(f"{phrase} {c.used[0]}")
    for phrase in _MODEL_PHRASES[:2]:
        out.append(f"{phrase} for sale")
    return out


def _fam_B(c: Country) -> list[str]:
    out = []
    for base in _MISLABEL_BASES:
        out.append(f"{base} {c.used[0]}")
    for tok in _POWER_TOKENS:
        out.append(f"Suzuki Ignis {tok} {c.used[0]}")
        out.append(f"Ignis {tok}")
    return out


def _fam_C(c: Country) -> list[str]:
    out = [f"Suzuki Ignis Sport {c.dealer[0]}"]
    for w in _DEALER_WORDS:
        out.append(f"Suzuki Ignis Sport {w}")
    return out


def _fam_D(c: Country, provider: str = "generic") -> list[str]:
    # Provider-specific exclusion syntax. Google/SerpApi supports -site:; Brave
    # honours plain -term exclusions. We emit a broadly-compatible form.
    if provider == "serpapi" or provider == "google_cse":
        excl = " ".join(f"-site:{p}" for p in ("autoscout24.de", "mobile.de",
                                               "theparking.eu", "autouncle.com"))
    else:
        excl = " ".join(f"-{p}" for p in BIG_PLATFORMS[:4])
    return [f'"Suzuki Ignis Sport" {excl}',
            f'Suzuki Ignis 1.5 {c.used[0]} {excl}']


def _fam_E(c: Country) -> list[str]:
    verbs = list(dict.fromkeys([c.for_sale[0], c.used[0], "sale"]))
    out = [f"{code} {v}" for code in _CHASSIS for v in verbs]
    out.append("HT81S")
    return out


def _fam_F(c: Country) -> list[str]:
    out = []
    for w in _FILE_WORDS:
        out.append(f"Suzuki Ignis Sport {w}")
    out.append(f"Suzuki Ignis Sport filetype:pdf")
    return out


def _fam_G(c: Country) -> list[str]:
    out = list(_COMMUNITY_TEMPLATES)
    out.append(f"Suzuki Ignis Sport {c.dealer[0]} forum")
    return out


_HIGH_VALUE_FAMILIES = {"A", "B", "E"}


def generate_queries(
    countries: list[str] | None = None,
    families: str = "ABCDEFG",
    provider: str = "generic",
    experimental_ratio: float = 0.2,
    seed: int | None = None,
) -> list[GeneratedQuery]:
    """Generate a de-duplicated list across countries and the requested query
    families. ~`experimental_ratio` of FAMILY B/D/F/G items are flagged
    experimental so the system keeps exploring instead of repeating."""
    rng = random.Random(seed)
    codes = countries or list(COUNTRIES.keys())
    builders = {"A": _fam_A, "B": _fam_B, "C": _fam_C,
                "D": lambda c: _fam_D(c, provider),
                "E": _fam_E, "F": _fam_F, "G": _fam_G}
    out: list[GeneratedQuery] = []
    seen: set[tuple[str, str]] = set()

    for code in codes:
        c = COUNTRIES.get(code.upper())
        if not c:
            continue
        for fam in families:
            build = builders.get(fam)
            if not build:
                continue
            queries = build(c)
            n_exp = max(1, int(len(queries) * experimental_ratio))
            rng.shuffle(queries)
            for i, q in enumerate(queries):
                key = (q.lower(), code)
                if key in seen:
                    continue
                seen.add(key)
                out.append(GeneratedQuery(
                    query=q, country=code, language=c.language, family=fam,
                    high_value=fam in _HIGH_VALUE_FAMILIES,
                    experimental=(fam in "BDFG" and i < n_exp),
                ))
    return out
