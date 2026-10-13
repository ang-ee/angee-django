"""Celery application and execution boundary for Angee jobs."""

from __future__ import annotations

import os
from typing import Any

from celery import Celery
from celery import Task as CeleryTask

from angee.base.changesets import changeset

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "angee.compose.settings")


class Task(CeleryTask):
    """Own task attribution; the task binds its principal before captured writes."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        with changeset(
            fresh=True,
            activity="system",
            change_reason=f"task.{self.name}",
            correlation_id=self.request.id or "",
        ):
            return super().__call__(*args, **kwargs)


app = Celery("angee", task_cls=Task)
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
