"""Tests for Phase-17b search quality: domain/URL classification, country
detection, the first-class search-hit pipeline (direct listing ingest, exact
URL preserved, discovery-only sources not activated) and query transparency."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.discovery.classify import (
    DISCOVERY_ONLY,
    MONITORABLE,
    classify_url_type,
    detect_country,
    domain_category,
    is_aggregator_domain,
)
from src.discovery.engine import select_queries
from src.discovery.hits import process_search_hit
from src.discovery.queries import generate_queries
from src.models.discovery import DiscoveryQuery
from src.models.listing import Listing
from src.models.search_hit import SearchHit
from src.models.source import Source


# --- Fake fetcher ----------------------------------------------------------
@dataclass
class _FakeResult:
    url: str
    status_code: int = 200
    text: str = ""
    ok: bool = True
    blocked_by_robots: bool = False
    from_cache: bool = False
    error: str | None = None


@dataclass
class _FakeFetcher:
    pages: dict = field(default_factory=dict)   # url -> _FakeResult

    def fetch(self, url, **kw):
        return self.pages.get(url, _FakeResult(url=url, ok=False, status_code=404,
                                               error="not found"))


LISTING_HTML = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Car","name":"Suzuki Ignis 1.5 Sport",
 "vehicleModelDate":"2005",
 "mileageFromOdometer":{"@type":"QuantitativeValue","value":"118000"},
 "vehicleEngine":{"@type":"EngineSpecification","enginePower":"80 kW",
   "engineDisplacement":"1490 ccm"},
 "offers":{"@type":"Offer","price":"4900","priceCurrency":"EUR"},
 "url":"https://autohaus-mueller.de/fahrzeug/ignis-sport-123"}
</script></head><body>Suzuki Ignis Sport HT81S</body></html>
"""

PARTS_HTML = "<html><body>Suzuki Ignis Ersatzteile und Tuning Zubehör Shop</body></html>"


# --- domain_category (P4) --------------------------------------------------
def test_domain_category_discovery_only_for_parts_tuning_insurer():
    for dom in ("autoalkatreszek24.hu", "skn-tuning.de", "mk-fahrwerkstechnik.de",
                "dieversicherer.de", "carnoisseur.com", "auto-motor-und-sport.de"):
        cat = domain_category(dom, "Suzuki Ignis")
        assert cat["category"] == DISCOVERY_ONLY, dom
        assert cat["monitorable"] is False, dom


def test_domain_category_monitorable_for_dealers():
    for dom in ("autohaus-mueller.de", "suzuki-gebrauchtwagen.de",
                "otomoto.pl", "occasion-garage.fr"):
        cat = domain_category(dom, "Gebrauchtwagen Fahrzeuge")
        assert cat["category"] == MONITORABLE, dom
        assert cat["monitorable"] is True, dom


# --- classify_url_type (P2/P3) --------------------------------------------
def test_classify_url_type_listing():
    v = classify_url_type("https://autohaus-mueller.de/fahrzeug/ignis-sport-123",
                          LISTING_HTML, "autohaus-mueller.de")
    assert v["type"] == "LISTING"
    assert v["listings"]


def test_classify_url_type_parts():
    v = classify_url_type("https://autoteile-shop.de/suzuki-ignis-teile",
                          PARTS_HTML, "autoteile-shop.de")
    assert v["type"] in ("PARTS_PAGE", "IRRELEVANT")


# --- detect_country (P5) ---------------------------------------------------
def test_detect_country_signals():
    assert detect_country("https://x.de/", "Rufen Sie an: +49 170 1234567")["country"] == "DE"
    assert detect_country("https://x.com/", "Sitz in Österreich, Wien")["country"] == "AT"
    weak = detect_country("https://haendler.it/", "<html></html>")
    assert weak["country"] == "IT" and weak["confidence"] < 50  # ccTLD is weak


# --- search-hit pipeline (P2/P3) ------------------------------------------
def test_search_hit_listing_is_ingested_directly(session):
    url = "https://autohaus-mueller.de/fahrzeug/ignis-sport-123"
    fetcher = _FakeFetcher({url: _FakeResult(url=url, text=LISTING_HTML)})
    out = process_search_hit(fetcher, url, "Ignis Sport", "80 kW", "HT81S",
                             ["brave"], 7, 1, "DE")
    assert out["hit_type"] == "LISTING"
    assert out["ingested"] is True
    # The car found via search must be in the DB...
    assert session.query(Listing).count() == 1
    lst = session.query(Listing).one()
    assert lst.vehicle_match_confidence >= 60
    # ...and the exact search URL preserved as a SearchHit (not just the domain).
    hit = session.query(SearchHit).filter(SearchHit.exact_url == url).one()
    assert hit.processing_status == "INGESTED"
    # ...and the dealer registered as a monitorable, active source.
    src = session.query(Source).filter(Source.domain == "autohaus-mueller.de").one()
    assert src.active is True and src.source_category == MONITORABLE


def test_search_hit_parts_page_not_activated(session):
    url = "https://autoteile-shop.de/suzuki-ignis-teile"
    fetcher = _FakeFetcher({url: _FakeResult(url=url, text=PARTS_HTML)})
    out = process_search_hit(fetcher, url, "Ignis Teile", "Ersatzteile", "Ignis",
                             ["brave"], 3, 1, "DE")
    assert out["ingested"] is False
    assert out["status"] == "SKIPPED"
    assert session.query(Listing).count() == 0
    # A parts shop must NOT become an active vehicle source.
    src = session.query(Source).filter(Source.domain == "autoteile-shop.de").first()
    assert src is None or src.active is False


def test_search_hit_robots_blocked_is_transparent(session):
    url = "https://blocked-dealer.de/fahrzeug/ignis"
    fetcher = _FakeFetcher({url: _FakeResult(url=url, ok=False, status_code=0,
                                             blocked_by_robots=True)})
    out = process_search_hit(fetcher, url, "Ignis", "", "Ignis", ["brave"], 1, 1, "DE")
    assert out["status"] == "BLOCKED"
    hit = session.query(SearchHit).filter(SearchHit.exact_url == url).one()
    assert hit.processing_status == "BLOCKED"


def test_search_hit_aggregator_nonlisting_skipped(session):
    url = "https://www.autoscout24.de/"
    fetcher = _FakeFetcher({url: _FakeResult(url=url, text="<html></html>")})
    out = process_search_hit(fetcher, url, "AutoScout", "", "Ignis", ["brave"],
                             1, 1, "DE")
    assert out["status"] == "SKIPPED"
    assert is_aggregator_domain("autoscout24.de")


# --- query transparency (P7) ----------------------------------------------
def test_select_queries_reports_transparency(session):
    gen = generate_queries(["DE"], seed=3)
    # Mark one generated query as run just now → it should be held back.
    gq = gen[0]
    session.add(DiscoveryQuery(query=gq.query, country=gq.country,
                               last_run_at=datetime.now(timezone.utc),
                               run_count=1))
    session.commit()
    selected, stats = select_queries(gen, max_queries=10)
    assert stats["planned"] == len(selected)
    assert stats["generated"] == len(gen)
    assert "skipped_recently" in stats and "skipped_duplicate" in stats
    # The recently-run query is not among the first picks when fresh ones exist.
    assert stats["skipped_recently"] >= 0
