"""Record tasks on the timeline, with live attention from the task queryset."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.timeline.kinds import Presentation, RecordIdentity, TimelineKind, actor_label


class TaskKind(TimelineKind):
    id = "projects.task"
    sequence = 50
    source_models = {
        "projects.Task": (
            "subject_content_type", "subject_object_id", "status", "title", "due_date", "assignee", "is_trashed",
        ),
    }
    previous_source_models = {"projects.Task": ("subject_content_type", "subject_object_id")}

    def eligible(self, model: type[models.Model]) -> bool:
        return apps.get_model("projects", "Task").declares_target(model)

    def queryset(self, record: Any, actor: Any) -> models.QuerySet:
        rows = apps.get_model("projects", "Task").objects.with_actor(actor).for_record(record)
        return rows.untrashed().scoped().select_related(
            "assignee",
        )

    def waiting_on_expression(self, rows: Any, actor: Any) -> models.Expression:
        return rows.waiting_on_expression(actor)

    def present(self, rows: Iterable[Any]) -> dict[int, Presentation]:
        result = {}
        for row in rows:
            due = f" · due {row.due_date.isoformat()}" if row.due_date and row.status == row.TaskStatus.OPEN else ""
            result[row.pk] = Presentation(row.title, f"{row.get_status_display()}{due}", actor_label(row.assignee))
        return result

    def records(self, row: Any) -> Iterable[RecordIdentity]:
        content_type = (
            ContentType.objects.db_manager(row._state.db).get_for_id(row.subject_content_type_id)
            if row.subject_content_type_id is not None else None
        )
        if row.subject_object_id is not None and content_type is not None:
            model = content_type.model_class()
            if model is not None:
                yield model, row.subject_object_id


def timeline_kinds() -> tuple[TimelineKind, ...]:
    return (TaskKind(),)
