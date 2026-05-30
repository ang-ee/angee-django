"""Build Strawberry schemas from native addon ``graphql.py`` modules.

Addons expose plain Strawberry classes in a conventional ``graphql.py`` module:

``schemas = {"public": {"query": [Query], "types": [ThingType]}}``

Angee does not define schema-part classes. Addons export ordinary Strawberry
classes and the framework merges them into named schemas in addon order.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Literal, TypeAlias

from django.core.exceptions import ImproperlyConfigured

from angee.base.apps import BaseAddonConfig
from angee.base.discovery import discover_addons

RootName: TypeAlias = Literal["query", "mutation", "subscription"]
SchemaEntry: TypeAlias = Mapping[str, object]

ROOT_NAMES: tuple[RootName, ...] = ("query", "mutation", "subscription")
SEQUENCE_KEYS = (*ROOT_NAMES, "types", "extensions")
ALLOWED_KEYS = frozenset(SEQUENCE_KEYS)


@dataclass(slots=True)
class CollectedSchemaConfig:
    """Native Strawberry contributions collected for one schema name."""

    query: list[Any] = field(default_factory=list)
    mutation: list[Any] = field(default_factory=list)
    subscription: list[Any] = field(default_factory=list)
    types: list[Any] = field(default_factory=list)
    extensions: list[Any] = field(default_factory=list)

    def add_entry(
        self,
        module: ModuleType,
        schema_name: str,
        entry: SchemaEntry,
    ) -> None:
        """Append one addon's root classes, types, and extensions."""

        for key in SEQUENCE_KEYS:
            values = _as_sequence(module, schema_name, key, entry.get(key))
            getattr(self, key).extend(values)

    def dedupe(self) -> None:
        """Remove duplicate classes from every collected schema bucket."""

        for key in SEQUENCE_KEYS:
            setattr(self, key, _dedupe(getattr(self, key)))


def build_schema(
    schema_name: str,
    addons: Iterable[BaseAddonConfig] | None = None,
    **schema_options: Any,
) -> Any:
    """Return a ``strawberry.Schema`` for ``schema_name``.

    ``schema_options`` are forwarded to Strawberry. Use them for native
    Strawberry settings such as ``extensions`` or ``scalar_overrides``.
    """

    strawberry = importlib.import_module("strawberry")
    tools = importlib.import_module("strawberry.tools")

    collected = collect_schema_config(schema_name, addons)
    if not collected.query:
        raise ImproperlyConfigured(
            f"GraphQL schema {schema_name!r} has no query root"
        )

    query = tools.merge_types("Query", tuple(collected.query))
    mutation = _merge_optional_root(tools, "Mutation", collected.mutation)
    subscription = _merge_optional_root(
        tools,
        "Subscription",
        collected.subscription,
    )

    options = dict(schema_options)
    options["types"] = [
        *collected.types,
        *list(options.pop("types", ()) or ()),
    ]
    options["extensions"] = [
        *collected.extensions,
        *list(options.pop("extensions", ()) or ()),
    ]

    return strawberry.Schema(
        query=query,
        mutation=mutation,
        subscription=subscription,
        **options,
    )


def collect_schema_config(
    schema_name: str,
    addons: Iterable[BaseAddonConfig] | None = None,
) -> CollectedSchemaConfig:
    """Collect native Strawberry classes exported for ``schema_name``."""

    collected = CollectedSchemaConfig()
    discovered = discover_addons() if addons is None else tuple(addons)
    for addon in discovered:
        module = addon.get_graphql_module()
        if module is None:
            continue
        schemas = getattr(module, "schemas", None)
        if schemas is None:
            continue
        entry = _schema_entry(module, schemas, schema_name)
        if entry is None:
            continue
        collected.add_entry(module, schema_name, entry)
    collected.dedupe()
    return collected


def _merge_optional_root(
    tools: ModuleType, name: str, roots: Sequence[Any]
) -> Any:
    """Merge a root type only when a schema contributes fields for it."""

    if not roots:
        return None
    return tools.merge_types(name, tuple(roots))


def _schema_entry(
    module: ModuleType,
    schemas: object,
    schema_name: str,
) -> SchemaEntry | None:
    """Return one schema entry from an addon's ``schemas`` export."""

    if not isinstance(schemas, Mapping):
        raise TypeError(f"{module.__name__}.schemas must be a mapping")
    entry = schemas.get(schema_name)
    if entry is None:
        return None
    if not isinstance(entry, Mapping):
        raise TypeError(
            f"{module.__name__}.schemas[{schema_name!r}] must be a mapping"
        )
    unknown = set(entry) - ALLOWED_KEYS
    if unknown:
        raise TypeError(
            f"{module.__name__}.schemas[{schema_name!r}] has unknown keys: "
            + ", ".join(sorted(unknown))
        )
    return entry


def _as_sequence(
    module: ModuleType,
    schema_name: str,
    key: str,
    value: object,
) -> tuple[object, ...]:
    """Normalize a schema entry value while rejecting unordered sets."""

    if value is None:
        return ()
    if isinstance(value, set | frozenset):
        raise TypeError(
            f"{module.__name__}.schemas[{schema_name!r}][{key!r}] "
            "must be a sequence, not a set"
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(value)
    return (value,)


def _dedupe(values: Iterable[Any]) -> list[Any]:
    """Keep the first occurrence of each object by identity."""

    seen: set[int] = set()
    deduped: list[Any] = []
    for value in values:
        marker = id(value)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(value)
    return deduped


# TODO: add optional shortcuts that return ordinary Strawberry classes for CRUD
# mutations and change subscriptions. Schema authors should still be able to
# write the fully manual Strawberry shape shown in the module docstring.
