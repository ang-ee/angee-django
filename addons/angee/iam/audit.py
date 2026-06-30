"""Reusable GraphQL projection of audit user-references.

``AuditMixin`` (``angee.base.mixins``) stamps ``created_by`` / ``updated_by``
foreign keys on a row. Every audited GraphQL type needs the same projection of
those keys: the actor's public id plus a display label, resolved **without**
exposing the guarded user object — the id goes through IAM's ``user_public_id``
and the label through ``user_display_label``, both of which read under IAM's
elevation and let only the scalar leave. This mixin owns that projection once so
audited types compose it instead of re-declaring the four resolvers.

It is a ``@strawberry.type`` (not an interface): a type composed alongside the
node base (``class FooType(AuthoredRefMixin, AngeeNode)``) merges these fields
into the concrete type without surfacing an extra GraphQL interface in the SDL.
"""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django

from angee.graphql.ids import optional_public_id
from angee.iam.identity import user_display_label, user_public_id
from angee.iam.permissions import request_from_info


@strawberry.type
class AuthoredRefMixin:
    """Project ``AuditMixin`` user references as ``{id, label}`` for a GraphQL type.

    Compose alongside the node base, e.g. ``class PageType(AuthoredRefMixin,
    AngeeNode)``. The ``only=[...]`` hints keep each resolver to the single id
    column it reads.
    """

    @strawberry_django.field(only=["created_by_id"])
    def created_by(self) -> strawberry.ID | None:
        """Return the creator's public id without exposing the user object."""

        return cast("strawberry.ID | None", optional_public_id(user_public_id(cast(Any, self).created_by_id)))

    @strawberry_django.field(only=["created_by_id"])
    def created_by_label(self, info: strawberry.Info) -> str | None:
        """Return the creator's display label - no user object exposed."""

        return user_display_label(cast(Any, self).created_by_id, request=request_from_info(info))

    @strawberry_django.field(only=["updated_by_id"])
    def updated_by(self) -> strawberry.ID | None:
        """Return the last editor's public id without exposing the user object."""

        return cast("strawberry.ID | None", optional_public_id(user_public_id(cast(Any, self).updated_by_id)))

    @strawberry_django.field(only=["updated_by_id"])
    def updated_by_label(self, info: strawberry.Info) -> str | None:
        """Return the last editor's display label - no user object exposed."""

        return user_display_label(cast(Any, self).updated_by_id, request=request_from_info(info))
