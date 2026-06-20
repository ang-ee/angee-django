"""GraphQL identity and pagination primitives for Angee types."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.db import models
from django.db.models import QuerySet
from strawberry.relay.types import NodeIterableType
from strawberry.types import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry_django.relay import DjangoCursorConnection
from typing_extensions import Self

from angee.base.models import public_id_of
from angee.graphql.ids import PublicID, instance_for_id
from angee.graphql.introspection import django_model


@strawberry.interface(name="Node")
class AngeeNode:
    """GraphQL object whose id field is the model's public id."""

    @strawberry.field(description="The public ID of this object.")
    def id(self) -> PublicID:
        """Return this row's public id."""

        return PublicID(public_id_of(cast(models.Model, self)))


@strawberry.type(name="CursorConnection")
class AngeeConnection(DjangoCursorConnection[Any]):
    """Keyset connection that honors each model's ``Meta.ordering``.

    ``DjangoCursorConnection`` keyset pagination orders by the queryset's
    explicit ``order_by``; a queryset that relies only on ``Meta.ordering``
    arrives with an empty ``order_by`` and the connection falls back to the
    primary key. Angee declares total ordering on ``Meta.ordering`` (a
    framework invariant), so apply it explicitly before pagination.
    """

    @classmethod
    def resolve_connection(
        cls,
        nodes: NodeIterableType[Any],
        *,
        info: Info,
        **kwargs: Any,
    ) -> AwaitableOrValue[Self]:
        """Apply the model's declared ordering, then paginate by keyset."""

        if isinstance(nodes, QuerySet) and not nodes.query.order_by and nodes.model._meta.ordering:
            nodes = nodes.order_by(*nodes.model._meta.ordering)
        return super().resolve_connection(nodes, info=info, **kwargs)


def detail(
    node: type,
    *,
    permission_classes: list[type] | None = None,
) -> Any:
    """Return a typed detail root field addressed by a public id."""

    model = django_model(node)

    def resolve(id: PublicID) -> Any | None:
        """Return the row addressed by the public id."""

        return instance_for_id(model, id)

    resolve.__annotations__ = {
        "id": PublicID,
        "return": node | None,
    }
    return strawberry_django.field(
        resolver=resolve,
        permission_classes=permission_classes,
    )
