"""Task attribution composed through Celery's native app-wide task class."""

from __future__ import annotations

from typing import Any

from celery.contrib.django.task import DjangoTask as CeleryTask

from angee.provenance.context import changeset


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
