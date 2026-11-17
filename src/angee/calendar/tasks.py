"""Celery tasks of the calendar addon: the purge an unsubscribe queues."""

from __future__ import annotations

from celery import shared_task
from django.apps import apps
from rebac import system_context

from angee.calendar.models import PURGE_SOURCE_TASK


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
