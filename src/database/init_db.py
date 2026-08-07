"""Schema creation, lightweight migrations and DB backups.

For a SQLite-first project we use `Base.metadata.create_all` plus additive
"migrations" (idempotent ALTER TABLE / new-table creation) rather than a heavy
migration framework. The structure is deliberately Postgres-portable.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.types import Boolean, DateTime, Float, Integer

from ..config.settings import BACKUP_DIR, PROJECT_ROOT, get_settings
from ..models import Base
from .base import engine
from ..utils.logging import get_logger

log = get_logger("database.init")


def init_db() -> None:
    """Create all tables that don't yet exist, then apply additive column
    migrations. Safe to call repeatedly."""
    get_settings().ensure_dirs()
    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()


def _sql_type(col) -> str:
    """Map a SQLAlchemy column type to a portable DDL type string."""
    t = col.type
    if isinstance(t, Integer):
        return "INTEGER"
    if isinstance(t, Boolean):
        return "BOOLEAN"
    if isinstance(t, Float):
        return "FLOAT"
    if isinstance(t, DateTime):
        return "TIMESTAMP"
    # JSON, String, Text and everything else store fine as TEXT in SQLite and
    # are acceptable defaults elsewhere for these additive columns.
    try:
        return t.compile(dialect=engine.dialect)
    except Exception:
        return "TEXT"


def _apply_additive_migrations() -> None:
    """Add any model columns that are missing from existing tables. This keeps
    a long-running SQLite deployment upgradable without a migration framework;
    for Postgres the same ADD COLUMN statements apply."""
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {_sql_type(col)}'
                default = col.default.arg if col.default is not None and \
                    not callable(getattr(col.default, "arg", None)) else None
                if isinstance(default, bool):
                    ddl += f" DEFAULT {1 if default else 0}"
                elif isinstance(default, (int, float)):
                    ddl += f" DEFAULT {default}"
                elif isinstance(default, str):
                    ddl += f" DEFAULT '{default}'"
                try:
                    conn.execute(text(ddl))
                    log.info("migration: added %s.%s", table.name, col.name)
                except Exception as exc:  # column may already exist on race
                    log.debug("migration skip %s.%s: %s", table.name, col.name, exc)


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
