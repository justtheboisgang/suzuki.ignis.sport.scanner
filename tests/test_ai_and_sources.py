"""Tests for AI schema validation + graceful fallback, source classification
fallback, and status recheck logic."""

from datetime import datetime, timedelta, timezone

from src.ai.schemas import VehicleVerdict
from src.ai.source_hunter import assess_source
from src.ai.vehicle_detective import analyze_candidate
from src.classification.confidence import score_confidence
from src.classification.prefilter import prefilter
from src.models.enums import ListingStatus
from src.models.listing import Listing
from src.models.source import Source


def test_vehicle_verdict_schema_validation():
    v = VehicleVerdict(vehicle_match_confidence=94,
                       classification="LIKELY_IGNIS_SPORT")
    assert v.vehicle_match_confidence == 94
    # Out-of-range confidence must be rejected by the schema.
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        VehicleVerdict(vehicle_match_confidence=150, classification="x")


def test_vehicle_detective_fallback_without_key():
    text = "Suzuki Ignis 1.5 2004, 80 kW, Sportsitze"
    pf = prefilter(text, year=2004, power_kw=80, displacement_cc=1490)
    baseline = score_confidence(pf, year=2004, power_kw=80, displacement_cc=1490)
    verdict = analyze_candidate("Suzuki Ignis 1.5 2004", text, pf, baseline,
                                year=2004, power_kw=80, displacement_cc=1490)
    # No API key in tests -> deterministic fallback mirrors the baseline.
    assert verdict.is_fallback is True
    assert verdict.vehicle_match_confidence == baseline.confidence


def test_source_hunter_heuristic_fallback():
    a = assess_source("suzuki-autohaus-beispiel.de",
                      snippet="Ihr Suzuki Händler mit Gebrauchtwagen", country="DE")
    assert a.is_fallback is True
    assert a.handles_japanese is True
    assert a.is_relevant is True
    # Big aggregator gets a low discovery value.
    big = assess_source("autoscout24.de", snippet="used cars marketplace")
    assert big.discovery_value <= 40


def test_status_recheck_escalation(session, monkeypatch):
    # A listing not seen for a while should degrade, not jump straight to SOLD.
    old = datetime.now(timezone.utc) - timedelta(days=2)
    l = Listing(internal_id="z", listing_url="https://a.de/z",
                vehicle_match_confidence=90,
                listing_status=ListingStatus.ACTIVE.value)
    session.add(l)
    session.flush()
    l.last_seen_at = old
    session.commit()

    from src.pipeline import scan as scanmod
    # Point recheck at our session's DB (same engine) and run it.
    removed = scanmod.recheck_disappeared(miss_threshold=3)
    session.expire_all()
    refreshed = session.query(Listing).filter(Listing.internal_id == "z").one()
    assert refreshed.listing_status == ListingStatus.MAYBE_ACTIVE.value
    assert refreshed.consecutive_misses == 1
