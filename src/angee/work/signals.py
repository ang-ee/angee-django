"""Work-owned reactions to upstream chatter and project lifecycle events."""

from __future__ import annotations

from typing import Any

from django.apps import apps

from angee.messaging.events import message_landed
from angee.projects.events import project_phase_changed, project_status_changed, task_promoted


def connect() -> None:
    """Listen through the declared messaging and composed-project event seams."""

    message_landed.connect(
        wake_snoozed_task,
        dispatch_uid="work.wake_snoozed_task.message_landed",
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

    del sender
    task_model = apps.get_model("projects", "Task")
    threads = {
        row.thread_id: row.thread for row in kwargs.get("created_batch") or (instance,) if row.thread_id is not None
    }
    for thread_id, thread in threads.items():
        if not thread.is_record_chatter:
            continue
        task_model.objects.db_manager(instance._state.db).sudo(reason="work.chatter.wake").wake_from_chatter_thread(
            thread_id,
        )
