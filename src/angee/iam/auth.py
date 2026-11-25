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


SESSION_BACKEND = "angee.iam.auth.ModelBackend"
"""The backend path every session records, whatever credential opened it; IAM's ``ModelBackend`` loads it.

A credential backend's caller passes it to ``login(request, user, backend=...)``,
so the session outlives that backend's removal or rename.
"""

_SPENT_CREDENTIAL = "angee_iam_spent_credential"
"""Request attribute holding the account a credential backend just refused as genuine but spent."""

_AUTHENTICATION_ATTEMPTED = "angee_iam_authentication_attempted"
"""Request attribute set once the request has made its one sign-in attempt."""

ONE_ATTEMPT_MESSAGE = "Only one sign-in attempt is allowed per request."
"""Refusal of a second sign-in attempt within one HTTP request."""


@sensitive_variables("credentials")
def authenticate_once(request: Any, **credentials: Any) -> Any | None:
    """Run the request's one sign-in attempt through Django's ``authenticate()``.

    Every sign-in shares this guard, whatever credential it presents: a second
    attempt within one HTTP request (aliased or batched GraphQL fields) is
    refused with :data:`ONE_ATTEMPT_MESSAGE` before ``authenticate()`` runs,
    so a request cannot interleave guesses with a sign-in that axes would
    count differently. A spent-credential mark never outlives the attempt.
    """

    if getattr(request, _AUTHENTICATION_ATTEMPTED, False):
        raise BadRequest(ONE_ATTEMPT_MESSAGE)
    setattr(request, _AUTHENTICATION_ATTEMPTED, True)
    try:
        return authenticate(request, **credentials)
    finally:
        vars(request).pop(_SPENT_CREDENTIAL, None)


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
    """``AXES_USERNAME_CALLABLE``: the account a credential names, else axes' own username lookup.

    A credential other than a password passes the account it names as
    ``sign_in_account``, unmasked, so its attempt is recorded under that
    account (the string it carries, for an account that does not exist),
    never under an empty username.
    """

    from axes.helpers import get_username_from_data

    if credentials and credentials.get("sign_in_account"):
        return str(credentials["sign_in_account"])
    return get_username_from_data(credentials if credentials else getattr(request, "data", request.POST))


def mark_spent_credential(request: Any, account: str) -> None:
    """Mark this attempt's refusal as a genuine but spent credential for ``account``, which is not a guess.

    A credential backend calls it when it refuses a credential its holder
    really had (used, expired, replaced), then returns ``None``;
    :func:`spent_credential` exempts that one failure. Nothing is marked
    without a request or an account.
    """

    if request is not None and account:
        setattr(request, _SPENT_CREDENTIAL, account)


def spent_credential(request: Any, credentials: Any) -> bool:
    """``AXES_WHITELIST_CALLABLE``: exempt the failure of a genuine but spent credential, and nothing else.

    Only credentials naming the account :func:`mark_spent_credential` marked
    (``sign_in_account``) are exempt, and the mark is cleared when read;
    :func:`authenticate_once` also clears it when the attempt ends, so it
    cannot exempt another attempt.
    """

    if request is None or not credentials:
        return False
    account = vars(request).get(_SPENT_CREDENTIAL)
    if not account or credentials.get("sign_in_account") != account:
        return False
    del vars(request)[_SPENT_CREDENTIAL]
    return True


class SignInLinkBackend(ModelBackend):
    """Django authentication from a single-use sign-in link, with IAM's session loading.

    Its credentials come from :func:`sign_in_credentials`, and callers run it
    through :func:`authenticate_once`; Django's ``authenticate()`` admits the
    backend only for ``sign_in_token``. django-axes counts and locks out
    failed links at the same seam as passwords, recorded by
    ``axes_username``, without the token reaching its records. A genuine link
    that is spent is marked through :func:`mark_spent_credential`.
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

        from angee.iam.models import SignInLinkSpent

        try:
            return cast(Any, apps.get_model("iam", "SignInLink")).objects.redeem(sign_in_token)
        except SignInLinkSpent:
            mark_spent_credential(request, sign_in_account)
            return None

    # Django's async default wraps ``authenticate``; the parent binds the password one.
    aauthenticate = BaseBackend.aauthenticate  # type: ignore[assignment]
