"""Query conveniences shared by scoped and explicitly unscoped managers."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from itertools import batched
from typing import Any, Self, TypeVar, cast

from django.core.exceptions import EmptyResultSet, ValidationError
from django.db import connections, models
from django.db.models.functions import Coalesce

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

    limit = connections[queryset.db].features.max_query_params
    if limit is None:
        return 5000
    try:
        _, params = queryset.query.get_compiler(using=queryset.db).as_sql()
    except EmptyResultSet:
        params = ()
    size = min(5000, (limit - len(params)) // width)
    if size < 1:
        raise ValueError("The query leaves no database parameters for its keys.")
    return size


def by_key(queryset: _QuerySetT, keys: Sequence[Any]) -> Iterator[_QuerySetT]:
    """Batch primary-key filtering with the database's native parameter budget."""

    if not keys:
        yield queryset.filter(pk__in=())
    for chunk in batched(keys, query_batch_size(queryset)):
        yield queryset.filter(pk__in=chunk)


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

    def attributed_user_id(self) -> Any:
        """A normal Django queryset carries no implicit user attribution."""
        return None

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

    def redirect_group(self, survivor: _ModelT) -> Self:
        """Return ``survivor`` and the one-hop redirects flattened into it.

        The disjunction probes the primary key and redirect indexes, which suits a
        bounded or per-row lookup; a corpus-wide join keys on :meth:`survivor_ids`.
        """

        return self.redirect_groups((survivor,))

    def redirect_groups(self, survivors: Sequence[_ModelT]) -> Self:
        """Return each survivor and its one-hop redirects in one query."""

        ids = [survivor.pk for survivor in survivors]
        return self.filter(models.Q(pk__in=ids) | models.Q(merged_into_id__in=ids))

    def survivor_ids(self) -> models.QuerySet[Any]:
        """Return each row's :meth:`survivor_id` as ``survivor``.

        A corpus-wide anti-join or semi-join on equality with this key is hashed
        once, where a correlated :meth:`redirect_group` probe runs once per outer row.
        """

        return self.order_by().values(survivor=self.survivor_id())


class _AngeeQuerySetMixin(CanonicalQuerySet[_ModelT]):
    """Shared native lookup and locking, preserving each manager's policy."""

    model: type[_ModelT]

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


class AngeeQuerySet(
    _AngeeQuerySetMixin[_ModelT],
):
    """QuerySet API shared by Angee source and runtime models."""

    def _keyset_cursor_scope(self, scope: tuple[Any, ...]) -> tuple[Any, ...]:
        """Own collection identity; optional capabilities contribute their audience."""
        return scope

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
        Database and model are always included; callers supply any audience identity. The salt versions the
        ordering contract. Cuts authorize nothing: each call reevaluates the
        readable queryset, including rows whose anchor was deleted or moved.

        ``before`` is exclusive; ``through`` is an inclusive fixed lower cut.
        ``after`` discovers the nearest newer page and cannot combine with either.
        Return rows newest-first and distinguish an exhausted fixed window from
        history below that window, even when the window is now empty. Metadata
        describes the authorized page/cut observations; these separate reads do
        not promise one database snapshot or the latest concurrent changes.
        """

        if after_cursor is not None and (before_cursor is not None or through_cursor is not None):
            raise ValueError("after_cursor cannot combine with before_cursor or through_cursor.")
        limit = max(1, min(int(limit), 200))
        signer = keyset_signer(
            [self.db, self.model._meta.label_lower, *self._keyset_cursor_scope(cursor_scope)], salt=cursor_salt,
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
            newer = list(
                self.filter(order.after(order.position(around))).order_by(*order.ordering).reverse()[: limit // 2]
            )
            upper = order.position(newer[-1] if newer else around)
            window = window.exclude(order.after(upper))
        ascending = after_cursor is not None
        window = window.order_by(*order.ordering)
        if ascending:
            window = window.reverse()
        selected = list(window[: limit + 1])
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
        if ascending or (
            has_more and not below and (
                selected[limit].pk == rows[-1].pk
                or order.position(selected[limit]) == order.position(rows[-1])
            )
        ):
            # Join multiplicity can repeat an identity with SQL-equal collated
            # annotations, or repeat the complete tuple, without proving older.
            has_older = self.filter(order.before(order.position(rows[-1]))).exists()
        else:
            # A descending window covers the older suffix down to its inclusive
            # lower cut. Extra rows or the authorized below-cut probe cover it.
            has_older = has_more or below
        # Re-fetch the bounded identities through the original queryset so callers
        # retain a composable queryset instead of inheriting this method's probe list.
        return KeysetPage(
            rows=self.filter(pk__in=[row.pk for row in rows]).order_by(*order.ordering),
            count=count,
            older_cursor=order.sign(rows[-1], signer),
            newer_cursor=order.sign(rows[0], signer),
            has_older=has_older,
            has_newer=self.filter(order.after(order.position(rows[0]))).exists(),
            has_more_in_window=has_more,
            has_older_than_through=below,
            has_newer_than_before=self.filter(order.after(anchor)).exists() if anchor is not None else False,
        )


class MergeableUnscopedQuerySet(MergeableQuerySet[_ModelT], AngeeQuerySet[_ModelT]):
    """Explicit operational identity API for a model that owns merge redirects."""
