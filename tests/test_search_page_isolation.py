"""Regression tests for the search-page-as-listing bug.

A search/inventory page must yield one isolated candidate PER card (with its own
exact detail href), never a single "Suzuki bus" listing pointing at the search
page, and neighbouring cards must not leak into each other's classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.parsers.html_generic import extract_page
from src.parsers.urltype import url_quality
from src.pipeline.ingest import process_candidate
from src.pipeline.reclassify import reclassify_listings
from src.dashboard.queries import filter_listings
from src.database.writer import write_session
from src.models.listing import Listing
from src.discovery.hits import process_search_hit


SEARCH_HTML = """
<html><head><title>suzuki bus - Marktplaats</title></head><body>
<ul class="results">
  <li><a href="/listing/12345">Suzuki Carry Bus 1.3 mini bus</a> €3.999 · 1999 · 201.300 km</li>
  <li><a href="/listing/22222">Suzuki Jimny 1.3</a> €5.500 · 2005</li>
  <li><a href="/listing/33333">Suzuki Ignis Sport 1.5 VVT</a> €4.900 · 2005 · 118.000 km · 80 kW</li>
  <li><a href="/listing/44444">Suzuki Vitara 2.0</a> €2.000</li>
  <li><a href="/l/auto-s/?q=meer">meer resultaten</a></li>
</ul></body></html>
"""
SEARCH_URL = "https://www.example.com/l/auto-s/suzuki/?q=suzuki+bus"


@dataclass
class _Res:
    url: str
    status_code: int = 200
    text: str = ""
    ok: bool = True
    blocked_by_robots: bool = False
    from_cache: bool = False
    error: str | None = None


@dataclass
class _Fetcher:
    pages: dict = field(default_factory=dict)

    def fetch(self, url, **kw):
        return self.pages.get(url, _Res(url=url, ok=False, status_code=404))


def test_search_page_yields_isolated_cards_with_exact_hrefs():
    cands, page_type = extract_page(SEARCH_HTML, SEARCH_URL, "example.com", "NL")
    assert page_type == "SEARCH_RESULTS"
    # 4 vehicle cards (the "meer resultaten" search link is excluded).
    assert len(cands) == 4
    urls = {c.url for c in cands}
    assert "https://www.example.com/listing/33333" in urls   # the Ignis Sport
    # None of them is the search page itself.
    assert SEARCH_URL not in urls
    for c in cands:
        assert c.listing_url_quality in ("EXACT_DETAIL", "LIKELY_DETAIL")


def test_only_ignis_sport_becomes_candidate_others_dropped(session):
    cands, _ = extract_page(SEARCH_HTML, SEARCH_URL, "example.com", "NL")
    stored = []
    for c in cands:
        r = process_candidate(c, None)
        if r.listing is not None:
            stored.append(r.listing)
    assert len(stored) == 1
    ignis = stored[0]
    assert "Ignis Sport" in ignis.title
    # listing_url is EXACTLY the card's individual detail href.
    assert ignis.listing_url == "https://www.example.com/listing/33333"
    # NOT the search page.
    assert ignis.listing_url != SEARCH_URL


def test_no_cross_card_context_leak(session):
    # A page that mentions "Ignis Sport" must not give the Bus/Jimny/Vitara
    # cards any Ignis match score.
    cands, _ = extract_page(SEARCH_HTML, SEARCH_URL, "example.com", "NL")
    for c in cands:
        r = process_candidate(c, None)
        if "Ignis" not in c.title:
            assert r.listing is None       # Bus/Jimny/Vitara dropped
            assert r.bucket == "OTHER_MODEL"


def test_search_query_never_becomes_a_listing_title():
    # "suzuki bus" is the query / page <title>, not a card — it must never be a
    # listing title.
    cands, _ = extract_page(SEARCH_HTML, SEARCH_URL, "example.com", "NL")
    titles = [c.title.lower() for c in cands]
    assert "suzuki bus" not in titles


def test_parser_unresolved_on_search_page_without_cards():
    html = "<html><head><title>suzuki bus</title></head><body>no cards here</body></html>"
    cands, page_type = extract_page(html, SEARCH_URL, "example.com", "NL")
    assert cands == []                     # 0 candidates, not a fabricated one
    assert page_type == "SEARCH_RESULTS"


def test_search_hit_ingests_cards_not_the_page(session):
    fetcher = _Fetcher({SEARCH_URL: _Res(url=SEARCH_URL, text=SEARCH_HTML)})
    out = process_search_hit(fetcher, SEARCH_URL, "suzuki bus", "", "suzuki ignis",
                             ["brave"], 1, 1, "NL")
    # The page itself is inventory/search — not ingested as a single listing.
    assert out["hit_type"] in ("DEALER_INVENTORY", "SEARCH_RESULTS")
    with write_session() as s:
        rows = s.query(Listing).all()
        assert len(rows) == 1
        assert rows[0].listing_url == "https://www.example.com/listing/33333"
        assert rows[0].listing_url != SEARCH_URL


# --- URL quality / Original button gating ---------------------------------
def test_url_quality_distinguishes_search_and_detail():
    assert url_quality(SEARCH_URL) == "SEARCH_PAGE"
    assert url_quality("https://www.example.com/listing/33333") == "EXACT_DETAIL"
    assert url_quality("https://www.example.com/") == "HOMEPAGE"


def test_original_button_hidden_for_search_url(session):
    with write_session() as s:
        s.add(Listing(internal_id="badurl", title="Suzuki Ignis Sport",
                      vehicle_match_confidence=85, listing_url=SEARCH_URL,
                      canonical_listing_url=SEARCH_URL,
                      listing_url_quality="SEARCH_PAGE"))
    with write_session() as s:
        lst = s.query(Listing).filter(Listing.internal_id == "badurl").one()
        assert lst.external_detail_url is None      # no misleading button


# --- Existing bad-data cleanup (P9) ----------------------------------------
def test_reclassify_hides_other_model_search_page_records(session):
    with write_session() as s:
        s.add(Listing(internal_id="suzukibus", title="Suzuki Bus",
                      vehicle_match_confidence=80, classification="LIKELY_IGNIS_SPORT",
                      opportunity_score=89, listing_status="ACTIVE",
                      listing_url=SEARCH_URL, canonical_listing_url=SEARCH_URL,
                      listing_url_quality="UNKNOWN"))
    reclassify_listings()
    with write_session() as s:
        lst = s.query(Listing).filter(Listing.internal_id == "suzukibus").one()
        assert lst.vehicle_match_confidence == 0          # model gate
        assert lst.listing_url_quality == "SEARCH_PAGE"   # url re-evaluated
        assert lst.external_detail_url is None
        # Must not appear in the candidate dashboard views.
        shown = filter_listings(s, min_confidence=0)
        assert all(x.internal_id != "suzukibus" for x in shown)
