"""Transactional account identity and Handle counter receivers."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_save
from rebac import system_context

from angee.base.signals import connect_for_models
from angee.iam.events import person_created
from angee.parties.models import Handle

_DISPATCH_PREFIX = "parties.counters"
_USER_NAME_FIELDS = frozenset({"first_name", "last_name", "username"})
"""User fields the person's display name derives from."""


def connect() -> None:
    """Wire person creation, account renames and the concrete Handle counter receiver."""

    person_created.connect(_link_person, dispatch_uid="parties.person_created")
    post_save.connect(_follow_user_name, sender=get_user_model(), dispatch_uid="parties.user_name")
    connect_for_models(
        post_delete,
        _recount_handle_party,
        applies=lambda model: issubclass(model, Handle),
        dispatch_uid=f"{_DISPATCH_PREFIX}.hdel",
    )


def _link_person(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Create the account's person in IAM's transaction, including on replay."""

    del sender, kwargs
    with system_context(reason="parties.person_created"):
        apps.get_model("parties", "Party").objects.for_user(instance)


def _follow_user_name(
    sender: Any,
    instance: Any,
    created: bool,
    update_fields: Any = None,
    raw: bool = False,
    **kwargs: Any,
) -> None:
    """Rename the account's person when a saved user's name may have changed."""

    del sender, kwargs
    if created or raw or (update_fields is not None and not _USER_NAME_FIELDS & set(update_fields)):
        return
    with system_context(reason="parties.user_name"):
        apps.get_model("parties", "Party").objects.follow_user_name(instance)


def _recount_handle_party(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Refresh the previous owner's counter inside the delete transaction."""
    if instance.party_id is None:
        return
    with system_context(reason="parties.counters.recount"):
        party = apps.get_model("parties", "Party")._base_manager.filter(pk=instance.party_id).first()
        if party is not None:
            apps.get_model("parties", "Handle").objects.recount(party)
