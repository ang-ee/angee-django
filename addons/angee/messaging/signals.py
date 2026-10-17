"""Delegate threaded-record deletion to the attachment owner's cleanup.

The retained reverse relation avoids collecting attachments before cleanup can
rebind a retired host's followers and notifications. The existing ``pre_delete``
receiver runs for instance and queryset deletion inside Django's transaction;
the owner removes the edge and any unshared, unmerged private thread graph.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db.models.signals import pre_delete

from angee.base.signals import connect_for_models
from angee.messaging.models import ThreadedModelMixin


def connect() -> None:
    """Wire chatter-thread teardown onto every threaded model, now and as they prepare."""

    connect_for_models(pre_delete, teardown_record_thread,
                       applies=lambda model: issubclass(model, ThreadedModelMixin),
                       dispatch_uid="messaging.chatter_teardown")


def teardown_record_thread(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Preserve retained chatter bookkeeping and tear down the deleted host's edge."""

    del sender, kwargs
    apps.get_model("messaging", "ThreadAttachment").objects.teardown_for_record(instance)
