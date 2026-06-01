"""IAM-owned REBAC relationship producers."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Model
from django.db.models.signals import post_delete, post_save
from rebac.roles import grant, revoke

PLATFORM_ADMIN_ROLE = "angee/role:admin"
"""Universal platform-admin role used across Angee addons."""


def connect() -> None:
    """Wire the swappable user model to its platform-admin role grant."""

    user_model = get_user_model()
    post_save.connect(
        sync_platform_admin_role,
        sender=user_model,
        dispatch_uid="angee-iam-platform-admin-save",
    )
    post_delete.connect(
        cleanup_platform_admin_role,
        sender=user_model,
        dispatch_uid="angee-iam-platform-admin-delete",
    )


def sync_platform_admin_role(
    sender: type[Model],
    instance: Model,
    raw: bool = False,
    update_fields: frozenset[str] | None = None,
    **kwargs: Any,
) -> None:
    """Mirror ``is_superuser`` onto ``angee/role:admin#member``.

    Membership in ``angee/role:admin`` is the only thing that grants platform
    admin under ``REBAC_SUPERUSER_BYPASS=False`` — the const-backed ``admin``
    relations in every schema resolve through it — so this runs whenever a
    user's superuser status can change: creation (``createsuperuser``, the ORM,
    the resources loader) and any save that targets ``is_superuser``.

    Bootstrap caveat: a superuser created *without* a ``save()`` —
    ``bulk_create``, ``loaddata``, raw SQL — fires no signal and gets no
    membership, so under bypass-off it would be locked out of its own data.
    Create superusers through a ``save()`` path (after ``rebac sync``), or
    re-grant the role explicitly.
    """

    del sender, kwargs
    if raw:
        return
    # Only is_superuser drives the grant; skip saves that cannot have touched it
    # (login writes ``update_fields={"last_login"}`` on every request). A full
    # save (``update_fields is None``) still reconciles — grant/revoke are
    # idempotent.
    if update_fields is not None and "is_superuser" not in update_fields:
        return
    if getattr(instance, "is_superuser", False):
        grant(actor=instance, role=PLATFORM_ADMIN_ROLE)
    else:
        revoke(actor=instance, role=PLATFORM_ADMIN_ROLE)


def cleanup_platform_admin_role(
    sender: type[Model],
    instance: Model,
    **kwargs: Any,
) -> None:
    """Revoke the platform-admin role when a user is deleted."""

    del sender, kwargs
    revoke(actor=instance, role=PLATFORM_ADMIN_ROLE)
