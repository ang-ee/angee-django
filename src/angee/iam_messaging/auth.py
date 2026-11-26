"""The sign-in link credential: its ``authenticate()`` keywords and its backend under IAM's login throttle."""

from __future__ import annotations

from typing import Any, cast

from django.apps import apps
from django.contrib.auth.backends import BaseBackend
from django.views.decorators.debug import sensitive_variables

from angee.iam.auth import ModelBackend, mark_spent_credential


def sign_in_credentials(token: str) -> dict[str, str]:
    """Return the ``authenticate()`` credentials of a sign-in link.

    ``sign_in_token`` carries the token; Django masks it before
    ``user_login_failed`` receivers see it. ``sign_in_account`` carries the
    account public id the token names, unmasked: IAM's credential-neutral
    keyword, under which ``axes_username`` records the attempt.
    """

    account = cast(Any, apps.get_model("iam_messaging", "SignInLink")).token_account(token)
    return {"sign_in_token": token, "sign_in_account": account}


class SignInLinkBackend(ModelBackend):
    """Django authentication from a single-use sign-in link, with IAM's session loading.

    Its credentials come from :func:`sign_in_credentials`, and callers run it
    through ``angee.iam.auth.authenticate_once``; Django's ``authenticate()``
    admits the backend only for ``sign_in_token``. django-axes counts and locks
    out failed links at the same seam as passwords, recorded by IAM's
    ``axes_username``, without the token reaching its records. A genuine link
    that is spent is marked through ``mark_spent_credential``.
    ``SignInLinkManager.redeem`` owns every check.
    """

    @sensitive_variables("sign_in_token")
    def authenticate(  # type: ignore[override]
        self,
        request: Any,
        *,
        sign_in_token: str,
        sign_in_account: str = "",
    ) -> Any | None:
        """Return the person ``sign_in_token`` signs in, consuming it, or ``None``."""

        from angee.iam_messaging.models import SignInLinkSpent

        try:
            return cast(Any, apps.get_model("iam_messaging", "SignInLink")).objects.redeem(sign_in_token)
        except SignInLinkSpent:
            mark_spent_credential(request, sign_in_account)
            return None

    # Django's async default wraps ``authenticate``; the parent binds the password one.
    aauthenticate = BaseBackend.aauthenticate  # type: ignore[assignment]
