"""Live GraphQL schema assembly from discovered addon parts."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, cast

import strawberry
from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.functional import cached_property
from django.utils.module_loading import import_string
from rebac import MissingActorError, PermissionDenied, RebacMixin
from rebac.graphql.strawberry import RebacExtension
from rebac.graphql.strawberry_django import RebacDjangoOptimizerExtension
from rebac.managers import RebacManager
from strawberry.tools import merge_types
from strawberry.types.base import get_object_definition
from strawberry.types.execution import ExecutionContext

from angee.graphql.introspection import (
    django_model,
    surface_field_names,
    surface_name,
)
from graphql import GraphQLError

DEFAULT_SCHEMA_NAME = "public"
"""Default GraphQL schema name served by Angee hosts."""

SCHEMA_PART_KEYS: tuple[str, ...] = (
    "query",
    "mutation",
    "subscription",
    "types",
    "extensions",
)
"""GraphQL merge buckets accepted from addon schema declarations."""

_ROOT_TYPE_NAMES = {
    key: key.title()
    for key in SCHEMA_PART_KEYS
    if key not in {"types", "extensions"}
}


class AngeeSchema(strawberry.Schema):
    """Strawberry schema that exposes stable REBAC denial codes."""

    def process_errors(
        self,
        errors: list[GraphQLError],
        execution_context: ExecutionContext | None = None,
    ) -> None:
        """Attach GraphQL error codes before Strawberry logs errors."""

        for error in errors:
            self._apply_rebac_code(error)
        super().process_errors(errors, execution_context)

    def _apply_rebac_code(self, error: GraphQLError) -> None:
        """Attach the code owned by a REBAC denial exception."""

        original = error.original_error
        if isinstance(original, MissingActorError):
            code = "UNAUTHENTICATED"
        elif isinstance(original, PermissionDenied):
            code = "PERMISSION_DENIED"
        else:
            return
        error.extensions = {**(error.extensions or {}), "code": code}


@dataclass(frozen=True, slots=True)
class SchemaParts:
    """Normalized GraphQL merge buckets for one schema name."""

    query: tuple[object, ...] = ()
    """Root query surfaces."""

    mutation: tuple[object, ...] = ()
    """Root mutation surfaces."""

    subscription: tuple[object, ...] = ()
    """Root subscription surfaces."""

    types: tuple[object, ...] = ()
    """Additional Strawberry types included in the schema."""

    extensions: tuple[object, ...] = ()
    """Additional Strawberry schema extensions."""

    @classmethod
    def from_mapping(
        cls,
        app_config: AppConfig,
        name: str,
        raw_entry: Mapping[object, object],
    ) -> SchemaParts:
        """Return normalized schema parts declared by one addon."""

        unknown = set(raw_entry) - set(SCHEMA_PART_KEYS)
        if unknown:
            listed = ", ".join(sorted(str(key) for key in unknown))
            raise ImproperlyConfigured(f"{app_config.name}.schemas[{name!r}] has unknown keys: {listed}")
        return cls(
            **{
                key: _schema_part_values(app_config, name, key, raw_entry.get(key))
                for key in SCHEMA_PART_KEYS
            }
        )

    def merge(self, other: SchemaParts) -> SchemaParts:
        """Return these parts folded with ``other`` and deduped by identity."""

        return type(self)(
            query=self._dedupe_by_identity(self.query + other.query),
            mutation=self._dedupe_by_identity(self.mutation + other.mutation),
            subscription=self._dedupe_by_identity(self.subscription + other.subscription),
            types=self._dedupe_by_identity(self.types + other.types),
            extensions=self._dedupe_by_identity(self.extensions + other.extensions),
        )

    @staticmethod
    def _dedupe_by_identity(
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


class GraphQLSchemas:
    """Collection owner for named GraphQL schema parts and builds."""

    _discovered: ClassVar[GraphQLSchemas | None] = None

    def __init__(self, addons: Iterable[AppConfig]) -> None:
        """Store addon configs in deterministic discovery order."""

        self.addons = tuple(addons)
        self._builds: dict[str, strawberry.Schema] = {}

    @classmethod
    def from_discovery(cls) -> GraphQLSchemas:
        """Return schemas built from installed addon discovery."""

        if cls._discovered is None:
            cls._discovered = cls(apps.get_app_configs())
        return cls._discovered

    @cached_property
    def parts(self) -> dict[str, SchemaParts]:
        """Return deduplicated schema parts folded in addon order."""

        collected: dict[str, SchemaParts] = {}
        for addon in self.addons:
            for name, parts in schema_parts_for(addon).items():
                collected[name] = collected.get(name, SchemaParts()).merge(parts)
        return collected

    def names(self) -> tuple[str, ...]:
        """Return contributed schema names in deterministic order."""

        return tuple(sorted(self.parts))

    def build(
        self,
        name: str = DEFAULT_SCHEMA_NAME,
    ) -> strawberry.Schema:
        """Return the merged live Strawberry schema named ``name``."""

        if name not in self._builds:
            self._builds[name] = self._build(name)
        return self._builds[name]

    def _build(
        self,
        name: str,
    ) -> strawberry.Schema:
        """Build the merged live Strawberry schema named ``name``."""

        try:
            parts = self.parts[name]
        except KeyError as error:
            available = ", ".join(self.names()) or "none"
            raise ImproperlyConfigured(
                f"GraphQL schema {name!r} has no contributions; available schemas: {available}"
            ) from error

        query = self._merge_root(name, "query", parts.query)
        if query is None:
            raise ImproperlyConfigured(f"GraphQL schema {name!r} has no query root")
        self._assert_rebac_managers(name, parts.types)
        self._describe_choice_enums(parts.types)
        schema = AngeeSchema(
            query=query,
            mutation=self._merge_root(name, "mutation", parts.mutation),
            subscription=self._merge_root(
                name,
                "subscription",
                parts.subscription,
            ),
            types=cast(list[Any], list(parts.types)),
            extensions=cast(
                list[Any],
                [
                    RebacExtension,
                    *parts.extensions,
                    RebacDjangoOptimizerExtension,
                ],
            ),
        )
        return schema

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

    def _describe_choice_enums(
        self,
        types: tuple[object, ...],
    ) -> None:
        """Put Django choice labels on Strawberry enum definitions before build."""

        for surface in types:
            model = self._django_model_or_none(surface)
            if model is None:
                continue
            for field in model._meta.get_fields():
                choices_enum = getattr(field, "choices_enum", None)
                if choices_enum is None:
                    continue
                self._describe_choice_enum(cast(Any, choices_enum))

    def _describe_choice_enum(self, choices_enum: Any) -> None:
        """Attach Django choice member labels to the owned Strawberry enum values."""

        definition = getattr(choices_enum, "__strawberry_definition__", None)
        if definition is None:
            strawberry.enum(choices_enum)
            definition = choices_enum.__strawberry_definition__
        labels = {member.value: str(member.label) for member in choices_enum}
        for value in definition.values:
            label = labels.get(value.value)
            if label is not None:
                value.description = label


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
        parts[name] = SchemaParts.from_mapping(app_config, name, raw_entry)
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
