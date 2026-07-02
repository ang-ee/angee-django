"""GraphQL delete-confirmation preview built on Django's deletion collector."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, TypeVar

import strawberry
from django.db import models, transaction
from django.db.models.deletion import (
    Collector,
    ProtectedError,
    RestrictedError,
)
from rebac import current_actor, system_context
from rebac.resources import model_resource_type

from angee.base.models import public_id_of, read_scoped_queryset
from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.data.metadata import (
    DataResourceRoots,
    DataResourceTypeNames,
    attach_data_resource_metadata,
    make_data_resource_metadata,
    resource_type_name,
    resource_wire_field_name,
)
from angee.graphql.ids import require_instance_for_id

_PREVIEW_LEAF_LIMIT = 50
_PREVIEW_PK_CHUNK_SIZE = 500

type _FastDelete = tuple[models.QuerySet[models.Model], int]


@strawberry.type
class DeletePreviewGroup:
    """A count of affected rows for one Django model."""

    label: str
    """Human-readable plural model label."""

    count: int
    """Number of affected rows for this model."""


@strawberry.type
class DeletePreviewNode:
    """One node in a cascade deletion preview tree."""

    label: str
    """Human-readable model or group label."""

    object_label: str
    """Human-readable object display label."""

    object_id: str | None
    """Public object id when the node represents a concrete row."""

    children: list[DeletePreviewNode]
    """Child nodes below this preview node."""

    @classmethod
    def from_row(cls, instance: models.Model) -> DeletePreviewNode:
        """Return a childless leaf node for one deleted row."""

        return cls(
            label=str(instance._meta.verbose_name),
            object_label=str(instance),
            object_id=public_id_of(instance) or None,
            children=[],
        )

    @classmethod
    def from_group(cls, model: type[models.Model], rows: _PreviewRows) -> DeletePreviewNode:
        """Return a grouped child node summarizing deleted rows of one model."""

        ordered_rows = sorted(rows.visible_rows, key=lambda row: row.pk)
        plural = str(model._meta.verbose_name_plural)
        leaves = [cls.from_row(row) for row in ordered_rows[:_PREVIEW_LEAF_LIMIT]]
        hidden_count = max(0, rows.total_count - rows.visible_count)
        capped_count = max(0, rows.visible_count - len(leaves))
        if hidden_count:
            overflow_label = f"{hidden_count + capped_count} more records"
        elif capped_count:
            overflow_label = f"… and {capped_count} more"
        else:
            overflow_label = ""
        if overflow_label:
            leaves.append(cls(label="", object_label=overflow_label, object_id=None, children=[]))
        return cls(
            label=plural,
            object_label=f"{rows.total_count} {plural}",
            object_id=None,
            children=leaves,
        )

    @classmethod
    def from_target(
        cls,
        instance: models.Model,
        groups: dict[type[models.Model], _PreviewRows],
    ) -> DeletePreviewNode:
        """Return the root node (the deletion target) with its grouped children."""

        model = type(instance)
        return cls(
            label=str(model._meta.verbose_name),
            object_label=str(instance),
            object_id=public_id_of(instance) or None,
            children=[
                cls.from_group(group_model, preview_rows)
                for group_model, preview_rows in sorted(
                    groups.items(),
                    key=lambda item: (str(item[0]._meta.verbose_name_plural), item[0]._meta.label_lower),
                )
            ],
        )


@strawberry.type
class DeletePreview:
    """Cascade forecast for deleting one Django model instance."""

    total_deleted_count: int
    """Total number of rows Django would delete."""

    deleted: list[DeletePreviewGroup]
    """Rows Django would delete."""

    updated: list[DeletePreviewGroup]
    """Rows Django would update because of ``on_delete`` behavior."""

    blocked: list[DeletePreviewGroup]
    """Rows whose ``on_delete`` behavior blocks deletion."""

    has_blockers: bool
    """Whether any related rows block deletion (i.e. ``blocked`` is non-empty)."""

    root: DeletePreviewNode = strawberry.field(
        default_factory=lambda: DeletePreviewNode(label="", object_label="", object_id=None, children=[]),
        description="Tree apex for the target row; deleted counts already include that row.",
    )
    """Rooted tree of rows Django would delete; deleted counts include this root row."""

    deleted_instance: strawberry.Private[models.Model | None] = None
    """Deleted row returned internally to mutation envelopes, never exposed in SDL."""

    @classmethod
    def from_instance(cls, instance: models.Model, actor: Any | None = None) -> DeletePreview:
        """Return Django's cascade forecast for ``instance``.

        Callers should run previews inside the same transaction as the eventual
        delete so fast-delete counts and visible rows share one database snapshot.
        """

        collector = Collector(using=instance._state.db or "default")
        blocked: list[DeletePreviewGroup] = []
        try:
            collector.collect([instance])
        except ProtectedError as error:
            blocked = _groups(_count_by_model(error.protected_objects))
        except RestrictedError as error:
            blocked = _groups(_count_by_model(error.restricted_objects))

        fast_deletes: tuple[_FastDelete, ...] = tuple(
            (queryset, queryset.count()) for queryset in collector.fast_deletes
        )
        groups = _PreviewRows.by_model(
            instance,
            collector,
            fast_deletes,
            actor if actor is not None else current_actor(),
        )
        root = DeletePreviewNode.from_target(instance, groups)
        deleted_counts = _deleted_counts(instance, groups)

        updated_counts: dict[type[models.Model], int] = {}
        for (model_field, _value), object_groups in collector.field_updates.items():
            model = model_field.model
            updated_counts[model] = updated_counts.get(model, 0) + sum(len(group) for group in object_groups)
        return cls(
            total_deleted_count=sum(deleted_counts.values()),
            deleted=_groups(deleted_counts),
            updated=_groups(updated_counts),
            blocked=blocked,
            has_blockers=bool(blocked),
            root=root,
        )


def delete_by_public_id(
    model: type[models.Model],
    public_id: str,
    *,
    confirm: bool = False,
    queryset: models.QuerySet[models.Model] | None = None,
    before_delete: Callable[[models.Model], None] | None = None,
    reason: str | None = None,
) -> DeletePreview:
    """Preview, then optionally delete, one public-id-addressed model row.

    The caller owns authorization. Passing ``reason`` elevates the lookup/delete
    under ``system_context`` for admin/action surfaces whose permission class has
    already gated the request actor.
    """

    context = system_context(reason=reason) if reason is not None else nullcontext()
    with context, transaction.atomic():
        instance = require_instance_for_id(
            model,
            public_id,
            queryset=queryset if queryset is not None else model._default_manager.all(),
        )
        preview = DeletePreview.from_instance(instance)
        if confirm and not preview.has_blockers:
            if before_delete is not None:
                before_delete(instance)
            deleted_pk = instance.pk
            try:
                instance.delete()
            except (ProtectedError, RestrictedError):
                preview = DeletePreview.from_instance(instance)
            else:
                instance.pk = deleted_pk
                preview.deleted_instance = instance
    return preview


_SurfaceT = TypeVar("_SurfaceT")


def attach_delete_preview_metadata(
    surface: type[_SurfaceT],
    *,
    model: type[models.Model],
    node: type,
    field: str,
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
) -> type[_SurfaceT]:
    """Attach resource metadata for one authored cascade-preview mutation."""

    return attach_data_resource_metadata(
        surface,
        make_data_resource_metadata(
            model=model,
            model_label=model_label,
            public_id_field=public_id_field,
            node_type=node,
            roots=DataResourceRoots(
                delete_preview_name=resource_wire_field_name(surface, field),
            ),
            type_names=DataResourceTypeNames(
                node=resource_type_name(node),
                delete_payload=resource_type_name(DeletePreview),
            ),
            capabilities=("deletePreview",),
        ),
    )


@dataclass(slots=True)
class _PreviewRows:
    """Access-scoped visible/count-only row state for one preview tree group."""

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

    @classmethod
    def by_model(
        cls,
        root: models.Model,
        collector: Collector,
        fast_deletes: tuple[_FastDelete, ...],
        actor: Any | None,
    ) -> dict[type[models.Model], _PreviewRows]:
        """Return deleted tree rows grouped by model, excluding ``root``."""

        groups: dict[type[models.Model], _PreviewRows] = {}
        for model, rows in collector.data.items():
            preview = cls.from_collected(root, model, rows, actor)
            if preview.total_count:
                groups.setdefault(model, cls()).add(
                    total_count=preview.total_count,
                    visible_count=preview.visible_count,
                    visible_rows=preview.visible_rows,
                )
        for queryset, total_count in fast_deletes:
            preview = cls.from_fast_delete(queryset, total_count, actor)
            if preview.total_count:
                groups.setdefault(queryset.model, cls()).add(
                    total_count=preview.total_count,
                    visible_count=preview.visible_count,
                    visible_rows=preview.visible_rows,
                )
        return {model: rows for model, rows in groups.items() if rows.total_count}

    @classmethod
    def from_collected(
        cls,
        root: models.Model,
        model: type[models.Model],
        rows: Iterable[models.Model],
        actor: Any | None,
    ) -> _PreviewRows:
        """Return access-scoped preview rows from collector-materialized rows."""

        collected = [row for row in rows if not _is_root(root, row)]
        if not collected:
            return cls()
        scoped = _read_scoped_queryset(model, actor)
        if scoped is None:
            if _requires_read_scope(model):
                return cls(total_count=len(collected), visible_count=0)
            return cls(total_count=len(collected), visible_count=len(collected), visible_rows=collected)
        return cls._from_scoped_collected(collected, scoped)

    @classmethod
    def _from_scoped_collected(
        cls,
        collected: list[models.Model],
        scoped: models.QuerySet[models.Model],
    ) -> _PreviewRows:
        """Return visible collected rows without issuing an unbounded ``pk__in`` query."""

        pk_to_row = {row.pk: row for row in collected}
        visible_rows: list[models.Model] = []
        visible_count = 0
        for pk_chunk in _chunks([row.pk for row in collected], _PREVIEW_PK_CHUNK_SIZE):
            chunk_visible = set(scoped.filter(pk__in=pk_chunk).values_list("pk", flat=True))
            visible_count += len(chunk_visible)
            if len(visible_rows) <= _PREVIEW_LEAF_LIMIT:
                visible_rows.extend(
                    pk_to_row[pk] for pk in pk_chunk if pk in chunk_visible and pk in pk_to_row
                )
                if len(visible_rows) > _PREVIEW_LEAF_LIMIT + 1:
                    visible_rows = visible_rows[: _PREVIEW_LEAF_LIMIT + 1]
        return cls(total_count=len(collected), visible_count=visible_count, visible_rows=visible_rows)

    @classmethod
    def from_fast_delete(
        cls,
        queryset: models.QuerySet[models.Model],
        total_count: int,
        actor: Any | None,
    ) -> _PreviewRows:
        """Return access-scoped preview rows from one fast-delete queryset."""

        if total_count == 0:
            return cls()
        scoped = _read_scoped_queryset(queryset.model, actor)
        if scoped is None:
            if _requires_read_scope(queryset.model):
                return cls(total_count=total_count, visible_count=0)
            return cls(
                total_count=total_count,
                visible_count=total_count,
                visible_rows=list(_order_by_pk(queryset)[: _PREVIEW_LEAF_LIMIT + 1]),
            )
        visible_queryset = scoped.filter(pk__in=models.Subquery(queryset.order_by().values("pk")))
        visible_count = visible_queryset.count()
        return cls(
            total_count=total_count,
            visible_count=visible_count,
            visible_rows=list(_order_by_pk(visible_queryset)[: _PREVIEW_LEAF_LIMIT + 1]),
        )


def _groups(counts: dict[type[models.Model], int]) -> list[DeletePreviewGroup]:
    """Return sorted non-empty deletion preview groups."""

    return [
        DeletePreviewGroup(
            label=str(model._meta.verbose_name_plural),
            count=count,
        )
        for model, count in sorted(
            counts.items(),
            key=lambda item: item[0]._meta.label,
        )
        if count
    ]


def _deleted_counts(
    root: models.Model,
    groups: dict[type[models.Model], _PreviewRows],
) -> dict[type[models.Model], int]:
    """Return deleted row counts from the preview row inventory."""

    counts: dict[type[models.Model], int] = {type(root): 1}
    for model, rows in groups.items():
        counts[model] = counts.get(model, 0) + rows.total_count
    return counts


def _count_by_model(
    instances: Iterable[models.Model],
) -> dict[type[models.Model], int]:
    """Return counts grouped by model class."""

    counts: dict[type[models.Model], int] = {}
    for instance in instances:
        model = type(instance)
        counts[model] = counts.get(model, 0) + 1
    return counts


def _chunks(values: list[Any], size: int) -> Iterable[list[Any]]:
    """Yield ``values`` in fixed-size chunks."""

    for index in range(0, len(values), size):
        yield values[index : index + size]


def _read_scoped_queryset(
    model: type[models.Model],
    actor: Any | None,
) -> models.QuerySet[models.Model] | None:
    """Return a read-scoped queryset for a REBAC model, if one can be resolved."""

    return read_scoped_queryset(model, actor)


def _requires_read_scope(model: type[models.Model]) -> bool:
    """Return whether concrete tree leaves for ``model`` must be actor scoped."""

    return bool(model_resource_type(model))


def _order_by_pk(queryset: models.QuerySet[models.Model]) -> models.QuerySet[models.Model]:
    """Return ``queryset`` ordered by its native primary-key column."""

    return queryset.order_by(queryset.model._meta.pk.name)


def _is_root(root: models.Model, row: models.Model) -> bool:
    """Return whether ``row`` is the deletion preview target."""

    return root._meta.concrete_model is row._meta.concrete_model and root.pk == row.pk
