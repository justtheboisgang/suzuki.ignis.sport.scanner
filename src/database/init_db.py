"""Schema creation, lightweight migrations and DB backups.

For a SQLite-first project we use `Base.metadata.create_all` plus additive
"migrations" (idempotent ALTER TABLE / new-table creation) rather than a heavy
migration framework. The structure is deliberately Postgres-portable.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect

from ..config.settings import BACKUP_DIR, PROJECT_ROOT, get_settings
from ..models import Base
from .base import engine


def init_db() -> None:
    """Create all tables that don't yet exist. Safe to call repeatedly."""
    get_settings().ensure_dirs()
    Base.metadata.create_all(bind=engine)


def existing_tables() -> list[str]:
    return inspect(engine).get_table_names()


def backup_database() -> Path | None:
    """Copy the SQLite file to data/backups/ with a timestamp. No-op for
    non-SQLite databases (use pg_dump in that case — see README)."""
    settings = get_settings()
    url = settings.resolved_database_url()
    if not url.startswith("sqlite:///"):
        return None
    db_path = Path(url.replace("sqlite:///", ""))
    if not db_path.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"ignis_hunter-{stamp}.db"
    # Use SQLite's online backup semantics via file copy of the main db file.
    shutil.copy2(db_path, dest)
    _prune_backups(keep=14)
    return dest


def _prune_backups(keep: int = 14) -> None:
    files = sorted(BACKUP_DIR.glob("ignis_hunter-*.db"), reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


if __name__ == "__main__":  # pragma: no cover
    init_db()
    print("Initialized tables:", ", ".join(existing_tables()))
