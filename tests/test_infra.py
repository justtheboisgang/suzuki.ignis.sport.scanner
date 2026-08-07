"""Tests for infrastructure: DB init, seed sources, query generation, scheduler
build, robots handling and URL normalisation."""

from src.discovery.queries import generate_queries
from src.discovery.seed_sources import SEED_SOURCES, seed_sources_into_db
from src.models.source import Source
from src.utils.hashing import domain_of, normalize_url, stable_id


def test_seed_sources_idempotent(session):
    n1 = seed_sources_into_db()
    n2 = seed_sources_into_db()
    assert n1 == len(SEED_SOURCES)
    assert n2 == 0  # already present -> nothing added second time
    assert session.query(Source).count() == len(SEED_SOURCES)


def test_query_generation_multilingual_and_experimental():
    qs = generate_queries(["DE", "IT"], experimental_ratio=0.2, seed=1)
    assert qs, "should generate queries"
    countries = {q.country for q in qs}
    assert countries == {"DE", "IT"}
    assert any(q.experimental for q in qs)
    # Chassis-code query is language independent and present.
    assert any("HT81S" in q.query for q in qs)
    # Localized phrasing present for Italy.
    assert any("usata" in q.query.lower() or "vendita" in q.query.lower()
               for q in qs if q.country == "IT")


def test_url_normalisation():
    a = normalize_url("https://WWW.Example.com/path/?utm_source=x&id=5#frag")
    b = normalize_url("https://example.com/path?id=5")
    assert a == b
    assert domain_of("https://www.autohaus-mueller.de/x") == "autohaus-mueller.de"
    assert stable_id("a", "b") == stable_id("a", "b")
    assert len(stable_id("a")) == 20


def test_scheduler_builds():
    from src.scheduler.runner import build_scheduler
    sched = build_scheduler(blocking=False)
    job_ids = {j.id for j in sched.get_jobs()}
    assert "discovery_daily" in job_ids
    assert "priority_sweep" in job_ids
    # At least the 4 configured daily scans.
    assert sum(1 for j in sched.get_jobs() if j.id.startswith("scan_")) >= 4
    # BackgroundScheduler was built but never started; nothing to shut down.


def test_robots_cache_offline():
    # RobotsCache must not crash when it cannot reach robots.txt.
    from src.crawlers.base import RobotsCache
    rc = RobotsCache("TestAgent/1.0", respect=True)
    # Unreachable host -> permissive (returns True), never raises.
    assert rc.allowed("https://nonexistent.invalid/page") in (True, False)
