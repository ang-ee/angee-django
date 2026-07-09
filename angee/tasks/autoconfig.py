"""Settings fragments required by Angee's task seam."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

SETTINGS = {
    "CELERY_BROKER_URL": "redis://redis:6379/1",
    "CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP": True,
    "CELERY_TASK_IGNORE_RESULT": True,
    "CELERY_TASK_SOFT_TIME_LIMIT": 840,
    "CELERY_TASK_TIME_LIMIT": 900,
    "CELERY_TASK_TRACK_STARTED": True,
    "CELERY_TIMEZONE": "UTC",
}
"""Django settings contributed when the framework task seam is installed."""


def settings(namespace: Mapping[str, Any]) -> dict[str, Any]:
    """Return environment-sensitive task settings."""

    broker_url = os.environ.get("CELERY_BROKER_URL") or namespace.get("CELERY_BROKER_URL")
    if not broker_url:
        return {}
    return {"CELERY_BROKER_URL": str(broker_url)}
