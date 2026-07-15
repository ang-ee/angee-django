"""Spaces-owned lifecycle receivers for membership role reconciliation."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db.models.signals import class_prepared, post_delete, post_save, pre_save
from rebac import system_context

from angee.parties.models import Person
from angee.spaces.models import Membership

_DISPATCH_PREFIX = "spaces.membership_roles"
_USER_NOT_TRACKED = object()


def connect() -> None:
    """Bind role revocation to every concrete Membership model."""

    for model in apps.get_models():
        _bind(model)
    class_prepared.connect(
        _on_class_prepared,
        dispatch_uid=f"{_DISPATCH_PREFIX}.class_prepared",
    )


def _on_class_prepared(sender: Any, **kwargs: Any) -> None:
    """Bind a newly prepared concrete Membership model."""

    del kwargs
    _bind(sender)


def _bind(model: Any) -> None:
    """Connect membership and Person lifecycle hooks to one concrete model."""

    if model._meta.abstract:
        return
    label = model._meta.label_lower
    if issubclass(model, Membership):
        post_delete.connect(
            revoke_membership_roles,
            sender=model,
            dispatch_uid=f"{_DISPATCH_PREFIX}.membership_delete.{label}",
        )
    elif issubclass(model, Person):
        pre_save.connect(
            snapshot_person_user,
            sender=model,
            dispatch_uid=f"{_DISPATCH_PREFIX}.person_pre_save.{label}",
        )
        post_save.connect(
            reconcile_person_memberships,
            sender=model,
            dispatch_uid=f"{_DISPATCH_PREFIX}.person_post_save.{label}",
        )


def snapshot_person_user(
    sender: Any,
    instance: Any,
    raw: bool = False,
    using: str = "default",
    update_fields: Any = None,
    **kwargs: Any,
) -> None:
    """Snapshot the committed Person user before a save that may change it."""

    del kwargs
    if raw or instance._state.adding:
        instance._spaces_previous_user_id = _USER_NOT_TRACKED
        return
    if update_fields is not None and not {"user", "user_id"}.intersection(update_fields):
        instance._spaces_previous_user_id = _USER_NOT_TRACKED
        return
    instance._spaces_previous_user_id = (
        sender._base_manager.using(using).filter(pk=instance.pk).values_list("user_id", flat=True).first()
    )


def reconcile_person_memberships(
    sender: Any,
    instance: Any,
    created: bool = False,
    raw: bool = False,
    **kwargs: Any,
) -> None:
    """Route memberships through their save reconciler after Person.user changes."""

    del sender, kwargs
    previous_user_id = getattr(instance, "_spaces_previous_user_id", _USER_NOT_TRACKED)
    if created or raw or previous_user_id is _USER_NOT_TRACKED or previous_user_id == instance.user_id:
        return
    membership_model = apps.get_model("spaces", "Membership")
    with system_context(reason="spaces.person.user_change"):
        for membership in membership_model._base_manager.filter(party_id=instance.pk).iterator():
            membership.granted_user_id = instance.user_id
            membership.save(update_fields=["granted_user", "updated_at"])


def revoke_membership_roles(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Revoke the deleted roster row's direct group role relationships."""

    del sender, kwargs
    with system_context(reason="spaces.membership.delete"):
        instance.revoke_role_relationships()
