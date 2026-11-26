"""Receivers of the emailed sign-in link addon: a deactivated account's links are retired."""

from __future__ import annotations

from typing import Any, cast

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save


def retire_sign_in_links(
    sender: Any,
    instance: Any,
    update_fields: Any = None,
    raw: bool = False,
    **kwargs: Any,
) -> None:
    """Retire the outstanding sign-in links of an account saved inactive, so reactivating it revives none.

    Fixture loading (``raw``) writes rows as given and retires nothing.
    """

    if raw or instance.is_active or (update_fields is not None and "is_active" not in update_fields):
        return
    cast(Any, apps.get_model("iam_messaging", "SignInLink")).objects.retire(instance)


def connect() -> None:
    """Retire links on deactivation (idempotent)."""

    post_save.connect(
        retire_sign_in_links, sender=get_user_model(), dispatch_uid="angee.iam_messaging.retire_sign_in_links",
    )
