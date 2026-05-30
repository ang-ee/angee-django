"""Library-backed CRUD mutation surfaces for source addons.

``crud`` builds a Strawberry mutation type whose create and update fields
delegate to ``strawberry_django.mutations``; validation, persistence, and write
authorization stay with strawberry-django and the model's REBAC-scoped manager.
The delete field is a cascade-aware mutation returning a :class:`DeletePreview`
of what removing the row affects. The result drops into a schema ``"mutation"``
parts list alongside hand-written surfaces.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import strawberry
import strawberry_django
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.deletion import Collector, ProtectedError

from angee.base.graphql.introspection import django_model, surface_name
from angee.base.models import instance_from_public_id


@strawberry.type
class DeletePreviewGroup:
    """A count of affected rows for one model."""

    label: str
    count: int


@strawberry.type
class DeletePreview:
    """Cascade forecast for deleting one row."""

    total_deleted_count: int
    deleted: list[DeletePreviewGroup]
    updated: list[DeletePreviewGroup]
    blocked: list[DeletePreviewGroup]
    has_blockers: bool


def crud(
    node: type,
    *,
    create: type | None = None,
    update: type | None = None,
    delete: bool = False,
    name: str | None = None,
    permission_classes: list[type] | None = None,
) -> type:
    """Return a Strawberry mutation surface for one model type.

    ``node`` is a ``strawberry_django`` type bound to a Django model.
    ``create`` and ``update`` take the input types; ``delete`` adds a
    cascade-previewing delete. Field names are derived from the model
    (``createNote`` and so on) unless ``name`` overrides the singular.
    """

    model = django_model(node)
    singular = name or model._meta.model_name
    annotations: dict[str, Any] = {}
    namespace: dict[str, Any] = {"__annotations__": annotations}

    def add(verb: str, annotation: Any, field: Any) -> None:
        attr = f"{verb}_{singular}"
        annotations[attr] = annotation
        namespace[attr] = field

    if create is not None:
        add(
            "create",
            node,
            strawberry_django.mutations.create(
                create, permission_classes=permission_classes
            ),
        )
    if update is not None:
        add(
            "update",
            node,
            strawberry_django.mutations.update(
                update, permission_classes=permission_classes
            ),
        )
    if delete:
        add(
            "delete",
            DeletePreview,
            strawberry.mutation(
                resolver=_delete_resolver(model),
                permission_classes=permission_classes,
            ),
        )

    if not annotations:
        raise ImproperlyConfigured(
            f"crud({surface_name(node)}) needs at least one of create, "
            "update, or delete"
        )
    type_name = f"{singular[:1].upper()}{singular[1:]}Mutation"
    surface = type(type_name, (), namespace)
    return strawberry.type(surface)


def collect_delete_preview(instance: models.Model) -> DeletePreview:
    """Return what deleting ``instance`` would cascade, without deleting it."""

    collector = Collector(using=instance._state.db or "default")
    blocked: list[DeletePreviewGroup] = []
    try:
        collector.collect([instance])
    except ProtectedError as exc:
        blocked = _groups(_count_by_model(exc.protected_objects))

    deleted_counts = {
        model: len(rows) for model, rows in collector.data.items()
    }
    updated_counts = {
        model: sum(len(rows) for rows in updates.values())
        for model, updates in collector.field_updates.items()
    }
    return DeletePreview(
        total_deleted_count=sum(deleted_counts.values()),
        deleted=_groups(deleted_counts),
        updated=_groups(updated_counts),
        blocked=blocked,
        has_blockers=bool(blocked),
    )


def _delete_resolver(model: type[models.Model]) -> Any:
    """Return a resolver that previews, then deletes when nothing blocks it."""

    def delete(id: strawberry.ID) -> DeletePreview:
        instance = _resolve_for_delete(model, str(id))
        preview = collect_delete_preview(instance)
        if not preview.has_blockers:
            instance.delete()
        return preview

    return delete


def _resolve_for_delete(
    model: type[models.Model], public_id: str
) -> models.Model:
    """Return the REBAC-scoped row to delete, or fail when out of reach."""

    instance = instance_from_public_id(model, public_id)
    if instance is None:
        raise ValueError(
            f"{model._meta.object_name} {public_id!r} was not found"
        )
    return instance


def _groups(counts: dict[type[models.Model], int]) -> list[DeletePreviewGroup]:
    """Return sorted preview groups for non-empty per-model counts."""

    return [
        DeletePreviewGroup(
            label=str(model._meta.verbose_name_plural), count=count
        )
        for model, count in sorted(
            counts.items(), key=lambda item: item[0]._meta.label
        )
        if count
    ]


def _count_by_model(
    instances: Iterable[models.Model],
) -> dict[type[models.Model], int]:
    """Return a per-model row count for a flat iterable of instances."""

    counts: dict[type[models.Model], int] = {}
    for instance in instances:
        counts[type(instance)] = counts.get(type(instance), 0) + 1
    return counts
