"""Scheduler wiring.

Jobs:
  * full scan at each SCAN_TIMES entry (default 00/06/12/18) — ≥4/day,
  * source discovery once daily at DISCOVERY_HOUR,
  * a lightweight high-priority sweep every 2h (only priority ≥ 70 sources),
  * a nightly database backup.

The scheduler is a plain BlockingScheduler so it runs happily under systemd or
Docker with no GUI/terminal attached and restarts cleanly.
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config.settings import get_settings
from ..database.init_db import backup_database, init_db
from ..discovery.seed_sources import seed_sources_into_db
from ..pipeline.scan import run_discovery, run_scan
from ..utils.logging import get_logger

log = get_logger("scheduler")


def _job_full_scan():
    log.info("[job] full scan starting")
    try:
        run_scan(force=False)
    except Exception as exc:  # a failed run must not kill the scheduler
        log.exception("full scan job failed: %s", exc)


def _job_priority_sweep():
    log.info("[job] priority sweep starting")
    try:
        # Only high-priority sources; force so they get checked more often.
        from ..database.base import session_scope
        from ..models.source import Source
        with session_scope() as s:
            ids = [row.id for row in s.query(Source)
                   .filter(Source.active.is_(True), Source.priority >= 70).all()]
        if ids:
            run_scan(limit=len(ids), force=True, backup=False)
    except Exception as exc:
        log.exception("priority sweep failed: %s", exc)


def _job_discovery():
    log.info("[job] daily discovery starting")
    try:
        run_discovery(max_queries=60)
    except Exception as exc:
        log.exception("discovery job failed: %s", exc)


def _job_backup():
    try:
        path = backup_database()
        if path:
            log.info("[job] backup written to %s", path)
    except Exception as exc:
        log.exception("backup job failed: %s", exc)


def build_scheduler(blocking: bool = True):
    settings = get_settings()
    init_db()
    seed_sources_into_db()

    tz = settings.timezone
    sched = (BlockingScheduler(timezone=tz) if blocking
             else BackgroundScheduler(timezone=tz))

    for hour, minute in settings.scan_time_list:
        sched.add_job(_job_full_scan, CronTrigger(hour=hour, minute=minute, timezone=tz),
                      id=f"scan_{hour:02d}{minute:02d}", replace_existing=True,
                      misfire_grace_time=3600, coalesce=True)

    sched.add_job(_job_discovery, CronTrigger(hour=settings.discovery_hour, minute=15,
                                              timezone=tz),
                  id="discovery_daily", replace_existing=True,
                  misfire_grace_time=3600, coalesce=True)

    sched.add_job(_job_priority_sweep, CronTrigger(hour="*/2", minute=30, timezone=tz),
                  id="priority_sweep", replace_existing=True,
                  misfire_grace_time=1800, coalesce=True)

    sched.add_job(_job_backup, CronTrigger(hour=4, minute=0, timezone=tz),
                  id="nightly_backup", replace_existing=True, coalesce=True)

    log.info("Scheduler built: scans at %s (%s), discovery at %02d:15",
             settings.scan_time_list, tz, settings.discovery_hour)
    return sched


def run_forever():
    sched = build_scheduler(blocking=True)
    log.info("Scheduler starting. Press Ctrl+C to stop.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover
        log.info("Scheduler stopped.")
