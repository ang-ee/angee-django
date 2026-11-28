"""IAM receivers: the sign-in stamp.

Django stamps ``last_login`` from ``user_logged_in`` with a plain instance save.
The account's audit dates are gated (``write__last_login``) so that a consumer's
``write`` never forges them, which would refuse that save. IAM stamps the sign-in
instead, as server-owned bookkeeping under ``system_context``, through a row
update so a sign-in never bumps the account's revision under an open edit.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import user_logged_in
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


def connect() -> None:
    """Replace Django's sign-in stamp with IAM's (idempotent)."""

    user_logged_in.disconnect(dispatch_uid=DJANGO_LAST_LOGIN_UID)
    user_logged_in.connect(stamp_last_login, dispatch_uid="angee.iam.stamp_last_login")
