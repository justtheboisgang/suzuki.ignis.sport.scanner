"""Tests for exact listing-link preservation and the no-navigation dashboard
buttons / external-link behaviour."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.dashboard.app import app
from src.discovery.queries import BIG_PLATFORMS
from src.parsers.html_generic import RawListing
from src.pipeline.ingest import process_candidate
from src.database.writer import write_session
from src.models.listing import Listing
from src.utils.hashing import is_specific_url, pick_canonical


# --- Exact link preservation (P8/P10) -------------------------------------
def test_is_specific_url():
    assert is_specific_url("https://dealer.com/vehicles/ignis-1") is True
    assert is_specific_url("https://dealer.com/") is False
    assert is_specific_url("https://dealer.com") is False


def test_pick_canonical_prefers_dealer_direct():
    urls = ["https://www.autoscout24.de/angebot/xyz",
            "https://haendler.de/auto/ignis-sport-1",
            "https://haendler.de/"]
    got = pick_canonical(urls, "haendler.de", tuple(BIG_PLATFORMS))
    assert got == "https://haendler.de/auto/ignis-sport-1"


def test_pick_canonical_never_marketplace_homepage():
    urls = ["https://dealer.com/", "https://dealer.com/vehicles/ignis-1"]
    assert pick_canonical(urls) == "https://dealer.com/vehicles/ignis-1"


def test_crawler_listing_url_is_preserved_exactly(session):
    raw = RawListing(title="Suzuki Ignis Sport",
                     url="https://haendler.de/auto/ignis-sport-99",
                     source_domain="haendler.de")
    res = process_candidate(raw, None)
    assert res.listing.listing_url == "https://haendler.de/auto/ignis-sport-99"
    # Canonical must be the concrete listing URL, never a homepage.
    assert res.listing.canonical_listing_url == "https://haendler.de/auto/ignis-sport-99"


def test_canonical_not_replaced_by_homepage_on_merge(session):
    # Same car seen first on a dealer's exact page, then via a marketplace URL.
    a = RawListing(title="Suzuki Ignis Sport", source_domain="haendler.de",
                   url="https://haendler.de/auto/ignis-sport-7", vin="JS1ABC000000001")
    process_candidate(a, None)
    b = RawListing(title="Suzuki Ignis Sport", source_domain="autoscout24.de",
                   url="https://www.autoscout24.de/angebot/abc", vin="JS1ABC000000001")
    process_candidate(b, None)
    with write_session() as s:
        lst = s.query(Listing).filter(Listing.vin_public == "JS1ABC000000001").one()
        # Canonical stays the concrete dealer listing, not a marketplace homepage.
        assert lst.canonical_listing_url == "https://haendler.de/auto/ignis-sport-7"
        assert is_specific_url(lst.canonical_listing_url)


# --- Dashboard: no navigation + external new tab (P11-P15) -----------------
def test_run_buttons_do_not_navigate():
    home = TestClient(app).get("/").text
    assert 'action="/run-discovery"' not in home
    assert 'action="/run-scan"' not in home
    assert "runJob(" in home                 # buttons use background fetch
    assert "JOB_RUNNING_LABEL" in home       # buttons reflect RUNNING state


def test_run_endpoints_return_json_not_html_redirect():
    c = TestClient(app)
    r = c.post("/run-discovery", data={"max_queries": 2})
    assert r.status_code == 200
    assert "started" in r.json()


def test_external_listing_opens_in_new_tab(session):
    with write_session() as s:
        s.add(Listing(internal_id="extlink", vehicle_match_confidence=85,
                      title="Suzuki Ignis Sport",
                      listing_url="https://haendler.de/auto/ignis-sport-1",
                      canonical_listing_url="https://haendler.de/auto/ignis-sport-1"))
    html = TestClient(app).get("/listings").text
    assert 'target="_blank" rel="noopener noreferrer"' in html
    assert "Original ↗" in html
