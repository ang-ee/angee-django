"""Live GraphQL schema assembly from discovered addon parts."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any, cast

import strawberry
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.functional import cached_property
from rebac import RebacMixin
from rebac.graphql.strawberry import RebacExtension
from rebac.graphql.strawberry_django import RebacDjangoOptimizerExtension
from rebac.managers import RebacManager
from strawberry.tools import merge_types
from strawberry.types.base import get_object_definition

from angee.base.apps import SCHEMA_PART_KEYS, BaseAddonConfig, SchemaParts
from angee.base.discovery import discover_addons
from angee.base.graphql.errors import AngeeSchema
from angee.base.graphql.introspection import (
    django_model,
    surface_field_names,
    surface_name,
)

DEFAULT_SCHEMA_NAME = "public"
"""Default GraphQL schema name served by Angee hosts."""

_ROOT_TYPE_NAMES = {
    "query": "Query",
    "mutation": "Mutation",
    "subscription": "Subscription",
}


class GraphQLSchemas:
    """Collection owner for named GraphQL schema parts and builds."""

    def __init__(self, addons: Iterable[BaseAddonConfig]) -> None:
        """Store addon configs in deterministic discovery order."""

        self.addons = tuple(addons)

    @classmethod
    def from_discovery(cls) -> GraphQLSchemas:
        """Return schemas built from installed addon discovery."""

        return cls(discover_addons())

    @classmethod
    def from_addons(
        cls,
        addons: Iterable[BaseAddonConfig],
    ) -> GraphQLSchemas:
        """Return schemas built from explicit addon configs."""

        return cls(addons)

    @cached_property
    def parts(self) -> dict[str, SchemaParts]:
        """Return deduplicated schema parts folded in addon order."""

        collected: dict[str, SchemaParts] = {}
        for addon in self.addons:
            for name, parts in addon.schema_parts.items():
                bucket = collected.setdefault(
                    name,
                    {key: () for key in SCHEMA_PART_KEYS},
                )
                for key in SCHEMA_PART_KEYS:
                    bucket[key] = self._dedupe_by_identity(
                        bucket[key] + parts[key]
                    )
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
                f"GraphQL schema {name!r} has no contributions; "
                f"available schemas: {available}"
            ) from error

        query = self._merge_root(name, "query", parts["query"])
        if query is None:
            raise ImproperlyConfigured(
                f"GraphQL schema {name!r} has no query root"
            )
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
                    f"GraphQL schema {schema_name!r} exposes "
                    f"{model._meta.label} without a RebacManager default "
                    "manager"
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
