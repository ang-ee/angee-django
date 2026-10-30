"""Lifecycle of non-login users linked to service-owning records."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction
from rebac import system_context


def sync_service_user(owner: Any, *, prefix: str) -> Any:
    """Create or relabel the stable service user linked to a saved owner."""

    if owner.pk is None:
        raise ValueError("The service owner must be saved before syncing its user.")
    username = f"{prefix}-{owner.sqid}"
    defaults = {"first_name": owner.name, "last_name": "", "email": "", "kind": "service"}
    with system_context(reason="iam.service_user.sync"), transaction.atomic():
        if owner.user_id:
            user = owner.user
            changed = set()
            for field, value in {"username": username, **defaults}.items():
                if getattr(user, field) != value:
                    setattr(user, field, value)
                    changed.add(field)
            if changed:
                user.save(update_fields=changed)
            return user
        user, _ = get_user_model()._base_manager.update_or_create(username=username, defaults=defaults)
        owner.user = user
        type(owner)._base_manager.filter(pk=owner.pk).update(user_id=user.pk)
        return user


def deactivate_service_user(owner: Any) -> None:
    """Retain attribution while deactivating a deleted owner's principal."""

    if owner.user_id:
        with system_context(reason="iam.service_user.deactivate"):
            get_user_model()._base_manager.filter(pk=owner.user_id).update(is_active=False)
