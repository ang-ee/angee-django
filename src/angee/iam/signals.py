"""IAM receivers: the sign-in stamp and the retirement of a deactivated account's sign-in links.

Django stamps ``last_login`` from ``user_logged_in`` with a plain instance save.
The account's audit dates are gated (``write__last_login``) so that a consumer's
``write`` never forges them, which would refuse that save. IAM stamps the sign-in
instead, as server-owned bookkeeping under ``system_context``, through a row
update so a sign-in never bumps the account's revision under an open edit.
"""

from __future__ import annotations

from typing import Any, cast

from django.apps import apps
from django.contrib.auth import get_user_model, user_logged_in
from django.db.models.signals import post_save
from django.utils import timezone
from rebac import system_context

DJANGO_LAST_LOGIN_UID = "update_last_login"
"""The ``dispatch_uid`` Django's auth app connects its own stamp with."""


def stamp_last_login(sender: Any, user: Any, **kwargs: Any) -> None:
    """Record the sign-in time on the account that just signed in."""

    now = timezone.now()
    with system_context(reason="iam.sign_in.last_login"):
        type(user)._base_manager.filter(pk=user.pk).update(last_login=now)
    user.last_login = now


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
    cast(Any, apps.get_model("iam", "SignInLink")).objects.retire(instance)


def connect() -> None:
    """Replace Django's sign-in stamp with IAM's and retire links on deactivation (idempotent)."""

    user_logged_in.disconnect(dispatch_uid=DJANGO_LAST_LOGIN_UID)
    user_logged_in.connect(stamp_last_login, dispatch_uid="angee.iam.stamp_last_login")
    post_save.connect(retire_sign_in_links, sender=get_user_model(), dispatch_uid="angee.iam.retire_sign_in_links")
