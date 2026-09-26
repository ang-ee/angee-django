"""Settings fragments required by Angee's job seam."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

SETTINGS: dict[str, int | str] = {
    # Beat keeps its schedule in the database (django-celery-beat): addons still
    # declare CELERY_BEAT_SCHEDULE in code, beat syncs those entries into
    # PeriodicTask rows at startup, and the rows hold run state and edits.
    "CELERY_BEAT_SCHEDULER": "django_celery_beat.schedulers:DatabaseScheduler",
    "CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP": True,
    "CELERY_TASK_IGNORE_RESULT": True,
    "CELERY_TASK_SOFT_TIME_LIMIT": 840,
    "CELERY_TASK_TIME_LIMIT": 900,
    "CELERY_TASK_TRACK_STARTED": True,
    "CELERY_TIMEZONE": "UTC",
}
"""Django settings contributed when the framework job seam is installed."""


def settings(namespace: Mapping[str, Any]) -> dict[str, Any]:
    """Return environment-sensitive job settings.

    The host/stack owns broker topology and supplies ``CELERY_BROKER_URL``
    through its environment or settings namespace; the framework job seam has
    no deployment-specific broker default.
    """

    result: dict[str, Any] = {}
    broker_url = os.environ.get("CELERY_BROKER_URL") or namespace.get("CELERY_BROKER_URL")
    if broker_url:
        result["CELERY_BROKER_URL"] = str(broker_url)
    return result
