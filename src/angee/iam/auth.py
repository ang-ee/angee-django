"""Django authentication backend adapters owned by IAM."""

from __future__ import annotations

from typing import Any, cast

from django.apps import apps
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.backends import ModelBackend as DjangoModelBackend
from django.core.exceptions import BadRequest
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
"""Request attribute holding the account of a genuine link ``SignInLinkBackend`` just refused."""

_AUTHENTICATION_ATTEMPTED = "angee_iam_authentication_attempted"
"""Request attribute set once the request has made its one sign-in attempt."""

ONE_ATTEMPT_MESSAGE = "Only one sign-in attempt is allowed per request."
"""Refusal of a second sign-in attempt within one HTTP request."""


@sensitive_variables("credentials")
def authenticate_once(request: Any, **credentials: Any) -> Any | None:
    """Run the request's one sign-in attempt through Django's ``authenticate()``.

    Password login and sign-in link redemption share this guard: a second
    attempt within one HTTP request (aliased or batched GraphQL fields) is
    refused with :data:`ONE_ATTEMPT_MESSAGE` before ``authenticate()`` runs,
    so a request cannot interleave guesses with a sign-in that axes would
    count differently. A sign-in link's spent mark never outlives the attempt.
    """

    if getattr(request, _AUTHENTICATION_ATTEMPTED, False):
        raise BadRequest(ONE_ATTEMPT_MESSAGE)
    setattr(request, _AUTHENTICATION_ATTEMPTED, True)
    try:
        return authenticate(request, **credentials)
    finally:
        vars(request).pop(_SPENT_SIGN_IN_LINK, None)


def sign_in_credentials(token: str) -> dict[str, str]:
    """Return the ``authenticate()`` credentials of a sign-in link.

    ``sign_in_token`` carries the token; Django masks it before
    ``user_login_failed`` receivers see it. ``sign_in_account`` carries the
    account public id the token names, unmasked, so ``axes_username`` records
    the attempt under that account.
    """

    account = cast(Any, apps.get_model("iam", "SignInLink")).token_account(token)
    return {"sign_in_token": token, "sign_in_account": account}


def axes_username(request: Any, credentials: Any) -> str | None:
    """``AXES_USERNAME_CALLABLE``: a sign-in link's account public id, else axes' own username lookup.

    A link attempt is thereby recorded under the account public id it names
    (the string it carries, for an account that does not exist), never under
    an empty username.
    """

    from axes.helpers import get_username_from_data

    if credentials and credentials.get("sign_in_account"):
        return str(credentials["sign_in_account"])
    return get_username_from_data(credentials if credentials else getattr(request, "data", request.POST))


def spent_sign_in_link(request: Any, credentials: Any) -> bool:
    """``AXES_WHITELIST_CALLABLE``: exempt the failure of a genuine but spent link, and nothing else.

    ``SignInLinkBackend`` marks the request with the refused link's account.
    Only credentials naming that same account are exempt, and the mark is
    cleared when read; :func:`authenticate_once` also clears it when the
    attempt ends, so it cannot exempt another attempt.
    """

    if request is None or not credentials:
        return False
    account = vars(request).get(_SPENT_SIGN_IN_LINK)
    if not account or credentials.get("sign_in_account") != account:
        return False
    del vars(request)[_SPENT_SIGN_IN_LINK]
    return True


class SignInLinkBackend(ModelBackend):
    """Django authentication from a single-use sign-in link, with IAM's session loading.

    Its credentials come from :func:`sign_in_credentials`, and callers run it
    through :func:`authenticate_once`; Django's ``authenticate()`` admits the
    backend only for ``sign_in_token``. django-axes counts and locks out
    failed links at the same seam as passwords, recorded by
    ``axes_username``, without the token reaching its records. A genuine link
    that is spent marks the request with its account for
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
            if request is not None and sign_in_account:
                setattr(request, _SPENT_SIGN_IN_LINK, sign_in_account)
            return None

    # Django's async default wraps ``authenticate``; the parent binds the password one.
    aauthenticate = BaseBackend.aauthenticate  # type: ignore[assignment]
