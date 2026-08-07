"""Top-level scan / discovery / recheck orchestration.

`run_scan` walks the sources that are due, crawls them politely, ingests every
candidate, fires alerts for strong new hits, updates per-source health (invoking
the parser diagnostic when a healthy source suddenly yields nothing), and writes
a ScanRun log row.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from ..ai.diagnostic import diagnose_parser
from ..config.settings import get_settings
from ..crawlers.base import CrawlContext, HttpFetcher
from ..crawlers.http_crawler import crawl_source
from ..database.base import session_scope
from ..database.init_db import backup_database
from ..models.enums import ListingStatus
from ..models.listing import Listing
from ..models.scan import ScanRun
from ..models.source import Source
from ..notifications.base import Notification, Priority, format_hit
from ..notifications.manager import get_manager
from ..utils.logging import get_logger
from .ingest import process_candidate

log = get_logger("pipeline.scan")

_FREQ_TO_DELTA = {
    "1h": timedelta(hours=1), "3h": timedelta(hours=3), "6h": timedelta(hours=6),
    "12h": timedelta(hours=12), "24h": timedelta(days=1), "daily": timedelta(days=1),
}


def _due_sources(session, limit: int | None, force: bool) -> list[Source]:
    now = datetime.now(timezone.utc)
    q = session.query(Source).filter(Source.active.is_(True),
                                     Source.manual_review.is_(False))
    sources = q.order_by(Source.priority.desc()).all()
    if force:
        due = sources
    else:
        due = [s for s in sources
               if s.next_check_at is None or s.next_check_at <= now]
    if limit:
        due = due[:limit]
    return due


def _schedule_next(source: Source) -> None:
    delta = _FREQ_TO_DELTA.get(source.check_frequency, timedelta(hours=6))
    source.next_check_at = datetime.now(timezone.utc) + delta


def _alert_for_hit(manager, listing: Listing, source: Source | None) -> None:
    settings = get_settings()
    if listing.vehicle_match_confidence < settings.match_confidence_threshold:
        return
    hot = listing.vehicle_match_confidence >= settings.hot_alert_confidence
    analysis = (listing.ai_analysis or {}).get("analyst", {})
    why_bits = []
    if listing.price_eur and listing.price_eur < 5000:
        why_bits.append("below-market price")
    if listing.mileage_km and listing.mileage_km < 90000:
        why_bits.append("low mileage")
    if listing.lhd_rhd == "LHD":
        why_bits.append("left-hand drive")
    if source and source.discovery_value >= 65:
        why_bits.append(f"long-tail source ({source.source_type})")
    if listing.classification and "CONFIRMED" in (listing.classification or ""):
        why_bits.append("confirmed Sport spec")
    risks = "; ".join(analysis.get("risks", []) or []) or (listing.damage_notes or "-")

    payload = {
        "country": listing.country, "city": listing.city,
        "price_eur": listing.price_eur, "mileage_km": listing.mileage_km,
        "year": listing.production_year, "seller_name": listing.seller_name,
        "seller_type": listing.seller_type,
        "match_confidence": listing.vehicle_match_confidence,
        "opportunity_score": listing.opportunity_score,
        "source": source.name if source else None,
        "url": listing.original_listing_url or listing.listing_url,
        "why": ", ".join(why_bits) or "matches Ignis Sport profile",
        "risks": risks,
    }
    priority = Priority.HOT if hot else Priority.NORMAL
    manager.dispatch(Notification(
        title=f"Ignis Sport candidate ({listing.vehicle_match_confidence}/100) "
              f"in {listing.country or '?'}",
        body=format_hit(payload),
        priority=priority,
        listing_id=listing.id,
        payload=payload,
    ))


def run_scan(limit: int | None = None, force: bool = False,
             backup: bool = True) -> dict:
    """Run one scan cycle over due sources. Returns a summary dict."""
    settings = get_settings()
    scan_id = uuid.uuid4().hex[:16]
    manager = get_manager()
    errors: list[str] = []

    with session_scope() as session:
        run = ScanRun(scan_id=scan_id, kind="scan")
        session.add(run)
        session.flush()
        due = _due_sources(session, limit, force)
        run.sources_attempted = len(due)
        log.info("Scan %s starting over %d sources", scan_id, len(due))

        totals = dict(new=0, changed=0, seen=0, ai=0, pages=0, ok=0, failed=0)

        with HttpFetcher(settings) as fetcher:
            for source in due:
                ctx = CrawlContext(fetcher=fetcher,
                                   max_pages=settings.max_pages_per_source)
                source.last_checked_at = datetime.now(timezone.utc)
                try:
                    candidates, pages = crawl_source(ctx, source)
                except Exception as exc:  # a bad source must not kill the scan
                    log.warning("source %s crawl error: %s", source.domain, exc)
                    errors.append(f"{source.domain}: {exc}")
                    source.failure_count += 1
                    source.health = "failed"
                    _schedule_next(source)
                    totals["failed"] += 1
                    continue

                totals["pages"] += pages
                errors.extend(ctx.errors)
                result_count = 0

                for raw in candidates:
                    res = process_candidate(session, raw, source)
                    if res.listing is None:
                        continue
                    totals["seen"] += 1
                    result_count += 1
                    if res.used_ai:
                        totals["ai"] += 1
                    if res.is_new:
                        totals["new"] += 1
                        _alert_for_hit(manager, res.listing, source)
                    elif res.price_changed or res.duplicate_merged:
                        totals["changed"] += 1

                # --- Source health / diagnostic. -------------------------
                _update_source_health(source, ctx, result_count)
                if source.health != "failed":
                    source.last_success_at = datetime.now(timezone.utc)
                    source.failure_count = 0
                    totals["ok"] += 1
                _schedule_next(source)

        run.sources_successful = totals["ok"]
        run.sources_failed = totals["failed"]
        run.pages_checked = totals["pages"]
        run.listings_seen = totals["seen"]
        run.new_listings = totals["new"]
        run.changed_listings = totals["changed"]
        run.ai_calls = totals["ai"]
        run.errors = errors[:200]
        run.end_time = datetime.now(timezone.utc)
        run.status = "done"
        summary = {
            "scan_id": scan_id,
            "sources": len(due),
            **totals,
            "errors": len(errors),
        }

    # Recheck listings we didn't see this round, then back up the DB.
    removed = recheck_disappeared()
    summary["removed"] = removed
    if backup:
        backup_database()
    log.info("Scan %s complete: %s", scan_id, summary)
    return summary


def _update_source_health(source: Source, ctx: CrawlContext, result_count: int):
    """Flag a probable parser failure when a normally-productive source returns
    nothing, and run the (fallback-safe) diagnostic."""
    prev_typical = source.typical_result_count or 0
    source.last_result_count = result_count

    if result_count == 0 and prev_typical >= 3:
        source.health = "degraded"
        diag = diagnose_parser(status=0, html="", typical=prev_typical,
                               current=0, domain=source.domain)
        note = f"POSSIBLE_PARSER_FAILURE: {diag.likely_cause} — {diag.suggested_action}"
        source.notes = note[:500]
        log.warning("%s: %s", source.domain, note)
    else:
        source.health = "healthy" if result_count > 0 else source.health or "unknown"
        # Smooth the typical count (EMA).
        source.typical_result_count = int(round(0.7 * prev_typical + 0.3 * result_count))


def recheck_disappeared(miss_threshold: int = 3) -> int:
    """Mark listings not seen recently. A single miss never means SOLD — we
    escalate ACTIVE → MAYBE_ACTIVE → EXPIRED/REMOVED over consecutive misses."""
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(hours=20)
    removed = 0
    with session_scope() as session:
        listings = session.query(Listing).filter(
            Listing.listing_status.in_([ListingStatus.ACTIVE.value,
                                        ListingStatus.MAYBE_ACTIVE.value])).all()
        for lst in listings:
            last = lst.last_seen_at
            if last and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last and last >= stale_before:
                continue
            lst.consecutive_misses += 1
            old = lst.listing_status
            if lst.consecutive_misses == 1:
                lst.listing_status = ListingStatus.MAYBE_ACTIVE.value
            elif lst.consecutive_misses >= miss_threshold:
                lst.listing_status = ListingStatus.EXPIRED.value
                removed += 1
            if lst.listing_status != old:
                from ..models.history import StatusHistory
                session.add(StatusHistory(listing_id=lst.id, old_status=old,
                                          new_status=lst.listing_status,
                                          note=f"{lst.consecutive_misses} consecutive misses"))
    return removed


def run_discovery(countries: list[str] | None = None, max_queries: int = 40) -> dict:
    """Run a source-discovery pass and log it as a ScanRun of kind=discovery."""
    from ..discovery.engine import DiscoveryEngine
    scan_id = uuid.uuid4().hex[:16]
    engine = DiscoveryEngine()
    summary = engine.run(countries=countries, max_queries=max_queries)
    with session_scope() as session:
        session.add(ScanRun(
            scan_id=scan_id, kind="discovery",
            end_time=datetime.now(timezone.utc), status="done",
            new_domains=summary.get("new_domains", 0),
            sources_attempted=summary.get("queries_run", 0)))
    summary["scan_id"] = scan_id
    return summary
