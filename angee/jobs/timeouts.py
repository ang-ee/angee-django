"""Worker time budgets shared by bounded task executors."""

from datetime import timedelta

from django.conf import settings

TASK_SETTLEMENT_RESERVE_SECONDS = 30
"""Time reserved for persisting results before either worker limit expires."""


def task_time_budget() -> timedelta:
    """Reserve settlement time below either worker limit."""

    worker_limit = min(settings.CELERY_TASK_SOFT_TIME_LIMIT, settings.CELERY_TASK_TIME_LIMIT)
    return timedelta(seconds=worker_limit - TASK_SETTLEMENT_RESERVE_SECONDS)
