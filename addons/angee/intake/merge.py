"""Intake's owned contribution to the work task-merge transaction."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.utils import timezone


def move_task_needs(source: Any, canonical: Any) -> int:
    """Move source-task Needs to ``canonical`` with source provenance.

    Work invokes this inside its row-locked atomic merge. The mover touches only
    intake-owned rows, and a second invocation for the same pair finds no source
    rows, making it an exact no-op.
    """

    need_model = apps.get_model("intake", "Need")
    return int(
        need_model._base_manager.filter(task_id=source.pk).update(
            task_id=canonical.pk,
            project_id=canonical.project_id,
            targets_project=False,
            original_task_id=source.pk,
            updated_at=timezone.now(),
        )
    )
