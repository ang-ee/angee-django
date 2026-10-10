"""Record merge: fold records into a survivor and re-point what refers to them.

A model composing :class:`MergeableMixin` merges through one template,
:meth:`MergeableMixin.absorb`, called on the survivor. Everything that points at
a merged record follows a declared :class:`MergePolicy`, much as Django's
``on_delete`` decides what a delete does to its referents:

- a concrete foreign key, or a many-to-many link row, moves to the survivor,
  except on an append-only model, whose rows stay as history;
- a polymorphic edge (:class:`~angee.base.refs.RecordRefMixin`) follows its
  model's ``merge_policy``, and an edge that declares none blocks the merge.

A model whose rows need more than a re-keyed column on a move (links that
deduplicate by URL, followers that join the survivor's thread) declares a
``merge_rows`` classmethod and moves them itself; :func:`move_rows` is the
default. Moves run as named system work once :meth:`~MergeableMixin.absorb` has
authorized the actor, so a row keeps its identity: under an actor REBAC only
deletes and recreates an edge.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import connection, models, transaction
from django.db.models import Exists, OuterRef, ProtectedError, Q
from rebac import PermissionDenied, actor_context, system_context

from angee.base.actors import instance_actor
from angee.base.identity import instances_from_public_ids
from angee.base.mixins import AppendOnlyModel
from angee.base.refs import RecordRefMixin, generic_pointer_target
from angee.base.scoping import lock_if_supported

MERGE_REASON = "merge"
"""The system-context reason every merge's re-pointing and retirement record."""


class MergePolicy(StrEnum):
    """What a merge does to rows that point at a merged record."""

    MOVE = "move"
    """Re-point the row to the survivor."""

    KEEP = "keep"
    """Leave the row on the merged record, as history."""

    BLOCK = "block"
    """Refuse the merge while such a row exists."""


@dataclass(frozen=True, slots=True)
class Referent:
    """One way rows of ``model`` point at a record: a foreign key or a generic pointer."""

    model: type[models.Model]
    policy: MergePolicy
    field: models.ForeignKey[Any, Any] | None = None
    pointer: GenericForeignKey | None = None

    @property
    def label(self) -> str:
        """Name the referent for a refusal: the model's plural and the pointing field."""

        name = self.field.name if self.field is not None else self.pointer.name if self.pointer is not None else ""
        return f"{self.model._meta.verbose_name_plural} ({name})"

    def lookup(self, record: models.Model) -> dict[str, Any]:
        """Return the filter selecting this referent's rows that point at ``record``."""

        if self.field is not None:
            return {self.field.name: record}
        assert self.pointer is not None
        content_type, object_id = generic_pointer_target(record)
        return {self.pointer.ct_field: content_type, self.pointer.fk_field: object_id}

    def rows(self, record: models.Model) -> models.QuerySet[Any]:
        """Return this referent's rows pointing at ``record``, outside any actor scope."""

        return self.model._base_manager.filter(**self.lookup(record))


def referents(model: type[models.Model]) -> Iterator[Referent]:
    """Yield every relation that can point at rows of ``model``, in a stable order.

    Concrete foreign keys and many-to-many link rows come from Django's related
    objects across the model and its multi-table parents; multi-table parent
    links are the row itself and are skipped. Polymorphic edges are every
    installed :class:`RecordRefMixin` model, since a generic pointer declares
    no target model. A model whose table the database lacks, such as a test
    fixture's, holds no rows and is skipped.
    """

    tables = set(connection.introspection.table_names())
    seen: set[tuple[str, str]] = set()
    for owner in (model, *model._meta.get_parent_list()):
        for relation in owner._meta.related_objects:
            if isinstance(relation, models.ManyToManyRel):
                through = relation.through
                field = through._meta.get_field(relation.field.m2m_reverse_field_name())
                related = through
            else:
                field = relation.field
                related = relation.related_model
                if relation.parent_link:
                    continue
            key = (related._meta.label, field.name)
            if key in seen or related._meta.db_table not in tables:
                continue
            seen.add(key)
            policy = MergePolicy.KEEP if issubclass(related, AppendOnlyModel) else MergePolicy.MOVE
            yield Referent(related, policy, field=field)
    for edge in sorted(apps.get_models(), key=lambda candidate: candidate._meta.label):
        if issubclass(edge, RecordRefMixin) and edge._meta.db_table in tables:
            yield Referent(edge, edge.merge_policy or MergePolicy.BLOCK, pointer=edge.record_ref_field())


def move_rows(referent: Referent, rows: models.QuerySet[Any], *, survivor: models.Model) -> None:
    """Re-point ``rows`` to ``survivor``, dropping each one the survivor already holds.

    A row duplicates the survivor's when a unique constraint over the moved
    column(s) would collide: the merged record's copy is deleted, so its
    cascades run. A unique one-to-one row the survivor already has refuses the
    merge instead, since nothing says which of the two to keep.
    """

    moved = (referent.field.name,) if referent.field is not None else _pointer_fields(referent)
    target = referent.lookup(survivor)
    held = referent.model._base_manager.filter(**target)
    for fields, condition in _unique_sets(referent.model):
        if not set(moved) <= set(fields):
            continue
        others = [name for name in fields if name not in moved]
        if not others:
            if rows.exists() and held.filter(condition).exists():
                raise ValidationError(f"Both records already have {referent.label}; resolve one before merging.")
            continue
        twins = held.filter(condition).filter(**{name: OuterRef(name) for name in others})
        rows.filter(condition).filter(Exists(twins)).delete()
    rows.update(**target)


class MergeableMixin(models.Model):
    """Merge records of this model into a survivor through one template.

    :meth:`absorb` locks the survivor and the merged records, authorizes the
    actor (``write`` on the survivor, ``delete`` on each merged record), asks
    :meth:`merge_blocker` for a refusal, lets :meth:`absorb_fields` set the
    survivor's chosen values, then re-points references
    (:meth:`merge_references`) and retires each merged record
    (:meth:`retire_merged`) as named system work. A model adapts the merge by
    overriding those hooks and calling ``super()``.
    """

    class Meta:
        """Django model options for merge-only abstract inheritance."""

        abstract = True

    def absorb(self, *, records: Sequence[str] = (), values: Mapping[str, Any] | None = None) -> Self:
        """Merge the records named by public id into this one and return it.

        Keyword-only, with public ids and JSON values, so a decision alternative
        can call it through ``decision_methods``. ``values`` sets the survivor's
        own fields in the same transaction. The whole merge commits or none of
        it does.
        """

        model = type(self)
        actor = instance_actor(self)
        if not records:
            raise ValidationError("Choose the records to merge into this one.")
        with transaction.atomic(), actor_context(actor):
            # Resolve through the actor's read scope: a record they cannot see reads as gone.
            found = instances_from_public_ids(model, records, queryset=model._default_manager.all())
            missing = sorted(set(records) - set(found))
            if missing:
                raise ValidationError(f"These records no longer exist: {', '.join(missing)}.")
            loser_ids = {row.pk for row in found.values()}
            if self.pk in loser_ids:
                raise ValidationError("A record cannot be merged into itself.")
            rows = lock_if_supported(model._base_manager.filter(pk__in=(self.pk, *loser_ids)).order_by("pk"))
            locked = {row.pk: row.with_actor(actor) for row in rows}
            survivor = locked[self.pk]
            losers = [locked[pk] for pk in sorted(loser_ids)]
            if not survivor.has_access("write"):
                raise PermissionDenied("You are not allowed to change the record that is kept.")
            for loser in losers:
                if not loser.has_access("delete"):
                    raise PermissionDenied("You are not allowed to remove every record being merged.")
                reason = survivor.merge_blocker(loser)
                if reason:
                    raise ValidationError(reason)
            survivor.absorb_fields(losers, values or {})
            with system_context(reason=MERGE_REASON):
                for loser in losers:
                    survivor.merge_references(loser)
                    survivor.retire_merged(loser)
        self.refresh_from_db()
        return self

    def merge_blocker(self, loser: Self) -> str | None:
        """Return why ``loser`` cannot merge into this record, or ``None``.

        The default refuses while a referent whose policy is ``BLOCK`` points at
        ``loser``. Overrides add the model's own rules and call ``super()``.
        """

        with system_context(reason=MERGE_REASON):
            blocking = [
                referent.label
                for referent in referents(type(loser))
                if referent.policy == MergePolicy.BLOCK and referent.rows(loser).exists()
            ]
        if blocking:
            return f"{loser} cannot be merged while these refer to it: {', '.join(blocking)}."
        return None

    def absorb_fields(self, losers: Sequence[Self], values: Mapping[str, Any]) -> None:
        """Set this survivor's chosen field values under the actor.

        The default accepts editable concrete scalar fields only. Overrides
        carry values over from ``losers`` as the model's own rules decide.
        """

        del losers
        if not values:
            return
        for name, value in values.items():
            try:
                field = self._meta.get_field(name)
            except FieldDoesNotExist:
                field = None
            if not isinstance(field, models.Field) or not field.editable or field.is_relation or field.primary_key:
                raise ValidationError({name: "This field cannot be set by a merge."})
            setattr(self, field.attname, field.clean(value, self))
        stamps = [field.name for field in self._meta.concrete_fields if getattr(field, "auto_now", False)]
        self.save(update_fields=[*values, *stamps])

    def merge_references(self, loser: Self) -> None:
        """Re-point what refers to ``loser`` according to each referent's policy.

        A model declaring ``merge_rows(rows, *, referent, survivor)`` moves its
        own rows; every other ``MOVE`` referent goes through :func:`move_rows`.
        A self-reference from the survivor to ``loser`` refuses the merge, since
        moving it would point the survivor at itself.
        """

        for referent in referents(type(loser)):
            if referent.policy != MergePolicy.MOVE:
                continue
            rows = referent.rows(loser)
            if isinstance(self, referent.model) and rows.filter(pk=self.pk).exists():
                raise ValidationError(f"{self} refers to {loser} through {referent.label}; change that first.")
            if not rows.exists():
                continue
            mover = getattr(referent.model, "merge_rows", None)
            if mover is not None:
                mover(rows, referent=referent, survivor=self)
            else:
                move_rows(referent, rows, survivor=self)

    def retire_merged(self, loser: Self) -> None:
        """Remove ``loser`` once nothing it owned points at it; the default deletes it."""

        try:
            loser.delete()
        except ProtectedError as error:
            raise ValidationError(f"{loser} is still referred to and cannot be removed.") from error


def _pointer_fields(referent: Referent) -> tuple[str, ...]:
    """Return the model field names a generic pointer stores, content type then id."""

    assert referent.pointer is not None
    content_type = referent.model._meta.get_field(referent.pointer.ct_field)
    return (content_type.name, referent.pointer.fk_field)


def _unique_sets(model: type[models.Model]) -> Iterator[tuple[tuple[str, ...], Q]]:
    """Yield each unique field set of ``model`` with the condition it applies under."""

    for field in model._meta.concrete_fields:
        if field.unique and not field.primary_key:
            yield (field.name,), Q()
    for together in model._meta.unique_together:
        yield tuple(together), Q()
    for constraint in model._meta.constraints:
        if isinstance(constraint, models.UniqueConstraint) and constraint.fields:
            yield tuple(constraint.fields), constraint.condition or Q()
