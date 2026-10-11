"""Celery's native task base with one changeset per execution."""

from __future__ import annotations

from typing import Any

from celery import Task as CeleryTask

from angee.base.changesets import changeset


class Task(CeleryTask):
    """Keep task execution attribution separate from its owner's authorization."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        with changeset(
            activity="system",
            change_reason=f"task.{self.name}",
            correlation_id=self.request.id or "",
        ):
            return super().__call__(*args, **kwargs)
