"""Worker time budgets shared by bounded task executors."""

from datetime import timedelta

from django.conf import settings

TASK_SETTLEMENT_RESERVE_SECONDS = 30
"""Time reserved for persisting results before either worker limit expires."""


def task_time_budget() -> timedelta:
    """Reserve settlement time below either worker limit."""

    worker_limit = min(settings.CELERY_TASK_SOFT_TIME_LIMIT, settings.CELERY_TASK_TIME_LIMIT)
    return timedelta(seconds=worker_limit - TASK_SETTLEMENT_RESERVE_SECONDS)


def task_claim_lease() -> timedelta:
    """Keep a persisted effect claim fenced until after either worker can survive.

    The hard limit may outlive the soft limit; reserve settlement time after
    both so a reaper cannot supersede a still-running task's row fence.
    """

    return timedelta(
        seconds=max(settings.CELERY_TASK_SOFT_TIME_LIMIT, settings.CELERY_TASK_TIME_LIMIT)
        + TASK_SETTLEMENT_RESERVE_SECONDS,
    )
