"""Record tasks on the timeline, with live attention from the task queryset."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import models

from angee.timeline.kinds import Presentation, RecordIdentity, TimelineKind, actor_label


class TaskKind(TimelineKind):
    id = "projects.task"
    sequence = 50
    source_models = ("projects.Task",)

    def eligible(self, model: type[models.Model]) -> bool:
        return apps.get_model("projects", "Task").declares_target(model)

    def occurs(self, record: Any) -> bool:
        task = apps.get_model("projects", "Task")
        return task._base_manager.filter(self._record_filter(record)).exists()

    def _record_filter(self, record: Any) -> models.Q:
        tasks = apps.get_model("projects", "Task").objects
        return tasks.for_record_filter(record) & tasks.untrashed_q()

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        rows = apps.get_model("projects", "Task").objects.with_actor(actor).filter(self._record_filter(record))
        return rows.scoped().select_related(
            "assignee",
        )

    def waiting_on(self, actor: Any) -> models.QuerySet:
        task = apps.get_model("projects", "Task")
        return task._base_manager.filter(task.objects.waiting_on_filter(actor))

    def present(self, rows: Iterable[Any]) -> dict[int, Presentation]:
        result = {}
        for row in rows:
            due = f" · due {row.due_date.isoformat()}" if row.due_date and row.status == row.TaskStatus.OPEN else ""
            result[row.pk] = Presentation(row.title, f"{row.get_status_display()}{due}", actor_label(row.assignee))
        return result

    def records(self, row: Any) -> Iterable[RecordIdentity]:
        if row.subject_object_id is not None and (model := row.subject_content_type.model_class()) is not None:
            yield model, row.subject_object_id


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (TaskKind(),)
