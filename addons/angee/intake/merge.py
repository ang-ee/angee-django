"""Intake's owned contribution to the work task-merge transaction."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError

from angee.base.errors import RecordAccessSubjectRefused


def move_task_needs(source: Any, canonical: Any) -> int:
    """Move source-task Needs to ``canonical`` with source provenance.

    Work invokes this inside its row-locked atomic merge. The mover touches only
    intake-owned rows, and a second invocation for the same pair finds no source
    rows, making it an exact no-op.
    """

    need_model = apps.get_model("intake", "Need")
    rows = (
        need_model.objects.sudo(reason="intake.need.merge").lock_if_supported().filter(task_id=source.pk).order_by("pk")
    )
    moved = 0
    for need in rows.iterator():
        need.task = canonical
        need.original_task_id = source.pk
        try:
            need.save(update_fields=("task", "original_task"))
        except RecordAccessSubjectRefused as error:
            # Work's action guard preserves field errors, and its transaction
            # rolls back every contributor and the task's own moved relations.
            raise ValidationError({"canonical": error.code}) from error
        moved += 1
    return moved
