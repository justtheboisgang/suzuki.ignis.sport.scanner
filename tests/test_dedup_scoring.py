"""Tests for image-hash distance, dedup key signals and opportunity scoring."""

from src.deduplication.dedup import dedup_key_signals
from src.deduplication.imagehashing import hamming, hashes_similar
from src.models.listing import Listing
from src.scoring.opportunity import compute_opportunity


def test_hamming_and_similarity():
    assert hamming("ffffffffffffffff", "ffffffffffffffff") == 0
    assert hamming("0000000000000000", "ffffffffffffffff") == 64
    assert hashes_similar(["ffffffffffffffff"], ["fffffffffffffffe"], threshold=2)
    assert not hashes_similar(["0000000000000000"], ["ffffffffffffffff"], threshold=8)


def test_dedup_key_signals_bands():
    l = Listing(internal_id="x", listing_url="https://a.de/1",
                price_eur=4950.0, mileage_km=118000,
                seller_phone_public="+49 170 1234567", vin_public="abc123")
    sig = dedup_key_signals(l)
    assert sig["vin"] == "ABC123"
    assert sig["phone"] == "701234567"
    assert sig["price_band"] == round(4950 / 300)
    assert sig["mileage_band"] == round(118000 / 5000)


def test_opportunity_score_is_explainable():
    l = Listing(internal_id="x", listing_url="https://a.de/1",
                vehicle_match_confidence=90, price_eur=4200.0, mileage_km=95000,
                seller_type="dealer", seller_website="https://a.de/",
                lhd_rhd="LHD")
    res = compute_opportunity(l)
    assert 0 <= res.score <= 100
    assert "match_confidence" in res.breakdown
    assert "price_vs_market" in res.breakdown
    # A clean, cheap, low-mileage LHD dealer car should score well.
    assert res.score >= 60


def test_opportunity_penalises_risks():
    l = Listing(internal_id="y", listing_url="https://a.de/2",
                vehicle_match_confidence=85, price_eur=5500.0, mileage_km=130000)
    clean = compute_opportunity(l).score
    risky = compute_opportunity(l, {"rust_mentioned": True,
                                    "accident_mentioned": True,
                                    "engine_issues": True}).score
    assert risky < clean
