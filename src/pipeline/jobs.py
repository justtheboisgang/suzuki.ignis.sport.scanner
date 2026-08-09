"""Job coordination + persistent job status.

One process runs the scheduler and the dashboard's manual jobs. Heavy jobs
(discovery, scan) share DB write paths, so we serialise them with a single
non-reentrant coordinator lock: only one heavy job runs at a time. For this rare
car, sequential work is perfectly acceptable and far safer than racing SQLite.

Job progress/outcome is persisted in `job_status` (one row per job type) so the
dashboard can show IDLE / RUNNING / FINISHED / FAILED with live metrics that
survive restarts.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable

from ..database.base import session_scope
from ..database.writer import run_write
from ..models.job import JobStatus
from ..utils.logging import get_logger

log = get_logger("pipeline.jobs")

# Only ONE heavy job (discovery or scan) may run at a time in this process.
JOB_LOCK = threading.Lock()


def _now():
    return datetime.now(timezone.utc)


def set_job_status(job_type: str, **fields) -> None:
    """Upsert the single status row for a job type. Telemetry — never raises."""
    def _do(session):
        row = session.query(JobStatus).filter(JobStatus.job_type == job_type).first()
        if row is None:
            row = JobStatus(job_type=job_type)
            session.add(row)
        for k, v in fields.items():
            setattr(row, k, v)
        return None
    run_write(_do, swallow=True, label=f"job_status[{job_type}]")


def get_job_status(job_type: str) -> dict | None:
    with session_scope() as s:
        row = s.query(JobStatus).filter(JobStatus.job_type == job_type).first()
        if not row:
            return None
        return {
            "job_type": row.job_type, "state": row.state,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "queries_total": row.queries_total, "queries_done": row.queries_done,
            "new_domains": row.new_domains, "sources_added": row.sources_added,
            "results": row.results, "duration_seconds": row.duration_seconds,
            "message": row.message, "error": row.error,
            "metrics": row.metrics or {},
        }


def all_job_status() -> dict:
    return {jt: get_job_status(jt) for jt in ("discovery", "scan")}


def is_any_job_running() -> bool:
    return JOB_LOCK.locked()


def run_exclusive(job_type: str, fn: Callable[[], dict]) -> dict:
    """Run `fn` only if no heavy job is currently running. Returns a dict with
    at least {"started": bool}. Records RUNNING/FINISHED/FAILED in job_status."""
    if not JOB_LOCK.acquire(blocking=False):
        log.info("%s requested but another job is running", job_type)
        return {"started": False, "reason": "another job is already running"}

    start = _now()
    set_job_status(job_type, state="RUNNING", started_at=start, finished_at=None,
                   error=None, message=f"{job_type} started",
                   queries_done=0, new_domains=0, sources_added=0, results=0,
                   metrics={})
    try:
        result = fn() or {}
        duration = (_now() - start).total_seconds()
        # Store the whole result as job-type-specific metrics; also mirror a few
        # common scalars into legacy columns for convenience.
        metrics = {k: v for k, v in result.items() if k != "started"}
        set_job_status(job_type, state="FINISHED", finished_at=_now(),
                       duration_seconds=round(duration, 1),
                       queries_total=result.get("queries_planned",
                                                result.get("sources", 0)),
                       queries_done=result.get("queries_run",
                                               result.get("sources", 0)),
                       new_domains=result.get("new_domains", 0),
                       sources_added=result.get("sources_added", 0),
                       results=result.get("listings_ingested",
                                          result.get("listings_accepted", 0)),
                       metrics=metrics,
                       message=f"{job_type} finished in {duration:.0f}s")
        result["started"] = True
        return result
    except Exception as exc:  # noqa: BLE001 - record + report, never crash caller
        log.exception("%s job failed", job_type)
        set_job_status(job_type, state="FAILED", finished_at=_now(),
                       error=f"{type(exc).__name__}: {exc}"[:500],
                       message=f"{job_type} failed")
        return {"started": True, "ok": False, "error": str(exc)}
    finally:
        JOB_LOCK.release()


def make_progress_updater(job_type: str):
    """Return a callback the engines call to stream progress into job_status.
    Accepts `metrics` (a dict of job-specific counters) and/or `message`, plus
    optional legacy scalars. Cheap: one short serialised write per tick."""
    def _update(metrics: dict | None = None, message: str | None = None, **legacy):
        fields = dict(legacy)
        if metrics is not None:
            fields["metrics"] = metrics
            # Mirror common counters into legacy columns for older views.
            for src_key, col in (("planned", "queries_total"),
                                 ("executed", "queries_done"),
                                 ("sources_done", "queries_done"),
                                 ("sources_total", "queries_total"),
                                 ("new_domains", "new_domains"),
                                 ("sources_registered", "sources_added")):
                if src_key in metrics:
                    fields[col] = metrics[src_key]
        if message is not None:
            fields["message"] = message
        if fields:
            set_job_status(job_type, **fields)
    return _update
