"""Claude intelligence layer.

Claude is used ONLY where semantic judgement adds real value — never for work
deterministic code does better (parsing, hashing, dedup, scheduling). Every
module here degrades to a deterministic fallback when no API key is configured,
so the whole system runs without AI.
"""

from .client import ClaudeClient, get_client
from .schemas import (
    VehicleVerdict,
    SourceAssessment,
    ListingAnalysis,
    ImageVerdict,
    VisionVerdict,
    ParserDiagnosis,
)
from .vision import (
    analyze_vehicle_images,
    should_run_vision,
    select_vision_images,
    merge_vision,
    vision_cache_key,
)
from .vehicle_detective import analyze_candidate
from .source_hunter import assess_source
from .listing_analyst import analyze_listing
from .image_detective import analyze_images
from .diagnostic import diagnose_parser

__all__ = [
    "ClaudeClient",
    "get_client",
    "VehicleVerdict",
    "SourceAssessment",
    "ListingAnalysis",
    "ImageVerdict",
    "ParserDiagnosis",
    "analyze_candidate",
    "assess_source",
    "analyze_listing",
    "analyze_images",
    "diagnose_parser",
    "VisionVerdict",
    "analyze_vehicle_images",
    "should_run_vision",
    "select_vision_images",
    "merge_vision",
    "vision_cache_key",
]
