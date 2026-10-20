"""Record tasks on the timeline, with live attention from the task queryset."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models
from rebac import to_subject_ref

from angee.timeline.kinds import EventData, TimelineKind, actor_label, target_scope


class TaskKind(TimelineKind):
    """A task waits on its assignee while it is open and readable."""

    id = "projects.task"
    sequence = 50
    source_models = ("projects.Task",)

    def eligible(self, model: type[models.Model]) -> bool:
        return True

    def readable(self, actor: Any) -> models.Q:
        return target_scope(apps.get_model("projects", "Task").objects.with_actor(actor))

    def waiting_on(self, actor: Any) -> models.Q:
        return target_scope(apps.get_model("projects", "Task").objects.with_actor(actor).waiting_on(actor))

    def rows(self) -> Iterable[models.Model]:
        return apps.get_model("projects", "Task").system_queryset().filter(subject_object_id__isnull=False).iterator()

    def events(self, task: Any) -> Iterable[EventData]:
        if task.subject_object_id is None or task.is_trashed:
            return []
        model = task.subject_content_type.model_class()
        if model is None:
            return []
        due = f" · due {task.due_date.isoformat()}" if task.due_date and task.status == task.TaskStatus.OPEN else ""
        assignee = task.assignee
        return [EventData(
            kind=self.id, target=task, record=(model, task.subject_object_id), at=task.created_at,
            title=task.title, detail=f"{task.get_status_display()}{due}",
            actor=str(to_subject_ref(assignee)) if assignee is not None else "", actor_label=actor_label(assignee),
        )]


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (TaskKind(),)
