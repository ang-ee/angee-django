"""Settings fragments required by Angee's job seam."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SETTINGS: dict[str, object] = {
    "ANGEE_HOOKS:append": ["ANGEE_TASK_LOCK_BACKEND"],
    # Beat keeps its schedule in the database (django-celery-beat) while code owns
    # it: addons declare CELERY_BEAT_SCHEDULE, beat writes those entries into
    # PeriodicTask rows at startup and prunes rows no longer declared. Rows hold
    # run state and the enabled flag; see angee.jobs.scheduler.
    "CELERY_BEAT_SCHEDULER": "angee.jobs.scheduler:DatabaseScheduler",
    "CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP": True,
    "CELERY_TASK_IGNORE_RESULT": True,
    "CELERY_TASK_SOFT_TIME_LIMIT": 840,
    "CELERY_TASK_TIME_LIMIT": 900,
    "CELERY_TASK_TRACK_STARTED": True,
    "CELERY_TIMEZONE": "UTC",
    "CELERY_WORKER_PREFETCH_MULTIPLIER": 1,
}
"""Django settings contributed when the framework job seam is installed."""


def settings(namespace: Mapping[str, Any]) -> dict[str, Any]:
    """Return environment-sensitive job settings.

    The host/stack owns broker topology and supplies ``CELERY_BROKER_URL``
    through its environment or settings namespace; the framework job seam has
    no deployment-specific broker default.
    """

    result: dict[str, Any] = {}
    broker_url = namespace.get("CELERY_BROKER_URL")
    if broker_url:
        result["CELERY_BROKER_URL"] = str(broker_url)
    return result
