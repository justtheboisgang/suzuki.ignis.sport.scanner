"""Pydantic schemas for every structured Claude response. Claude output is
always validated against one of these; invalid output triggers a bounded retry
and, failing that, a deterministic fallback. No critical data path consumes
free-form model text."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VehicleVerdict(BaseModel):
    """AI Vehicle Detective output — is this ambiguous listing an Ignis Sport?"""

    vehicle_match_confidence: int = Field(ge=0, le=100)
    classification: str  # LIKELY_IGNIS_SPORT / UNCERTAIN / LIKELY_NOT_SPORT ...
    positive_signals: list[str] = Field(default_factory=list)
    negative_signals: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    is_fallback: bool = False


class SourceAssessment(BaseModel):
    """AI Source Hunter output — is this newly found domain a place an Ignis
    Sport could be sold, and how do we crawl it?"""

    is_relevant: bool = False
    source_type: str = "other"
    country: str | None = None
    language: str | None = None
    sells_vehicles: bool = False
    is_suzuki_dealer: bool = False
    handles_japanese: bool = False
    automatable: bool = True
    recommended_parser: str = "html_generic"
    requires_browser: bool = False
    discovery_value: int = Field(default=50, ge=0, le=100)
    priority: int = Field(default=50, ge=0, le=100)
    reasoning: str = ""
    is_fallback: bool = False


class ListingAnalysis(BaseModel):
    """AI Listing Analyst output — condition/risks summary for a serious hit."""

    condition_summary: str = ""
    translated_summary: str = ""
    rust_mentioned: bool = False
    accident_mentioned: bool = False
    engine_issues: bool = False
    transmission_issues: bool = False
    modifications: bool = False
    imported: bool = False
    service_history_present: bool = False
    inspection_valid: str | None = None
    owner_count: int | None = None
    seller_type: str = "unknown"
    notable_claims: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    positives: list[str] = Field(default_factory=list)
    is_fallback: bool = False


class ImageVerdict(BaseModel):
    """AI Image Vehicle Detective — visual Sport cues. An *indicator*, never
    the sole source of truth."""

    likely_sport: bool = False
    visual_confidence: int = Field(default=0, ge=0, le=100)
    features_detected: list[str] = Field(default_factory=list)
    notes: str = ""
    is_fallback: bool = False


class ParserDiagnosis(BaseModel):
    """AI Source/Parser Diagnostic — why did a working source return nothing?"""

    likely_cause: str = "unknown"
    is_blocked: bool = False
    structure_changed: bool = False
    suggested_action: str = ""
    suggested_selector_hint: str | None = None
    confidence: int = Field(default=0, ge=0, le=100)
    is_fallback: bool = False
