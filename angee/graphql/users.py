"""GraphQL projections of user references.

``AuditMixin`` stamps ``created_by`` / ``updated_by`` across framework
models, so addon GraphQL types keep needing the same two renderings of a
user id: the opaque public id and a display label. Both resolve without
exposing the guarded user object — an actor-scoped query must never pull a
REBAC-bound user row into its own queryset, so the label reads under
``system_context`` and only a display string leaves the resolver.
"""

from __future__ import annotations

from typing import Any

import strawberry
from django.contrib.auth import get_user_model
from rebac import system_context

from angee.base.models import public_id_of


def user_public_id(user_id: Any) -> strawberry.ID | None:
    """Return a user's opaque public id without fetching the user row."""

    if user_id is None:
        return None
    return strawberry.ID(public_id_of(get_user_model()(id=user_id)))


def user_label(user_id: Any) -> str | None:
    """Return a user's display label without exposing the user object.

    Intended for single-record renderings; resolve lists through their own
    batched shape before exposing this as a column.
    """

    if user_id is None:
        return None
    with system_context(reason="graphql.user_label"):
        user = (
            get_user_model()
            .objects.filter(pk=user_id)
            .only("first_name", "last_name", "username")
            .first()
        )
    if user is None:
        return None
    return str(user.get_full_name() or user.username)
