"""Work-owned reactions to upstream chatter events."""

from __future__ import annotations

from typing import Any

from django.apps import apps

from angee.messaging.events import message_ingested


def connect() -> None:
    """Listen for new chatter activity through messaging's declared seam."""

    message_ingested.connect(
        wake_snoozed_task,
        dispatch_uid="work.wake_snoozed_task.message_ingested",
    )


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
