"""End-to-end pipeline tests on the real ingest code (no network): a realistic
dealer candidate becomes a scored, de-duplicated, history-tracked listing."""

from src.models.enums import ListingStatus
from src.models.history import PriceHistory
from src.models.listing import Listing
from src.models.source import Source
from src.parsers.html_generic import RawListing
from src.pipeline.ingest import process_candidate


def _dealer(session):
    src = Source(domain="autohaus-mueller.de", name="Autohaus Müller",
                 country="DE", source_type="dealer",
                 base_url="https://autohaus-mueller.de/", discovery_value=75)
    session.add(src)
    # Commit so process_candidate's separate write session (a different
    # connection) can see the source row (FK + counter updates).
    session.commit()
    return src


def _sport_raw(url="https://autohaus-mueller.de/f/ignis-sport-1"):
    return RawListing(
        title="Suzuki Ignis Sport 1.5 VVT",
        url=url, source_domain="autohaus-mueller.de",
        description="HT81S, 80 kW, Sportsitze, Spoiler, Scheckheft",
        price=4900.0, currency="EUR", mileage_km=118000, year=2005,
        power_kw=80, displacement_cc=1490, images=[], country="DE",
    )


def test_ingest_creates_scored_listing(session):
    src = _dealer(session)
    res = process_candidate(_sport_raw(), src)
    assert res.is_new and res.is_ignis and res.is_sport_candidate
    lst = session.query(Listing).one()
    assert lst.vehicle_match_confidence >= 80
    assert lst.opportunity_score is not None
    assert lst.chassis_code == "HT81S"
    assert lst.original_listing_url  # dealer's own URL kept as canonical
    assert lst.lhd_rhd in ("LHD", "RHD", "UNKNOWN")


def test_irrelevant_candidate_dropped(session):
    src = _dealer(session)
    raw = RawListing(title="VW Golf GTI", url="https://x.de/golf",
                     source_domain="autohaus-mueller.de")
    res = process_candidate(raw, src)
    assert res.listing is None
    assert session.query(Listing).count() == 0


def test_price_change_recorded(session):
    src = _dealer(session)
    process_candidate(_sport_raw(), src)
    # Same car, lower price, seen again.
    cheaper = _sport_raw()
    cheaper.price = 4500.0
    process_candidate(cheaper, src)
    assert session.query(Listing).count() == 1
    ph = session.query(PriceHistory).all()
    assert len(ph) == 1
    assert ph[0].delta_eur == -400.0


def test_cross_platform_dedup_by_vin(session):
    src = _dealer(session)
    a = _sport_raw("https://autohaus-mueller.de/f/ignis-1")
    a.vin = "JSAFHX51S00123456"
    process_candidate(a, src)
    # Same VIN on a different marketplace URL -> must merge into ONE listing.
    b = _sport_raw("https://autoscout24.de/angebot/xyz")
    b.vin = "JSAFHX51S00123456"
    b.source_domain = "autoscout24.de"
    res = process_candidate(b, src)
    assert res.duplicate_merged
    assert session.query(Listing).count() == 1
    lst = session.query(Listing).one()
    assert any("autoscout24.de" in u for u in (lst.alternate_urls or []))
