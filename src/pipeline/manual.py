"""Manual entry points: add a dealer/source by URL, or analyse a single listing
URL on demand. Both do a first scan immediately."""

from __future__ import annotations

from datetime import datetime, timezone

from ..ai.source_hunter import assess_source
from ..config.settings import get_settings
from ..crawlers.base import CrawlContext, HttpFetcher
from ..crawlers.http_crawler import crawl_source
from ..database.base import session_scope
from ..models.source import Source
from ..parsers.html_generic import extract_listings_from_html
from ..utils.hashing import domain_of
from ..utils.logging import get_logger
from .ingest import process_candidate

log = get_logger("pipeline.manual")


def add_watch_source(url: str, country: str | None = None,
                     first_scan: bool = True) -> dict:
    """Analyse a URL the user pasted, register it as a source, and optionally
    run a first scan. Returns a summary."""
    dom = domain_of(url)
    if not dom:
        return {"ok": False, "error": "could not parse domain"}
    settings = get_settings()

    with HttpFetcher(settings) as fetcher:
        res = fetcher.fetch(url if "//" in url else f"https://{url}")
        snippet = res.text[:2000] if res.ok else ""
        assessment = assess_source(dom, snippet=snippet, country=country)

        with session_scope() as session:
            source = session.query(Source).filter(Source.domain == dom).first()
            if not source:
                source = Source(
                    domain=dom,
                    name=assessment.reasoning[:80] or dom,
                    country=assessment.country or country,
                    language=assessment.language,
                    source_type=assessment.source_type,
                    base_url=f"https://{dom}/",
                    search_url=url if "//" in url else None,
                    discovery_method="manual_watchlist",
                    discovery_value=assessment.discovery_value,
                    priority=max(60, assessment.priority),
                    parser_type=assessment.recommended_parser,
                    requires_browser=assessment.requires_browser,
                    manual_review=not assessment.automatable,
                )
                session.add(source)
                session.flush()
            source_id = source.id
            summary = {"ok": True, "domain": dom, "source_id": source_id,
                       "source_type": source.source_type,
                       "discovery_value": source.discovery_value,
                       "assessment_fallback": assessment.is_fallback}

            if first_scan and not source.manual_review:
                ctx = CrawlContext(fetcher=fetcher,
                                   max_pages=settings.max_pages_per_source)
                candidates, pages = crawl_source(ctx, source)
                found = 0
                for raw in candidates:
                    r = process_candidate(session, raw, source)
                    if r.listing is not None:
                        found += 1
                source.last_checked_at = datetime.now(timezone.utc)
                source.last_success_at = source.last_checked_at
                summary.update({"pages": pages, "listings_found": found})
    log.info("Manual source added: %s", summary)
    return summary


def analyze_manual_url(url: str, country: str | None = None) -> dict:
    """Analyse a single listing URL: is it an Ignis Sport? Known already? Who is
    the seller, and should their site become a source?"""
    settings = get_settings()
    dom = domain_of(url)
    with HttpFetcher(settings) as fetcher:
        res = fetcher.fetch(url)
        if not res.ok:
            return {"ok": False, "error": res.error or "fetch failed"}
        candidates = extract_listings_from_html(res.text, res.url, dom, country)
        if not candidates:
            return {"ok": True, "found": False,
                    "note": "No vehicle data extracted from page"}
        with session_scope() as session:
            source = session.query(Source).filter(Source.domain == dom).first()
            best = None
            for raw in candidates:
                r = process_candidate(session, raw, source)
                if r.listing and (best is None or
                                  r.listing.vehicle_match_confidence >
                                  best["confidence"]):
                    best = {
                        "confidence": r.listing.vehicle_match_confidence,
                        "classification": r.listing.classification,
                        "is_new": r.is_new,
                        "duplicate_merged": r.duplicate_merged,
                        "internal_id": r.listing.internal_id,
                        "opportunity_score": r.listing.opportunity_score,
                    }
    return {"ok": True, "found": best is not None, "best_match": best,
            "seller_domain": dom}
