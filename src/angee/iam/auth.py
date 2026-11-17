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


class SignInLinkBackend(ModelBackend):
    """Django authentication from a single-use sign-in link, with IAM's session loading.

    Its one credential is ``sign_in_token``. Django's ``authenticate()`` admits
    the backend only for that keyword and masks credential names containing
    ``token`` before ``user_login_failed`` receivers see them, so django-axes
    counts and locks out failed links at the same seam as passwords without the
    token reaching its records. ``SignInLinkManager.redeem`` owns every check.
    """

    @sensitive_variables("sign_in_token")
    def authenticate(self, request: Any, *, sign_in_token: str) -> Any | None:  # type: ignore[override]
        """Return the person ``sign_in_token`` signs in, consuming it, or ``None``."""

        return cast(Any, apps.get_model("iam", "SignInLink")).objects.redeem(sign_in_token)

    # Django's async default wraps ``authenticate``; the parent binds the password one.
    aauthenticate = BaseBackend.aauthenticate  # type: ignore[assignment]
