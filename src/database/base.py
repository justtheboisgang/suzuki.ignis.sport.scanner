"""Engine + session management. SQLite by default with sane pragmas; the URL
is swappable to PostgreSQL without touching model code."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config.settings import get_settings

_settings = get_settings()
_url = _settings.resolved_database_url()

# check_same_thread only matters for SQLite; harmless to pass conditionally.
_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}

engine: Engine = create_engine(
    _url,
    echo=False,
    future=True,
    connect_args=_connect_args,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - driver level
    """Enable WAL + foreign keys for SQLite so concurrent reads (dashboard)
    don't block the scheduler's writes."""
    if _url.startswith("sqlite"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                            class_=Session, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Session:
    """Return a raw session (caller is responsible for closing)."""
    return SessionLocal()
