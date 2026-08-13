"""Spaces-owned lifecycle receivers for role and thread-audience reconciliation."""

from __future__ import annotations

from typing import Any, cast

from django.apps import apps
from django.db import models
from django.db.models.signals import class_prepared, m2m_changed, post_delete, post_save, pre_save
from rebac import system_context

from angee.base.rebac_m2m import reconcile_m2m_changed_relationships_on_commit
from angee.parties.models import Person
from angee.spaces.models import Group, Membership, ThreadSpace

_DISPATCH_PREFIX = "spaces.lifecycle"
_USER_NOT_TRACKED = object()


def connect() -> None:
    """Bind spaces lifecycle hooks to every concrete composed model."""

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
    """Connect membership, group, thread, and Person lifecycle hooks."""

    if model._meta.abstract:
        return
    label = model._meta.label_lower
    if issubclass(model, Membership):
        post_delete.connect(
            revoke_membership_roles,
            sender=model,
            dispatch_uid=f"{_DISPATCH_PREFIX}.membership_delete.{label}",
        )
    elif issubclass(model, Group):
        post_delete.connect(
            revoke_group_thread_relationships,
            sender=model,
            dispatch_uid=f"{_DISPATCH_PREFIX}.group_delete.{label}",
        )
    elif issubclass(model, ThreadSpace):
        m2m_changed.connect(
            sync_thread_group_relationships,
            sender=getattr(model, "groups").through,
            dispatch_uid=f"{_DISPATCH_PREFIX}.thread_groups.{label}",
        )
        post_delete.connect(
            revoke_thread_group_relationships,
            sender=model,
            dispatch_uid=f"{_DISPATCH_PREFIX}.thread_delete.{label}",
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


def sync_thread_group_relationships(
    sender: type[models.Model],
    instance: models.Model,
    action: str,
    reverse: bool,
    model: type[models.Model],
    pk_set: set[Any] | None,
    using: str | None = None,
    **kwargs: Any,
) -> None:
    """Mirror Thread.groups M2M edits into stored ``messaging/thread#group`` tuples."""

    del sender, kwargs

    def grant(forward: models.Model, reverse_instance: models.Model) -> None:
        cast(ThreadSpace, forward).grant_group_relationship(cast(Group, reverse_instance))

    def revoke(forward: models.Model, reverse_instance: models.Model) -> None:
        cast(ThreadSpace, forward).revoke_group_relationship(cast(Group, reverse_instance))

    reconcile_m2m_changed_relationships_on_commit(
        instance=instance,
        action=action,
        reverse=reverse,
        model=model,
        pk_set=pk_set,
        forward_field_name="groups",
        reverse_field_name="threads",
        grant=grant,
        revoke=revoke,
        using=using,
        context=lambda: system_context(reason="spaces.thread.groups"),
    )


def revoke_thread_group_relationships(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Revoke stored group-audience tuples for a deleted thread."""

    del sender, kwargs
    with system_context(reason="spaces.thread.delete"):
        instance.revoke_group_relationships()


def revoke_group_thread_relationships(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Revoke stored thread audience tuples where a deleted group is the subject."""

    del sender, kwargs
    with system_context(reason="spaces.group.delete"):
        instance.revoke_thread_relationships()
