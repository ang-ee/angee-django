"""Declared transformations at native resource construction boundaries."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, fields
from typing import Any, Protocol

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from strawberry.extensions import FieldExtension

from angee.base.hooks import resolve_hooks


class PrepareQueryset(Protocol):
    """Prepare a resource queryset, optionally bounding its record read proof."""

    def __call__(
        self,
        queryset: models.QuerySet[Any],
        node: type,
        required: bool,
        target: tuple[type[models.Model], Any] | None = None,
    ) -> models.QuerySet[Any]: ...


@dataclass(frozen=True)
class ResourceExtensions:
    """Optional native query/backend transforms; each slot has one declaring owner."""

    aggregate_queryset: Callable[[models.QuerySet[Any]], models.QuerySet[Any]] | None = None
    write_queryset: Callable[[type[models.Model]], models.QuerySet[Any]] | None = None
    declaration_queryset: Callable[[type[models.Model]], models.QuerySet[Any]] | None = None
    relation_queryset: Callable[[type[models.Model], models.QuerySet[Any]], models.QuerySet[Any]] | None = None
    relation_expression: Callable[..., Any] | None = None
    validate_fields: Callable[[type[models.Model], Iterable[str], str, str], None] | None = None
    prepare_queryset: PrepareQueryset | None = None
    write_backend_factory: Callable[..., Any] | None = None
    delete_context: Callable[[str | None], AbstractContextManager[Any]] | None = None
    validate_delete: Callable[[models.Model], None] | None = None
    preview_queryset: Callable[[type[models.Model], Any], models.QuerySet[Any]] | None = None
    row_extensions: Callable[[type], tuple[FieldExtension, ...]] | None = None


def resource_extensions(model: type[models.Model]) -> ResourceExtensions:
    """Merge explicitly declared native resource slots, rejecting competing owners."""
    values: dict[str, Any] = {}
    for provider in resolve_hooks("ANGEE_GRAPHQL_RESOURCE_CONTRIBUTORS"):
        contribution = provider(model)
        if not isinstance(contribution, ResourceExtensions):
            raise ImproperlyConfigured("Resource contributors must return ResourceExtensions.")
        for field in fields(contribution):
            value = getattr(contribution, field.name)
            if value is None:
                continue
            if field.name in values:
                raise ImproperlyConfigured(f"{model._meta.label}: duplicate resource slot {field.name!r}.")
            values[field.name] = value
    return ResourceExtensions(**values)
