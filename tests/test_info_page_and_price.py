"""Regression tests for the real dashboard failures reported in production:

  1. INFO / SPEC / TAX / TUNING-PARTS / WANTED pages were matching as candidates
     ("Kfz-Steuer Suzuki Ignis 1.5 Sport", "Wertverlust Suzuki Ignis 1.5 Sport",
     "Vogtland Tieferlegungsfedern für SUZUKI IGNIS", "H&R Sportfedersatz für
     SUZUKI IGNIS", "Suzuki Ignis Sport 1.5 Stoßdämpfer hinten Suche",
     "Moteur Ignis Hatchback"). These are pages ABOUT the car or parts FOR it,
     never an individual vehicle for sale, and must score 0 / NOT_IGNIS.

  2. PRICE parsing: a 4-digit YEAR mistaken for a price (€2.002 / €2.003 /
     €2.005 for 2002/2003/2005 cars) and implausibly high figures (€55,411 /
     €92,600 / €162,499 — part / index numbers) must be dropped as SUSPECT,
     while real HT81S-class prices survive.

  3. A genuine Ignis Sport listing must STILL pass after all the above.
"""

import pytest

from src.classification.confidence import score_confidence
from src.classification.prefilter import (
    CLEAR_IGNIS_SPORT,
    NON_LISTING,
    POSSIBLE_IGNIS_SPORT,
    is_non_listing_title,
    prefilter,
)
from src.models.enums import Classification
from src.parsers.normalize import price_sanity


def _run(title, description="", **kw):
    pf = prefilter(title, description, **kw)
    cr = score_confidence(pf, **kw)
    return pf, cr


# --- 1. Non-listing (info / spec / tax / parts / tuning / wanted) pages ------
@pytest.mark.parametrize("title", [
    "Kfz-Steuer Suzuki Ignis 1.5 Sport",
    "Kfz-Versicherung Suzuki Ignis 1.5 Sport",
    "Wertverlust Suzuki Ignis 1.5 Sport",
    "Verbrauch Suzuki Ignis 1.5 Sport",
    "Finanzierung Suzuki Ignis 1.5 Sport",
    "Reifen Suzuki Ignis 1.5 Sport",
    "Versicherung Suzuki Ignis 1.5 Sport",
    "Leasing Suzuki Ignis 1.5 Sport",
])
def test_info_calculator_pages_are_non_listing(title):
    pf, cr = _run(title, year=2005)
    assert pf.bucket == NON_LISTING
    assert pf.is_ignis is False
    assert cr.confidence == 0
    assert cr.classification == Classification.NOT_IGNIS.value


@pytest.mark.parametrize("title", [
    "Vogtland Tieferlegungsfedern für SUZUKI IGNIS",
    "H&R Sportfedersatz für SUZUKI IGNIS",
    "Eibach Federn für Suzuki Ignis",
    "Bilstein Stoßdämpfer für Suzuki Ignis",
    "Sportfahrwerk für Suzuki Ignis 1.5",
    "Moteur Ignis Hatchback",
    "Technische Daten Suzuki Ignis 1.5 Sport",
    "Suzuki Ignis 1.5 Sport - Fiche technique",
])
def test_parts_and_spec_pages_are_non_listing(title):
    pf, cr = _run(title, year=2005)
    assert pf.bucket == NON_LISTING
    assert cr.confidence == 0
    assert cr.classification == Classification.NOT_IGNIS.value


@pytest.mark.parametrize("title", [
    "Suzuki Ignis Sport 1.5 Stoßdämpfer hinten Suche",
    "Suzuki Ignis Sport gesucht",
    "Ankauf Suzuki Ignis Sport",
])
def test_wanted_ads_are_non_listing(title):
    pf, cr = _run(title)
    assert pf.bucket == NON_LISTING
    assert cr.confidence == 0
    assert cr.classification == Classification.NOT_IGNIS.value


# Spec-catalog / datasheet pages (cars-data / AutoUncle / autoevolution) that
# leaked into the dashboard as "Uncertain" candidates with year-as-price.
@pytest.mark.parametrize("title", [
    "Suzuki Ignis 1.5 Club Four Grip | 73 kW/99 PS | Baujahre 2003 - 2005",
    "Suzuki Ignis 1.5 Comfort | 73 kW/99 PS | Baujahre 2003 - 2006",
    "Suzuki Ignis I FH 1.5 i 16V Sport (109 Hp)",
    "Suzuki Ignis I MH 1.5 i 16V (99 Hp) Automatic 2003",
    "Suzuki Ignis I MH 1.5 i 16V (99 Hp) 4WD 2003",
    "Ignis (Hatchback)",
])
def test_spec_catalog_pages_are_non_listing(title):
    pf, cr = _run(title, year=2003)
    assert pf.bucket == NON_LISTING
    assert cr.confidence == 0
    assert cr.classification == Classification.NOT_IGNIS.value


def test_real_ad_with_parenthetical_power_is_not_catalog():
    # A genuine ad may write "80 kW (109 PS)" — must NOT be mistaken for a
    # cars-data catalog entry (those use the "Ignis I MH/FH" generation code).
    assert is_non_listing_title("Suzuki Ignis Sport 1.5 80 kW (109 PS)") is None
    pf, cr = _run("Suzuki Ignis Sport 1.5 80 kW (109 PS)", year=2005)
    assert pf.is_ignis is True
    assert pf.bucket == CLEAR_IGNIS_SPORT


def test_is_non_listing_title_helper_returns_none_for_real_listing():
    assert is_non_listing_title("Suzuki Ignis Sport 1.5 109ps Recaro TÜV Neu") is None
    assert is_non_listing_title("Suzuki Ignis 1,5 von einem Rentner gefahren") is None


# --- 2. Price sanity: year-as-price and implausible figures ------------------
@pytest.mark.parametrize("amount,year", [
    (2002, 2002),
    (2003, 2003),
    (2005, 2005),
])
def test_year_as_price_is_suspect(amount, year):
    price, status = price_sanity(float(amount), "Suzuki Ignis 1.5 Sport",
                                 year=year, price_original=float(amount))
    assert status == "SUSPECT"
    assert price is None


def test_bare_year_price_without_context_is_suspect():
    # No year / no mileage, a bare "2003" reads as a spec-DB year, not a price.
    price, status = price_sanity(2003.0, "Suzuki Ignis 1.5 Sport",
                                 year=None, price_original=2003.0,
                                 mileage_km=None)
    assert status == "SUSPECT"
    assert price is None


@pytest.mark.parametrize("amount", [55_411, 92_600, 162_499, 92_003])
def test_implausibly_high_prices_are_suspect(amount):
    price, status = price_sanity(float(amount), "Suzuki Ignis Sport part",
                                 year=2004)
    assert status == "SUSPECT"
    assert price is None


@pytest.mark.parametrize("amount,year,mileage", [
    (2199, 2004, 229_000),
    (1490, 2008, 187_000),
    (3900, 2006, 120_000),
    (4750, 2005, 98_000),
])
def test_real_prices_survive(amount, year, mileage):
    price, status = price_sanity(float(amount), "Suzuki Ignis Sport",
                                 year=year, price_original=float(amount),
                                 mileage_km=mileage)
    assert status == "OK"
    assert price == float(amount)


# --- 3. A genuine Ignis Sport listing must STILL pass ------------------------
def test_real_ignis_sport_still_passes():
    pf, cr = _run("Suzuki Ignis Sport 1.5 109ps Recaro TÜV Neu", year=2005)
    assert pf.bucket == CLEAR_IGNIS_SPORT
    assert pf.is_ignis is True
    assert cr.confidence >= 80


def test_plain_ignis_still_surfaces_for_review():
    pf, cr = _run("Suzuki Ignis 1,5 von einem Rentner gefahren", year=2005)
    assert pf.is_ignis is True
    assert pf.bucket in (POSSIBLE_IGNIS_SPORT, "NORMAL_IGNIS")


# --- 4. "Sort by newest" in the Listings view --------------------------------
def test_newest_sort_orders_by_recency_with_id_tiebreaker(session):
    """Reported bug: sorting the Listings view by newest did nothing. Rows that
    share first_seen_at (bulk inserts) must fall back to id desc so the most
    recently ingested listing is first."""
    from datetime import datetime, timezone

    from src.dashboard import queries as Q
    from src.models.listing import Listing

    same_ts = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    ids = []
    for n in range(1, 4):
        lst = Listing(
            internal_id=f"newest{n}", vehicle_match_confidence=85,
            title="Suzuki Ignis Sport",
            listing_url=f"https://haendler.de/auto/ignis-sport-{n}",
            canonical_listing_url=f"https://haendler.de/auto/ignis-sport-{n}",
            listing_status="ACTIVE", first_seen_at=same_ts)
        session.add(lst)
        session.flush()
        ids.append(lst.id)
    session.commit()

    rows = Q.filter_listings(session, min_confidence=0, order_by="newest")
    got = [r.id for r in rows if r.id in ids]
    # Most recently inserted (highest id) first, despite identical first_seen_at.
    assert got == sorted(ids, reverse=True)
