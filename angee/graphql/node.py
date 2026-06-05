"""Relay identity and pagination primitives for Angee GraphQL types."""

from __future__ import annotations

from typing import Any

import strawberry
from django.db.models import QuerySet
from strawberry import relay
from strawberry.relay.types import NodeIterableType, NodeType
from strawberry.types import Info
from strawberry.utils.await_maybe import AwaitableOrValue
from strawberry_django.relay import DjangoCursorConnection
from typing_extensions import Self


@strawberry.interface
class AngeeNode(relay.Node):
    """Relay node whose object id is the model's public sqid."""

    sqid: relay.NodeID[str]


@strawberry.type(name="CursorConnection")
class AngeeConnection(DjangoCursorConnection[relay.NodeType]):
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
        nodes: NodeIterableType[NodeType],
        *,
        info: Info,
        **kwargs: Any,
    ) -> AwaitableOrValue[Self]:
        """Apply the model's declared ordering, then paginate by keyset."""

        if isinstance(nodes, QuerySet) and not nodes.query.order_by and nodes.model._meta.ordering:
            nodes = nodes.order_by(*nodes.model._meta.ordering)
        return super().resolve_connection(nodes, info=info, **kwargs)
