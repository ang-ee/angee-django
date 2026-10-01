"""Messaging-owned signal receivers wired when the addon is installed.

A record's chatter thread is private to that record, so a hard delete of the record
must tear down the whole thread graph — on the instance ``delete()`` path and the bulk
``QuerySet.delete()`` path alike. ``ThreadAttachment`` binds the record through a
``GenericForeignKey`` the delete collector cannot cascade *up* from: the attachment's FK
points at the ``Thread``, so collecting the attachment never reaches the private thread
or its messages. This connects a ``pre_delete`` receiver to every concrete
``ThreadedModelMixin`` model, now and as new ones are prepared; the receiver runs the
``ThreadAttachment`` owner's teardown inside the collector's own transaction, keeping the
teardown atomic with the row delete on both paths.
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
    """Delete a record's private chatter thread graph before the row itself is deleted."""

    del sender, kwargs
    apps.get_model("messaging", "ThreadAttachment").objects.teardown_for_record(instance)
