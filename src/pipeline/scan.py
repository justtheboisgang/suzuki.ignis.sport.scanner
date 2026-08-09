"""Top-level scan / discovery / recheck orchestration.

Concurrency contract (fixes the DB-lock bug): the scan loop NEVER holds a write
transaction across a crawl. It reads the due sources in one short read, crawls
each source with no DB txn open, ingests candidates (each `process_candidate`
does its own short write), then finalises per-source health in a short write.
The parser diagnostic (a network/AI call) runs before that write, never inside.
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
from ..database.writer import run_write, write_session
from ..models.enums import ListingStatus
from ..models.history import StatusHistory
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
    # Expunge so we can safely read attributes after the read session closes.
    for s in due:
        session.expunge(s)
    return due


def _next_check(freq: str) -> datetime:
    delta = _FREQ_TO_DELTA.get(freq, timedelta(hours=6))
    return datetime.now(timezone.utc) + delta


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
    if source and (source.discovery_value or 0) >= 65:
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
        body=format_hit(payload), priority=priority,
        listing_id=listing.id, payload=payload))


def run_scan(limit: int | None = None, force: bool = False,
             backup: bool = True, progress_cb=None) -> dict:
    """Run one scan cycle over due sources. Returns a summary dict."""
    settings = get_settings()
    scan_id = uuid.uuid4().hex[:16]
    manager = get_manager()
    errors: list[str] = []

    run_id = run_write(lambda s: _create_scan_run(s, scan_id), label="scan.create")

    with session_scope() as session:
        due = _due_sources(session, limit, force)
    log.info("Scan %s starting over %d sources", scan_id, len(due))

    totals = dict(new=0, changed=0, seen=0, ai=0, pages=0, ok=0, failed=0,
                  candidates=0)
    # Transparent per-source outcome tally (P1).
    outcomes: dict[str, int] = {}

    def _metrics(done):
        return {"sources_total": len(due), "sources_done": done,
                "pages_fetched": totals["pages"],
                "candidates_extracted": totals["candidates"],
                "listings_accepted": totals["seen"],
                "new_listings": totals["new"],
                "ai_calls": totals["ai"], "failed": totals["failed"],
                "outcome_breakdown": dict(outcomes)}

    if progress_cb:
        progress_cb(metrics=_metrics(0), message="scan starting")

    with HttpFetcher(settings) as fetcher:
        for idx, source in enumerate(due, start=1):
            ctx = CrawlContext(fetcher=fetcher,
                               max_pages=settings.max_pages_per_source)
            # 1) NETWORK: crawl (no DB txn open).
            crawl_error = None
            candidates, pages = [], 0
            try:
                candidates, pages = crawl_source(ctx, source)
            except Exception as exc:  # a bad source must not kill the scan
                crawl_error = str(exc)
                ctx.outcome = "ERROR"
                log.warning("source %s crawl error: %s", source.domain, exc)
                errors.append(f"{source.domain}: {exc}")

            totals["pages"] += pages
            totals["candidates"] += len(candidates)
            errors.extend(ctx.errors)
            outcomes[ctx.outcome] = outcomes.get(ctx.outcome, 0) + 1
            result_count = 0

            # 2) Ingest each candidate (each does its own short write).
            for raw in candidates:
                res = process_candidate(raw, source)
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

            # 3) Diagnostic (network) BEFORE the write, only when needed.
            prev_typical = source.typical_result_count or 0
            diag_note = None
            if crawl_error is None and result_count == 0 and prev_typical >= 3:
                diag = diagnose_parser(status=0, html="", typical=prev_typical,
                                       current=0, domain=source.domain)
                diag_note = (f"POSSIBLE_PARSER_FAILURE: {diag.likely_cause} — "
                             f"{diag.suggested_action}")
                log.warning("%s: %s", source.domain, diag_note)

            # 4) SHORT WRITE: update this source's health/counters/schedule.
            failed = crawl_error is not None or ctx.outcome in (
                "HTTP_403", "HTTP_429", "HTTP_404", "ERROR", "ROBOTS")
            run_write(lambda s, sid=source.id, rc=result_count, pt=prev_typical,
                      failed=failed, note=diag_note, freq=source.check_frequency:
                      _finalize_source(s, sid, rc, pt, failed, note, freq),
                      label="scan.finalize_source", swallow=True)
            if failed:
                totals["failed"] += 1
            else:
                totals["ok"] += 1

            if progress_cb and (idx % 3 == 0 or idx == len(due)):
                progress_cb(metrics=_metrics(idx), message=f"source {idx}/{len(due)}")

    # 5) Finalise the ScanRun row.
    run_write(lambda s: _finalize_scan_run(s, run_id, totals, errors, outcomes),
              label="scan.finalize", swallow=True)

    summary = {"scan_id": scan_id, "sources": len(due), **totals,
               "outcome_breakdown": outcomes, "errors": len(errors),
               "listings_accepted": totals["seen"]}
    log.info("SCAN SUMMARY %s: sources=%d pages=%d candidates=%d accepted=%d "
             "new=%d failed=%d outcomes=%s", scan_id, len(due), totals["pages"],
             totals["candidates"], totals["seen"], totals["new"],
             totals["failed"], outcomes)
    removed = recheck_disappeared()
    summary["removed"] = removed
    if backup:
        backup_database()
    log.info("Scan %s complete: %s", scan_id, summary)
    return summary


def _create_scan_run(session, scan_id: str) -> int:
    run = ScanRun(scan_id=scan_id, kind="scan")
    session.add(run)
    session.flush()
    return run.id


def _finalize_scan_run(session, run_id: int, totals: dict, errors: list,
                       outcomes: dict) -> None:
    run = session.get(ScanRun, run_id)
    if not run:
        return
    run.sources_successful = totals["ok"]
    run.sources_failed = totals["failed"]
    run.pages_checked = totals["pages"]
    run.listings_seen = totals["seen"]
    run.new_listings = totals["new"]
    run.changed_listings = totals["changed"]
    run.ai_calls = totals["ai"]
    run.sources_attempted = totals["ok"] + totals["failed"]
    run.candidates_extracted = totals.get("candidates", 0)
    run.listings_accepted = totals["seen"]
    run.outcome_breakdown = outcomes
    run.errors = errors[:200]
    run.end_time = datetime.now(timezone.utc)
    run.status = "done"


def _finalize_source(session, source_id: int, result_count: int,
                     prev_typical: int, failed: bool, diag_note: str | None,
                     freq: str) -> None:
    src = session.get(Source, source_id)
    if not src:
        return
    now = datetime.now(timezone.utc)
    src.last_checked_at = now
    src.last_result_count = result_count
    src.next_check_at = _next_check(freq)
    if failed:
        src.failure_count = (src.failure_count or 0) + 1
        src.health = "failed"
        return
    if diag_note is not None:
        src.health = "degraded"
        src.notes = diag_note[:500]
    else:
        src.health = "healthy" if result_count > 0 else (src.health or "unknown")
        src.typical_result_count = int(round(0.7 * prev_typical + 0.3 * result_count))
    src.last_success_at = now
    src.failure_count = 0


def recheck_disappeared(miss_threshold: int = 3) -> int:
    """Mark listings not seen recently. A single miss never means SOLD — we
    escalate ACTIVE → MAYBE_ACTIVE → EXPIRED over consecutive misses. One short
    write transaction, no network."""
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(hours=20)
    removed = {"n": 0}

    def _do(session):
        removed["n"] = 0
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
                removed["n"] += 1
            if lst.listing_status != old:
                session.add(StatusHistory(
                    listing_id=lst.id, old_status=old,
                    new_status=lst.listing_status,
                    note=f"{lst.consecutive_misses} consecutive misses"))

    run_write(_do, label="recheck_disappeared", swallow=True)
    return removed["n"]


def run_discovery(countries: list[str] | None = None, max_queries: int = 40,
                  progress_cb=None) -> dict:
    """Run a source-discovery pass and log it as a ScanRun of kind=discovery."""
    from ..discovery.engine import DiscoveryEngine
    scan_id = uuid.uuid4().hex[:16]
    engine = DiscoveryEngine()
    summary = engine.run(countries=countries, max_queries=max_queries,
                         progress_cb=progress_cb)
    run_write(lambda s: s.add(ScanRun(
        scan_id=scan_id, kind="discovery", end_time=datetime.now(timezone.utc),
        status="done", new_domains=summary.get("new_domains", 0),
        sources_attempted=summary.get("queries_run", 0))),
        label="discovery.scanrun", swallow=True)
    summary["scan_id"] = scan_id
    return summary
