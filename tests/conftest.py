"""Pytest fixtures. Every DB-touching test runs against an isolated temporary
SQLite database so the real data/ignis_hunter.db is never mutated by tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# IMPORTANT: point the app at a throwaway DB *before* importing any src module
# (src.database.base binds the engine at import time from these settings).
_TMP_DB = Path(tempfile.mkdtemp(prefix="ignis-test-")) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "")  # force AI fallback in tests
os.environ.setdefault("SEARCH_PROVIDER", "none")

import pytest  # noqa: E402

from src.database.base import SessionLocal, engine  # noqa: E402
from src.database.init_db import init_db  # noqa: E402
from src.models import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def session():
    """A clean session; each test's rows are removed afterwards."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        # Clean all tables so tests stay independent.
        for table in reversed(Base.metadata.sorted_tables):
            s.execute(table.delete())
        s.commit()
        s.close()
