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
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.signals import class_prepared, pre_delete

from angee.messaging.manager_authority import part_claim_write_is_authorized
from angee.messaging.models import ThreadedModelMixin


def connect() -> None:
    """Wire chatter-thread teardown onto every threaded model, now and as they prepare."""

    for model in apps.get_models():
        _bind_teardown(model)
        _bind_claimed_part_guard(model)
    # Models prepared after app population — e.g. test-defined threaded records — bind as
    # their class is finalized, so the teardown covers them too.
    class_prepared.connect(_on_class_prepared, dispatch_uid="messaging.chatter_teardown.class_prepared")


def _on_class_prepared(sender: Any, **kwargs: Any) -> None:
    """Bind teardown onto a newly prepared model when it is a threaded record."""

    del kwargs
    _bind_teardown(sender)
    _bind_claimed_part_guard(sender)


def _bind_teardown(model: Any) -> None:
    """Connect the chatter-thread teardown receiver to one concrete threaded model."""

    if model._meta.abstract or not issubclass(model, ThreadedModelMixin):
        return
    pre_delete.connect(
        teardown_record_thread,
        sender=model,
        dispatch_uid=f"messaging.chatter_teardown.{model._meta.label_lower}",
    )


def teardown_record_thread(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Delete a record's private chatter thread graph before the row itself is deleted."""

    del sender, kwargs
    apps.get_model("messaging", "ThreadAttachment").objects.teardown_for_record(instance)


def _bind_claimed_part_guard(model: Any) -> None:
    """Bind cascade protection to the concrete runtime Part only."""

    if (
        model._meta.abstract
        or model._meta.label_lower != "messaging.part"
        or not any(field.name == "source_claim" for field in model._meta.fields)
    ):
        return
    pre_delete.connect(
        protect_claimed_part,
        sender=model,
        dispatch_uid="messaging.part_source_claim.pre_delete",
    )


def protect_claimed_part(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Refuse instance, queryset, or cascade deletion of a retained source Part."""

    alias = kwargs.get("using") or instance._state.db or "default"
    with transaction.atomic(using=alias):
        source_claim_id = (
            sender._base_manager.using(alias)
            .select_for_update()
            .filter(pk=instance.pk)
            .values_list("source_claim_id", flat=True)
            .first()
        )
        if source_claim_id is not None and not part_claim_write_is_authorized(alias):
            raise ValidationError(
                "Delete claim-backed Parts through the historical file owner."
            )
