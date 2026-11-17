"""Django authentication backend adapters owned by IAM."""

from __future__ import annotations

from typing import Any, cast

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.backends import ModelBackend as DjangoModelBackend
from django.views.decorators.debug import sensitive_variables


def can_authenticate_user(user: object) -> bool:
    """Admit active people from Django's configured user model, including swaps."""

    return isinstance(user, get_user_model()) and bool(user.is_active and user.is_person)


class ModelBackend(BaseBackend):
    """Django password authentication with IAM session loading and no codename grants.

    Django owns credential verification, timing protection and password upgrades.
    Its permission-neutral base owns the optional permission API; IAM's access
    checks use REBAC actions directly.
    """

    authenticate = DjangoModelBackend.authenticate
    aauthenticate = DjangoModelBackend.aauthenticate

    def user_can_authenticate(self, user: Any) -> bool:
        """Return whether ``user`` may authenticate through IAM login surfaces."""

        return can_authenticate_user(user)

    def get_user(self, user_id: Any) -> Any | None:
        """Return the session user, or ``None`` when the row is gone."""

        try:
            user = get_user_model().objects.get_for_session(user_id)
        except get_user_model().DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None


_SPENT_SIGN_IN_LINK = "angee_iam_spent_sign_in_link"
"""Request attribute ``SignInLinkBackend`` sets when a genuine link is refused."""


def sign_in_credentials(token: str) -> dict[str, str]:
    """Return the ``authenticate()`` credentials of a sign-in link.

    ``sign_in_token`` carries the token; Django masks it before
    ``user_login_failed`` receivers see it. ``sign_in_account`` carries the
    account public id the token names, unmasked, so ``axes_username`` keys
    the attempt by account.
    """

    account = cast(Any, apps.get_model("iam", "SignInLink")).token_account(token)
    return {"sign_in_token": token, "sign_in_account": account}


def axes_username(request: Any, credentials: Any) -> str | None:
    """``AXES_USERNAME_CALLABLE``: a sign-in link's account public id, else axes' own username lookup.

    Each link attempt is thereby throttled under the account it names, never
    in a shared null username.
    """

    from axes.helpers import get_username_from_data

    if credentials and credentials.get("sign_in_account"):
        return str(credentials["sign_in_account"])
    return get_username_from_data(credentials if credentials else getattr(request, "data", request.POST))


def spent_sign_in_link(request: Any, credentials: Any) -> bool:
    """``AXES_WHITELIST_CALLABLE``: exempt the one failure a genuine but spent link just caused.

    ``SignInLinkBackend`` marks the request when a token matched a real link
    that can no longer sign in; this reads and clears the mark, so axes does
    not count that failure as a guess, while the next attempt on the same
    request is lockout-checked and counted as usual.
    """

    return request is not None and bool(vars(request).pop(_SPENT_SIGN_IN_LINK, False))


class SignInLinkBackend(ModelBackend):
    """Django authentication from a single-use sign-in link, with IAM's session loading.

    Its credentials come from :func:`sign_in_credentials`; Django's
    ``authenticate()`` admits the backend only for ``sign_in_token``.
    django-axes counts and locks out failed links at the same seam as
    passwords, keyed by ``axes_username``, without the token reaching its
    records. A genuine link that is spent marks the request for
    ``spent_sign_in_link``. ``SignInLinkManager.redeem`` owns every check.
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

        from angee.iam.models import SignInLinkSpent

        try:
            return cast(Any, apps.get_model("iam", "SignInLink")).objects.redeem(sign_in_token)
        except SignInLinkSpent:
            if request is not None:
                setattr(request, _SPENT_SIGN_IN_LINK, True)
            return None

    # Django's async default wraps ``authenticate``; the parent binds the password one.
    aauthenticate = BaseBackend.aauthenticate  # type: ignore[assignment]
