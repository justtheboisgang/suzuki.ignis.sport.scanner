"""End-to-end scan orchestration: crawl → pre-filter → classify → (AI) →
dedup → history → score → alert."""

from .ingest import process_candidate, IngestResult
from .scan import run_scan, run_discovery, recheck_disappeared
from .manual import add_watch_source, analyze_manual_url
from .jobs import (
    run_exclusive,
    is_any_job_running,
    get_job_status,
    all_job_status,
    make_progress_updater,
    JOB_LOCK,
)

__all__ = [
    "process_candidate",
    "IngestResult",
    "run_scan",
    "run_discovery",
    "recheck_disappeared",
    "add_watch_source",
    "analyze_manual_url",
    "run_exclusive",
    "is_any_job_running",
    "get_job_status",
    "all_job_status",
    "make_progress_updater",
    "JOB_LOCK",
]
