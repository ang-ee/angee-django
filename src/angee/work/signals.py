"""Work-owned reactions to upstream chatter and project lifecycle events."""

from __future__ import annotations

from typing import Any

from django.apps import apps

from angee.messaging.events import message_ingested
from angee.projects.events import project_phase_changed, project_status_changed, task_promoted


def connect() -> None:
    """Listen through the declared messaging and composed-project event seams."""

    message_ingested.connect(
        wake_snoozed_task,
        dispatch_uid="work.wake_snoozed_task.message_ingested",
    )
    for event in (task_promoted, project_phase_changed, project_status_changed):
        event.connect(
            follow_project,
            dispatch_uid="work.follow_project",
        )


def follow_project(sender: Any, project: Any, **kwargs: Any) -> None:
    """Follow current project state synchronously; a failed rule aborts the sender's transaction."""

    del sender, kwargs
    project.sync_source_task_stage()


def wake_snoozed_task(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Clear snooze state when a new message lands on a task chatter thread."""

    del sender, kwargs
    try:
        task_model = apps.get_model("projects", "Task")
    except LookupError:
        # Source-model test/profile graphs can install the addon declarations
        # without composing the concrete Task runtime model. The chatter seam
        # is global, so those graphs must stay a no-op rather than failing every
        # unrelated message ingest.
        return
    task_model.wake_from_chatter_thread(instance.thread_id)
