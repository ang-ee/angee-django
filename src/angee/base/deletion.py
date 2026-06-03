"""Cascade deletion preview domain objects."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from django.db import models
from django.db.models.deletion import (
    Collector,
    ProtectedError,
    RestrictedError,
)
from rebac import current_actor
from rebac.resources import model_resource_type

from angee.base.models import public_id_of

_PREVIEW_LEAF_LIMIT = 50

type _FastDelete = tuple[models.QuerySet[models.Model], int]


@dataclass(frozen=True, slots=True)
class DeletionPreviewGroup:
    """A count of affected rows for one Django model."""

    label: str
    """Human-readable plural model label."""

    count: int
    """Number of affected rows for this model."""


@dataclass(frozen=True, slots=True)
class DeletionPreviewNode:
    """One node in a cascade deletion preview tree."""

    label: str
    """Human-readable model or group label."""

    object_label: str
    """Human-readable object display label."""

    object_id: str | None
    """Public object id when the node represents a concrete row."""

    children: tuple[DeletionPreviewNode, ...]
    """Child nodes below this preview node."""


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

    root: DeletionPreviewNode = DeletionPreviewNode(label="", object_label="", object_id=None, children=())
    """Rooted tree of rows Django would delete; deleted counts include this root row."""

    @property
    def has_blockers(self) -> bool:
        """Return whether any related rows block deletion."""

        return bool(self.blocked)

    @classmethod
    def from_instance(cls, instance: models.Model, actor: Any | None = None) -> DeletionPreview:
        """Return Django's cascade forecast for ``instance``."""

        collector = Collector(using=instance._state.db or "default")
        blocked: tuple[DeletionPreviewGroup, ...] = ()
        try:
            collector.collect([instance])
        except ProtectedError as error:
            blocked = _groups(_count_by_model(error.protected_objects))
        except RestrictedError as error:
            blocked = _groups(_count_by_model(error.restricted_objects))

        fast_deletes: tuple[_FastDelete, ...] = tuple(
            (queryset, queryset.count()) for queryset in collector.fast_deletes
        )
        root = _root_node(instance, collector, fast_deletes, actor if actor is not None else current_actor())
        deleted_counts: dict[type[models.Model], int] = {model: len(rows) for model, rows in collector.data.items()}
        for queryset, count in fast_deletes:
            deleted_counts[queryset.model] = deleted_counts.get(queryset.model, 0) + count

        updated_counts: dict[type[models.Model], int] = {}
        for (model_field, _value), object_groups in collector.field_updates.items():
            model = model_field.model
            updated_counts[model] = updated_counts.get(model, 0) + sum(len(group) for group in object_groups)
        return cls(
            total_deleted_count=sum(deleted_counts.values()),
            deleted=_groups(deleted_counts),
            updated=_groups(updated_counts),
            blocked=blocked,
            root=root,
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


@dataclass(slots=True)
class _PreviewRows:
    """Visible and count-only row state for one preview tree group."""

    total_count: int = 0
    visible_count: int = 0
    visible_rows: list[models.Model] = field(default_factory=list)

    def add(
        self,
        *,
        total_count: int,
        visible_count: int,
        visible_rows: Iterable[models.Model],
    ) -> None:
        """Merge one collector source into this model group."""

        self.total_count += total_count
        self.visible_count += visible_count
        self.visible_rows.extend(visible_rows)


def _root_node(
    instance: models.Model,
    collector: Collector,
    fast_deletes: tuple[_FastDelete, ...],
    actor: Any | None,
) -> DeletionPreviewNode:
    """Return the root node for a collector-backed deletion preview."""

    model = type(instance)
    return DeletionPreviewNode(
        label=str(model._meta.verbose_name),
        object_label=str(instance),
        object_id=_object_id(instance),
        children=tuple(
            _group_node(group_model, preview_rows)
            for group_model, preview_rows in sorted(
                _deleted_rows_by_model(instance, collector, fast_deletes, actor).items(),
                key=lambda item: (str(item[0]._meta.verbose_name_plural), item[0]._meta.label_lower),
            )
        ),
    )


def _deleted_rows_by_model(
    root: models.Model,
    collector: Collector,
    fast_deletes: tuple[_FastDelete, ...],
    actor: Any | None,
) -> dict[type[models.Model], _PreviewRows]:
    """Return deleted tree rows grouped by model, excluding ``root``."""

    groups: dict[type[models.Model], _PreviewRows] = {}
    for model, rows in collector.data.items():
        preview = _collector_preview_rows(root, model, rows, actor)
        if preview.total_count:
            groups.setdefault(model, _PreviewRows()).add(
                total_count=preview.total_count,
                visible_count=preview.visible_count,
                visible_rows=preview.visible_rows,
            )
    for queryset, total_count in fast_deletes:
        preview = _fast_delete_preview_rows(queryset, total_count, actor)
        if preview.total_count:
            groups.setdefault(queryset.model, _PreviewRows()).add(
                total_count=preview.total_count,
                visible_count=preview.visible_count,
                visible_rows=preview.visible_rows,
            )
    return {model: rows for model, rows in groups.items() if rows.total_count}


def _collector_preview_rows(
    root: models.Model,
    model: type[models.Model],
    rows: Iterable[models.Model],
    actor: Any | None,
) -> _PreviewRows:
    """Return access-scoped preview rows from collector materialized rows."""

    collected = [row for row in rows if not _is_root(root, row)]
    if not collected:
        return _PreviewRows()
    scoped = _read_scoped_queryset(model, actor)
    if scoped is None:
        if _requires_read_scope(model):
            return _PreviewRows(total_count=len(collected), visible_count=0)
        return _PreviewRows(
            total_count=len(collected),
            visible_count=len(collected),
            visible_rows=collected,
        )
    visible_pks = set(scoped.filter(pk__in=[row.pk for row in collected]).values_list("pk", flat=True))
    visible_rows = [row for row in collected if row.pk in visible_pks]
    return _PreviewRows(
        total_count=len(collected),
        visible_count=len(visible_pks),
        visible_rows=visible_rows,
    )


def _fast_delete_preview_rows(
    queryset: models.QuerySet[models.Model],
    total_count: int,
    actor: Any | None,
) -> _PreviewRows:
    """Return access-scoped preview rows from one fast-delete queryset."""

    if total_count == 0:
        return _PreviewRows()
    scoped = _read_scoped_queryset(queryset.model, actor)
    if scoped is None:
        if _requires_read_scope(queryset.model):
            return _PreviewRows(total_count=total_count, visible_count=0)
        return _PreviewRows(
            total_count=total_count,
            visible_count=total_count,
            visible_rows=list(_order_by_pk(queryset)[: _PREVIEW_LEAF_LIMIT + 1]),
        )
    visible_queryset = scoped.filter(pk__in=models.Subquery(queryset.order_by().values("pk")))
    visible_count = visible_queryset.count()
    return _PreviewRows(
        total_count=total_count,
        visible_count=visible_count,
        visible_rows=list(_order_by_pk(visible_queryset)[: _PREVIEW_LEAF_LIMIT + 1]),
    )


def _group_node(
    model: type[models.Model],
    rows: _PreviewRows,
) -> DeletionPreviewNode:
    """Return a grouped child node for deleted rows of one model."""

    ordered_rows = sorted(rows.visible_rows, key=lambda row: row.pk)
    plural = str(model._meta.verbose_name_plural)
    leaves = tuple(_leaf_node(row) for row in ordered_rows[:_PREVIEW_LEAF_LIMIT])
    hidden_count = max(0, rows.total_count - rows.visible_count)
    capped_count = max(0, rows.visible_count - len(leaves))
    if hidden_count:
        leaves = (
            *leaves,
            DeletionPreviewNode(
                label="",
                object_label=f"{hidden_count + capped_count} more records",
                object_id=None,
                children=(),
            ),
        )
    elif capped_count:
        leaves = (
            *leaves,
            DeletionPreviewNode(
                label="",
                object_label=f"… and {capped_count} more",
                object_id=None,
                children=(),
            ),
        )
    return DeletionPreviewNode(
        label=plural,
        object_label=f"{rows.total_count} {plural}",
        object_id=None,
        children=leaves,
    )


def _leaf_node(instance: models.Model) -> DeletionPreviewNode:
    """Return a leaf node for one deleted row."""

    return DeletionPreviewNode(
        label=str(instance._meta.verbose_name),
        object_label=str(instance),
        object_id=_object_id(instance),
        children=(),
    )


def _object_id(instance: models.Model) -> str | None:
    """Return the public id used in deletion preview nodes."""

    return public_id_of(instance) or None


def _read_scoped_queryset(
    model: type[models.Model],
    actor: Any | None,
) -> models.QuerySet[models.Model] | None:
    """Return a read-scoped queryset for a REBAC model, if one can be resolved."""

    if not _requires_read_scope(model) or actor is None:
        return None
    manager = model._default_manager
    with_actor = getattr(manager, "with_actor", None)
    if not callable(with_actor):
        return None
    queryset = with_actor(actor)
    with_action = getattr(queryset, "with_action", None)
    if callable(with_action):
        queryset = with_action("read")
    return queryset


def _requires_read_scope(model: type[models.Model]) -> bool:
    """Return whether concrete tree leaves for ``model`` must be actor scoped."""

    return bool(model_resource_type(model))


def _order_by_pk(queryset: models.QuerySet[models.Model]) -> models.QuerySet[models.Model]:
    """Return ``queryset`` ordered by its native primary-key column."""

    return queryset.order_by(queryset.model._meta.pk.name)


def _is_root(root: models.Model, row: models.Model) -> bool:
    """Return whether ``row`` is the deletion preview target."""

    return root._meta.concrete_model is row._meta.concrete_model and root.pk == row.pk
