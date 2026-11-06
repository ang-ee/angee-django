"""Reusable GraphQL projection of audit user-references.

``AuditMixin`` (``angee.base.models``) stamps ``created_by`` / ``updated_by``
foreign keys on a row. Every audited GraphQL type needs the same projection of
those keys: the actor's public id plus a display label, resolved **without**
exposing the guarded user object — the id goes through IAM's ``user_public_id``
and labels use IAM's narrow scalar label projection. Django annotates each
selected label relation in the record read; only the scalar leaves the resolver. This
mixin owns that projection once so audited types compose the four resolvers;
:class:`TrashedRefMixin` does the same for ``TrashMixin`` stamps.

It is a ``@strawberry.type`` (not an interface): a type composed alongside the
node base (``class FooType(AuthoredRefMixin, AngeeNode)``) merges these fields
into the concrete type without surfacing an extra GraphQL interface in the SDL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry
import strawberry_django
from strawberry_django.utils.requests import get_request

from angee.graphql.ids import optional_public_id
from angee.iam.identity import user_display_label, user_label_annotation, user_public_id


@strawberry.type
class AuthoredRefMixin:
    """Project ``AuditMixin`` user references as ``{id, label}`` for a GraphQL type.

    Compose alongside the node base, e.g. ``class PageType(AuthoredRefMixin,
    AngeeNode)``. Native field hints select only the scalar labels requested.
    """

    @strawberry_django.field(only=["created_by_id"])
    def created_by(self) -> strawberry.ID | None:
        """Return the creator's public id without exposing the user object."""

        return cast("strawberry.ID | None", optional_public_id(user_public_id(cast(Any, self).created_by_id)))

    @strawberry_django.field(
        only=["created_by_id"], annotate={"_iam_created_by_label": user_label_annotation("created_by")},
    )
    def created_by_label(self, info: strawberry.Info) -> str | None:
        """Return the creator's display label - no user object exposed."""

        if "_iam_created_by_label" in cast(Any, self).__dict__:
            return cast(str | None, cast(Any, self)._iam_created_by_label)
        return user_display_label(cast(Any, self).created_by_id, request=get_request(info))

    @strawberry_django.field(only=["updated_by_id"])
    def updated_by(self) -> strawberry.ID | None:
        """Return the last editor's public id without exposing the user object."""

        return cast("strawberry.ID | None", optional_public_id(user_public_id(cast(Any, self).updated_by_id)))

    @strawberry_django.field(
        only=["updated_by_id"], annotate={"_iam_updated_by_label": user_label_annotation("updated_by")},
    )
    def updated_by_label(self, info: strawberry.Info) -> str | None:
        """Return the last editor's display label - no user object exposed."""

        if "_iam_updated_by_label" in cast(Any, self).__dict__:
            return cast(str | None, cast(Any, self)._iam_updated_by_label)
        return user_display_label(cast(Any, self).updated_by_id, request=get_request(info))


@strawberry.type
class TrashedRefMixin:
    """Project ``TrashMixin`` stamps for a GraphQL type: when, why and by whom.

    Compose alongside the node base on a trashable type; the acting user is a
    display label, never the guarded user object.
    """

    @strawberry_django.field(only=["trashed_at"])
    def trashed_at(self) -> datetime | None:
        """Return when the record was moved to the trash."""

        return cast(Any, self).trashed_at

    @strawberry_django.field(only=["trash_reason"])
    def trash_reason(self) -> str:
        """Return the optional reason recorded with the trash."""

        return str(cast(Any, self).trash_reason)

    @strawberry_django.field(
        only=["trashed_by_id"], annotate={"_iam_trashed_by_label": user_label_annotation("trashed_by")},
    )
    def trashed_by_label(self, info: strawberry.Info) -> str | None:
        """Return the display label of the user who moved the record to the trash."""

        if "_iam_trashed_by_label" in cast(Any, self).__dict__:
            return cast(str | None, cast(Any, self)._iam_trashed_by_label)
        return user_display_label(cast(Any, self).trashed_by_id, request=get_request(info))
