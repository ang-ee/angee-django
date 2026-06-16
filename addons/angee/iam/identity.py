"""Identity helpers: user-reference display without exposing the user object.

The OIDC login/link resolution that used to live here moved to the
``iam_integrate_oidc`` addon (it composes the ``integrate`` connection substrate
with this user). What remains is the pure user-reference display other addons use
to label a grant/principal without pulling a guarded user row into their scope.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from rebac import system_context

from angee.base.models import public_id_of


def user_public_id(user_id: Any) -> str | None:
    """Return a user's opaque public id without fetching the user row."""

    if user_id is None:
        return None
    return public_id_of(get_user_model()(id=user_id))


def user_display_label(user_id: Any) -> str | None:
    """Return a user's display label (name) without exposing the user object.

    Resolved under ``system_context`` (IAM's elevation for server-side
    reads) so an actor-scoped caller never pulls a guarded User row into
    its own queryset — REBAC rejects that; only a display string leaves
    the helper. Intended for the single-record form — not selected as a
    list column.
    """

    if user_id is None:
        return None
    with system_context(reason="iam.identity.user_label"):
        user = (
            get_user_model()
            .objects.filter(pk=user_id)
            .only("first_name", "last_name", "username")
            .first()
        )
    if user is None:
        return None
    return str(user.get_full_name() or user.username)
