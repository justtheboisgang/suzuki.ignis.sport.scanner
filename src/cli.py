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


def cmd_add_source(args):
    from .pipeline.manual import add_watch_source
    print(json.dumps(add_watch_source(args.url, country=args.country), indent=2, default=str))


def cmd_analyze_url(args):
    from .pipeline.manual import analyze_manual_url
    print(json.dumps(analyze_manual_url(args.url, country=args.country), indent=2, default=str))


def cmd_backup(args):
    path = backup_database()
    print(f"Backup written to {path}" if path else "No SQLite DB to back up.")


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
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
