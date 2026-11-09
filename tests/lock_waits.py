"""Contender connections for PostgreSQL lock-wait races.

A race holds a lock in the test's connection, starts the competing operation
through :func:`submit`, and proves with :func:`wait_for_lock` that PostgreSQL
parked it on that lock before the holder commits. Failures raise
``AssertionError``, so pytest modules and composed-host ``TransactionTestCase``
modules report them the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from queue import Queue
from time import monotonic, sleep
from typing import Any

from django.db import close_old_connections, connection, connections


def submit[T](pool: ThreadPoolExecutor, call: Callable[[], T]) -> tuple[Future[T], int]:
    """Run ``call`` on its own database connection; return its future and PostgreSQL backend pid.

    The worker runs in a copy of the caller's context, so the test's attribution
    boundary and actor reach its writes the way a framework thread pool carries
    them, and it closes its connections when ``call`` returns.
    """

    started: Queue[int] = Queue()

    def invoke() -> T:
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                started.put(cursor.fetchone()[0])
            return call()
        finally:
            connections.close_all()

    return pool.submit(copy_context().run, invoke), started.get(timeout=10)


def wait_for_lock(pid: int, future: Future[Any]) -> None:
    """Return once backend ``pid`` waits on a PostgreSQL lock; fail if it finishes first or never waits."""

    deadline = monotonic() + 10
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid])
            if cursor.fetchone() == ("Lock",):
                return
        if future.done():
            raise AssertionError(f"The contender finished before waiting on a lock: {future.result()!r}")
        sleep(0.01)
    raise AssertionError(f"PostgreSQL backend {pid} did not reach a lock wait.")
