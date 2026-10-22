"""Transactional account identity receivers."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from rebac import system_context

from angee.iam.events import person_created

_USER_NAME_FIELDS = frozenset({"first_name", "last_name", "username"})
"""User fields the person's display name derives from."""


def connect() -> None:
    """Wire person creation and account renames."""

    person_created.connect(_link_person, dispatch_uid="parties.person_created")
    post_save.connect(_follow_user_name, sender=get_user_model(), dispatch_uid="parties.user_name")


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
