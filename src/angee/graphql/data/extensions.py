"""Declared transformations at native resource construction boundaries."""

from __future__ import annotations
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, fields
from typing import Any
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from angee.base.hooks import resolve_hooks


@dataclass(frozen=True)
class ResourceExtensions:
    """Optional native query/backend transforms; each slot has one declaring owner."""

    aggregate_queryset: Callable[[models.QuerySet[Any]], models.QuerySet[Any]] | None = None
    write_queryset: Callable[[type[models.Model]], models.QuerySet[Any]] | None = None
    declaration_queryset: Callable[[type[models.Model]], models.QuerySet[Any]] | None = None
    relation_queryset: Callable[[type[models.Model], models.QuerySet[Any]], models.QuerySet[Any]] | None = None
    relation_expression: Callable[..., Any] | None = None
    validate_fields: Callable[[type[models.Model], Iterable[str], str, str], None] | None = None
    prepare_queryset: Callable[[models.QuerySet[Any], type, bool], models.QuerySet[Any]] | None = None
    write_backend_factory: Callable[..., Any] | None = None
    delete_context: Callable[[str | None], AbstractContextManager[Any]] | None = None
    validate_delete: Callable[[models.Model], None] | None = None
    preview_queryset: Callable[[type[models.Model], Any], models.QuerySet[Any]] | None = None
    implementation_defaults: Callable[[type[models.Model], dict[str, Any]], dict[str, Any]] | None = None


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
