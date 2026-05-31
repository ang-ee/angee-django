"""Cascade deletion preview domain objects."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from django.db import models
from django.db.models.deletion import (
    Collector,
    ProtectedError,
    RestrictedError,
)


@dataclass(frozen=True, slots=True)
class DeletionPreviewGroup:
    """A count of affected rows for one Django model."""

    label: str
    """Human-readable plural model label."""

    count: int
    """Number of affected rows for this model."""


@dataclass(frozen=True, slots=True)
class DeletionPreview:
    """Cascade forecast for deleting one Django model instance."""

    total_deleted_count: int
    """Total number of rows Django would delete."""

    deleted: tuple[DeletionPreviewGroup, ...]
    """Rows Django would delete."""

    updated: tuple[DeletionPreviewGroup, ...]
    """Rows Django would update because of ``on_delete`` behavior."""

    blocked: tuple[DeletionPreviewGroup, ...]
    """Rows whose ``on_delete`` behavior blocks deletion."""

    @property
    def has_blockers(self) -> bool:
        """Return whether any related rows block deletion."""

        return bool(self.blocked)

    @classmethod
    def from_instance(cls, instance: models.Model) -> DeletionPreview:
        """Return Django's cascade forecast for ``instance``."""

        collector = Collector(using=instance._state.db or "default")
        blocked: tuple[DeletionPreviewGroup, ...] = ()
        try:
            collector.collect([instance])
        except ProtectedError as error:
            blocked = _groups(_count_by_model(error.protected_objects))
        except RestrictedError as error:
            blocked = _groups(_count_by_model(error.restricted_objects))

        deleted_counts: dict[type[models.Model], int] = {
            model: len(rows) for model, rows in collector.data.items()
        }
        for queryset in collector.fast_deletes:
            deleted_counts[queryset.model] = (
                deleted_counts.get(queryset.model, 0) + queryset.count()
            )

        updated_counts: dict[type[models.Model], int] = {}
        for (field, _value), object_groups in collector.field_updates.items():
            model = field.model
            updated_counts[model] = updated_counts.get(model, 0) + sum(
                len(group) for group in object_groups
            )
        return cls(
            total_deleted_count=sum(deleted_counts.values()),
            deleted=_groups(deleted_counts),
            updated=_groups(updated_counts),
            blocked=blocked,
        )


def _groups(
    counts: dict[type[models.Model], int],
) -> tuple[DeletionPreviewGroup, ...]:
    """Return sorted non-empty deletion preview groups."""

    return tuple(
        DeletionPreviewGroup(
            label=str(model._meta.verbose_name_plural),
            count=count,
        )
        for model, count in sorted(
            counts.items(),
            key=lambda item: item[0]._meta.label,
        )
        if count
    )


def _count_by_model(
    instances: Iterable[models.Model],
) -> dict[type[models.Model], int]:
    """Return counts grouped by model class."""

    counts: dict[type[models.Model], int] = {}
    for instance in instances:
        model = type(instance)
        counts[model] = counts.get(model, 0) + 1
    return counts
