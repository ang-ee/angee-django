"""Compose named GraphQL schemas by merging addon-contributed parts.

Addons declare schema parts in their ``graphql.py`` ``schemas`` mapping, keyed
by schema name; each part is a mapping of merge buckets
(``query``/``mutation``/``subscription``/``types``/``extensions``) to ordinary
Strawberry classes. The framework asks each discovered addon config for its
parts, folds the contributions for one name in addon order, and merges each
root with ``strawberry.tools.merge_types``. Field collisions across
surfaces fail fast, so two addons cannot claim the same root field.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

import strawberry
from django.core.exceptions import ImproperlyConfigured
from strawberry.tools import merge_types

from angee.base.apps import SCHEMA_PART_KEYS, BaseAddonConfig, SchemaParts
from angee.base.discovery import discover_addons
from angee.base.graphql.introspection import (
    surface_field_names,
    surface_name,
)

DEFAULT_SCHEMA_NAME = "public"

_ROOT_TYPE_NAMES = {
    "query": "Query",
    "mutation": "Mutation",
    "subscription": "Subscription",
}


def collect_schema_parts(
    addons: Iterable[BaseAddonConfig] | None = None,
) -> dict[str, SchemaParts]:
    """Return merged schema parts per name, folded in addon order."""

    discovered = discover_addons() if addons is None else tuple(addons)
    collected: dict[str, SchemaParts] = {}
    for addon in discovered:
        for name, parts in addon.schema_parts.items():
            bucket = collected.setdefault(
                name, {key: () for key in SCHEMA_PART_KEYS}
            )
            for key in SCHEMA_PART_KEYS:
                bucket[key] = _dedupe_by_identity(bucket[key] + parts[key])
    return collected


def collect_schema_names(
    addons: Iterable[BaseAddonConfig] | None = None,
) -> tuple[str, ...]:
    """Return contributed schema names in deterministic order."""

    return tuple(sorted(collect_schema_parts(addons)))


def build_schema(
    schema_name: str = DEFAULT_SCHEMA_NAME,
    addons: Iterable[BaseAddonConfig] | None = None,
) -> strawberry.Schema:
    """Return the merged Strawberry schema named ``schema_name``."""

    collected = collect_schema_parts(addons)
    try:
        parts = collected[schema_name]
    except KeyError as exc:
        available = ", ".join(sorted(collected)) or "none"
        raise ImproperlyConfigured(
            f"GraphQL schema {schema_name!r} has no contributions; "
            f"available schemas: {available}"
        ) from exc

    query = _merge_root(schema_name, "query", parts["query"])
    if query is None:
        raise ImproperlyConfigured(
            f"GraphQL schema {schema_name!r} has no query root"
        )
    return strawberry.Schema(
        query=query,
        mutation=_merge_root(schema_name, "mutation", parts["mutation"]),
        subscription=_merge_root(
            schema_name, "subscription", parts["subscription"]
        ),
        types=cast("list[Any]", list(parts["types"])),
        extensions=cast("list[Any]", list(parts["extensions"])),
    )


def render_sdl(
    addons: Iterable[BaseAddonConfig] | None = None,
) -> dict[str, str]:
    """Return printed SDL per schema name in deterministic order."""

    discovered = discover_addons() if addons is None else tuple(addons)
    return {
        name: build_schema(name, discovered).as_str()
        for name in collect_schema_names(discovered)
    }


def _merge_root(
    schema_name: str, key: str, surfaces: tuple[object, ...]
) -> Any | None:
    """Merge one root bucket, failing fast on field-name collisions."""

    if not surfaces:
        return None
    owners: dict[str, object] = {}
    for surface in surfaces:
        for field_name in surface_field_names(surface):
            previous = owners.setdefault(field_name, surface)
            if previous is not surface:
                raise ImproperlyConfigured(
                    f"GraphQL schema {schema_name!r} {key} field "
                    f"{field_name!r} is contributed by both "
                    f"{surface_name(previous)} and {surface_name(surface)}"
                )
    return merge_types(
        _ROOT_TYPE_NAMES[key], cast("tuple[type, ...]", surfaces)
    )


def _dedupe_by_identity(values: tuple[object, ...]) -> tuple[object, ...]:
    """Keep the first occurrence of each contribution by identity."""

    seen: set[int] = set()
    deduped: list[object] = []
    for value in values:
        marker = id(value)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(value)
    return tuple(deduped)
