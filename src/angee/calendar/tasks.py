"""Celery tasks of the calendar addon: the purge an unsubscribe queues, and its reconciler."""

from __future__ import annotations

from celery import shared_task
from django.apps import apps
from rebac import system_context

from angee.base.locks import LockKey, task_lock
from angee.calendar.models import PURGE_SOURCE_TASK, PURGE_UNSUBSCRIBED_TASK


@shared_task(
    name=PURGE_SOURCE_TASK,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def purge_source(pk: int) -> int:
    """Purge one unsubscribed calendar source through its owner (``CalendarSource.purge``); return its purged events.

    A source that is gone, or connected again, purges nothing.
    """

    with system_context(reason="calendar.tasks.purge_source"):
        source = apps.get_model("calendar", "CalendarSource").system_queryset().filter(pk=pk).first()
        return int(source.purge()) if source is not None else 0


@shared_task(name=PURGE_UNSUBSCRIBED_TASK)
def purge_unsubscribed_sources(timestamp: int | None = None) -> int:
    """Retry every purge an unsubscribe left unfinished (``CalendarSource.purge_unsubscribed``).

    The advisory task lock keeps a slow run from stacking onto the next tick.
    """

    del timestamp
    with task_lock(LockKey("calendar", ("purge_unsubscribed_sources",))) as acquired:
        if not acquired:
            return 0
        with system_context(reason="calendar.tasks.purge_unsubscribed_sources"):
            return int(apps.get_model("calendar", "CalendarSource").purge_unsubscribed())
