"""Deterministic Ignis / Ignis Sport classification. Runs on every candidate so
that Claude only ever sees the genuinely ambiguous few."""

from .prefilter import PreFilterResult, prefilter
from .confidence import score_confidence, ConfidenceResult

__all__ = [
    "PreFilterResult",
    "prefilter",
    "score_confidence",
    "ConfidenceResult",
]
