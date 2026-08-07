"""Multilingual search-query generation.

For every country we emit queries in the local language, English and (where
useful) German, plus language-independent chassis/technical queries. A share of
each run is intentionally EXPERIMENTAL to avoid a static search bubble.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..config.countries import COUNTRIES, Country
from ..config.target import TARGET

# Core model phrasings that are worth searching everywhere.
_MODEL_PHRASES = (
    "Suzuki Ignis Sport",
    "Suzuki Ignis 1.5 Sport",
    "Suzuki Ignis 1.5 VVT Sport",
    "Suzuki Ignis 80 kW",
    "Suzuki Ignis 109 PS",
    "Ignis HT81S",
    "Suzuki HT81S",
)

# Language-independent, high-signal.
_CHASSIS_PHRASES = ("HT81S", "Suzuki Ignis HT81S")


@dataclass
class GeneratedQuery:
    query: str
    country: str
    language: str
    experimental: bool = False


def _dealer_directory_queries(c: Country) -> list[str]:
    """Queries aimed at discovering *dealers* (long-tail), not single cars."""
    out = []
    for rel in TARGET.related_vehicles[:6]:
        for word in (c.for_sale[0], c.used[0]):
            out.append(f"{rel} {word}")
    out.append(f"Suzuki {c.dealer[0]}")
    out.append(f"suzuki händler {c.name_en}" if c.language != "de"
               else f"suzuki {c.dealer[0]}")
    return out


def _model_queries(c: Country) -> list[str]:
    out: list[str] = []
    local_sale = c.for_sale[0]
    local_used = c.used[0]
    for phrase in _MODEL_PHRASES:
        out.append(f"{phrase} {local_sale}")
        out.append(f"{phrase} {local_used}")
    # English variants everywhere (imports, expat sellers).
    for phrase in _MODEL_PHRASES[:3]:
        out.append(f"{phrase} for sale")
    # Chassis / technical (language independent).
    for phrase in _CHASSIS_PHRASES:
        out.append(phrase)
    out.append(f'"Suzuki Ignis Sport" site:{c.tld}')
    # The "mislabelled Sport" hunt: bare Ignis + tech spec.
    out.append(f"Suzuki Ignis 1.5 {local_used}")
    out.append(f"Suzuki Ignis 80 kW {local_used}")
    return out


_EXPERIMENTAL_TEMPLATES = (
    '"Suzuki Ignis Sport" -autoscout -mobile',
    'Suzuki Ignis 1.5 VVT {used}',
    'Ignis Sport {dealer}',
    'Suzuki Ignis {used} youngtimer',
    'Suzuki Ignis Sport {region}',
    'Suzuki Ignis 109 {sale}',
)


def _experimental_queries(c: Country) -> list[str]:
    out = []
    for tmpl in _EXPERIMENTAL_TEMPLATES:
        out.append(tmpl.format(
            used=c.used[0], dealer=c.dealer[0], sale=c.for_sale[0],
            region=c.name_en,
        ))
    return out


def generate_queries(
    countries: list[str] | None = None,
    experimental_ratio: float = 0.2,
    include_dealer_discovery: bool = True,
    seed: int | None = None,
) -> list[GeneratedQuery]:
    """Produce a de-duplicated list of GeneratedQuery across the given
    countries (all by default). ~`experimental_ratio` of them are flagged
    experimental so the system keeps exploring."""
    rng = random.Random(seed)
    codes = countries or list(COUNTRIES.keys())
    queries: list[GeneratedQuery] = []
    seen: set[tuple[str, str]] = set()

    for code in codes:
        c = COUNTRIES.get(code.upper())
        if not c:
            continue
        core = _model_queries(c)
        if include_dealer_discovery:
            core += _dealer_directory_queries(c)
        for q in core:
            key = (q.lower(), code)
            if key in seen:
                continue
            seen.add(key)
            queries.append(GeneratedQuery(q, code, c.language, experimental=False))

        # Experimental slice.
        n_exp = max(1, int(len(core) * experimental_ratio))
        exp = _experimental_queries(c)
        rng.shuffle(exp)
        for q in exp[:n_exp]:
            key = (q.lower(), code)
            if key in seen:
                continue
            seen.add(key)
            queries.append(GeneratedQuery(q, code, c.language, experimental=True))

    return queries
