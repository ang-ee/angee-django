"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar, Self, TypeVar, cast

import reversion
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Value
from django.db.models.functions import Replace
from rebac import current_actor

from angee.base.actors import actor_user_id
from angee.base.emission import ModelClassAttribute, ModelDecorator
from angee.base.fields import SqidField
from angee.base.indexes import PatternOpsIndex

_ArchiveModelT = TypeVar("_ArchiveModelT", bound=models.Model)
_HierarchyModelT = TypeVar("_HierarchyModelT", bound="HierarchyMixin")

ARCHIVE_FLAG_FIELD = "is_archived"
"""The one archive-flag column name — the single archive vocabulary word.

Every model that composes :class:`ArchiveMixin` carries this exact column, and
the resource-metadata field classifier recognises the archive flag by this name
(``angee.graphql.data.field_classification.is_archive_field``). Keeping the name
identical everywhere is the contract that lets pickers default-filter archived
rows and lists expose an archived facet without per-model wiring.
"""


class TimestampMixin(models.Model):
    """Add conventional creation and update timestamps to a model."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    """The timestamp when the row was first created."""

    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    """The timestamp when the row was most recently saved."""

    class Meta:
        """Django model options for timestamp-only abstract inheritance."""

        abstract = True


def update_fields_with_auto_now(instance: models.Model, update_fields: Any) -> set[str]:
    """Return non-empty ``update_fields`` plus this model's ``auto_now`` fields."""

    fields = set(update_fields)
    if not fields:
        return fields
    return fields | {field.name for field in instance._meta.fields if getattr(field, "auto_now", False)}


class SqidMixin(models.Model):
    """Add an opaque public identifier backed by the model primary key.

    A model sets only the varying fact — its prefix — as ``sqid_prefix``
    (e.g. ``sqid_prefix = "nte_"``); the shared ``sqid`` column reads it (see
    ``SqidField.contribute_to_class``), so no model re-declares the field.
    """

    sqid_prefix: ClassVar[str] = ""
    """Public-id prefix for ``sqid`` (e.g. ``"nte_"``); empty means no prefix."""

    sqid = SqidField(real_field_name="id", min_length=8)
    """Opaque public identifier encoded from the integer primary key."""

    class Meta:
        """Django model options for sqid-only abstract inheritance."""

        abstract = True

    def public_id_value(self) -> Any:
        """Return the raw public identifier value for this instance."""

        return self.sqid

    @classmethod
    def public_id_lookup(cls, value: str) -> dict[str, Any]:
        """Return the Django lookup for this model's public identifier."""

        return {"sqid": value}

    @classmethod
    def public_id_from_pk(cls, value: Any) -> str:
        """Return the public id encoded from this model's primary-key value."""

        # SqidMixin declares ``sqid = SqidField(...)`` unconditionally, so the column
        # is always a SqidField on any subclass.
        field = cast(SqidField, cls._meta.get_field("sqid"))
        return field.public_id_from_value(value)


class AuditMixin(models.Model):
    """Add conventional user-owned audit foreign keys to a model."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    """The user that created the row, when known."""

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    """The user that most recently updated the row, when known."""

    class Meta:
        """Django model options for audit-only abstract inheritance."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the row after stamping user audit fields."""

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if not update_fields:
                super().save(*args, **kwargs)
                return

        actor_getter = getattr(self, "actor", None)
        actor = actor_getter() if callable(actor_getter) else None
        if actor is None:
            actor = current_actor()
        user_id = actor_user_id(actor)
        touched: set[str] = set()
        if user_id is not None:
            if self._state.adding:
                if getattr(self, "created_by_id", None) is None:
                    self.created_by_id = user_id
                    touched.add("created_by")
                if getattr(self, "updated_by_id", None) is None:
                    self.updated_by_id = user_id
                    touched.add("updated_by")
            else:
                self.updated_by_id = user_id
                touched.add("updated_by")

        if update_fields is not None:
            kwargs["update_fields"] = update_fields_with_auto_now(self, update_fields | touched)
        super().save(*args, **kwargs)


class ArchiveMixin(models.Model):
    """Add a soft-archive flag to a model.

    One vocabulary, everywhere: the column is ``is_archived`` (see
    :data:`ARCHIVE_FLAG_FIELD`) and the read scopes are ``.archived()`` /
    ``.unarchived()`` (compose :class:`ArchiveQuerySet` into the model's
    queryset). Archived rows are soft-hidden from default surfaces but kept for
    an explicit archived facet — a metadata fact the field classifier carries as
    ``archivable``, not per-page logic. This is archival, distinct from a
    soft-delete/trash flag or an enablement flag, which own different contracts.
    """

    is_archived = models.BooleanField(default=False, db_index=True)
    """Whether the row is archived — soft-hidden from default pickers and lists."""

    class Meta:
        """Django model options for archive-only abstract inheritance."""

        abstract = True


class ArchiveQuerySet(models.QuerySet[_ArchiveModelT]):
    """Composable read scopes for the :class:`ArchiveMixin` archive flag.

    Mix into a model's queryset alongside its base queryset (e.g.
    ``class DriveQuerySet(ArchiveQuerySet[Drive], AngeeQuerySet[Drive])``) so the
    archive vocabulary — ``.archived()`` / ``.unarchived()`` — reads as chainable
    predicates over the one ``is_archived`` column rather than repeated inline
    filters.
    """

    def archived(self) -> Self:
        """Return rows flagged archived."""

        return cast(Self, self.filter(**{ARCHIVE_FLAG_FIELD: True}))

    def unarchived(self) -> Self:
        """Return rows not flagged archived — the default picker/list scope."""

        return cast(Self, self.filter(**{ARCHIVE_FLAG_FIELD: False}))


class HistoryMixin(models.Model):
    """Mark a model as tracked by django-simple-history."""

    @classmethod
    def angee_model_attributes(
        cls,
        *,
        app_label: str,
        model_class: type[models.Model],
        extension_bases: tuple[type[models.Model], ...],
    ) -> tuple[ModelClassAttribute, ...]:
        """Return the simple-history class attribute for a concrete model."""

        kwargs: list[tuple[str, Any]] = [("app", app_label)]
        excluded = cls.angee_history_excluded_fields((*extension_bases, model_class))
        if excluded:
            kwargs.append(("excluded_fields", excluded))
        return (
            ModelClassAttribute(
                name="history",
                import_path="simple_history.models.HistoricalRecords",
                kwargs=tuple(kwargs),
            ),
        )

    @staticmethod
    def angee_history_excluded_fields(
        model_bases: tuple[type[models.Model], ...],
    ) -> list[str]:
        """Return source fields simple-history cannot mirror."""

        excluded: set[str] = set()
        for model_base in model_bases:
            meta = model_base._meta
            own_fields = (
                *meta.local_fields,
                *meta.private_fields,
                *meta.local_many_to_many,
            )
            excluded.update(
                field.name
                for field in own_fields
                if getattr(field, "concrete", True) is False
                and not field.is_relation
                and not getattr(field, "auto_created", False)
            )
        return sorted(excluded)

    class Meta:
        """Django model options for history-only abstract inheritance."""

        abstract = True


class RevisionMixin(models.Model):
    """Mark a model as tracked by django-reversion snapshots."""

    angee_model_decorators: ClassVar[tuple[ModelDecorator, ...]] = (
        ModelDecorator(
            import_path="reversion.register",
            kwargs_from_model=(("fields", "revisioned_fields"),),
            enabled_by_model_attr="revisioned_fields",
        ),
    )
    """Composer decorators applied to emitted concrete revision models."""

    revisioned_fields: ClassVar[tuple[str, ...]] = ()
    """Model field names registered with django-reversion."""

    class Meta:
        """Django model options for revision-only abstract inheritance."""

        abstract = True

    @property
    def revisions(self) -> Any:
        """Return this row's django-reversion versions newest-first."""

        versions = reversion.models.Version.objects.get_for_object(self)
        return versions.select_related("revision")

    def revert_to(self, version: Any) -> None:
        """Restore declared revisioned fields from ``version`` and save.

        Saves with ``update_fields`` so unrelated in-memory columns are not
        flushed. The method records its own revert revision so integrity does
        not depend on the caller's transport opening a reversion block.
        """

        data = version.field_dict
        reverted: list[str] = []
        for name in self.revisioned_fields:
            if name in data:
                setattr(self, name, data[name])
                reverted.append(name)
        if not reverted:
            return
        with reversion.create_revision():
            self.save(update_fields=update_fields_with_auto_now(self, reverted))
            reversion.set_comment(f"Reverted to revision {version.revision_id}.")


class HierarchyQuerySet(models.QuerySet[_HierarchyModelT]):
    """Subtree read scopes for models composing :class:`HierarchyMixin`.

    Compose alongside the model's base queryset (e.g.
    ``class LocationQuerySet(HierarchyQuerySet[Location], AngeeQuerySet[Location])``)
    so the subtree vocabulary — :meth:`subtree_of` / :meth:`ancestors_of` — reads
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

    def ancestors_of(self, node: HierarchyMixin) -> Self:
        """Return every proper ancestor of ``node`` (EXCLUSIVE of ``node``)."""

        return cast(Self, self.filter(path__in=node.ancestor_paths()))


class HierarchyMixin(models.Model):
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

        class Location(HierarchyMixin, CompanyScopedMixin, AngeeDataModel):
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

    hierarchy_scope_fields: ClassVar[tuple[str, ...]] = ()
    """Field names a child must share with its parent (e.g. ``("company",)``).

    A reparent (and a create under a parent) rejects a parent that differs on any
    of these fields, so a subtree never straddles a scope boundary. It is a
    declared contract — generic and iam-free — owned by the consuming model rather
    than probed by column name: a company-scoped tree declares
    ``hierarchy_scope_fields = ("company",)``, an unscoped tree leaves it empty. An
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
    Maintenance writes go through queryset ``update()`` (the one-UPDATE cascade),
    so ``path`` changes bypass ``post_save`` — a ``HistoryMixin`` consumer's
    historical rows do not track ``path``, a derivable server-owned value.
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
    def from_db(cls, db: Any, field_names: Sequence[str], values: Sequence[Any]) -> Self:
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
        fields: Sequence[str] | None = None,
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

        if self._state.adding:
            self._save_created(*args, **kwargs)
        elif self._hierarchy_needs_repath():
            self._save_reparented(*args, **kwargs)
        else:
            super().save(*args, **kwargs)
        self._hierarchy_saved_parent_id = self.parent_id

    def _save_created(self, *args: Any, **kwargs: Any) -> None:
        """Insert the row, then derive its ``path`` from the parent's committed path."""

        with transaction.atomic():
            super().save(*args, **kwargs)
            parent = self._hierarchy_parent()
            if parent is not None:
                # Re-read the parent's committed path under lock before deriving the
                # child prefix: a create racing a reparent of that parent would
                # otherwise bake in a stale prefix that the reparent's cascade never
                # reaches (the new row is not yet under the old prefix it rewrites).
                fresh = self._locked_paths([parent.pk])
                if parent.pk in fresh:
                    parent.path = fresh[parent.pk]
            self._reject_cross_scope_parent(parent)
            new_path = self._hierarchy_path(parent)
            if new_path != self.path:
                self._hierarchy_writer().filter(pk=self.pk).update(path=new_path)
                self.path = new_path

    def _save_reparented(self, *args: Any, **kwargs: Any) -> None:
        """Validate the move under lock, then rewrite the subtree in one UPDATE."""

        # A reparent is defined by the moved ``parent``, so persist it (and the
        # derived ``path``) even under a partial ``update_fields`` that named
        # neither — otherwise the FK and the cascaded paths would diverge.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"parent", "path"}
        with transaction.atomic():
            old_path = self._lock_moved_paths()
            parent = self._hierarchy_parent()
            self._reject_cycle(parent)
            self._reject_cross_scope_parent(parent)
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
                self._hierarchy_writer().filter(path__startswith=old_path).update(
                    path=Replace(F("path"), Value(old_path), Value(new_path))
                )

    def _lock_moved_paths(self) -> str:
        """Row-lock this node and its new parent, refreshing committed paths.

        Two overlapping reparents interleaving on stale in-memory paths is the
        classic materialized-path hazard, so the moved node and its new parent are
        read under the queryset's row-lock owner and their committed paths replace
        the in-memory ones before validation and the cascade prefix derive from
        them. Returns this row's committed (old) path.
        """

        pks = [self.pk] if self.parent_id is None else [self.pk, self.parent_id]
        fresh = self._locked_paths(pks)
        self.path = fresh.get(self.pk, self.path)
        if self.parent_id is not None and self.parent_id in fresh:
            parent = self._hierarchy_parent()
            if parent is not None:
                parent.path = fresh[self.parent_id]
        return self.path

    def _locked_paths(self, pks: list[Any]) -> dict[Any, str]:
        """Return committed ``path`` values for ``pks``, row-locked where supported.

        Routes the lock through the queryset's ``lock_if_supported`` owner (the
        greppable, backend-gated ``select_for_update`` helper the ``AngeeQuerySet``
        exposes) rather than re-deciding the SQLite floor here: a locking backend
        serializes overlapping moves on the maintained ``path``; SQLite has no row
        locks and reads the committed paths unlocked (the documented floor).
        """

        writer = self._hierarchy_writer()
        locker = getattr(writer, "lock_if_supported", None)
        reader = locker(of=()) if callable(locker) else writer
        return dict(reader.filter(pk__in=pks).values_list("pk", "path"))

    def _hierarchy_needs_repath(self) -> bool:
        """Return whether an existing row's ``parent`` moved (or its path is unset)."""

        if not self.path:
            return True
        if hasattr(self, "_hierarchy_saved_parent_id"):
            return self._hierarchy_saved_parent_id != self.parent_id
        # A deferred load (``.only(...)`` excluding ``parent``) carries no baseline,
        # so a reparent would be invisible if we compared ``parent_id`` to itself.
        # Fetch the committed ``parent_id`` from the row to compare against the
        # in-memory FK the caller may have moved.
        return self._hierarchy_committed_parent_id() != self.parent_id

    def _hierarchy_committed_parent_id(self) -> Any:
        """Return this row's committed ``parent_id`` from the database."""

        return self._hierarchy_writer().filter(pk=self.pk).values_list("parent_id", flat=True).first()

    def _hierarchy_parent(self) -> HierarchyMixin | None:
        """Return the parent instance (cached when assigned), or ``None`` for a root."""

        if self.parent_id is None:
            return None
        return cast("HierarchyMixin", self.parent)

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

    def _reject_cross_scope_parent(self, parent: HierarchyMixin | None) -> None:
        """Reject a parent that differs on any declared :attr:`hierarchy_scope_fields`."""

        if parent is None:
            return
        for name in self.hierarchy_scope_fields:
            attname = self._meta.get_field(name).attname
            if getattr(self, attname) != getattr(parent, attname):
                raise ValidationError({"parent": f"Parent must belong to the same {name}."})

    def _hierarchy_writer(self) -> models.QuerySet[Self]:
        """Return an unscoped queryset for the mixin's own path maintenance.

        Path maintenance is a system fact, not an actor read: a reparent must
        rewrite every descendant even where the acting user's REBAC scope hides
        some, so the write elevates when the manager supports it. This assumes
        ``_base_manager`` is unscoped-or-sudo (the framework default); a
        consumer must not repoint ``Meta.base_manager_name`` at an actor-scoped
        manager, or descendant rewrites would silently REBAC-scope.
        """

        queryset = type(self)._base_manager.all()
        sudo = getattr(queryset, "sudo", None)
        return cast("models.QuerySet[Self]", sudo() if callable(sudo) else queryset)
