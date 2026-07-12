"""Django authentication backend adapters owned by IAM."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend as DjangoModelBackend

from angee.iam.models import UserKind


def can_authenticate_user(user: Any) -> bool:
    """Return whether ``user`` is a login-capable human principal."""

    is_active = getattr(user, "is_active", True)
    return is_active and str(getattr(user, "kind", None)) == str(UserKind.PERSON)


class ModelBackend(DjangoModelBackend):
    """Password backend whose session reload uses IAM's named bypass."""

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
