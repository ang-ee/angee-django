"""Celery entrypoints for decision mail: one question's asked or reminder mail, and the daily digest.

Each retries what a retry may cure (``retry_may_cure``). A database or broker hiccup is
retried on Celery's own short backoff. A system channel that is missing or not
connected (``RETRIED_REFUSALS``) is retried 5, 15 and 45 minutes later, so an operator
has time to restore it; a template row saved mid-run (``TemplateRowChanged``) after
:data:`ROW_CHANGE_RETRY_COUNTDOWN`, since the retry only reads the rows again.
``PERMANENT_ERRORS`` are not retried, so a deployment without a public origin or a
shipped row fails each run once, in its log. The ledgers make a retry mail nobody
twice.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from celery import Task, shared_task
from django.apps import apps

from angee.decisions_messaging.constants import MAIL_DECISION_TASK, MAIL_DIGESTS_TASK
from angee.decisions_messaging.models import PERMANENT_ERRORS, RETRIED_REFUSALS, TemplateRowChanged
from angee.jobs.scheduler import periodic_now

CHANNEL_RETRY_COUNTDOWNS = (5 * 60, 15 * 60, 45 * 60)
"""Seconds before each retry of a channel refusal; their count is the tasks' ``max_retries``."""

ROW_CHANGE_RETRY_COUNTDOWN = 30
"""Seconds before a run whose template row was saved mid-run is retried; the retry reads the rows again."""

_RETRYING: dict[str, Any] = {
    "bind": True, "autoretry_for": (Exception,), "dont_autoretry_for": PERMANENT_ERRORS,
    "retry_backoff": True, "max_retries": len(CHANNEL_RETRY_COUNTDOWNS),
}
"""One retry policy for both tasks: Celery's autoretry for hiccups, :func:`_run` for a channel refusal."""


def _run(task: Task, call: Callable[[], int]) -> int:
    """Run ``call``, retrying a refused run: a changed row soon, a channel after :data:`CHANNEL_RETRY_COUNTDOWNS`."""

    try:
        return call()
    except RETRIED_REFUSALS as refusal:
        countdown = ROW_CHANGE_RETRY_COUNTDOWN if isinstance(refusal, TemplateRowChanged) else (
            CHANNEL_RETRY_COUNTDOWNS[min(task.request.retries, len(CHANNEL_RETRY_COUNTDOWNS) - 1)]
        )
        raise task.retry(exc=refusal, countdown=countdown) from refusal


@shared_task(name=MAIL_DECISION_TASK, **_RETRYING)
def mail_decision(self: Task, decision_id: Any, occasion: str) -> int:
    """Mail one question's ``occasion`` to its deciders."""

    notices = apps.get_model("decisions_messaging", "DecisionNotice").objects
    return _run(self, lambda: notices.mail_decision(decision_id, occasion=occasion))


@shared_task(name=MAIL_DIGESTS_TASK, **_RETRYING)
def mail_digests(self: Task, timestamp: int | None = None) -> int:
    """Mail each active person the open questions waiting on them, once per day."""

    notices = apps.get_model("decisions_messaging", "DigestNotice").objects
    return _run(self, lambda: notices.mail_digests(now=periodic_now(timestamp)))
