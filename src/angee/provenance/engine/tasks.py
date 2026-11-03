"""Optional Celery integration using its native task extension point."""

from __future__ import annotations

from typing import Any

from celery.contrib.django.task import DjangoTask

from .context import changeset


class Task(DjangoTask):
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        with changeset(
            fresh=True,
            activity="system",
            change_reason=f"task.{self.name}",
            correlation_id=self.request.id or "",
        ):
            return super().__call__(*args, **kwargs)
