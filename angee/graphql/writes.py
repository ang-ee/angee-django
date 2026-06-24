"""Shared GraphQL write-target helpers."""

from __future__ import annotations

from django.db import models


def write_queryset(model: type[models.Model]) -> models.QuerySet[models.Model]:
    """Return a write-target queryset with row scope and full field values.

    Both mutation apply steps and delete-preview history need to load the
    in-memory instance with field-read redaction disabled while preserving
    REBAC row scope. REBAC models expose this as ``for_write()``; plain Django
    models have no field redaction and use their default manager.
    """

    manager = model._default_manager
    for_write = getattr(manager, "for_write", None)
    return for_write() if callable(for_write) else manager.all()
