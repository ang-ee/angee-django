"""Django authentication owned by IAM: its backend, the one attempt per request and the login throttle's seams."""

from __future__ import annotations

from typing import Any

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

SIGN_IN_ACCOUNT = "sign_in_account"
"""The ``authenticate()`` keyword that names, unmasked, the account a credential other than a password signs in.

``axes_username`` records the attempt under it and ``spent_credential`` reads
the account a spent mark names from it.
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


def axes_username(request: Any, credentials: Any) -> str | None:
    """``AXES_USERNAME_CALLABLE``: the account a credential names, else axes' own username lookup.

    A credential other than a password passes the account it names as
    :data:`SIGN_IN_ACCOUNT`, unmasked, so its attempt is recorded under that
    account (the string it carries, for an account that does not exist),
    never under an empty username.
    """

    from axes.helpers import get_username_from_data

    if credentials and credentials.get(SIGN_IN_ACCOUNT):
        return str(credentials[SIGN_IN_ACCOUNT])
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
    (:data:`SIGN_IN_ACCOUNT`) are exempt, and the mark is cleared when read;
    :func:`authenticate_once` also clears it when the attempt ends, so it
    cannot exempt another attempt.
    """

    if request is None or not credentials:
        return False
    account = vars(request).get(_SPENT_CREDENTIAL)
    if not account or credentials.get(SIGN_IN_ACCOUNT) != account:
        return False
    del vars(request)[_SPENT_CREDENTIAL]
    return True
