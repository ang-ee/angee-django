"""Settings fragments required by Angee's job seam."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SETTINGS = {
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
    # Beat's schedule state file belongs in the stack's data dir, never in beat's
    # workdir — a beat whose workdir is the project root would otherwise litter
    # celerybeat-schedule* into the project source tree.
    if data_dir := namespace.get("ANGEE_DATA_DIR"):
        result["CELERY_BEAT_SCHEDULE_FILENAME"] = str(Path(data_dir) / "celerybeat-schedule")
    broker_url = os.environ.get("CELERY_BROKER_URL") or namespace.get("CELERY_BROKER_URL")
    if broker_url:
        result["CELERY_BROKER_URL"] = str(broker_url)
    return result
