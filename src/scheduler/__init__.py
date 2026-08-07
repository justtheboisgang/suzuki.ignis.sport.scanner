"""APScheduler-based long-running scheduler. Runs ≥4 full scans/day plus daily
source discovery and DB backups, independent of any terminal/browser."""

from .runner import build_scheduler, run_forever

__all__ = ["build_scheduler", "run_forever"]
