"""Receivers that mail a question once it is asked and once its reminder is due, after commit."""

from __future__ import annotations

from typing import Any

from angee.decisions.signals import decision_asked, decision_reminder_due
from angee.decisions_messaging.constants import MAIL_DECISION_TASK
from angee.decisions_messaging.models import DecisionNoticeOccasion
from angee.jobs.enqueue import enqueue_task


def mail_asked(sender: Any, *, decision: Any, **kwargs: Any) -> None:
    """Mail the question to its deciders once its admission commits."""

    enqueue_task(MAIL_DECISION_TASK, kwargs={"decision_id": decision.pk, "occasion": DecisionNoticeOccasion.ASKED},
                 robust=True)


def mail_reminder(sender: Any, *, decision: Any, **kwargs: Any) -> None:
    """Mail the question to its deciders once its reminder stamp commits."""

    enqueue_task(MAIL_DECISION_TASK, kwargs={"decision_id": decision.pk, "occasion": DecisionNoticeOccasion.REMINDER},
                 robust=True)


def connect() -> None:
    """Listen for questions asked and reminders due."""

    decision_asked.connect(mail_asked, dispatch_uid="decisions_messaging.mail_asked")
    decision_reminder_due.connect(mail_reminder, dispatch_uid="decisions_messaging.mail_reminder")
