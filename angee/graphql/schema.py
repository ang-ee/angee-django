"""Live GraphQL schema assembly from discovered addon parts."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

import strawberry
from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.functional import cached_property
from django.utils.module_loading import import_string
from rebac import RebacMixin
from rebac.graphql.strawberry import RebacExtension
from rebac.graphql.strawberry_django import RebacDjangoOptimizerExtension
from rebac.managers import RebacManager
from strawberry.tools import merge_types
from strawberry.types.base import get_object_definition

from angee.graphql.errors import AngeeSchema
from angee.graphql.introspection import (
    django_model,
    surface_field_names,
    surface_name,
)

DEFAULT_SCHEMA_NAME = "public"
"""Default GraphQL schema name served by Angee hosts."""

SchemaParts = dict[str, tuple[object, ...]]
"""GraphQL merge buckets for one schema name."""

SCHEMA_PART_KEYS: tuple[str, ...] = (
    "query",
    "mutation",
    "subscription",
    "types",
    "extensions",
)
"""GraphQL merge buckets accepted from addon schema declarations."""

_ROOT_TYPE_NAMES = {
    "query": "Query",
    "mutation": "Mutation",
    "subscription": "Subscription",
}


class GraphQLSchemas:
    """Collection owner for named GraphQL schema parts and builds."""

    def __init__(self, addons: Iterable[AppConfig]) -> None:
        """Store addon configs in deterministic discovery order."""

        self.addons = tuple(addons)

    @classmethod
    def from_discovery(cls) -> GraphQLSchemas:
        """Return schemas built from installed addon discovery."""

        return cls(apps.get_app_configs())

    @classmethod
    def from_addons(
        cls,
        addons: Iterable[AppConfig],
    ) -> GraphQLSchemas:
        """Return schemas built from explicit addon configs."""

        return cls(addons)

    @cached_property
    def parts(self) -> dict[str, SchemaParts]:
        """Return deduplicated schema parts folded in addon order."""

        collected: dict[str, SchemaParts] = {}
        for addon in self.addons:
            for name, parts in schema_parts_for(addon).items():
                bucket = collected.setdefault(
                    name,
                    {key: () for key in SCHEMA_PART_KEYS},
                )
                for key in SCHEMA_PART_KEYS:
                    bucket[key] = self._dedupe_by_identity(bucket[key] + parts[key])
        return collected

    def names(self) -> tuple[str, ...]:
        """Return contributed schema names in deterministic order."""

        return tuple(sorted(self.parts))

    def build(
        self,
        name: str = DEFAULT_SCHEMA_NAME,
    ) -> strawberry.Schema:
        """Return the merged live Strawberry schema named ``name``."""

        try:
            parts = self.parts[name]
        except KeyError as error:
            available = ", ".join(self.names()) or "none"
            raise ImproperlyConfigured(
                f"GraphQL schema {name!r} has no contributions; available schemas: {available}"
            ) from error

        query = self._merge_root(name, "query", parts["query"])
        if query is None:
            raise ImproperlyConfigured(f"GraphQL schema {name!r} has no query root")
        self._assert_rebac_managers(name, parts["types"])
        return AngeeSchema(
            query=query,
            mutation=self._merge_root(name, "mutation", parts["mutation"]),
            subscription=self._merge_root(
                name,
                "subscription",
                parts["subscription"],
            ),
            types=cast(list[Any], list(parts["types"])),
            extensions=cast(
                list[Any],
                [
                    RebacExtension,
                    *parts["extensions"],
                    RebacDjangoOptimizerExtension,
                ],
            ),
        )

    def render_sdl(self) -> dict[str, str]:
        """Return printed GraphQL SDL for every contributed schema."""

        return {name: self.build(name).as_str() for name in self.names()}

    def _merge_root(
        self,
        schema_name: str,
        key: str,
        surfaces: tuple[object, ...],
    ) -> Any | None:
        """Merge one root bucket after checking field collisions."""

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
        root = merge_types(
            _ROOT_TYPE_NAMES[key],
            cast(tuple[type, ...], surfaces),
        )
        # Each named schema owns independent field objects: relay field
        # extensions mutate fields in place during build, so a surface shared
        # across schemas must not hand the same field to two schema builds.
        definition = get_object_definition(root, strict=True)
        definition.fields = [copy.copy(field) for field in definition.fields]
        return root

    def _dedupe_by_identity(
        self,
        values: tuple[object, ...],
    ) -> tuple[object, ...]:
        """Return values with duplicate identities removed."""

        seen: set[int] = set()
        deduped: list[object] = []
        for value in values:
            marker = id(value)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(value)
        return tuple(deduped)

    def _assert_rebac_managers(
        self,
        schema_name: str,
        types: tuple[object, ...],
    ) -> None:
        """Raise when a GraphQL-exposed REBAC model is not manager-scoped."""

        for surface in types:
            model = self._django_model_or_none(surface)
            if model is None or not issubclass(model, RebacMixin):
                continue
            if not isinstance(model._default_manager, RebacManager):
                raise ImproperlyConfigured(
                    f"GraphQL schema {schema_name!r} exposes {model._meta.label} without a RebacManager default manager"
                )

    def _django_model_or_none(
        self,
        surface: object,
    ) -> type[models.Model] | None:
        """Return the strawberry-django model for ``surface`` when present."""

        try:
            return django_model(cast(type, surface))
        except ImproperlyConfigured:
            return None


def schema_parts_for(app_config: AppConfig) -> dict[str, SchemaParts]:
    """Return normalized GraphQL schema parts declared by one addon."""

    raw_schemas = _raw_schemas(app_config)
    if raw_schemas is None:
        return {}
    if not isinstance(raw_schemas, Mapping):
        raise ImproperlyConfigured(f"{app_config.name}.schemas must resolve to a mapping")

    parts: dict[str, SchemaParts] = {}
    for raw_name, raw_entry in raw_schemas.items():
        name = str(raw_name)
        if not isinstance(raw_entry, Mapping):
            raise ImproperlyConfigured(f"{app_config.name}.schemas[{name!r}] must be a mapping")
        unknown = set(raw_entry) - set(SCHEMA_PART_KEYS)
        if unknown:
            listed = ", ".join(sorted(str(key) for key in unknown))
            raise ImproperlyConfigured(f"{app_config.name}.schemas[{name!r}] has unknown keys: {listed}")
        parts[name] = {key: _schema_part_values(app_config, name, key, raw_entry.get(key)) for key in SCHEMA_PART_KEYS}
    return parts


def _raw_schemas(app_config: AppConfig) -> object:
    """Return the raw schema declaration object for one addon, when present."""

    declaration = getattr(app_config, "schemas", None)
    if declaration is None:
        return None
    if isinstance(declaration, Mapping):
        return declaration
    if not isinstance(declaration, str):
        raise ImproperlyConfigured(f"{app_config.name}.schemas must be a mapping or dotted reference")
    dotted_path = declaration if declaration.startswith(f"{app_config.name}.") else f"{app_config.name}.{declaration}"
    try:
        return import_string(dotted_path)
    except ImportError as error:
        raise ImproperlyConfigured(f"{app_config.name}.schemas references {dotted_path!r}") from error


def _schema_part_values(
    app_config: AppConfig,
    name: str,
    key: str,
    value: object,
) -> tuple[object, ...]:
    """Return one schema part as a deterministic tuple."""

    if value is None:
        return ()
    if isinstance(value, set | frozenset):
        raise ImproperlyConfigured(f"{app_config.name}.schemas[{name!r}][{key!r}] must be a sequence, not a set")
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(value)
    return (value,)
