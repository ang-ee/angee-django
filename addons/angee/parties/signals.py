"""Parties-owned signal receivers that keep the derived contact counters honest.

``Handle.party`` (the resolved owner) and ``Party.handle_count`` are derived facts
the managers/mixin maintain on every supported save path (``link`` / ``confirm`` /
``dismiss`` / ``resolve``). These receivers cover only the delete-path gap: a raw
or cascaded ``PartyHandle`` delete and a removed ``Handle``. They do **not** fire on
``bulk_create`` or
``QuerySet.update()`` — that class of drift is repaired by the idempotent
``PartyHandleManager.recount`` / ``resolve`` being callable as a repair pass.

Receivers run under ``system_context`` because the derived writes are server-owned
bookkeeping that must land even when the triggering write ran under a bare actor.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db.models.signals import class_prepared, post_delete
from rebac import system_context

from angee.parties.models import Handle, PartyHandle

_DISPATCH_PREFIX = "parties.counters"


def connect() -> None:
    """Wire counter-integrity receivers onto every concrete Handle/PartyHandle model."""

    for model in apps.get_models():
        _bind(model)
    # Models prepared after app population — e.g. test-defined concrete models — bind
    # as their class finalizes, so the receivers cover them too.
    class_prepared.connect(_on_class_prepared, dispatch_uid=f"{_DISPATCH_PREFIX}.class_prepared")


def _on_class_prepared(sender: Any, **kwargs: Any) -> None:
    """Bind receivers onto a newly prepared concrete Handle/PartyHandle model."""

    del kwargs
    _bind(sender)


def _bind(model: Any) -> None:
    """Connect the counter receivers to one concrete Handle or PartyHandle model."""

    if model._meta.abstract:
        return
    label = model._meta.label_lower
    if issubclass(model, PartyHandle):
        post_delete.connect(_resolve_from_link, sender=model, dispatch_uid=f"{_DISPATCH_PREFIX}.phdel.{label}")
    elif issubclass(model, Handle):
        post_delete.connect(_recount_handle_party, sender=model, dispatch_uid=f"{_DISPATCH_PREFIX}.hdel.{label}")


def _resolve_from_link(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Re-resolve a handle's owner after one of its links was saved or deleted."""

    del kwargs
    handle_model = apps.get_model("parties", "Handle")
    with system_context(reason="parties.counters.resolve"):
        # Cascades emit child post_delete signals while their parent may also be
        # disappearing; re-fetch the parent before any derived save.
        handle = handle_model._base_manager.filter(pk=instance.handle_id).first()
        if handle is None:
            return  # the handle itself was deleted; its own receiver recounts the party
        sender.objects.resolve(handle)


def _recount_handle_party(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Recount the party a deleted handle was resolved onto, so its count never sticks."""

    del sender, kwargs
    if instance.party_id is None:
        return
    party_handle_model = apps.get_model("parties", "PartyHandle")
    party_model = apps.get_model("parties", "Party")
    # A Party cascade can delete a resolved Handle while the party itself is
    # already gone; never save the in-memory doomed parent from signal state.
    party = party_model._base_manager.filter(pk=instance.party_id).first()
    if party is None:
        return
    with system_context(reason="parties.counters.recount"):
        party_handle_model.objects.recount(party)
