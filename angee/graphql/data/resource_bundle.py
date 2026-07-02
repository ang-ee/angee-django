"""Generated Hasura resource bundle inspection helpers."""

from __future__ import annotations

from typing import Any

from strawberry_django_hasura import HasuraResource

from angee.graphql.data.metadata import resource_type_name


def resource_attr(resource: HasuraResource, name: str, fallback: Any) -> Any:
    """Return a HasuraResource attribute, falling back for older package shapes."""

    return getattr(resource, name, fallback)


def resource_type_by_name(resource: HasuraResource, name: str) -> type | None:
    """Return the generated type with GraphQL name ``name`` when present."""

    return next(
        (item for item in resource.types if resource_type_name(item) == name),
        None,
    )


def resource_type_by_suffix(resource: HasuraResource, suffix: str) -> type | None:
    """Return the single generated type whose GraphQL name ends with ``suffix``."""

    matches = [
        item
        for item in resource.types
        if (resource_type_name(item) or "").endswith(suffix)
    ]
    return matches[0] if len(matches) == 1 else None
