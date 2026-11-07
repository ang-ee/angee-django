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

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        rows = apps.get_model("projects", "Task").objects.with_actor(actor).for_record(record)
        return rows.untrashed().scoped().select_related(
            "assignee",
        )

    def waiting_on(self, rows: Any, actor: Any) -> models.QuerySet:
        return rows.waiting_on(actor)

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
