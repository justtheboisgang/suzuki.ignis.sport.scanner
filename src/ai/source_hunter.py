"""AI Module 2 — Source Hunter.

Given a newly discovered domain (and a snippet of its content or a search
result), decide whether it's a place an Ignis Sport could ever be sold, classify
it, and recommend how to crawl it. This is about *finding new places to look* —
not about grading individual cars.
"""

from __future__ import annotations

import re

from ..utils.hashing import domain_of
from .client import get_client
from .schemas import SourceAssessment

_SYSTEM = """You classify websites for a system that hunts rare Suzuki Ignis
Sport cars across Europe, prioritising long-tail sources (small dealers, local
garages, Suzuki dealers, youngtimer/JDM specialists, classifieds, forums,
clubs). Given a domain and a content/search snippet, decide if it is relevant
and how to crawl it. Favour sources that sell used cars, Suzuki, or Japanese
vehicles. Assign a high `discovery_value` to small/obscure dealer sites and a
LOW discovery_value to giant aggregators everyone already watches. Recommend a
parser from: html_generic, jsonld, feed. Only set requires_browser if the site
clearly needs JavaScript to show inventory."""

# Deterministic keyword heuristics used both as a pre-signal and as fallback.
_TYPE_KEYWORDS = {
    "suzuki_dealer": ("suzuki",),
    "youngtimer_dealer": ("youngtimer", "oldtimer", "classic", "klassiker"),
    "forum": ("forum", "board", "community"),
    "club": ("club", "verein", "clube"),
    "classifieds": ("kleinanzeigen", "annonces", "annunci", "anuncios",
                    "marktplaats", "ogloszenia", "classified", "bazar"),
    "dealer": ("autohaus", "garage", "motors", "automobile", "cars", "auto",
               "voiture", "occasion", "gebrauchtwagen", "dealer"),
}


def _heuristic(domain: str, text: str) -> SourceAssessment:
    blob = f"{domain} {text}".lower()
    stype = "other"
    for t, kws in _TYPE_KEYWORDS.items():
        if any(k in blob for k in kws):
            stype = t
            break
    sells = stype in {"dealer", "suzuki_dealer", "youngtimer_dealer",
                      "classifieds"} or bool(re.search(r"\b(km|kw|ps|€|eur)\b", blob))
    is_suzuki = "suzuki" in blob
    japanese = any(k in blob for k in ("suzuki", "toyota", "daihatsu", "honda",
                                       "mazda", "nissan", "subaru", "jdm"))
    # Big aggregators = low discovery value.
    big = any(k in domain for k in ("autoscout", "mobile.de", "theparking",
                                    "autouncle", "ebay", "leboncoin", "marktplaats"))
    dv = 25 if big else (75 if stype in {"suzuki_dealer", "youngtimer_dealer",
                                         "dealer"} else 55)
    return SourceAssessment(
        is_relevant=sells or is_suzuki or stype != "other",
        source_type=stype,
        sells_vehicles=sells,
        is_suzuki_dealer=is_suzuki and stype == "suzuki_dealer",
        handles_japanese=japanese,
        automatable=True,
        recommended_parser="html_generic",
        requires_browser=False,
        discovery_value=dv,
        priority=60 if stype in {"suzuki_dealer", "youngtimer_dealer"} else 45,
        reasoning="Heuristic classification (AI unavailable)",
        is_fallback=True,
    )


def assess_source(domain: str, snippet: str = "", country: str | None = None,
                  ) -> SourceAssessment:
    domain = domain_of(domain) or domain
    client = get_client()
    user = (f"Domain: {domain}\nCountry hint: {country or 'unknown'}\n"
            f"Content/search snippet:\n{snippet[:1500]}")
    result = client.structured(
        module="source_hunter",
        system=_SYSTEM,
        user=user,
        schema=SourceAssessment,
        target_type="source",
        target_id=domain,
        model=client.settings.anthropic_model_fast,  # cheap triage
    )
    if result is not None:
        if not result.country:
            result.country = country
        return result
    fb = _heuristic(domain, snippet)
    fb.country = country
    return fb
