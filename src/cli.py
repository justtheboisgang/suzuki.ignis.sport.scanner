"""Command-line interface for the Ignis Sport Europe Hunter.

Examples:
    python -m src.cli init            # create DB + seed sources
    python -m src.cli scan --limit 20 # run one scan cycle
    python -m src.cli discover        # run source discovery
    python -m src.cli dashboard       # start the web dashboard
    python -m src.cli scheduler       # start the long-running scheduler
    python -m src.cli add-source URL
    python -m src.cli analyze-url URL
    python -m src.cli report          # print the initial-run summary
    python -m src.cli stats
"""

from __future__ import annotations

import argparse
import json
import sys

from .config.settings import get_settings
from .database.init_db import backup_database, init_db
from .discovery.seed_sources import seed_sources_into_db
from .utils.logging import get_logger, setup_logging

log = get_logger("cli")


def cmd_init(args):
    init_db()
    added = seed_sources_into_db()
    print(f"Database initialised. Seeded {added} new sources.")


def cmd_scan(args):
    from .pipeline.scan import run_scan
    summary = run_scan(limit=args.limit, force=args.force, backup=not args.no_backup)
    print(json.dumps(summary, indent=2, default=str))


def cmd_discover(args):
    from .pipeline.scan import run_discovery
    countries = args.countries.split(",") if args.countries else None
    summary = run_discovery(countries=countries, max_queries=args.max_queries)
    print(json.dumps(summary, indent=2, default=str))


def cmd_dashboard(args):
    import uvicorn
    settings = get_settings()
    init_db()
    seed_sources_into_db()
    uvicorn.run("src.dashboard.app:app", host=args.host or settings.dashboard_host,
                port=args.port or settings.dashboard_port, reload=False)


def cmd_scheduler(args):
    from .scheduler.runner import run_forever
    run_forever()


def cmd_serve(args):
    """Single-process production entrypoint (for Railway / any PaaS):
    initialise + migrate the DB, seed sources, start the background scheduler
    (the 4x/day scans etc.), then serve the dashboard on 0.0.0.0:$PORT.

    Everything runs in ONE service; no second worker, no external cron.
    """
    import os
    import uvicorn

    settings = get_settings()
    # 1-2. DB present? initialise + run migrations. 3. scheduler. Both are done
    # by build_scheduler (which calls init_db + seed_sources_into_db).
    # One-shot cleanup of any pre-existing false positives / mis-categorised
    # sources with the current classifier (safe + idempotent).
    try:
        from .pipeline.reclassify import reclassify_all
        log.info("Startup reclassification: %s", reclassify_all())
    except Exception as exc:  # never block startup on this
        log.warning("startup reclassify skipped: %s", exc)

    from .scheduler.runner import build_scheduler
    scheduler = build_scheduler(blocking=False)
    scheduler.start()  # background thread: keeps the 4 daily scans alive
    log.info("Scheduler started in background (%d jobs).",
             len(scheduler.get_jobs()))

    # 4-5. Dashboard in the foreground keeps the process (and scheduler) alive.
    port = int(os.environ.get("PORT") or args.port or settings.dashboard_port)
    log.info("Serving dashboard on http://0.0.0.0:%d (scheduler running).", port)
    try:
        uvicorn.run("src.dashboard.app:app", host="0.0.0.0", port=port,
                    reload=False, log_level="info")
    finally:
        # 6. Clean shutdown on restart/stop.
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass


def cmd_add_source(args):
    from .pipeline.manual import add_watch_source
    print(json.dumps(add_watch_source(args.url, country=args.country), indent=2, default=str))


def cmd_analyze_url(args):
    from .pipeline.manual import analyze_manual_url
    print(json.dumps(analyze_manual_url(args.url, country=args.country), indent=2, default=str))


def cmd_backup(args):
    path = backup_database()
    print(f"Backup written to {path}" if path else "No SQLite DB to back up.")


def cmd_network_test(args):
    from .network.validate import run_network_test
    run_network_test(probe_sources=not args.no_sources)


def cmd_validate_sources(args):
    from .pipeline.validation import validate_sources
    summary = validate_sources(limit=args.limit, country=args.country)
    print(json.dumps(summary, indent=2, default=str))


def cmd_audit_sources(args):
    from .pipeline.validation import audit_sources
    rows = audit_sources()
    print(f"{'Country':<18}{'Total':>6}{'Working':>8}  Missing categories")
    print("-" * 70)
    for r in rows:
        print(f"{r['country']:<18}{r['total_sources']:>6}{r['working']:>8}  "
              f"{', '.join(r['missing_categories']) or '—'}")


def cmd_reclassify(args):
    from .pipeline.reclassify import reclassify_all
    print(json.dumps(reclassify_all(), indent=2, default=str))


def cmd_real_report(args):
    from .pipeline.reporting import build_report, print_report
    rep = build_report()
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print_report(rep)


def cmd_stats(args):
    from .database.base import SessionLocal
    from .dashboard import queries as Q
    s = SessionLocal()
    try:
        print(json.dumps({
            "home": {k: (getattr(v, "scan_id", None) if hasattr(v, "scan_id") else v)
                     for k, v in Q.home_stats(s).items()},
            "metrics": Q.secondary_metrics(s),
            "health": Q.source_health(s),
        }, indent=2, default=str))
    finally:
        s.close()


def cmd_report(args):
    """Print the exact initial-run report the spec asks for."""
    from .database.base import SessionLocal
    from .dashboard import queries as Q
    from .models.enums import ListingStatus
    from .models.listing import Listing
    from .models.source import Source
    s = SessionLocal()
    try:
        def block(title, rows, fmt):
            print(f"\n=== {title} ({len(rows)}) ===")
            for r in rows[:25]:
                print("  " + fmt(r))

        active = Q.filter_listings(s, min_confidence=60, order_by="opportunity", limit=50)
        uncertain = [l for l in Q.filter_listings(s, min_confidence=40, limit=100)
                     if l.vehicle_match_confidence < 60]
        archived = s.query(Listing).filter(
            Listing.listing_status.in_([ListingStatus.EXPIRED.value,
                                        ListingStatus.ARCHIVED.value,
                                        ListingStatus.REMOVED.value])).limit(50).all()
        dealers = s.query(Source).filter(Source.source_type.in_(
            ["dealer", "suzuki_dealer", "garage", "youngtimer_dealer"])).all()
        new_sources = Q.newest_sources(s, 25)
        failed = s.query(Source).filter(Source.health.in_(["failed", "degraded"])).all()
        manual = s.query(Source).filter(Source.manual_review.is_(True)).all()

        print("############ INITIAL EUROPEAN DISCOVERY REPORT ############")
        block("ACTIVE IGNIS SPORT LISTINGS", active,
              lambda l: f"[{l.vehicle_match_confidence}] {l.country} {(l.title or '')[:60]} "
                        f"€{l.price_eur or '?'} -> {l.listing_url[:60]}")
        block("UNCERTAIN CANDIDATES", uncertain,
              lambda l: f"[{l.vehicle_match_confidence}] {l.country} {(l.title or '')[:60]}")
        block("ARCHIVED LISTINGS", archived,
              lambda l: f"{l.country} {(l.title or '')[:60]} [{l.listing_status}]")
        block("NEWLY DISCOVERED DEALERS", dealers,
              lambda x: f"{x.domain} ({x.country or '?'}) DV={x.discovery_value}")
        block("NEWLY DISCOVERED SOURCES", new_sources,
              lambda x: f"{x.domain} [{x.source_type}] {x.country or '?'} DV={x.discovery_value}")
        block("FAILED SOURCES", failed,
              lambda x: f"{x.domain} health={x.health} fails={x.failure_count}")
        block("SOURCES REQUIRING MANUAL REVIEW", manual,
              lambda x: f"{x.domain} [{x.source_type}] {(x.notes or '')[:50]}")

        print("\n=== COUNTRY COVERAGE ===")
        for c in Q.country_coverage(s):
            if c["sources"]:
                print(f"  {c['name']:<16} sources={c['sources']:<3} "
                      f"healthy={c['healthy']} failed={c['failed']}")
    finally:
        s.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ignis-hunter",
                                description="Suzuki Ignis Sport Europe Hunter")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create DB and seed sources").set_defaults(func=cmd_init)

    sp = sub.add_parser("scan", help="run one scan cycle")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--no-backup", action="store_true")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("discover", help="run source discovery")
    sp.add_argument("--countries", type=str, default=None, help="comma list e.g. DE,IT")
    sp.add_argument("--max-queries", type=int, default=40)
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("dashboard", help="start web dashboard")
    sp.add_argument("--host", default=None)
    sp.add_argument("--port", type=int, default=None)
    sp.set_defaults(func=cmd_dashboard)

    sub.add_parser("scheduler", help="run long-running scheduler").set_defaults(func=cmd_scheduler)

    sp = sub.add_parser("serve", help="ONE-service production mode: scheduler + "
                                      "dashboard together (Railway/PaaS)")
    sp.add_argument("--port", type=int, default=None)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("add-source", help="add + scan a source URL")
    sp.add_argument("url")
    sp.add_argument("--country", default=None)
    sp.set_defaults(func=cmd_add_source)

    sp = sub.add_parser("analyze-url", help="analyse one listing URL")
    sp.add_argument("url")
    sp.add_argument("--country", default=None)
    sp.set_defaults(func=cmd_analyze_url)

    sub.add_parser("backup", help="back up the database").set_defaults(func=cmd_backup)
    sub.add_parser("stats", help="print JSON stats").set_defaults(func=cmd_stats)
    sub.add_parser("report", help="print initial discovery report").set_defaults(func=cmd_report)

    sp = sub.add_parser("network-test", help="validate real network/API/source connectivity")
    sp.add_argument("--no-sources", action="store_true", help="skip probing sample sources")
    sp.set_defaults(func=cmd_network_test)

    sp = sub.add_parser("validate-sources", help="fetch each source and record live status")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--country", default=None)
    sp.set_defaults(func=cmd_validate_sources)

    sub.add_parser("audit-sources", help="per-country source-coverage gap audit")\
        .set_defaults(func=cmd_audit_sources)

    sub.add_parser("reclassify", help="re-evaluate stored listings + sources with "
                                      "the current classifier")\
        .set_defaults(func=cmd_reclassify)

    sp = sub.add_parser("real-report", help="print the Real-World Validation Report")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_real_report)
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
