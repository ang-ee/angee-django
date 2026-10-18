"""Query conveniences shared by scoped and explicitly unscoped managers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from itertools import batched
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from django.conf import settings
from django.core import signing
from django.core.exceptions import EmptyResultSet
from django.db import NotSupportedError, connections, models, transaction
from django.db.models.functions import Coalesce
from rebac import current_actor, system_context
from rebac.managers import RebacQuerySet, TrackedQuerySet
from rebac.resources import model_resource_type
from rebac.watch import KEY_CHUNK, statement_keys
from rebac.watch import by_key as native_by_key

from angee.changesets import changeset_model_label, current_changeset, write_group
from angee.pagination import KeysetOrder, KeysetPage

if TYPE_CHECKING:
    from angee.models.history import ModelHistory

_ModelT = TypeVar("_ModelT", bound=models.Model)
_QuerySetT = TypeVar("_QuerySetT", bound=models.QuerySet[Any])


class SystemQuerysetMixin:
    """Elevated unscoped reads for models that own internal persistence work."""

    @classmethod
    def system_queryset(
        cls,
        *,
        lock: tuple[str, ...] | None = None,
    ) -> AngeeQuerySet[Self]:
        """Return an elevated unscoped queryset with the shared lock contract."""

        model = cast(type[models.Model], cls)
        queryset = cast(AngeeQuerySet[Self], model._default_manager.get_queryset())
        if isinstance(queryset, RebacQuerySet):
            queryset = queryset.system_context(reason=f"{model._meta.label_lower}.system_queryset")
        if lock is None:
            return queryset
        if "self" in lock:
            lock = (
                *lock,
                *(
                    path
                    for path in _parent_link_paths(model._meta.concrete_model)
                    if path not in lock
                ),
            )
        lock_if_supported = getattr(queryset, "lock_if_supported", None)
        return (
            lock_if_supported(of=lock)
            if callable(lock_if_supported)
            else cast(AngeeQuerySet[Self], queryset.select_for_update(of=lock))
        )


@dataclass(frozen=True, slots=True)
class CandidateRelationships:
    """The relations a row would carry for one action, as rebac's candidate preflight reads them.

    This is the one adapter over rebac's field-backing owner (still private
    there): a caller asks about a row that is not, or not yet, the stored one (an
    unsaved task's assignee, a retained history snapshot) and checks a subject
    against the relations its fields and ledger would give it.
    """

    resource_type: str
    definition: Any
    required: frozenset[str]
    relationships: dict[str, Any]

    def allows(self, subject: Any, action: str) -> bool:
        """Whether ``subject`` may perform ``action`` on the candidate these relations describe."""

        from rebac import check_new, to_subject_ref

        return check_new(
            subject=to_subject_ref(subject), action=action, resource_type=self.resource_type,
            relationships=self.relationships,
        ).allowed


def candidate_relationships(instance: models.Model, action: str) -> CandidateRelationships | None:
    """Read the relations ``instance`` would carry for ``action``; ``None`` when its type has no definition."""

    # The composer imports this module at settings time, before the app registry
    # is ready; rebac's backend and field backing load auth models on import.
    from rebac.backends import backend
    from rebac.field_backing import _proposed_forward_relationships
    from rebac.schema.introspection import relation_dependencies

    resource_type = model_resource_type(type(instance))
    if resource_type is None:
        return None
    schema = backend().schema()
    definition = schema.get_definition(resource_type)
    if definition is None:
        return None
    required = frozenset(relation_dependencies(schema, resource_type, action))
    relationships = _proposed_forward_relationships(
        instance,
        definition,
        required_relations=required,
        using=instance._state.db,
    )
    return CandidateRelationships(resource_type, definition, required, relationships)


def query_batch_size(queryset: models.QuerySet[Any], *, width: int = 1) -> int:
    """Bound native key batches by this query's remaining database parameters."""

    limit = connections[queryset.db].features.max_query_params
    if limit is None:
        return KEY_CHUNK
    try:
        _, params = queryset.query.get_compiler(using=queryset.db).as_sql()
    except EmptyResultSet:
        params = ()
    size = min(KEY_CHUNK, (limit - len(params)) // width)
    if size < 1:
        raise ValueError("The query leaves no database parameters for its keys.")
    return size


def by_key(queryset: _QuerySetT, keys: Sequence[Any]) -> Iterator[_QuerySetT]:
    """Compose REBAC key filtering with the database's native parameter budget."""

    if not keys:
        yield from native_by_key(queryset, keys)
    for chunk in batched(keys, query_batch_size(queryset)):
        yield from native_by_key(queryset, chunk)


def _parent_link_paths(model: type[models.Model], prefix: str = "") -> tuple[str, ...]:
    """Return ``of`` paths reaching each concrete multi-table ancestor of ``model``."""

    paths: list[str] = []
    for parent, link in model._meta.parents.items():
        if link is None:
            continue
        path = f"{prefix}{link.name}"
        paths.append(path)
        paths.extend(_parent_link_paths(parent, f"{path}__"))
    return tuple(paths)


def model_history(model: type[models.Model]) -> ModelHistory | None:
    """Resolve the history registration owned by ``model``'s concrete table.

    A proxy write mutates that same table.  Returning the concrete registration
    lets queryset history capture read and snapshot the stored concrete row while
    the proxy queryset retains its own guards and delete receivers.
    """

    if not getattr(settings, "SIMPLE_HISTORY_ENABLED", True):
        return None
    if model._meta.label_lower == changeset_model_label().lower():
        return None
    concrete = model._meta.concrete_model
    history = getattr(concrete._meta, "angee_model_history", None)
    if history is None:
        history = next(
            (
                getattr(parent._meta, "angee_model_history", None)
                for parent in concrete._meta.all_parents
                if getattr(parent._meta, "angee_model_history", None) is not None
            ),
            None,
        )
    return cast("ModelHistory | None", history)


def record_history(model: type[models.Model]) -> ModelHistory | None:
    """Return the history owner of a permission-governed record model.

    History is read through the record's own REBAC gate, so a tracked table
    without a resource type (a statement shadow table) has no readable history
    of its own: it is read through its subject. Resource metadata and the
    provenance history surface share this one admission rule.
    """

    if not model_resource_type(model):
        return None
    return model_history(model)


class MergeableQuerySet(models.QuerySet[_ModelT]):
    """Operational identities; compose trash and archival scopes independently."""

    def canonical(self) -> Any:
        """Rows that redirect nowhere, plus a retained redirect whose survivor these rows do not reach.

        A redirect is identity, not content: a reader this scope withholds the
        survivor from (an actor's read, a channel filter) still reads the copy it
        does reach, so a merged record never disappears from a reader who could
        see only it.
        """

        rows = super().canonical()
        survivors = self.filter(pk=models.OuterRef("merged_into_id")).order_by().values("pk")
        return rows.filter(models.Q(merged_into__isnull=True) | ~models.Exists(survivors))

    def redirect_group(self, survivor: _ModelT) -> Self:
        """Return ``survivor`` and the one-hop redirects flattened into it."""

        return self.filter(models.Q(pk=survivor.pk) | models.Q(merged_into_id=survivor.pk))


class _AngeeQuerySetMixin(models.QuerySet[_ModelT]):
    """Shared lookup, locking and history capture, preserving each manager's policy.

    Bulk updates dispatch through Django's native update batches. Each executed
    batch opens its own write group, so repeated rows across batches retain every
    stored state. SQL execution always delegates to the downstream REBAC owner.
    """

    model: type[_ModelT]

    def canonical(self) -> Any:
        """All rows until an owning capability narrows the operational scope."""

        return self

    def update(self, **kwargs: Any) -> int:
        """Capture stored state after an authorized write of frozen, locked keys.

        A write to columns the history excludes (a counter), with at most its
        timestamp stamps, changes no tracked state and records no version.
        """

        history_model = self.model._meta.concrete_model
        history = model_history(history_model)
        if history is None or self.query.is_sliced or not self._changes_tracked_state(
            history, history_model, kwargs
        ):
            return super().update(**kwargs)
        self._for_write = True
        selection = self._system_capture(reason="angee.history.rows") if isinstance(self, RebacQuerySet) else self
        with transaction.atomic(using=self.db):
            keys = list(dict.fromkeys(statement_keys(cast(Any, selection).lock_if_supported())))
            if not keys:
                return 0
            current_changeset(required=True, record=True)
            with transaction.atomic(using=self.db, savepoint=False), write_group():
                count = sum(super(_AngeeQuerySetMixin, rows).update(**kwargs) for rows in by_key(self, keys))
                history.bulk_history_create(history_model, keys, "~", using=self.db)
                self._result_cache = None
                return count

    def _changes_tracked_state(
        self,
        history: ModelHistory,
        model: type[models.Model],
        values: Mapping[str, Any],
    ) -> bool:
        """Whether an update writes a column the history tracks, beyond auto-now stamps."""

        tracked = {field.attname for field in history.fields_included(model)} | {
            field.name for field in history.fields_included(model)
        }
        stamps = {
            name for field in model._meta.concrete_fields if getattr(field, "auto_now", False)
            for name in (field.name, field.attname)
        }
        return any(name in tracked for name in values if name not in stamps)

    def bulk_create(
        self,
        objs: Iterable[_ModelT],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        unique_fields: Iterable[str] | None = None,
    ) -> list[_ModelT]:
        """Capture inserted/upserted stored rows, refusing unknowable identities."""

        history_model = self.model._meta.concrete_model
        history = model_history(history_model)
        candidates = list(objs)
        if history is not None and candidates:
            if ignore_conflicts:
                raise NotSupportedError("History capture cannot identify bulk_create(ignore_conflicts=True) rows.")
            if not connections[self.db].features.can_return_rows_from_bulk_insert and any(
                obj.pk is None for obj in candidates
            ):
                raise NotSupportedError("History capture requires bulk-insert primary keys on this database.")
            current_changeset(required=True, record=True)
        self._for_write = True
        with transaction.atomic(using=self.db) if history is not None else nullcontext(), write_group():
            rows = super().bulk_create(
                candidates,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts,
                update_fields=update_fields,
                unique_fields=unique_fields,
            )
            if history is not None and rows:
                if any(row.pk is None for row in rows):
                    raise NotSupportedError("History capture requires a primary key for every bulk-inserted row.")
                history.bulk_history_create(
                    history_model,
                    [row.pk for row in rows],
                    "~" if update_conflicts else "+",
                    using=self.db,
                    batch_size=batch_size,
                )
            return rows

    def delete(self) -> tuple[int, dict[str, int]]:
        """Batch root snapshots; native receivers retain collector cascade capture."""

        history_model = self.model._meta.concrete_model
        history = model_history(history_model)
        if history is not None and history.cascade_delete_history:
            history = None
        if history is None or self.query.is_sliced:
            return super().delete()
        self._for_write = True
        selection = self._system_capture(reason="angee.history.rows") if isinstance(self, RebacQuerySet) else self
        with transaction.atomic(using=self.db):
            keys = list(dict.fromkeys(statement_keys(cast(Any, selection).lock_if_supported())))
            if not keys:
                return 0, {}
            current_changeset(required=True, record=True)
            with transaction.atomic(using=self.db, savepoint=False), write_group():
                history.bulk_history_create(history_model, keys, "-", using=self.db)
                counts: dict[str, int] = {}
                for rows in by_key(self, keys):
                    _, deleted = super(_AngeeQuerySetMixin, rows).delete()
                    for label, count in deleted.items():
                        counts[label] = counts.get(label, 0) + count
                self._result_cache = None
                return sum(counts.values()), counts

    def from_public_id(self, value: str) -> _ModelT | None:
        """Return the row addressed by ``value`` within this queryset policy."""

        if value == "":
            return None
        try:
            lookup = cast(Any, self.model).public_id_lookup(value)
            return cast(_ModelT | None, cast(Any, self).filter(**lookup).first())
        except TypeError, ValueError:
            return None

    def lock_if_supported(
        self, *, of: tuple[str, ...] = ("self",), skip_locked: bool = False, no_key: bool = False
    ) -> Self:
        """Expose shared lock intent on Angee querysets and managers."""

        if "self" in of:
            of = (*of, *(path for path in _parent_link_paths(self.model._meta.concrete_model) if path not in of))
        return cast(Self, self.select_for_update(of=of, skip_locked=skip_locked, no_key=no_key))

    def locked_get(self, *args: Any, **kwargs: Any) -> _ModelT:
        """Return one row under a database row lock when the backend supports it."""

        return cast(models.QuerySet[_ModelT], self.lock_if_supported()).get(*args, **kwargs)


class RetainedReferenceQuerySet(_AngeeQuerySetMixin[_ModelT], TrackedQuerySet[_ModelT]):
    """Django's scheduled retained-FK updates, with capture and system authority."""

    def update(self, **kwargs: Any) -> int:
        """Remove the edge as part of its authorized target deletion."""

        with system_context(reason="delete.retain"):
            return super().update(**kwargs)


class AngeeQuerySet(
    _AngeeQuerySetMixin[_ModelT],
    RebacQuerySet[_ModelT],
):
    """QuerySet API shared by Angee source and runtime models."""

    def readable_scalar_subquery(
        self,
        field: str,
        *,
        actor: Any = None,
        default: Any = None,
        output_field: models.Field | None = None,
    ) -> models.Expression:
        """Project one correlated readable value, including under an elevated parent.

        Add correlation predicates before calling. No actor yields no value;
        callers own domain fallbacks and may explicitly coalesce an absent value.
        """

        actor = actor or self.actor() or current_actor()
        readable = self.with_actor(actor).scoped() if actor is not None else self
        # The selected field owns the scalar type; only the outer fallback needs
        # a common output type (overriding Subquery changes empty-set compilation).
        scalar = models.Subquery(readable.values(field)[:1])
        if actor is None:
            return models.Value(default, output_field=output_field or scalar.output_field)
        if default is None:
            return scalar
        return Coalesce(
            scalar,
            models.Value(default),
            output_field=output_field or scalar.output_field,
        )

    def readable_count_subquery(self, *, actor: Any = None) -> models.Expression:
        """Count a correlated actor-scoped row set, returning zero when empty."""

        rows = self.order_by().annotate(_count_group=models.Value(1)).values("_count_group")
        return rows.annotate(_count=models.Count("pk", distinct=True)).readable_scalar_subquery(
            "_count",
            actor=actor,
            default=0,
            output_field=models.IntegerField(),
        )

    def keyset_page(
        self,
        *,
        order: KeysetOrder,
        cursor_scope: tuple[Any, ...],
        cursor_salt: str = "angee.keyset.v1",
        before_cursor: str | None = None,
        after_cursor: str | None = None,
        through_cursor: str | None = None,
        around: models.Model | None = None,
        limit: int = 50,
    ) -> KeysetPage[_ModelT]:
        """Page this authorized queryset using signed, stable timestamp/PK cuts.

        The caller supplies a prepared, scoped queryset and canonical effective
        query identity in ``cursor_scope`` (root, filters and any variable order).
        Database, model and actor are always included. The salt versions the
        ordering contract. Cuts authorize nothing: each call reevaluates the
        readable queryset, including rows whose anchor was deleted or moved.

        ``before`` is exclusive; ``through`` is an inclusive fixed lower cut.
        ``after`` discovers the nearest newer page and cannot combine with either.
        Return rows newest-first and distinguish an exhausted fixed window from
        history below that window, even when the window is now empty.
        """

        if after_cursor is not None and (before_cursor is not None or through_cursor is not None):
            raise ValueError("after_cursor cannot combine with before_cursor or through_cursor.")
        limit = max(1, min(int(limit), 200))
        actor = self.actor() or current_actor()
        namespace = json.dumps([self.db, self.model._meta.label_lower, *cursor_scope, str(actor)])
        fingerprint = hashlib.sha256(namespace.encode()).hexdigest()
        signer = signing.Signer(salt=f"{cursor_salt}.{fingerprint}")
        cursor = before_cursor if before_cursor is not None else after_cursor
        anchor = order.unsign(cursor, signer, self.model._meta.pk) if cursor is not None else None
        lower = order.unsign(through_cursor, signer, self.model._meta.pk) if through_cursor is not None else None
        count = self.count()
        window = self
        if anchor is not None:
            window = window.filter(order.before(anchor) if before_cursor is not None else order.after(anchor))
        if lower is not None:
            window = window.exclude(order.before(lower))
        if around is not None and before_cursor is None and after_cursor is None:
            # Bound the first window around an already authorized record. The
            # domain resolves a message/date; this owner only handles tuple order.
            newer = list(self.filter(order.after(order.position(around))).order_by(order.field, "pk")[: limit // 2])
            upper = order.position(newer[-1] if newer else around)
            window = window.exclude(order.after(upper))
        ascending = after_cursor is not None
        ordering = (order.field, "pk") if ascending else (f"-{order.field}", "-pk")
        selected = list(window.order_by(*ordering)[: limit + 1])
        has_more = len(selected) > limit
        rows = selected[:limit]
        if ascending:
            rows.reverse()
        below = self.filter(order.before(lower)).exists() if lower is not None else False
        if not rows:
            return KeysetPage(
                rows=[],
                count=count,
                older_cursor=None,
                newer_cursor=None,
                has_older=False,
                has_newer=False,
                has_more_in_window=False,
                has_older_than_through=below,
                has_newer_than_before=self.filter(order.after(anchor)).exists() if anchor is not None else False,
            )
        # Re-fetch the bounded identities through the original queryset so callers
        # retain a composable queryset instead of inheriting this method's probe list.
        return KeysetPage(
            rows=self.filter(pk__in=[row.pk for row in rows]).order_by(f"-{order.field}", "-pk"),
            count=count,
            older_cursor=order.sign(rows[-1], signer),
            newer_cursor=order.sign(rows[0], signer),
            has_older=self.filter(order.before(order.position(rows[-1]))).exists(),
            has_newer=self.filter(order.after(order.position(rows[0]))).exists(),
            has_more_in_window=has_more,
            has_older_than_through=below,
            has_newer_than_before=self.filter(order.after(anchor)).exists() if anchor is not None else False,
        )


class AngeeUnscopedQuerySet(
    MergeableQuerySet[_ModelT],
    _AngeeQuerySetMixin[_ModelT],
    TrackedQuerySet[_ModelT],
):
    """Angee queryset API for intentionally permission-naive managers.

    Used by models without REBAC row policy and explicit Django base managers
    whose unfiltered relation reads must retain native Django semantics. It
    composes the library's tracked writes required by a declared base manager.
    """

    def scoped_for_aggregate(self) -> Self:
        """Return this queryset for permission-naive aggregation.

        The manager deliberately supplies no row authorization; aggregation
        preserves that same explicit unscoped policy.
        """

        return self
