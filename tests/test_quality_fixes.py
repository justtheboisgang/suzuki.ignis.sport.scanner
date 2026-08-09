"""Regression tests for the Phase-17c quality fixes: price sanity, opportunity
contamination, the uncertain-band query, budget accounting and reclassification
of already-stored false positives."""

from __future__ import annotations

from src.dashboard.queries import filter_listings
from src.discovery import budget
from src.models.listing import Listing
from src.parsers.normalize import price_sanity
from src.pipeline.reclassify import reclassify_listings, reclassify_sources
from src.models.source import Source
from src.scoring.opportunity import compute_opportunity


# --- Price sanity (P5) -----------------------------------------------------
def test_price_one_euro_is_suspect():
    price, status = price_sanity(1.0, "Suzuki Ignis Sport, mooie auto")
    assert status == "SUSPECT" and price is None


def test_real_price_ok():
    price, status = price_sanity(4500.0, "Prijs 4.500 euro")
    assert status == "OK" and price == 4500.0


def test_price_on_request_is_unknown():
    price, status = price_sanity(1.0, "Prijs op aanvraag")
    assert status == "UNKNOWN" and price is None
    price2, status2 = price_sanity(None, "Preis auf Anfrage")
    assert status2 == "UNKNOWN" and price2 is None


# --- Opportunity contamination (P6) ---------------------------------------
def test_opportunity_zero_for_non_ignis():
    l = Listing(internal_id="a", listing_url="https://x/1",
                vehicle_match_confidence=0)
    assert compute_opportunity(l).score == 0


def test_opportunity_ignores_suspect_price():
    good = Listing(internal_id="b", listing_url="https://x/2",
                   vehicle_match_confidence=85, price_eur=4200.0,
                   price_parse_status="OK", mileage_km=95000)
    suspect = Listing(internal_id="c", listing_url="https://x/3",
                      vehicle_match_confidence=85, price_eur=1.0,
                      price_parse_status="SUSPECT", mileage_km=95000)
    s_good = compute_opportunity(good).score
    s_suspect = compute_opportunity(suspect).score
    # The €1 placeholder must NOT be rewarded as a bargain over a real price.
    assert s_suspect <= s_good
    assert compute_opportunity(suspect).breakdown.get("price_vs_market", 0) == 0.0


# --- Uncertain band query (P7) --------------------------------------------
def test_uncertain_query_excludes_high_confidence(session):
    session.add_all([
        Listing(internal_id="u50", listing_url="https://x/50",
                vehicle_match_confidence=50),
        Listing(internal_id="u80", listing_url="https://x/80",
                vehicle_match_confidence=80),
    ])
    session.commit()
    rows = filter_listings(session, min_confidence=40, max_confidence=59)
    ids = {r.internal_id for r in rows}
    assert "u50" in ids and "u80" not in ids


# --- Budget accounting (P9) -----------------------------------------------
def test_few_requests_do_not_exhaust_budget(session):
    # Simulate ~27 successful Brave requests against a 1000 budget.
    for _ in range(27):
        budget.record_request("brave", "q", "DE", 1, 20, ok=True)
    # A few failed/429 rows must NOT count toward the guard.
    for _ in range(5):
        budget.record_request("brave", "q", "DE", 1, 0, ok=False, error="429")
    st = budget.budget_state("brave")
    assert st["used"] == 27          # only successful requests
    assert st["ok"] is True
    assert budget.can_request("brave", 1) is True


# --- Reclassify existing false positives (P4/P10) --------------------------
def test_reclassify_demotes_stored_other_model(session):
    session.add(Listing(internal_id="wagonr", listing_url="https://x/w",
                        title="Suzuki Wagon R", vehicle_match_confidence=80,
                        classification="LIKELY_IGNIS_SPORT",
                        opportunity_score=89, price_eur=1.0,
                        price_parse_status="OK"))
    session.commit()
    reclassify_listings()
    session.expire_all()
    lst = session.query(Listing).filter(Listing.internal_id == "wagonr").one()
    assert lst.vehicle_match_confidence == 0
    assert lst.classification == "NOT_IGNIS"
    assert lst.opportunity_score == 0
    assert lst.price_eur is None  # €1 placeholder cleaned


def test_reclassify_deactivates_non_vehicle_sources(session):
    session.add_all([
        Source(domain="dieversicherer.de", source_type="dealer",
               base_url="https://dieversicherer.de/", active=True,
               discovery_value=100, discovery_method="search:brave"),
        Source(domain="skn-tuning.de", source_type="dealer",
               base_url="https://skn-tuning.de/", active=True,
               discovery_value=90, discovery_method="search:brave"),
    ])
    session.commit()
    reclassify_sources()
    session.expire_all()
    for dom in ("dieversicherer.de", "skn-tuning.de"):
        s = session.query(Source).filter(Source.domain == dom).one()
        assert s.active is False
        assert s.source_category == "DISCOVERY_ONLY_SOURCE"
        assert s.discovery_value <= 25
