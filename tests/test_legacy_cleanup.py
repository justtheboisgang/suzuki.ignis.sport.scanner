"""Regression tests for legacy contamination cleanup, the shared valid-candidate
predicate, budget counting and original-currency price drops."""

from __future__ import annotations

from src.classification.confidence import score_confidence
from src.classification.prefilter import prefilter
from src.dashboard.queries import filter_listings, home_stats
from src.discovery import budget
from src.models.enums import Classification, ListingStatus
from src.models.listing import Listing
from src.parsers.html_generic import RawListing
from src.pipeline.ingest import process_candidate
from src.pipeline.reclassify import reclassify_listings
from src.database.writer import write_session


# --- P1: TITLE-FIRST hard model gate (contaminated description ignored) -----
def test_title_bus_with_ignis_description_is_not_ignis():
    pf = prefilter("Suzuki Bus",
                   "... Suzuki Ignis Sport HT81S ... Suzuki Jimny ... 1.5 80 kW ...",
                   year=2005, power_kw=80, displacement_cc=1490)
    cr = score_confidence(pf)
    assert pf.is_ignis is False
    assert cr.confidence == 0
    assert cr.classification == Classification.NOT_IGNIS.value


def test_title_swift_sport_with_ignis_description_is_not_ignis():
    pf = prefilter("Suzuki Swift Sport 1.6", "contains Ignis Sport somewhere")
    cr = score_confidence(pf)
    assert pf.bucket == "OTHER_MODEL"
    assert cr.confidence == 0
    assert cr.classification == Classification.NOT_IGNIS.value


def test_title_ignis_still_uses_description_for_sport():
    # A genuine Ignis title may still be lifted to Sport by description/tech.
    pf = prefilter("Suzuki Ignis", "1.5 VVT, 80 kW, Sportsitze, Heckspoiler",
                   year=2005, power_kw=80, displacement_cc=1490)
    assert pf.is_ignis is True
    assert pf.bucket in ("POSSIBLE_IGNIS_SPORT", "CLEAR_IGNIS_SPORT")


# --- P3: existing DB cleanup via reclassification ---------------------------
def test_reclassify_demotes_legacy_bus_with_contaminated_description(session):
    with write_session() as s:
        s.add(Listing(
            internal_id="legacybus", title="Suzuki Bus",
            description_original="Suzuki Ignis Sport 1.5 80 kW ... Suzuki Jimny ...",
            vehicle_match_confidence=80, classification="LIKELY_IGNIS_SPORT",
            opportunity_score=89, listing_status="ACTIVE",
            listing_url="https://mp.nl/l/auto-s/?q=suzuki+bus",
            canonical_listing_url="https://mp.nl/l/auto-s/?q=suzuki+bus",
            listing_url_quality="UNKNOWN"))
    reclassify_listings()
    with write_session() as s:
        l = s.query(Listing).filter(Listing.internal_id == "legacybus").one()
        assert l.vehicle_match_confidence == 0
        assert l.classification == Classification.NOT_IGNIS.value
        assert l.opportunity_score == 0
        assert all(x.internal_id != "legacybus"
                   for x in filter_listings(s, min_confidence=0))


# --- P2/P4: shared valid-candidate predicate (tables AND counters) ----------
def _add(session, **kw):
    base = dict(listing_status="ACTIVE", listing_url_quality="EXACT_DETAIL",
                classification="LIKELY_IGNIS_SPORT",
                listing_url="https://d.de/auto/x", canonical_listing_url="https://d.de/auto/x")
    base.update(kw)
    session.add(Listing(**base))


def test_dashboard_hides_not_ignis_and_unresolved(session):
    with write_session() as s:
        _add(s, internal_id="good", title="Suzuki Ignis Sport",
             vehicle_match_confidence=80)
        _add(s, internal_id="notignis", title="Suzuki Wagon R",
             vehicle_match_confidence=80, classification=Classification.NOT_IGNIS.value)
        _add(s, internal_id="unresolved", title="Suzuki Ignis Sport",
             vehicle_match_confidence=80, listing_status=ListingStatus.UNRESOLVED.value,
             listing_url_quality="SEARCH_PAGE",
             listing_url="https://mp.nl/l/?q=ignis",
             canonical_listing_url="https://mp.nl/l/?q=ignis")
    with write_session() as s:
        shown = {l.internal_id for l in filter_listings(s, min_confidence=60)}
        assert "good" in shown
        assert "notignis" not in shown
        assert "unresolved" not in shown
        stats = home_stats(s)
        # Only the one genuine candidate counts as active ≥60.
        assert stats["total_active"] == 1


# --- P5/P6: budget counts only successful requests --------------------------
def test_budget_counts_only_successful(session, monkeypatch):
    monkeypatch.setattr(budget.get_settings(), "brave_monthly_request_budget", 1000,
                        raising=False)
    for _ in range(10):
        budget.record_request("brave", "q", "DE", 1, 20, ok=True)
    for _ in range(20):
        budget.record_request("brave", "q", "DE", 1, 0, ok=False, error="429")
    st = budget.budget_state("brave")
    assert st["used"] == 10
    assert st["budget"] == 1000
    assert st["remaining"] == 990


# --- P7: price drops in original currency -----------------------------------
def _seen(session_unused, url, price, currency):
    return RawListing(title="Suzuki Ignis Sport", url=url, source_domain="d.pl",
                      description="1.5 VVT 80 kW HT81S", price=price,
                      currency=currency, vin="JSPLN0000000001")


def test_fx_only_change_is_not_a_price_drop(session):
    from src.models.history import PriceHistory
    process_candidate(_seen(session, "https://d.pl/auto/ignis-1", 15000, "PLN"), None)
    # Same PLN amount seen again (EUR value would differ only via FX).
    process_candidate(_seen(session, "https://d.pl/auto/ignis-1", 15000, "PLN"), None)
    with write_session() as s:
        assert s.query(PriceHistory).count() == 0


def test_real_original_price_drop_is_recorded(session):
    from src.models.history import PriceHistory
    process_candidate(_seen(session, "https://d.pl/auto/ignis-2", 15000, "PLN"), None)
    process_candidate(_seen(session, "https://d.pl/auto/ignis-2", 14000, "PLN"), None)
    with write_session() as s:
        assert s.query(PriceHistory).count() == 1
