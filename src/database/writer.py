"""Serialised, retrying SQLite writes.

Root-cause fix for the production `database is locked` error: SQLite allows only
ONE writer at a time. In our single Railway process (dashboard + scheduler +
background discovery job in one process) several threads may try to write at
once — and, worse, the discovery/scan loops used to hold ONE long write
transaction open across slow Brave/Claude network calls while telemetry
(`provider_usage`, `ai_usage`) tried to write from a second connection.

This module provides the two primitives every writer must use:

  * ``write_session()`` — a short, lock-serialised write transaction. Acquire
    the process-wide writer lock, open a session, commit (or rollback+close on
    error). The body must NOT do network/API work.
  * ``run_write(fn)``   — same, but retries the whole ``fn`` on a transient
    "database is locked" error with capped exponential backoff, and can
    optionally swallow failures (used for telemetry that must never kill a run).

Reads keep using ``session_scope`` / ``SessionLocal`` directly (WAL allows
concurrent readers). The golden rule enforced across the codebase: never open a
second write while another ``write_session`` is open, and never hold a write
open across network/API calls.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable, TypeVar

from sqlalchemy.exc import OperationalError

from .base import SessionLocal
from ..utils.logging import get_logger

log = get_logger("database.writer")

T = TypeVar("T")

# Process-wide single-writer lock. Re-entrant so the *same* thread can nest a
# helper that also grabs it; cross-thread writers queue in an orderly fashion
# instead of colliding on SQLITE_BUSY.
WRITE_LOCK = threading.RLock()


def is_locked_error(exc: BaseException) -> bool:
    msg = str(getattr(exc, "orig", exc)).lower()
    return ("database is locked" in msg or "database is busy" in msg
            or "sqlite_busy" in msg)


@contextmanager
def write_session():
    """Short, lock-serialised write transaction. Commit on success; rollback +
    close on any error. Keep the body free of network/API calls."""
    with WRITE_LOCK:
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def run_write(fn: Callable[..., T], *, retries: int = 5, base_delay: float = 0.2,
              max_delay: float = 5.0, swallow: bool = False,
              label: str = "write") -> T | None:
    """Run ``fn(session)`` inside a lock-serialised write transaction, retrying
    the whole callable on a transient lock error with exponential backoff.

    * ``swallow=True`` → on final failure, log and return None instead of
      raising (use for telemetry: it must never abort a discovery/scan run).
    """
    attempt = 0
    while True:
        delay = 0.0
        with WRITE_LOCK:
            session = SessionLocal()
            try:
                result = fn(session)
                session.commit()
                return result
            except OperationalError as exc:
                session.rollback()
                if is_locked_error(exc) and attempt < retries:
                    attempt += 1
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    log.warning("%s locked; retry %d/%d in %.2fs",
                                label, attempt, retries, delay)
                elif swallow:
                    log.warning("%s failed after %d attempt(s), swallowed: %s",
                                label, attempt + 1, exc)
                    return None
                else:
                    raise
            except Exception as exc:
                session.rollback()
                if swallow:
                    log.warning("%s failed, swallowed: %s", label, exc)
                    return None
                raise
            finally:
                session.close()
        # Only reached on a transient-lock retry; sleep OUTSIDE the lock so
        # other writers (and the checkpointer) can make progress.
        time.sleep(delay)
