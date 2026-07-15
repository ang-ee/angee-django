"""Small task submission API over Celery."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from angee.tasks.celery import app as celery_app


def enqueue_task(
    name: str,
    *,
    kwargs: Mapping[str, Any],
    eta: datetime | None = None,
    queue: str | None = None,
    expires: float | datetime | None = None,
) -> None:
    """Send one named Celery task.

    ``expires`` (seconds or an absolute instant) discards the task if no worker
    picks it up in time — a periodic reconciler enqueues with the tick period so
    a saturated or absent worker never accumulates a stale backlog.
    """

    celery_app.send_task(name, kwargs=dict(kwargs), eta=eta, queue=queue, expires=expires)
