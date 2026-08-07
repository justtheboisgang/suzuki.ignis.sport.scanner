"""Heuristic left-/right-hand-drive determination. Country of origin alone is
NOT sufficient — we combine it with explicit textual cues and always report a
confidence so uncertainty is visible."""

from __future__ import annotations

RHD_COUNTRIES = {"GB", "IE", "CY", "MT"}

_RHD_CUES = (
    "rhd", "right hand drive", "right-hand drive", "rechtslenker",
    "conduite à droite", "guida a destra", "volante a la derecha",
)
_LHD_CUES = (
    "lhd", "left hand drive", "left-hand drive", "linkslenker",
    "conduite à gauche", "guida a sinistra", "volante a la izquierda",
)


def infer_lhd_rhd(text: str | None, country: str | None) -> tuple[str, int]:
    """Return (value, confidence 0-100). value ∈ {LHD, RHD, UNKNOWN}."""
    t = (text or "").lower()

    for cue in _RHD_CUES:
        if cue in t:
            return "RHD", 90
    for cue in _LHD_CUES:
        if cue in t:
            return "LHD", 90

    if country:
        c = country.upper()
        if c in RHD_COUNTRIES:
            return "RHD", 55  # likely, but imports exist
        return "LHD", 55
    return "UNKNOWN", 0
