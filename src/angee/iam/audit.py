"""Reusable GraphQL projection of audit user-references.

``AuditMixin`` (``angee.base.models``) stamps ``created_by`` / ``updated_by``
foreign keys on a row. Every audited GraphQL type needs the same projection of
those keys: the actor's public id plus a display label, resolved **without**
exposing the guarded user object — the id goes through IAM's ``user_public_id``
and labels use IAM's narrow, elevated user-label queryset. Native Django prefetch
batches each selected label relation; only the scalar leaves the resolver. This
mixin owns that projection once so audited types compose the four resolvers;
:class:`TrashedRefMixin` does the same for ``TrashMixin`` stamps.

It is a ``@strawberry.type`` (not an interface): a type composed alongside the
node base (``class FooType(AuthoredRefMixin, AngeeNode)``) merges these fields
into the concrete type without surfacing an extra GraphQL interface in the SDL.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any, cast

import strawberry
import strawberry_django
from django.db.models import DEFERRED, Model, Prefetch
from django.http import HttpRequest
from strawberry_django.utils.requests import get_request

from angee.graphql.ids import optional_public_id
from angee.iam.identity import user_display_label, user_label, user_label_queryset, user_public_id


def user_label_prefetch(relation: str, _info: strawberry.Info) -> Prefetch:
    """Bind native relation loading to IAM's scalar-label projection owner."""

    return Prefetch(relation, queryset=user_label_queryset(), to_attr=f"_iam_{relation}_label_source")


def user_relation_label(row: Model, relation: str, *, request: HttpRequest | None) -> str | None:
    """Read the same named label projection as user_label_prefetch, with a request-memo fallback."""

    user = getattr(row, f"_iam_{relation}_label_source", DEFERRED)
    if user is not DEFERRED:
        return user_label(user) if user is not None else None
    return user_display_label(getattr(row, f"{relation}_id"), request=request)


@strawberry.type
class AuthoredRefMixin:
    """Project ``AuditMixin`` user references as ``{id, label}`` for a GraphQL type.

    Compose alongside the node base, e.g. ``class PageType(AuthoredRefMixin,
    AngeeNode)``. Native field hints select the FK columns and prefetch narrow
    label sources only when those labels are selected.
    """

    @strawberry_django.field(only=["created_by_id"])
    def created_by(self) -> strawberry.ID | None:
        """Return the creator's public id without exposing the user object."""

        return cast("strawberry.ID | None", optional_public_id(user_public_id(cast(Any, self).created_by_id)))

    @strawberry_django.field(only=["created_by_id"], prefetch_related=[partial(user_label_prefetch, "created_by")])
    def created_by_label(self, info: strawberry.Info) -> str | None:
        """Return the creator's display label - no user object exposed."""

        return user_relation_label(cast(Any, self), "created_by", request=get_request(info))

    @strawberry_django.field(only=["updated_by_id"])
    def updated_by(self) -> strawberry.ID | None:
        """Return the last editor's public id without exposing the user object."""

        return cast("strawberry.ID | None", optional_public_id(user_public_id(cast(Any, self).updated_by_id)))

    @strawberry_django.field(only=["updated_by_id"], prefetch_related=[partial(user_label_prefetch, "updated_by")])
    def updated_by_label(self, info: strawberry.Info) -> str | None:
        """Return the last editor's display label - no user object exposed."""

        return user_relation_label(cast(Any, self), "updated_by", request=get_request(info))


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

    @strawberry_django.field(only=["trashed_by_id"], prefetch_related=[partial(user_label_prefetch, "trashed_by")])
    def trashed_by_label(self, info: strawberry.Info) -> str | None:
        """Return the display label of the user who moved the record to the trash."""

        return user_relation_label(cast(Any, self), "trashed_by", request=get_request(info))
