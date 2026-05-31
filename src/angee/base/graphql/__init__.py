"""Public GraphQL helpers for Angee runtime schemas."""

from __future__ import annotations

from strawberry_django.pagination import OffsetPaginated

from angee.base.graphql.crud import crud
from angee.base.graphql.events import ChangeEvent
from angee.base.graphql.node import AngeeConnection as Connection
from angee.base.graphql.node import AngeeNode
from angee.base.graphql.schema import DEFAULT_SCHEMA_NAME, GraphQLSchemas
from angee.base.graphql.subscriptions import changes

__all__ = [
    "DEFAULT_SCHEMA_NAME",
    "AngeeNode",
    "ChangeEvent",
    "Connection",
    "GraphQLSchemas",
    "OffsetPaginated",
    "changes",
    "crud",
]
