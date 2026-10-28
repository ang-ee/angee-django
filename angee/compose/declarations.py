"""Literal model declarations and generated artifacts contributed by build owners."""

from __future__ import annotations

import keyword
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.options import DEFAULT_NAMES
from django.utils.module_loading import import_string

if TYPE_CHECKING:
    from angee.compose.model_composition import ModelComposition
    from angee.compose.runtime import Runtime


class AppDeclarationSource:
    """Host adapter for app dependencies and native migration declarations.

    Plain Django apps need no adapter. Hosts select one dotted class path through
    COMPOSE_APP_DECLARATIONS before settings composition; manifest parsing and
    caching remain with that host's authoritative declaration owner.
    """

    def dependencies(self, app: AppConfig, *, refresh: bool = False) -> tuple[str, ...]:
        """Return app paths required by this native config."""

        return ()

    def migrations(self, app: AppConfig) -> tuple[Mapping[str, Any], ...]:
        """Return native migration declarations owned by this app."""

        return ()

    @staticmethod
    def configured(namespace: Mapping[str, Any] | None = None) -> AppDeclarationSource:
        """Resolve the optional adapter without importing any feature models."""

        path = (
            namespace.get("COMPOSE_APP_DECLARATIONS") if namespace is not None
            else getattr(settings, "COMPOSE_APP_DECLARATIONS", None)
        )
        if path is None:
            return AppDeclarationSource()
        if not isinstance(path, str):
            raise ImproperlyConfigured("COMPOSE_APP_DECLARATIONS must be a dotted class path.")
        adapter = import_string(path)
        if not isinstance(adapter, type) or not issubclass(adapter, AppDeclarationSource):
            raise ImproperlyConfigured(f"{path} must subclass AppDeclarationSource.")
        return adapter()


type DeclarationValue = (
    str | bool | int | None | tuple[DeclarationValue, ...] | list[DeclarationValue] | dict[str, DeclarationValue]
)


@dataclass(frozen=True, slots=True)
class ModelDeclaration:
    """Additional literal attributes and custom Meta options for a concrete class.

    Django owns native Meta options, fields, managers and inheritance. Integrations
    project only feature declarations whose abstract-to-concrete semantics need
    explicit handling. They cannot contribute executable Python or replace the
    composer's structural declarations.
    """

    attributes: Mapping[str, DeclarationValue] = field(default_factory=dict)
    meta_options: Mapping[str, DeclarationValue] = field(default_factory=dict)


class BuildIntegration:
    """One installed capability's optional contributions to the existing build.

    App autoconfig appends dotted class paths to COMPOSE_INTEGRATIONS. Instances
    have no registry or lifecycle beyond one Runtime. Rendering is read-only;
    bind receives the exact emitted source map, including on an unchanged boot.
    """

    def model_declaration(self, composition: ModelComposition, source: type[models.Model]) -> ModelDeclaration:
        """Return feature declarations for this source and its ordered donors."""

        return ModelDeclaration()

    def render_sources(self, runtime: Runtime) -> Mapping[Path, str]:
        """Return generated text at paths relative to the configured runtime."""

        return {}

    def bind(self, runtime: Runtime, sources: Mapping[Path, str]) -> None:
        """Bind generated paths after successful emission, without rerendering."""

    def retired_primary_keys(self, model: type[models.Model], alias: str) -> Iterable[Any]:
        """Return rows the capability explicitly permits to retire with a model.

        The drop guard still proves every other row absent on every routed
        database. The native field owns conversion of the supplied keys.
        """

        return ()

    @staticmethod
    def configured() -> tuple[BuildIntegration, ...]:
        """Resolve the installed settings contract once for a runtime."""

        paths = getattr(settings, "COMPOSE_INTEGRATIONS", ())
        if isinstance(paths, str) or not isinstance(paths, (list, tuple)):
            raise ImproperlyConfigured("COMPOSE_INTEGRATIONS must be a list or tuple of dotted class paths.")
        integrations: list[BuildIntegration] = []
        seen: set[type[BuildIntegration]] = set()
        for path in paths:
            if not isinstance(path, str):
                raise ImproperlyConfigured("COMPOSE_INTEGRATIONS entries must be dotted class paths.")
            integration = import_string(path)
            if not isinstance(integration, type) or not issubclass(integration, BuildIntegration):
                raise ImproperlyConfigured(f"{path} must subclass BuildIntegration.")
            if integration in seen:
                raise ImproperlyConfigured(f"Duplicate build integration: {path}")
            seen.add(integration)
            integrations.append(integration())
        return tuple(integrations)


def model_declaration(
    composition: ModelComposition,
    source: type[models.Model],
    integrations: tuple[BuildIntegration, ...],
) -> ModelDeclaration:
    """Combine contributions deterministically, refusing competing owners."""

    attributes: dict[str, DeclarationValue] = {}
    meta_options: dict[str, DeclarationValue] = {}
    reserved_attributes = {"Meta", "runtime", "extends", "requires_parent", "compose_contributed_field_origins"}
    participant: type[models.Model] | None = source
    while participant is not None:
        for owner in (participant, *composition.donors(participant)):
            for native_field in (
                *owner._meta.local_fields, *owner._meta.local_many_to_many, *owner._meta.private_fields,
            ):
                reserved_attributes.update((native_field.name, native_field.attname))
            reserved_attributes.update(manager.name for manager in owner._meta.managers)
            reserved_attributes.update(
                name for base in owner.__mro__ for name, value in vars(base).items()
                if callable(value) or isinstance(value, (property, classmethod, staticmethod))
            )
        participant = composition.parent(participant)
    for integration in integrations:
        declaration = integration.model_declaration(composition, source)
        for target, contributed, reserved in (
            (attributes, declaration.attributes, reserved_attributes),
            (meta_options, declaration.meta_options, set(DEFAULT_NAMES)),
        ):
            for name, value in sorted(contributed.items()):
                if not name.isidentifier() or keyword.iskeyword(name) or name.startswith("_") or name in reserved:
                    raise ImproperlyConfigured(f"{source._meta.label}: build integration cannot declare {name!r}.")
                if name in target:
                    raise ImproperlyConfigured(f"{source._meta.label}: competing build declarations for {name!r}.")
                render_literal(value)
                target[name] = value
    return ModelDeclaration(attributes, meta_options)


def render_literal(value: DeclarationValue) -> str:
    """Render only deterministic Python literals, never executable contributions."""

    if value is None or type(value) in (str, bool, int):
        return repr(value)
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return "{" + ", ".join(f"{key!r}: {render_literal(item)}" for key, item in sorted(value.items())) + "}"
    if isinstance(value, (tuple, list)):
        rendered = ", ".join(render_literal(item) for item in value)
        if isinstance(value, tuple):
            return "(" + rendered + ("," if len(value) == 1 else "") + ")"
        return "[" + rendered + "]"
    raise ImproperlyConfigured(f"Build declarations require literal values; got {type(value).__name__}.")
