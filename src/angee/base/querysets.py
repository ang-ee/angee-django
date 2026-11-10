"""Query conveniences shared by scoped and explicitly unscoped managers."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from itertools import batched
from typing import Any, Self, TypeVar, cast

from django.core.exceptions import EmptyResultSet, ValidationError
from django.db import connection, models
from django.db.models.expressions import BaseExpression
from django.db.models.functions import Coalesce, RowNumber
from rebac import current_actor
from rebac.managers import RebacQuerySet, TrackedQuerySet
from rebac.watch import KEY_CHUNK
from rebac.watch import by_key as native_by_key

from angee.base.models.identity import instance_from_public_id
from angee.base.pagination import KeysetOrder, KeysetPage, keyset_signer

_ModelT = TypeVar("_ModelT", bound=models.Model)
_QuerySetT = TypeVar("_QuerySetT", bound=models.QuerySet[Any])


class ClosedUpdateQuerySet(models.QuerySet[_ModelT]):
    """Close generic collection edits while retaining validated owner updates.

    Compose this policy before other queryset capabilities. ``owner_update``
    skips this sole closed-edit guard and preserves downstream guards, native
    capture, database routing and tracked writes. Insertion and deletion remain
    separate policy owners; ``immutable_error`` is their shared error hook.
    """

    def immutable_error(self, operation: str) -> ValidationError:
        """Identify the model whose generic collection mutation is forbidden."""

        action = "deleted" if operation in {"delete", "_raw_delete"} else "edited"
        return ValidationError(f"{self.model._meta.label} rows cannot be {action}.")

    def owner_update(self, **kwargs: Any) -> int:
        """Protected API: apply an owner-validated write through downstream guards."""

        return super().update(**kwargs)

    def update(self, **kwargs: Any) -> int:
        """Reject every collection edit."""

        raise self.immutable_error("update")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        """Reject every batched edit."""

        raise self.immutable_error("bulk_update")


def query_batch_size(queryset: models.QuerySet[Any], *, width: int = 1) -> int:
    """Bound native key batches by this query's remaining database parameters."""

    limit = connection.features.max_query_params
    if limit is None:
        return KEY_CHUNK
    try:
        _, params = queryset.query.get_compiler(connection=connection).as_sql()
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


def row_lock_paths(model: type[models.Model], of: tuple[str, ...] = ("self",)) -> tuple[str, ...]:
    """Lock this row's MTI tables without adding unrelated joined relations."""

    if "self" not in of:
        return of
    return (*of, *(
        path for path in _parent_link_paths(model._meta.concrete_model or model) if path not in of
    ))


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


class CanonicalQuerySet(models.QuerySet[_ModelT]):
    """Terminal operational scope for cooperative queryset capabilities."""

    def canonical(self) -> Self:
        """All rows until an owning capability narrows the operational scope."""

        return self


class MergeableQuerySet(CanonicalQuerySet[_ModelT]):
    """Operational identities; compose trash and archival scopes independently."""

    def canonical(self) -> Self:
        """Rows that redirect nowhere, plus a retained redirect whose survivor these rows do not reach.

        A redirect is identity, not content: a reader this scope withholds the
        survivor from (an actor's read, a channel filter) still reads the copy it
        does reach, so a merged record never disappears from a reader who could
        see only it.
        """

        rows = super().canonical()
        survivors = self.filter(pk=models.OuterRef("merged_into_id")).order_by().values("pk")
        return rows.filter(models.Q(merged_into__isnull=True) | ~models.Exists(survivors))

    @staticmethod
    def survivor_id(through: str = "", *, ref: Callable[[str], Any] = models.F) -> Coalesce:
        """Return the survivor id of a row, or of the mergeable row reached through ``through``.

        The survivor id is the id a row redirects to, else its own. Redirects are
        flattened to one hop, so :meth:`redirect_group` of a survivor is exactly the
        rows whose survivor id is its pk. ``ref`` builds each column reference:
        ``F`` within the query, ``OuterRef`` from a subquery.
        """

        return Coalesce(ref(f"{through}merged_into_id"), ref(f"{through}pk"))

    @staticmethod
    def survivor_field(through: str, name: str, *, ref: Callable[[str], Any] = models.F) -> Coalesce:
        """Return column ``name`` of the survivor of the mergeable row reached through ``through``.

        A row pointing at a retired record reads its survivor's value, as if the
        merge had moved it. It is a ``Coalesce``: where the survivor's value is
        null (a survivor with no party), the row's own value reads instead.
        Redirects are flattened to one hop, so the survivor is one join away.
        ``ref`` builds each column reference, as in :meth:`survivor_id`.
        """

        return Coalesce(ref(f"{through}merged_into__{name}"), ref(f"{through}{name}"))

    def redirect_group(self, survivor: _ModelT) -> Self:
        """Return ``survivor`` and the one-hop redirects flattened into it.

        The disjunction probes the primary key and redirect indexes, which suits a
        bounded or per-row lookup; a corpus-wide join keys on :meth:`survivor_ids`.
        """

        return self.filter(models.Q(pk=survivor.pk) | models.Q(merged_into_id=survivor.pk))

    def redirect_groups(self, survivors: Any) -> Self:
        """Return every survivor in ``survivors`` and the one-hop redirects flattened into it.

        ``survivors`` holds canonical primary keys, as values or a subquery. A
        reader of rows a merge kept on retired records selects them through the
        groups of the survivors it reads, on the same indexes as :meth:`redirect_group`.
        """

        return self.filter(models.Q(pk__in=survivors) | models.Q(merged_into_id__in=survivors))

    def survivor_ids(self) -> models.QuerySet[Any]:
        """Return each row's :meth:`survivor_id` as ``survivor``.

        A corpus-wide anti-join or semi-join on equality with this key is hashed
        once, where a correlated :meth:`redirect_group` probe runs once per outer row.
        """

        return self.order_by().values(survivor=self.survivor_id())


class AngeeQuerySetMixin(CanonicalQuerySet[_ModelT]):
    """Shared native lookup and locking, preserving each manager's policy.

    Every Angee queryset composes it. An owner that has already validated a
    write reaches the persistence beneath its own guards through
    ``super(AngeeQuerySetMixin, rows)``.
    """

    model: type[_ModelT]
    _hints: dict[str, models.Model]

    def from_public_id(self, value: str) -> _ModelT | None:
        """Return the row addressed by ``value`` within this queryset policy."""

        return instance_from_public_id(self.model, value, queryset=self)

    def lock_if_supported(
        self, *, of: tuple[str, ...] = ("self",), skip_locked: bool = False, no_key: bool = False
    ) -> Self:
        """Expose shared lock intent on Angee querysets and managers."""

        return cast(Self, self.select_for_update(
            of=row_lock_paths(self.model, of), skip_locked=skip_locked, no_key=no_key,
        ))

    def locked_get(self, *args: Any, **kwargs: Any) -> _ModelT:
        """Return one row under a database row lock when the backend supports it."""

        return cast(models.QuerySet[_ModelT], self.lock_if_supported()).get(*args, **kwargs)

    def first_per_group(
        self,
        *partition_by: str | BaseExpression | models.F,
        order_by: Sequence[str | BaseExpression | models.F],
        limit: int,
    ) -> Self:
        """Keep the first ``limit`` rows of each group, ranked by one SQL window.

        A group is one distinct value of ``partition_by`` (field paths, annotations
        or expressions). ``order_by`` ranks the rows inside each group and should end
        in a unique column so the kept rows are deterministic. The result keeps this
        queryset's scope, annotations and selection but not its ordering, so an
        inherited ``Meta.ordering`` never joins a grouped selection's ``GROUP BY``;
        callers order the kept rows. Filters chained afterwards apply before the
        ranking, as Django wraps a window filter in an outer query.

        The rank is a selected annotation (``_group_rank``). Callers that need
        both model rows and duplicate column annotations should select ranked
        identities in a subquery, then project the rows outside the window mask.
        Django can add that mask for unselected ordering columns even when the
        rank is selected, collapsing duplicate expressions and shifting converters.
        """

        if not partition_by:
            raise ValueError("first_per_group needs at least one partition.")
        if limit < 1:
            raise ValueError("first_per_group keeps at least one row per group.")
        rank = models.Window(RowNumber(), partition_by=list(partition_by), order_by=list(order_by))
        return cast(Self, self.order_by().annotate(_group_rank=rank).filter(_group_rank__lte=limit))


class AngeeQuerySet(
    AngeeQuerySetMixin[_ModelT],
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
        aggregate: bool = False,
    ) -> models.Expression | models.Subquery:
        """Project one correlated readable value, including under an elevated parent.

        Add correlation predicates before calling. No actor yields no value;
        callers own domain fallbacks and may explicitly coalesce an absent value.
        ``aggregate`` reads under REBAC's eager aggregate scope
        (``scoped_for_aggregate``), the row authorization the aggregate roots
        use, instead of the row scope.
        """

        actor = actor or self.actor() or current_actor()
        readable = self
        if actor is not None:
            bound = self.with_actor(actor)
            readable = bound.scoped_for_aggregate() if aggregate else bound.scoped()
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

    def readable_count_subquery(
        self, *, actor: Any = None, aggregate: bool = False,
    ) -> models.Expression | models.Subquery:
        """Count a correlated actor-scoped row set's distinct rows, returning zero when empty.

        ``aggregate`` counts under the aggregate roots' eager row scope, as
        :meth:`readable_scalar_subquery` reads.
        """

        rows = self.order_by().annotate(_count_group=models.Value(1)).values("_count_group")
        return rows.annotate(_count=models.Count("pk", distinct=True)).readable_scalar_subquery(
            "_count",
            actor=actor,
            default=0,
            output_field=models.IntegerField(),
            aggregate=aggregate,
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
        signer = keyset_signer(
            [self.db, self.model._meta.label_lower, *cursor_scope, str(actor)], salt=cursor_salt,
        )
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
            oldest_first = order.ordering(oldest_first=True)
            newer = list(self.filter(order.after(order.position(around))).order_by(*oldest_first)[: limit // 2])
            upper = order.position(newer[-1] if newer else around)
            window = window.exclude(order.after(upper))
        ascending = after_cursor is not None
        selected = list(window.order_by(*order.ordering(oldest_first=ascending))[: limit + 1])
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
            rows=self.filter(pk__in=[row.pk for row in rows]).order_by(*order.ordering()),
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
    AngeeQuerySetMixin[_ModelT],
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


class MergeableUnscopedQuerySet(MergeableQuerySet[_ModelT], AngeeUnscopedQuerySet[_ModelT]):
    """Explicit operational identity API for a model that owns merge redirects."""
