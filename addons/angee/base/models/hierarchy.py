"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Sequence
from copy import copy
from itertools import batched
from typing import TYPE_CHECKING, Any, ClassVar, Self, TypeVar, cast

from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import NotSupportedError, connections, models, router, transaction
from django.db.models import F, Value
from django.db.models.functions import Cast, Replace

from angee.base.fields.ordering import FractionalRankField
from angee.base.indexes import PatternOpsIndex
from angee.base.querysets import SystemQuerysetMixin, _AngeeQuerySetMixin, query_batch_size

_ModelT = TypeVar("_ModelT", bound=models.Model)
_HierarchyModelT = TypeVar("_HierarchyModelT", bound="HierarchyMixin")


class _AncestorPrefetcher:
    """Adapt the path relation to Django's native instance-prefetch protocol."""

    def get_prefetch_querysets(
        self,
        instances: list[HierarchyMixin],
        querysets: list[HierarchyQuerySet[HierarchyMixin]] | None = None,
    ) -> tuple[
        list[HierarchyMixin], Callable[[HierarchyMixin], object], Callable[[HierarchyMixin], object], bool, str, bool,
    ]:
        """Fetch once, then associate each ancestor with every requesting node.

        A shared ancestor needs one model copy per source key, like rows repeated
        by a native many-to-many join. Django assigns the resulting lists to the
        caller's ``to_attr``; no request cache or model metadata is changed.
        """

        if querysets is None or len(querysets) != 1:
            raise ValueError("Ancestor prefetch requires one explicit queryset and to_attr.")
        rows = querysets[0].ancestors_of(instances).annotate(_hierarchy_ancestor_path=F("path"))
        ancestors = {getattr(row, "_hierarchy_ancestor_path"): row for row in rows}
        related: list[HierarchyMixin] = []
        sources: dict[int, object] = {}
        for instance in instances:
            for path in instance.ancestor_paths():
                if (ancestor := ancestors.get(path)) is not None:
                    # An in-process copy must retain queryset-bound state.
                    # copy(model) uses pickling hooks, which may strip authority.
                    bound = object.__new__(type(ancestor))
                    bound.__dict__ = ancestor.__dict__.copy()
                    bound._state = copy(ancestor._state)
                    bound._state.fields_cache = ancestor._state.fields_cache.copy()
                    related.append(bound)
                    sources[id(bound)] = instance.pk
        return related, lambda row: sources[id(row)], lambda row: row.pk, False, "_hierarchy_ancestors", False


class HierarchyQuerySet(models.QuerySet[_HierarchyModelT]):
    """Subtree read scopes for models composing :class:`HierarchyMixin`.

    Compose alongside the model's base queryset (e.g.
    ``class LocationQuerySet(HierarchyQuerySet[Location], AngeeQuerySet[Location])``)
    and put it FIRST: the owner's path rewrite skips only this class's write
    guard through ``owner_update``, so any write-guarding
    queryset composed before it would be skipped too. The subtree vocabulary
    — :meth:`subtree_of` / :meth:`ancestors_of` — reads
    as chainable predicates over the maintained ``path`` column, served by the
    prefix index rather than a client-side ``parent`` walk.
    """

    def subtree_of(self, node: HierarchyMixin) -> Self:
        """Return ``node`` and every descendant (INCLUSIVE), by path prefix.

        A node's own ``path`` is the prefix of every descendant's path and of
        itself, so a single ``LIKE 'path%'`` covers the whole subtree. An
        unmaterialized ``node`` (empty ``path``) matches nothing rather than the
        whole table.
        """

        if not node.path:
            return cast(Self, self.none())
        return cast(Self, self.filter(path__startswith=node.path))

    def ancestors_of(self, node: HierarchyMixin | Iterable[HierarchyMixin]) -> Self:
        """Return proper ancestors of one node or a batch, in one query."""

        nodes = (node,) if isinstance(node, HierarchyMixin) else node
        return cast(Self, self.filter(path__in={path for item in nodes for path in item.ancestor_paths()}))

    def update(self, **kwargs: Any) -> int:
        """Keep parent moves and derived paths on the saved-row owner."""

        owned = {"path", "parent", "parent_id"}
        for name in self.model.hierarchy_scope_fields:
            field = self.model._hierarchy_column(name)
            owned.update((field.name, field.attname))
        if owned & kwargs.keys():
            raise ValidationError("The hierarchy parent, scope and path belong to the saved-row owner.")
        return super().update(**kwargs)

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[_HierarchyModelT]:
        """Require saves to derive paths from persisted row and parent identities."""

        raise ValidationError(
            "Create hierarchy rows through save() or create_forest(); paths belong to the saved-row owner."
        )

    def create_forest(self, rows: Sequence[_HierarchyModelT]) -> list[_HierarchyModelT]:
        """Insert unsaved rows and derive every parent link and path; return them saved.

        A row's ``parent`` is a saved row (with its path loaded) or another row of
        ``rows``, in the same scope. Every row is one INSERT, then one UPDATE sets
        each parent and path, whatever the tree's depth; each row records one
        creation version, path included. No row is saved one by one, so ``save()``
        hooks do not run; the caller sends what lifecycle signals it owes.
        """

        model = self.model
        self._for_write = True
        using = self.db
        field = model._hierarchy_parent_field()
        rows = list(rows)
        if any(row.pk is not None for row in rows):
            raise ValidationError("A forest insert takes unsaved rows.")
        if not connections[using].features.can_return_rows_from_bulk_insert:
            raise NotSupportedError("A forest insert requires bulk-insert primary keys on this database.")
        members = {id(row) for row in rows}
        parents: dict[int, HierarchyMixin | None] = {}
        for row in rows:
            parent = field.get_cached_value(row, None)
            if parent is not None and not isinstance(parent, HierarchyMixin):
                raise ImproperlyConfigured("Hierarchy parents must compose HierarchyMixin.")
            parents[id(row)] = parent
        order: list[_HierarchyModelT] = []
        placed: set[int] = set()
        pending = rows
        while pending:
            ready = [row for row in pending if (parent := parents[id(row)]) is None
                     or id(parent) in placed or (id(parent) not in members and parent.pk is not None)]
            if not ready:
                raise ValidationError("Forest parents must form a tree of these rows and saved rows.")
            order.extend(ready)
            placed.update(id(row) for row in ready)
            pending = [row for row in pending if id(row) not in placed]
        scope = [model._hierarchy_column(name).attname for name in model.hierarchy_scope_fields]
        for row in rows:
            parent = parents[id(row)]
            if parent is not None and any(getattr(row, name) != getattr(parent, name) for name in scope):
                raise ValidationError({"parent": "Parent must belong to the same scope."})
        with transaction.atomic(using=using):
            rank_fields = tuple(
                column for column in model._meta.concrete_fields if isinstance(column, FractionalRankField)
            )
            for column in rank_fields:
                column.allocate_missing_ranks(rows, using=using)
            final_ranks = {
                column: tuple(getattr(row, column.attname) for row in rows)
                for column in rank_fields if field in column._unique_context_fields(model)
            }
            # Django refuses an unsaved parent. Stage parent-context ranks too:
            # every insert temporarily occupies the persisted NULL-parent scope.
            for row in rows:
                row.parent = None
            for column, values in final_ranks.items():
                temporary = column.get_temporary_ranks(rows, values, using=using)
                for row, rank in zip(rows, temporary, strict=True):
                    setattr(row, column.attname, rank)
            with model.initialize_rows(rows, using=using):
                # Below the hierarchy guard and deferred observers: the creation version
                # below records each row with its parent and path.
                super(_AngeeQuerySetMixin, cast(_AngeeQuerySetMixin, self)).bulk_create(rows)
                for row in order:
                    parent = parents[id(row)]
                    row.parent = parent
                    row.path = row._hierarchy_path(parent)
                for column, values in final_ranks.items():
                    for row, rank in zip(rows, values, strict=True):
                        setattr(row, column.attname, rank)
                # One filter key plus one key/value CASE pair per initialized column.
                width = 5 + 2 * len(final_ranks)
                for chunk in batched(rows, query_batch_size(self, width=width)):
                    # The caller's queryset carries its authority (a sudo) to this write.
                    batch = cast(_AngeeQuerySetMixin, self.filter(pk__in=[row.pk for row in chunk]))
                    super(_AngeeQuerySetMixin, batch).update(
                        parent_id=Cast(
                            models.Case(
                                *(models.When(pk=row.pk, then=models.Value(row.parent_id)) for row in chunk),
                                output_field=field.target_field,
                            ),
                            field.target_field,
                        ),
                        path=models.Case(
                            *(models.When(pk=row.pk, then=models.Value(row.path)) for row in chunk),
                            output_field=model._hierarchy_column("path"),
                        ),
                        **{
                            column.name: models.Case(
                                *(models.When(pk=row.pk, then=models.Value(getattr(row, column.attname)))
                                  for row in chunk),
                                output_field=column,
                            )
                            for column in final_ranks
                        },
                    )
        return rows

    def owner_update(self, **kwargs: Any) -> int:
        """Protected API: apply an owner-validated write through downstream guards.

        HierarchyMixin owns the selected rows and derived path value; bypass
        only this class's external-write guard after validating the move.
        """

        return super().update(**kwargs)


class HierarchyMixin(SystemQuerysetMixin):
    """Materialized-path tree membership for a self-parented model.

    Adds a ``parent`` self-FK and a maintained ``path`` column of zero-padded,
    delimiter-terminated primary-key segments (``/0000000012/0000000045/``), so
    subtree membership is a prefix test the database serves from an index rather
    than a fact each addon re-derives by walking ``parent`` in the client. The
    terminal delimiter is the correctness guarantee — a ``path`` is a string
    prefix of another exactly when the first node is an ancestor-or-self of the
    second — and the zero-padding (see :attr:`path_segment_width`) keeps segments
    lexically ordered.

    Compose it on a self-parented model, pair it with :class:`HierarchyQuerySet`
    for the ``subtree_of`` / ``ancestors_of`` read scopes, and **inherit its
    ``Meta``** so the concrete table carries the prefix index::

        class Location(HierarchyMixin, AngeeDataModel):
            ...

            class Meta(HierarchyMixin.Meta):
                abstract = False
                app_label = "inventory"
                rebac_resource_type = "inventory/location"

    (Django propagates ``Meta.indexes`` only through ``Meta``-class inheritance,
    not across sibling abstract bases, so a consumer that needs other indexes
    lists ``*HierarchyMixin.Meta.indexes`` alongside its own.)

    :meth:`save` maintains the path: derived from the parent on create, and on
    reparent it rejects a cycle (a new parent inside the node's own subtree) and a
    parent in a different scope (any field the model names in
    :attr:`hierarchy_scope_fields`), then rewrites the whole subtree's paths in one
    bulk ``UPDATE``. It owns no REBAC of its own; path maintenance runs unscoped so
    a reparent reaches descendants the acting user cannot read.
    """

    if TYPE_CHECKING:
        # The abstract self-FK has no PK type until its concrete model exists.
        parent_id: object

    _hierarchy_ancestors = _AncestorPrefetcher()

    @classmethod
    def ancestors_prefetch(cls, queryset: HierarchyQuerySet[Self], *, to_attr: str) -> models.Prefetch:
        """Batch proper ancestors through the supplied scope into a native list.

        Select ``path`` on the requesting nodes. The supplied queryset owns
        authorization, filtering and nested prefetches; related paths remain
        available for matching even when its model fields are deferred.
        """

        return models.Prefetch("_hierarchy_ancestors", queryset=queryset, to_attr=to_attr)

    hierarchy_scope_fields: ClassVar[tuple[str, ...]] = ()
    """Field names a child must share with its parent (e.g. ``("scope",)``).

    A reparent (and a create under a parent) rejects a parent that differs on any
    of these fields, so a subtree never straddles a scope boundary. It is a
    declared contract — generic and iam-free — owned by the consuming model rather
    than probed by column name: a scoped tree declares
    ``hierarchy_scope_fields = ("scope",)``, an unscoped tree leaves it empty. An
    FK is compared by its stored id (the field's ``attname``); a parent must agree
    on every listed field.
    """

    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    """The parent node, or ``NULL`` for a root; PROTECT keeps a subtree whole."""

    path = models.CharField(max_length=255, default="", editable=False)
    """Maintained root-to-self path of padded pk segments; server-owned.

    ``editable=False`` keeps it out of forms and the auto-CRUD write surface —
    the mixin is its only writer. The column width bounds tree depth: at
    :attr:`path_segment_width` = 12 each segment costs 13 characters, so
    ``max_length=255`` holds ~19 levels — deeper than any ERP location/category
    tree, but a consumer expecting deeper nesting must widen the column.
    Maintenance writes go through the shared queryset capture path. History
    includes these writes; replay derives paths again from the restored parents.
    """

    path_segment_width: ClassVar[int] = 12
    """Zero-pad width for one pk segment.

    Governs lexical ordering only; correctness rests on the terminal delimiter,
    so a primary key wider than this stays correct (it just sorts by raw digits
    within its level). Twelve digits order rows up to a trillion per table.
    """

    PATH_DELIMITER: ClassVar[str] = "/"
    """Segment delimiter; safe because a padded pk segment is digits only."""

    class Meta:
        """Abstract options carrying the prefix-serving ``path`` index."""

        abstract = True
        indexes = (PatternOpsIndex(fields=["path"], opclasses=["varchar_pattern_ops"]),)

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Keep path rewrites behind every manager's write guards."""

        errors = super().check(**kwargs)
        if cls._meta.abstract:
            return errors
        model = cls
        for manager in model._meta.managers:
            queryset_type = type(manager.get_queryset())
            if not issubclass(queryset_type, HierarchyQuerySet):
                continue
            preceding = queryset_type.__mro__[: queryset_type.__mro__.index(HierarchyQuerySet)]
            if any(not issubclass(owner, HierarchyQuerySet) or "update" in owner.__dict__ for owner in preceding):
                errors.append(
                    checks.Error(
                        f"{model._meta.label}.{manager.name} must compose HierarchyQuerySet "
                        "before every other queryset guard.",
                        hint="Put HierarchyQuerySet first and define update guards in a following queryset base.",
                        obj=model,
                        id="angee.E021",
                    )
                )
        return errors

    @classmethod
    def _hierarchy_column(cls, name: str) -> models.Field:
        field = cls._meta.get_field(name)
        if not isinstance(field, models.Field):
            raise ImproperlyConfigured(f"Hierarchy column {cls._meta.label}.{name} must be a forward field.")
        return field

    @classmethod
    def _hierarchy_parent_field(cls) -> models.ForeignKey:
        field = cls._hierarchy_column("parent")
        if not isinstance(field, models.ForeignKey):
            raise ImproperlyConfigured(f"{cls._meta.label}.parent must be a ForeignKey.")
        return field

    @classmethod
    def from_db(cls, db: Any, field_names: Collection[str], values: Collection[Any]) -> Self:
        """Record the loaded ``parent`` so :meth:`save` can detect a reparent.

        Only when ``parent`` was actually loaded: seeding the baseline off a
        deferred field (``.only(...)``/``.defer(...)`` excluding ``parent``)
        would trigger one extra query per row. :meth:`_hierarchy_needs_repath`
        falls back to the live ``parent_id`` when the baseline is absent, so a
        deferred load simply stays lazy.
        """

        instance = super().from_db(db, field_names, values)
        if "parent_id" in field_names:
            instance._hierarchy_saved_parent_id = instance.parent_id
        return instance

    def refresh_from_db(
        self,
        using: str | None = None,
        fields: Iterable[str] | None = None,
        from_queryset: models.QuerySet[Any] | None = None,
    ) -> None:
        """Reload the row, re-syncing the reparent baseline to the loaded ``parent``.

        Without the re-sync a refresh after an external ``parent`` change leaves
        the baseline stale, and the next unrelated ``save()`` would be
        misclassified as a reparent.
        """

        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)
        if fields is None or "parent" in fields or "parent_id" in fields:
            self._hierarchy_saved_parent_id = self.parent_id

    def ancestor_paths(self) -> list[str]:
        """Return the paths of this node's proper ancestors, root-first.

        Decomposes this node's own ``path`` into the cumulative prefixes at each
        delimiter boundary, dropping the last (the node itself) — so a root node
        yields an empty list.
        """

        delimiter = self.PATH_DELIMITER
        segments = [segment for segment in self.path.split(delimiter) if segment]
        prefix = delimiter
        paths: list[str] = []
        for segment in segments[:-1]:
            prefix += segment + delimiter
            paths.append(prefix)
        return paths

    def is_within(self, other: HierarchyMixin) -> bool:
        """Return whether this node is ``other`` or a descendant of ``other``.

        The test is intentionally inclusive and query-free: the maintained,
        delimiter-terminated ``path`` column is a prefix of exactly its own
        subtree. Empty/unmaterialized paths match nothing so they cannot become
        an accidental whole-tree prefix.
        """

        return bool(self.path and other.path and self.path.startswith(other.path))

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the row, maintaining ``path`` on create and reparent."""

        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        kwargs["using"] = using
        with transaction.atomic(using=using):
            if self._state.adding:
                self._save_created(*args, **kwargs)
            else:
                self._validate_hierarchy_scope(kwargs.get("update_fields"), using=using)
                if self._hierarchy_needs_repath(using=using):
                    self._save_reparented(*args, **kwargs)
                else:
                    super().save(*args, **kwargs)
        if "parent_id" in self.__dict__:
            self._hierarchy_saved_parent_id = self.parent_id

    def _validate_hierarchy_scope(self, update_fields: Iterable[str] | None, *, using: str | None = None) -> None:
        """A scope edit must keep the parent and existing children in one scope.

        Moving an entire tree between owners requires its own admitted verb;
        changing one row must never silently move or strand its descendants.
        """

        fields = [self._hierarchy_column(name) for name in self.hierarchy_scope_fields]
        touched = [field for field in fields if field.attname in self.__dict__ and (
            update_fields is None or field.name in update_fields or field.attname in update_fields
        )]
        if not touched:
            return
        rows = type(self).system_queryset(lock=("self",)).using(using or self._state.db)
        stored = rows.filter(pk=self.pk).values(*(field.attname for field in touched)).get()
        changed = {field.attname: getattr(self, field.attname) for field in touched
                   if stored[field.attname] != getattr(self, field.attname)}
        if not changed:
            return
        self._reject_cross_scope_parent(self._hierarchy_parent(using=using), using=using)
        if rows.filter(parent_id=self.pk).exclude(**changed).exists():
            raise ValidationError("Move or detach this node's children before changing its scope.")

    def _save_created(self, *args: Any, **kwargs: Any) -> None:
        """Insert the row, then derive its ``path`` from the parent's committed path."""

        using = kwargs["using"]
        with transaction.atomic(using=using), type(self).initialize_rows([self], using=using) as deferred:
            super().save(*args, **kwargs)
            parent = self._hierarchy_parent(using=using)
            if parent is not None:
                # Re-read the parent's committed path under lock before deriving the
                # child prefix: a create racing a reparent of that parent would
                # otherwise bake in a stale prefix that the reparent's cascade never
                # reaches (the new row is not yet under the old prefix it rewrites).
                fresh = self._locked_paths([parent.pk], using=using)
                if parent.pk in fresh:
                    parent.path = fresh[parent.pk]
            self._reject_cross_scope_parent(parent, using=using)
            new_path = self._hierarchy_path(parent)
            if new_path != self.path:
                rows = type(self).system_queryset().using(using).filter(pk=self.pk)
                if deferred:
                    # Below the hierarchy guard and deferred observers: the
                    # creation version records the path with the row.
                    super(_AngeeQuerySetMixin, cast(_AngeeQuerySetMixin, rows)).update(path=new_path)
                else:
                    self._write_hierarchy_path(rows, new_path)
                self.path = new_path

    def _save_reparented(self, *args: Any, **kwargs: Any) -> None:
        """Validate the move under lock, then rewrite the subtree in one UPDATE."""

        # A reparent is defined by the moved ``parent``, so persist it (and the
        # derived ``path``) even under a partial ``update_fields`` that named
        # neither — otherwise the FK and the cascaded paths would diverge.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"parent", "path"}
        using = kwargs["using"]
        with transaction.atomic(using=using):
            old_path = self._lock_moved_paths(using=using)
            subtree = type(self).system_queryset(lock=("self",)).using(using).filter(path__startswith=old_path)
            if old_path:
                # Evaluate SELECT FOR UPDATE before moving any descendant's path.
                list(subtree.order_by("pk").values_list("pk", flat=True))
            parent = self._hierarchy_parent(using=using)
            self._reject_cycle(parent)
            self._reject_cross_scope_parent(parent, using=using)
            new_path = self._hierarchy_path(parent)
            self.path = new_path
            super().save(*args, **kwargs)
            if old_path:
                # One bulk UPDATE rewrites the old prefix on every row still
                # under it (descendants only — the save above already repathed
                # self) — never a per-row walk. The prefix is unique to this
                # root-to-self chain, so a whole-string REPLACE only touches the
                # head. An empty old path (an unmaterialized row) skips the
                # cascade: it has nothing under it, and ``LIKE '%'`` would
                # rewrite the whole table.
                replacement = Replace(F("path"), Value(old_path), Value(new_path))
                self._write_hierarchy_path(
                    subtree,
                    replacement,
                )

    def _write_hierarchy_path(
        self,
        queryset: models.QuerySet[Any],
        path_value: Any,
    ) -> int:
        """Rewrite the caller's system-scoped queryset inside its atomic block.

        Skip only the hierarchy's external-write guard; the remaining queryset
        chain, including REBAC, still owns authorization and persistence.
        """

        if isinstance(queryset, HierarchyQuerySet):
            return queryset.owner_update(path=path_value)
        return queryset.update(path=path_value)

    def _lock_moved_paths(self, *, using: str | None = None) -> str:
        """Row-lock this node and its new parent, refreshing committed paths.

        Two overlapping reparents interleaving on stale in-memory paths is the
        classic materialized-path hazard, so the moved node and its new parent are
        read under the queryset's row-lock owner and their committed paths replace
        the in-memory ones before validation and the cascade prefix derive from
        them. Returns this row's committed (old) path.
        """

        pks = [self.pk] if self.parent_id is None else [self.pk, self.parent_id]
        fresh = self._locked_paths(pks, using=using)
        self.path = fresh.get(self.pk, self.path)
        if self.parent_id is not None and self.parent_id in fresh:
            parent = self._hierarchy_parent(using=using)
            if parent is not None:
                parent.path = fresh[self.parent_id]
        return self.path

    def _locked_paths(self, pks: list[Any], *, using: str | None = None) -> dict[Any, str]:
        """Return committed paths, serializing overlapping moves when supported."""

        reader = type(self).system_queryset(lock=("self",)).using(using or self._state.db)
        return dict(reader.filter(pk__in=pks).values_list("pk", "path"))

    def _hierarchy_needs_repath(self, *, using: str | None = None) -> bool:
        """Return whether an existing row's ``parent`` moved (or its path is unset)."""

        # An unrelated deferred save must not load tree columns into its UPDATE.
        if "path" in self.__dict__ and not self.path:
            return True
        if "parent_id" not in self.__dict__:
            return False
        if hasattr(self, "_hierarchy_saved_parent_id") and (using is None or using == self._state.db):
            return self._hierarchy_saved_parent_id != self.parent_id
        # A deferred load (``.only(...)`` excluding ``parent``) carries no baseline,
        # so a reparent would be invisible if we compared ``parent_id`` to itself.
        # Fetch the committed ``parent_id`` from the row to compare against the
        # in-memory FK the caller may have moved.
        return self._hierarchy_committed_parent_id(using=using) != self.parent_id

    def _hierarchy_committed_parent_id(self, *, using: str | None = None) -> Any:
        """Return this row's committed ``parent_id`` from the database."""

        return (
            type(self).system_queryset()
            .using(using or self._state.db)
            .filter(pk=self.pk)
            .values_list("parent_id", flat=True)
            .first()
        )

    def _hierarchy_parent(self, *, using: str | None = None) -> HierarchyMixin | None:
        """Return the parent on this database, preserving Django's relation cache."""

        if self.parent_id is None:
            return None
        field = self._hierarchy_parent_field()
        alias = using or self._state.db
        if field.is_cached(self):
            parent = field.get_cached_value(self)
            if parent is not None and parent._state.db == alias:
                return cast("HierarchyMixin", parent)
        parent_model = field.related_model
        if parent_model is None or not issubclass(parent_model, HierarchyMixin):
            raise ImproperlyConfigured("Hierarchy parents must compose HierarchyMixin.")
        parent = parent_model.system_queryset().using(alias).get(pk=self.parent_id)
        field.set_cached_value(self, parent)
        return cast("HierarchyMixin", parent)

    def _hierarchy_path(self, parent: HierarchyMixin | None) -> str:
        """Return this node's derived path under ``parent`` (a root when ``None``)."""

        prefix = parent.path if parent is not None else self.PATH_DELIMITER
        return prefix + f"{self.pk:0{self.path_segment_width}d}{self.PATH_DELIMITER}"

    def _reject_cycle(self, parent: HierarchyMixin | None) -> None:
        """Reject a reparent whose new parent is this node or one of its descendants."""

        if parent is None or not self.path:
            return
        if parent.path.startswith(self.path):
            raise ValidationError({"parent": "A node cannot be moved under itself or a descendant."})

    def _reject_cross_scope_parent(self, parent: HierarchyMixin | None, *, using: str | None = None) -> None:
        """Reject a parent that differs on any declared :attr:`hierarchy_scope_fields`."""

        if parent is None:
            return
        if not self.hierarchy_scope_fields:
            return
        fields = [self._hierarchy_column(name) for name in self.hierarchy_scope_fields]
        # The parent is a row of the hierarchy's own model; a multi-table child
        # of it may not have a row in this model's table.
        committed = type(parent).system_queryset().using(using or self._state.db).filter(pk=parent.pk).values(
            *(field.attname for field in fields),
        ).get()
        for field in fields:
            if getattr(self, field.attname) != committed[field.attname]:
                raise ValidationError({"parent": f"Parent must belong to the same {field.name}."})
