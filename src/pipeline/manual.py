"""Manual entry points: add a dealer/source by URL, or analyse a single listing
URL on demand. Both do a first scan immediately.

Concurrency contract: network first (fetch + assess + crawl), then short
serialised writes. No DB transaction is held across network/AI work, and
`process_candidate` manages its own short write per candidate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..ai.source_hunter import assess_source
from ..config.settings import get_settings
from ..crawlers.base import CrawlContext, HttpFetcher
from ..crawlers.http_crawler import crawl_source
from ..database.base import session_scope
from ..database.writer import run_write
from ..models.source import Source
from ..parsers.html_generic import extract_listings_from_html
from ..utils.hashing import domain_of
from ..utils.logging import get_logger
from .ingest import process_candidate

log = get_logger("pipeline.manual")


def _get_or_create_source(dom: str, assessment, url: str, country) -> int:
    """Short write txn: return the source id, creating it if missing."""
    def _do(session):
        source = session.query(Source).filter(Source.domain == dom).first()
        if not source:
            source = Source(
                domain=dom, name=(assessment.reasoning[:80] or dom),
                country=assessment.country or country, language=assessment.language,
                source_type=assessment.source_type, base_url=f"https://{dom}/",
                search_url=url if "//" in url else None,
                discovery_method="manual_watchlist",
                discovery_value=assessment.discovery_value,
                priority=max(60, assessment.priority),
                parser_type=assessment.recommended_parser,
                requires_browser=assessment.requires_browser,
                manual_review=not assessment.automatable)
            session.add(source)
            session.flush()
        return source.id
    return run_write(_do, label="manual.create_source")


def _load_source(source_id: int) -> Source | None:
    with session_scope() as session:
        src = session.get(Source, source_id)
        if src:
            session.expunge(src)  # detach for read-only attribute access
        return src


def add_watch_source(url: str, country: str | None = None,
                     first_scan: bool = True) -> dict:
    """Analyse a pasted URL, register it as a source, optionally first-scan it."""
    dom = domain_of(url)
    if not dom:
        return {"ok": False, "error": "could not parse domain"}
    settings = get_settings()

    with HttpFetcher(settings) as fetcher:
        # NETWORK: fetch + classify.
        res = fetcher.fetch(url if "//" in url else f"https://{url}")
        snippet = res.text[:2000] if res.ok else ""
        assessment = assess_source(dom, snippet=snippet, country=country)

        # WRITE: create/get the source.
        source_id = _get_or_create_source(dom, assessment, url, country)
        source = _load_source(source_id)
        summary = {"ok": True, "domain": dom, "source_id": source_id,
                   "source_type": source.source_type if source else None,
                   "discovery_value": source.discovery_value if source else None,
                   "assessment_fallback": assessment.is_fallback}

        # NETWORK: first scan (each candidate self-persists via short writes).
        if first_scan and source and not source.manual_review:
            ctx = CrawlContext(fetcher=fetcher,
                               max_pages=settings.max_pages_per_source)
            candidates, pages = crawl_source(ctx, source)
            found = sum(1 for raw in candidates
                        if process_candidate(raw, source).listing is not None)
            run_write(lambda s, sid=source_id: _touch_source(s, sid),
                      label="manual.touch", swallow=True)
            summary.update({"pages": pages, "listings_found": found})
    log.info("Manual source added: %s", summary)
    return summary


def _touch_source(session, source_id: int) -> None:
    src = session.get(Source, source_id)
    if src:
        now = datetime.now(timezone.utc)
        src.last_checked_at = now
        src.last_success_at = now


def analyze_manual_url(url: str, country: str | None = None) -> dict:
    """Analyse a single listing URL: is it an Ignis Sport? Known already?"""
    settings = get_settings()
    dom = domain_of(url)
    with HttpFetcher(settings) as fetcher:
        res = fetcher.fetch(url)  # NETWORK
        if not res.ok:
            return {"ok": False, "error": res.error or "fetch failed"}
        candidates = extract_listings_from_html(res.text, res.url, dom, country)
        if not candidates:
            return {"ok": True, "found": False,
                    "note": "No vehicle data extracted from page"}
    source = None
    with session_scope() as session:
        src = session.query(Source).filter(Source.domain == dom).first()
        if src:
            session.expunge(src)
            source = src

    best = None
    for raw in candidates:  # each self-persists via a short write
        r = process_candidate(raw, source)
        if r.listing and (best is None
                          or r.listing.vehicle_match_confidence > best["confidence"]):
            best = {
                "confidence": r.listing.vehicle_match_confidence,
                "classification": r.listing.classification,
                "is_new": r.is_new, "duplicate_merged": r.duplicate_merged,
                "internal_id": r.listing.internal_id,
                "opportunity_score": r.listing.opportunity_score,
            }
    return {"ok": True, "found": best is not None, "best_match": best,
            "seller_domain": dom}
