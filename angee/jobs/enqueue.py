"""Small task submission API over Celery."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from functools import partial
from typing import Any

from django.db import transaction

from angee.jobs.celery import app as celery_app


def enqueue_task(
    name: str,
    *,
    kwargs: Mapping[str, Any],
    queue: str | None = None,
    expires: float | datetime | None = None,
) -> None:
    """Send one named Celery task after the current transaction commits.

    Without an active transaction the send is immediate. Broker errors propagate
    from the send, including from the committing transaction's exit.

    ``expires`` (seconds or an absolute instant) discards the task if no worker
    picks it up in time — a periodic reconciler enqueues with the tick period so
    a saturated or absent worker never accumulates a stale backlog.
    """

    transaction.on_commit(partial(celery_app.send_task, name, kwargs=dict(kwargs), queue=queue, expires=expires))
