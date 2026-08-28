"""IAM-owned REBAC relationship cleanup."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Model
from django.db.models.signals import post_delete
from rebac import app_settings
from rebac.roles import revoke


def connect() -> None:
    """Wire swappable user deletion cleanup."""

    user_model = get_user_model()
    post_delete.connect(
        cleanup_platform_admin_role,
        sender=user_model,
        dispatch_uid="angee-iam-platform-admin-delete",
    )


def cleanup_platform_admin_role(
    sender: type[Model],
    instance: Model,
    **kwargs: Any,
) -> None:
    """Revoke the platform-admin role when a user is deleted."""

    del sender, kwargs
    role = app_settings.REBAC_UNIVERSAL_ADMIN_ROLE
    if role:
        revoke(actor=instance, role=role)
