"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, ClassVar, Self, TypeVar, cast

import reversion
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Value
from django.db.models.functions import Replace
from rebac import (
    RelationshipTuple,
    SubjectRef,
    current_actor,
    delete_relationships,
    to_object_ref,
    write_relationships,
)
from rebac.managers import RebacQuerySet
from rebac.types import RelationshipFilter
from simple_history.models import HistoricalRecords

from angee.base.actors import actor_user_id
from angee.base.fields import SqidField
from angee.base.indexes import PatternOpsIndex
from angee.base.scoping import system_queryset

_ModelT = TypeVar("_ModelT", bound=models.Model)
_ArchiveModelT = TypeVar("_ArchiveModelT", bound=models.Model)
_HierarchyModelT = TypeVar("_HierarchyModelT", bound="HierarchyMixin")

ARCHIVE_FLAG_FIELD = "is_archived"
"""The one archive-flag column name — the single archive vocabulary word.

Every model that composes :class:`ArchiveMixin` carries this exact column, and
the resource-metadata field classifier recognises the archive flag by this name
(``angee.data.field_classification.is_archive_field``). Keeping the name
identical everywhere is the contract that lets pickers default-filter archived
rows and lists expose an archived facet without per-model wiring.
"""

_EVERY_AUTHENTICATED_USER = SubjectRef.of("auth/user", "*")


def audit_set_null(collector: Any, field: Any, sub_objs: Iterable[models.Model], using: str) -> None:
    """Null audit attribution even when the referencing rows are append-only.

    Django's unevaluated SET_NULL path calls QuerySet.update(), which append-only
    querysets reject. Materializing here selects the collector's native
    UpdateQuery.update_batch path without opening a general update escape hatch.
    This loads all matching audit rows into memory for each FK being nullified;
    deleting a heavily referenced actor can therefore require substantial memory.
    AuditMixin uses one policy so its fields also work on append-only consumers.
    """

    del using
    collector.add_field_update(field, None, list(sub_objs))


def _shared_reader_policy_field_spellings(model: type[models.Model]) -> frozenset[str]:
    """Return every model field spelling that participates in reader policy."""

    names = {
        name
        for owner in model.__mro__
        for declaration in (owner.__dict__.get("shared_reader_policy_fields", ()),)
        for name in declaration
    }
    return frozenset(
        spelling
        for name in names
        for field in (model._meta.get_field(name),)
        for spelling in (field.name, field.attname)
    )


class ConditionalSharedReaderQuerySet(models.QuerySet[_ArchiveModelT]):
    """Protect fields that decide whether one row receives a wildcard reader."""

    def update(self, **kwargs: Any) -> int:
        """Keep eligibility changes on the owner that reconciles wildcard readers."""

        if kwargs.keys() & _shared_reader_policy_field_spellings(self.model):
            raise ValidationError("Change shared-reader eligibility through its native owner.")
        return super().update(**kwargs)

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[_ArchiveModelT]:
        """Require instance creation so wildcard readers are reconciled."""

        raise ValidationError("Create conditional shared-reader rows through their native owner.")


class ConditionalSharedReaderMixin(models.Model):
    """Keep one per-record wildcard reader aligned with canonical persisted facts.

    Consumers declare the stored fields that decide eligibility and override
    :attr:`shared_reader_eligible`; the generic default is private. The
    reconciler reads a fresh canonical row after persistence,
    so deferred or dirty values excluded by ``update_fields`` never drive access.
    It changes only its configured wildcard tuple; manual and source-scope grants
    remain owned by their distinct relations. Shared-reader persistence and tuple
    reconciliation remain in the same transaction.
    """

    shared_reader_relation: ClassVar[str | None] = "shared"
    shared_reader_policy_fields: ClassVar[tuple[str, ...]] = ()

    class Meta:
        abstract = True

    @property
    def shared_reader_eligible(self) -> bool:
        """Deny wildcard visibility unless the native model opts in explicitly."""

        return False

    def proposed_relationships(self, *, using: str | None = None) -> Mapping[str, Iterable[SubjectRef | models.Model]]:
        """Propose only the shared-reader tuple that save will reconcile atomically."""

        # ``using`` is django-zed-rebac's own override signature; pass it through.
        relationships = dict(super().proposed_relationships(using=using))
        if self.shared_reader_relation is not None:
            relationships[self.shared_reader_relation] = (
                (_EVERY_AUTHENTICATED_USER,) if self.shared_reader_eligible else ()
            )
        return relationships

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist and reconcile when this write can change reader eligibility."""

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = {str(field) for field in update_fields}
            kwargs["update_fields"] = update_fields
            if not update_fields:
                super().save(*args, **kwargs)
                return
        reconcile = (
            self._state.adding
            or update_fields is None
            or bool(update_fields & _shared_reader_policy_field_spellings(type(self)))
        )
        if not reconcile:
            super().save(*args, **kwargs)
            return
        with transaction.atomic():
            super().save(*args, **kwargs)
            self.reconcile_shared_reader()

    def reconcile_shared_reader(self) -> None:
        """Reconcile only this owner's wildcard tuple from persisted row facts."""

        if self.pk is None:
            raise ValidationError("A shared reader requires a saved row.")
        with transaction.atomic():
            canonical = system_queryset(type(self), lock=("self",)).get(pk=self.pk)
            relation = canonical.shared_reader_relation
            if relation is None:
                return
            resource = to_object_ref(canonical)
            if canonical.shared_reader_eligible:
                write_relationships(
                    [RelationshipTuple(resource=resource, relation=relation, subject=_EVERY_AUTHENTICATED_USER)]
                )
            else:
                delete_relationships(
                    RelationshipFilter(
                        resource_type=resource.resource_type,
                        resource_id=resource.resource_id,
                        relation=relation,
                        subject_type=_EVERY_AUTHENTICATED_USER.subject_type,
                        subject_id=_EVERY_AUTHENTICATED_USER.subject_id,
                    )
                )


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
        on_delete=audit_set_null,
        related_name="+",
    )
    """The user that created the row, when known."""

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=audit_set_null,
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


class AppendOnlyQuerySet(RebacQuerySet[_ModelT]):
    """Close generic collection edits and deletion around owner-controlled writes.

    Compose before the domain's base queryset to preserve authorization.
    Retained evidence uses insert admission; retained state machines expose
    exact conditional writes through their own methods and ``owner_update``.
    ``owner_update`` and ``owner_bulk_create`` are public, framework-protected
    APIs for domain owners that have already validated fields and predicates.
    They skip this class's guard only, preserving every downstream guard.
    Instance invariants and collector retention remain model/FK concerns;
    ``AuditMixin`` clears audit FKs through its collector policy without
    calling this queryset.
    """

    def immutable_error(self, operation: str) -> ValidationError:
        """Identify the model whose generic collection mutation is forbidden."""

        action = "deleted" if operation in {"delete", "_raw_delete"} else "edited"
        return ValidationError(f"{self.model._meta.label} rows cannot be {action}.")

    def validate_insert(self) -> None:
        """Let the domain owner narrow insert admission."""

    def insert(self, obj: _ModelT) -> _ModelT:
        """Validate one append before its ordinary authorized insertion."""

        self.validate_insert()
        return super().insert(obj)

    def bulk_create(
        self,
        objs: Iterable[_ModelT],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        unique_fields: Iterable[str] | None = None,
    ) -> list[_ModelT]:
        """Reject conflict handling before asking the owner to admit inserts."""

        if ignore_conflicts or update_conflicts:
            raise self.immutable_error("bulk_create")
        self.validate_insert()
        return self.owner_bulk_create(objs, batch_size=batch_size)

    def owner_bulk_create(self, objs: Iterable[_ModelT], *, batch_size: int | None = None) -> list[_ModelT]:
        """Protected API: insert an owner-validated batch through downstream guards."""

        return super().bulk_create(objs, batch_size=batch_size)

    def owner_update(self, **kwargs: Any) -> int:
        """Protected API: apply an owner-validated write through downstream guards."""

        return super().update(**kwargs)

    def update(self, **kwargs: Any) -> int:
        """Reject every collection edit."""

        raise self.immutable_error("update")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        """Reject every batched edit."""

        raise self.immutable_error("bulk_update")

    def delete(self) -> tuple[int, dict[str, int]]:
        """Reject deletion through the collection."""

        raise self.immutable_error("delete")

    def _raw_delete(self, using: str) -> int:
        """Reject direct SQL deletion through the queryset."""

        raise self.immutable_error("_raw_delete")


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


class ModelHistory(HistoricalRecords):
    """Native history adapted to abstract sources and generated module names.

    Tracking belongs to an abstract source/donor that includes HistoryMixin.
    Inheriting only a concrete tracked parent does not create a second history
    table for its MTI child. All copying, signals and history behavior stay with
    django-simple-history.
    """

    def finalize(self, sender: type[models.Model], **kwargs: Any) -> None:
        history_source = cast(type[models.Model], self.cls)
        tracked_abstract_parent = False
        for candidate in sender.__bases__:
            if not isinstance(candidate, type) or not issubclass(candidate, models.Model):
                continue
            base = cast(type[models.Model], candidate)
            if base is not models.Model and issubclass(base, history_source) and base._meta.abstract:
                tracked_abstract_parent = True
                break
        if not tracked_abstract_parent:
            return
        super().finalize(sender, **kwargs)

    def get_meta_options(self, model: type[models.Model]) -> dict[str, Any]:
        options = super().get_meta_options(model)
        options["app_label"] = model._meta.app_label
        return options

    def copy_fields(self, model: type[models.Model]) -> dict[str, models.Field]:
        """Copy MTI identity as a regular historical relation, not an inheritance link."""

        fields = super().copy_fields(model)
        for name, field in fields.items():
            source = model._meta.get_field(name)
            if (
                source.remote_field is None
                or not source.auto_created
                or not source.remote_field.parent_link
            ):
                continue
            field.auto_created = False
            field.remote_field.parent_link = False
        return fields

    def fields_included(self, model: type[models.Model]) -> list[models.Field]:
        """Keep snapshot fields, excluding virtual and row-derived generated columns."""

        return [
            field
            for field in super().fields_included(model)
            if (field.concrete or field.is_relation or field.auto_created)
            and not isinstance(field, models.GeneratedField)
        ]


class HistoryMixin(models.Model):
    """Track concrete models through django-simple-history's inherited descriptor."""

    history = ModelHistory(inherit=True)

    class Meta:
        abstract = True


class RevisionMixin(models.Model):
    """Track declared field snapshots through django-reversion."""

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
        with transaction.atomic(), reversion.create_revision():
            self.save(update_fields=update_fields_with_auto_now(self, reverted))
            reversion.set_comment(f"Reverted to revision {version.revision_id}.")


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

    def ancestors_of(self, node: HierarchyMixin) -> Self:
        """Return every proper ancestor of ``node`` (EXCLUSIVE of ``node``)."""

        return cast(Self, self.filter(path__in=node.ancestor_paths()))

    def update(self, **kwargs: Any) -> int:
        """Keep parent moves and derived paths on the saved-row owner."""

        if {"path", "parent", "parent_id"} & kwargs.keys():
            raise ValidationError("The hierarchy parent and path belong to the saved-row owner.")
        return super().update(**kwargs)

    def owner_update(self, **kwargs: Any) -> int:
        """Protected API: apply an owner-validated write through downstream guards.

        HierarchyMixin owns the selected rows and derived path value; bypass
        only this class's external-write guard after validating the move.
        """

        return super().update(**kwargs)


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
        if "parent_id" in self.__dict__:
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
                self._write_hierarchy_path(
                    system_queryset(type(self)).filter(pk=self.pk),
                    new_path,
                )
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
            subtree = system_queryset(type(self), lock=()).filter(path__startswith=old_path)
            if old_path:
                # Evaluate SELECT FOR UPDATE before moving any descendant's path.
                list(subtree.order_by("pk").values_list("pk", flat=True))
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
        """Return committed paths, serializing overlapping moves when supported."""

        reader = system_queryset(type(self), lock=())
        return dict(reader.filter(pk__in=pks).values_list("pk", "path"))

    def _hierarchy_needs_repath(self) -> bool:
        """Return whether an existing row's ``parent`` moved (or its path is unset)."""

        # An unrelated deferred save must not load tree columns into its UPDATE.
        if "path" in self.__dict__ and not self.path:
            return True
        if "parent_id" not in self.__dict__:
            return False
        if hasattr(self, "_hierarchy_saved_parent_id"):
            return self._hierarchy_saved_parent_id != self.parent_id
        # A deferred load (``.only(...)`` excluding ``parent``) carries no baseline,
        # so a reparent would be invisible if we compared ``parent_id`` to itself.
        # Fetch the committed ``parent_id`` from the row to compare against the
        # in-memory FK the caller may have moved.
        return self._hierarchy_committed_parent_id() != self.parent_id

    def _hierarchy_committed_parent_id(self) -> Any:
        """Return this row's committed ``parent_id`` from the database."""

        return system_queryset(type(self)).filter(pk=self.pk).values_list("parent_id", flat=True).first()

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
