"""Transaction-local limits, savepoint isolation and retryable SQLSTATEs for workflow execution."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from django.db import connection, transaction

from angee.workflows.steps import Superseded

logger = logging.getLogger(__name__)
RETRYABLE_SQLSTATES = frozenset({"57014", "40P01", "55P03"})
"""A canceled statement, a deadlock or an unavailable lock: the transaction may succeed when retried."""


def sqlstate(error: Exception) -> str | None:
    """Read the native database driver's SQLSTATE at one boundary."""
    # psycopg database errors carry SQLSTATE; other Django DB causes need not.
    return getattr(error.__cause__, "sqlstate", None)


@contextmanager
def database_timeout(timeout: timedelta, *, setting: str = "statement_timeout") -> Iterator[None]:
    """Bound each PostgreSQL statement, flooring sub-millisecond limits to 1 ms."""
    previous = None
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting(%s)", [setting])
            previous = cursor.fetchone()[0]
            cursor.execute(
                "SELECT set_config(%s, %s, true)",
                [setting, str(max(1, int(timeout.total_seconds() * 1000)))],
            )
    try:
        yield
    finally:
        if previous is not None:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config(%s, %s, true)", [setting, previous])


@contextmanager
def isolate_failure(operation: str) -> Iterator[None]:
    """Run a diagnostic write in its own savepoint and log its failure as ``operation``.

    A lost claim (``Superseded``) still propagates and aborts its execution transaction.
    """
    try:
        with transaction.atomic():
            yield
    except Superseded:
        raise
    except Exception:
        logger.exception("Workflow %s failed.", operation)
