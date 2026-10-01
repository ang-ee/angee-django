"""Parties-owned identity links and derived contact-counter receivers.

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

import logging
from typing import Any

from django.apps import apps
from django.db import transaction
from django.db.models.signals import post_delete
from rebac import system_context

from angee.base.signals import connect_for_models
from angee.iam.events import person_created
from angee.parties.models import Handle, PartyHandle

_DISPATCH_PREFIX = "parties.counters"
logger = logging.getLogger(__name__)


def connect() -> None:
    """Wire person creation and concrete Handle/PartyHandle counter receivers."""

    person_created.connect(_link_person, dispatch_uid="parties.person_created")
    connect_for_models(post_delete, _resolve_from_link, applies=lambda model: issubclass(model, PartyHandle),
                       dispatch_uid=f"{_DISPATCH_PREFIX}.phdel")
    connect_for_models(post_delete, _recount_handle_party, applies=lambda model: issubclass(model, Handle),
                       dispatch_uid=f"{_DISPATCH_PREFIX}.hdel")


def _link_person(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Create the account's person in IAM's transaction, including on replay."""

    del sender, kwargs
    with system_context(reason="parties.person_created"):
        apps.get_model("parties", "Party").objects.for_user(instance)


def _resolve_from_link(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Re-resolve a handle's owner after one of its links was saved or deleted."""

    del kwargs
    handle_id = instance.handle_id
    # A caller may mutate the deleted instance before this transaction commits.
    handle_model = instance._meta.get_field("handle").remote_field.model

    def repair() -> None:
        try:
            with system_context(reason="parties.counters.resolve"):
                handle = handle_model._base_manager.filter(pk=handle_id).first()
                if handle is None:
                    return
                sender.objects.resolve(handle)
        except Exception:
            logger.exception("Failed to repair PartyHandle resolution after delete", extra={"handle_id": handle_id})

    transaction.on_commit(repair)


def _recount_handle_party(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Recount the party a deleted handle was resolved onto, so its count never sticks."""

    del sender, kwargs
    if instance.party_id is None:
        return
    party_handle_model = apps.get_model("parties", "PartyHandle")
    party_id = instance.party_id
    party_model = instance._meta.get_field("party").remote_field.model

    def repair() -> None:
        try:
            with system_context(reason="parties.counters.recount"):
                party = party_model._base_manager.filter(pk=party_id).first()
                if party is None:
                    return
                party_handle_model.objects.recount(party)
        except Exception:
            logger.exception("Failed to recount Party after Handle delete", extra={"party_id": party_id})

    transaction.on_commit(repair)
