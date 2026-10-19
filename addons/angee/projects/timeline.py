"""Record tasks on the record timeline: one line per task, waiting on its assignee while open."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac import to_subject_ref

from angee.identity import public_id_of
from angee.timeline.events import target_ref
from angee.timeline.kinds import EventData, TimelineKind, actor_label

KIND = "projects.task"


class TaskKind:
    """A task scheduled on a record: its line waits on the assignee until it is done or dropped."""

    id = KIND
    sequence = 50
    models = ("projects.Task",)

    def eligible(self, model: type[models.Model]) -> bool:
        return model._meta.label != "projects.Task"

    def rows(self) -> Iterable[models.Model]:
        task_model = apps.get_model("projects", "Task")
        return task_model.system_queryset().filter(subject_object_id__isnull=False).iterator()

    def events(self, task: Any) -> Iterable[EventData]:
        if task.subject_object_id is None or getattr(task, "is_trashed", False):
            return []
        record = task.subject
        if record is None:
            return []
        status = task.TaskStatus(task.status)
        due = f" · due {task.due_date.isoformat()}" if task.due_date else ""
        detail = {
            task.TaskStatus.OPEN: f"{task.get_status_display()}{due}",
            task.TaskStatus.DONE: task.get_status_display(),
            task.TaskStatus.DROPPED: task.get_status_display(),
        }[status]
        target_model, target_id = target_ref(task)
        assignee = task.assignee
        return [EventData(
            key=f"{self.id}:{public_id_of(task)}:1", kind=self.id, at=task.created_at, record=record,
            target_model=target_model, target_id=target_id, title=task.title, detail=detail,
            actor=str(to_subject_ref(assignee)) if assignee is not None else "", actor_label=actor_label(assignee),
            needs=(task.assignee_id,) if status == task.TaskStatus.OPEN and task.assignee_id else (),
        )]


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (TaskKind(),)
