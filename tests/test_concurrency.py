"""Concurrency / DB-locking regression tests for the production bug:
  (sqlite3.OperationalError) database is locked  during provider_usage INSERT.

These simulate the real failure modes: two concurrent writers, provider_usage
written while other discovery writes happen, manual+scheduled job collision,
repeated Run-Discovery clicks, transient SQLITE_BUSY retry, and rollback on a
failed write.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy.exc import OperationalError

from src.database.writer import WRITE_LOCK, is_locked_error, run_write, write_session
from src.discovery import budget
from src.models.provider import ProviderUsage
from src.models.source import Source
from src.pipeline import jobs


def test_write_lock_serialises_concurrent_writers(session):
    """Many threads writing at once must all succeed (serialised), never raising
    'database is locked'."""
    errors = []

    def worker(i):
        try:
            def _do(s):
                s.add(Source(domain=f"concurrent-{i}.de", source_type="dealer",
                             base_url=f"https://concurrent-{i}.de/"))
            run_write(_do, label="test.concurrent")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"writers raised: {errors}"
    assert session.query(Source).filter(
        Source.domain.like("concurrent-%.de")).count() == 25


def test_provider_usage_during_other_writes(session):
    """provider_usage logging (telemetry) interleaved with source writes must
    all commit without locking — the exact production scenario."""
    stop = threading.Event()
    errors = []

    def usage_writer():
        n = 0
        while not stop.is_set() and n < 40:
            try:
                budget.record_request("brave", f"Ignis 109 ch {n}", "DE", 1, 20)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            n += 1

    def source_writer():
        for i in range(40):
            try:
                def _do(s, i=i):
                    s.add(Source(domain=f"disc-{i}.de", source_type="dealer",
                                 base_url=f"https://disc-{i}.de/"))
                run_write(_do, label="test.source")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    t1 = threading.Thread(target=usage_writer)
    t2 = threading.Thread(target=source_writer)
    t1.start(); t2.start(); t1.join(); stop.set(); t2.join()

    assert not errors, f"locking errors: {errors}"
    assert session.query(ProviderUsage).count() >= 1
    assert session.query(Source).filter(Source.domain.like("disc-%")).count() == 40


def test_manual_and_scheduled_job_collision(session):
    """Only ONE heavy job may run at a time; a second concurrent job is rejected
    rather than racing the DB."""
    started_order = []
    release = threading.Event()

    def long_job():
        started_order.append("long")
        release.wait(timeout=2)
        return {"queries_run": 1}

    def second_job():
        started_order.append("second-should-not-run")
        return {}

    holder = threading.Thread(
        target=lambda: jobs.run_exclusive("discovery", long_job))
    holder.start()
    time.sleep(0.2)  # let the first job take the lock

    # While the first job holds the lock, a second heavy job is refused.
    result = jobs.run_exclusive("scan", second_job)
    assert result["started"] is False
    assert "second-should-not-run" not in started_order

    release.set()
    holder.join()
    st = jobs.get_job_status("discovery")
    assert st["state"] == "FINISHED"


def test_repeated_run_discovery_is_single_flight(session):
    """Clicking Run Discovery twice quickly must not start two jobs."""
    release = threading.Event()

    def job():
        release.wait(timeout=2)
        return {"queries_run": 2, "new_domains": 0}

    t = threading.Thread(target=lambda: jobs.run_exclusive("discovery", job))
    t.start()
    time.sleep(0.2)
    assert jobs.is_any_job_running() is True
    second = jobs.run_exclusive("discovery", job)
    assert second["started"] is False
    release.set()
    t.join()


def test_transient_sqlite_busy_is_retried(session):
    """run_write retries a transient 'database is locked' error and then
    succeeds, rather than propagating it."""
    calls = {"n": 0}

    def flaky(s):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("INSERT ...", {}, Exception("database is locked"))
        s.add(Source(domain="retry-ok.de", source_type="dealer",
                     base_url="https://retry-ok.de/"))

    run_write(flaky, base_delay=0.01, label="test.retry")
    assert calls["n"] == 2  # failed once, retried, succeeded
    assert session.query(Source).filter(Source.domain == "retry-ok.de").count() == 1


def test_failed_write_rolls_back(session):
    """A non-transient failure rolls back and never half-commits."""
    def boom(s):
        s.add(Source(domain="rollback.de", source_type="dealer",
                     base_url="https://rollback.de/"))
        raise ValueError("boom after add")

    with pytest.raises(ValueError):
        run_write(boom, label="test.rollback")
    # The add must have been rolled back.
    assert session.query(Source).filter(Source.domain == "rollback.de").count() == 0


def test_is_locked_error_detection():
    assert is_locked_error(OperationalError("x", {}, Exception("database is locked")))
    assert not is_locked_error(ValueError("nope"))


def test_swallow_returns_none_on_failure(session):
    """Telemetry writes swallow failures so they never abort a run."""
    def boom(s):
        raise RuntimeError("telemetry fail")
    assert run_write(boom, swallow=True, label="test.swallow") is None
