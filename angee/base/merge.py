"""Record merge: fold records into a survivor and re-point what refers to them.

A model composing :class:`MergeableMixin` merges through one template,
:meth:`MergeableMixin.merge`, called on the survivor. Every row that points at a
merged record is a :class:`Reference` with a :class:`~angee.base.refs.MergePolicy`,
much as Django's ``on_delete`` decides what a delete does to such rows:

- a foreign key moves to the survivor, except one on an append-only model,
  which can neither move nor outlive the record and so blocks;
- a polymorphic edge (:class:`~angee.base.refs.RecordRefMixin`) follows its
  model's ``merge_policy``, and an edge that declares none blocks.

A move re-keys the pointing column. Where the survivor already holds an equal
row under a unique set the edge declares as identity (``merge_identity``), the
merged record's copy is dropped; any other unique collision refuses the merge.
The merged record's own many-to-many rows are its data, not references: they
leave with it unless :meth:`MergeableMixin.merge_fields` carries them over.

Moves run as named system work once :meth:`MergeableMixin.merge` has authorized
the actor: under an actor REBAC only deletes and recreates an edge, which would
cost a row its identity. Retiring a merged record is the actor's own delete.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Self

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Exists, OuterRef, ProtectedError, Q, RestrictedError
from django.db.models.functions import Cast
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context

from angee.base.actors import instance_actor
from angee.base.identity import instances_from_public_ids
from angee.base.mixins import AppendOnlyModel, auto_now_stamps
from angee.base.refs import MergePolicy, RecordRefMixin, concrete_child_models, generic_pointer_model
from angee.base.scoping import lock_if_supported

MERGE_REASON = "merge"
"""The system-context reason a merge's moves record."""

MERGE_LIMIT = 100
"""The most records one merge folds into its survivor."""


@dataclass(frozen=True, slots=True)
class Reference:
    """One way rows of ``model`` point at records being merged: a foreign key or an edge's pointer."""

    model: type[models.Model]
    policy: MergePolicy
    field: models.ForeignObject[Any, Any] | None = None
    pointer: GenericForeignKey | None = None

    @property
    def label(self) -> str:
        """Name the pointing rows for a refusal: the model's plural and the pointing column."""

        name = self.field.name if self.field is not None else self.pointer.name if self.pointer else ""
        return f"{self.model._meta.verbose_name_plural} ({name})"

    @property
    def columns(self) -> tuple[str, ...]:
        """Return the model fields a move rewrites: the foreign key, or the pointer's type and id."""

        if self.field is not None:
            return (self.field.name,)
        assert self.pointer is not None
        return (self.model._meta.get_field(self.pointer.ct_field).name, self.pointer.fk_field)

    def lookup(self, record: models.Model) -> dict[str, Any]:
        """Return the column values that point at ``record``."""

        if self.field is not None:
            return {self.field.name: record}
        assert self.pointer is not None
        model = generic_pointer_model(type(record))
        return {self.pointer.ct_field: ContentType.objects.get_for_model(model), self.pointer.fk_field: record.pk}

    def rows(self, record: models.Model) -> models.QuerySet[Any]:
        """Return the rows pointing at ``record``, outside any actor scope."""

        return self.model._base_manager.filter(**self.lookup(record))

    def pointing(self, target: type[models.Model]) -> Exists:
        """Return a condition on ``target`` rows: some row of this reference points at it."""

        if self.field is not None:
            column = self.field.target_field.attname
            return Exists(self.model._base_manager.filter(**{self.field.name: OuterRef(column)}))
        assert self.pointer is not None
        object_id = self.model._meta.get_field(self.pointer.fk_field)
        return Exists(self.model._base_manager.filter(**{
            self.pointer.ct_field: ContentType.objects.get_for_model(generic_pointer_model(target)),
            self.pointer.fk_field: Cast(OuterRef("pk"), output_field=object_id),
        }))

    def move(self, rows: models.QuerySet[Any], *, survivor: models.Model) -> None:
        """Re-point ``rows`` to ``survivor``, dropping copies of identity rows it already holds.

        A collision on any other unique set refuses the merge: rows that merely
        share a slot, such as a rank, are different facts.
        """

        if self.field is not None and not isinstance(survivor, self.field.related_model):
            raise ValidationError(f"The record that is kept cannot hold {self.label}.")
        target = self.lookup(survivor)
        held = self.model._base_manager.filter(**target)
        identity = set(self.model.merge_identity) if issubclass(self.model, RecordRefMixin) else set()
        for fields, condition in _unique_sets(self.model):
            if not set(self.columns) <= set(fields):
                continue
            others = [name for name in fields if name not in self.columns]
            twins = held.filter(condition).filter(**{name: OuterRef(name) for name in others})
            clashing = rows.filter(condition).filter(Exists(twins))
            if fields in identity:
                clashing.delete()
            elif clashing.exists():
                raise ValidationError(
                    f"Both records already have {self.label} with the same {', '.join(fields)}; "
                    "resolve that before merging."
                )
        rows.update(**target, **auto_now_stamps(self.model, timezone.now()))


def references(model: type[models.Model]) -> Iterator[Reference]:
    """Yield every way rows can point at records of ``model``, in a stable order.

    Foreign keys are the ones Django's delete collects (hidden ones included)
    across the model's multi-table parents and children, which share its key;
    parent links are the row itself. Link rows of the model's own forward
    many-to-many fields are its data and are left out. Polymorphic edges are
    every installed :class:`RecordRefMixin` model: some accept targets their
    schema does not declare, so declarations cannot narrow the scan.
    """

    tree = [model, *model._meta.get_parent_list(), *_descendants(model)]
    own_links = {field.remote_field.through for owner in tree for field in owner._meta.local_many_to_many}
    seen: set[tuple[str, str]] = set()
    for owner in tree:
        for relation in owner._meta.get_fields(include_hidden=True):
            if not (relation.auto_created and not relation.concrete and (relation.one_to_one or relation.one_to_many)):
                continue
            if getattr(relation, "parent_link", False) or relation.related_model in own_links:
                continue
            field = relation.field
            key = (relation.related_model._meta.label, field.name)
            if key in seen:
                continue
            seen.add(key)
            policy = MergePolicy.BLOCK if issubclass(relation.related_model, AppendOnlyModel) else MergePolicy.MOVE
            yield Reference(relation.related_model, policy, field=field)
    for edge in sorted(apps.get_models(), key=lambda candidate: candidate._meta.label):
        if issubclass(edge, RecordRefMixin):
            yield Reference(edge, edge.merge_policy or MergePolicy.BLOCK, pointer=edge.record_ref_field())


class MergeableMixin(models.Model):
    """Merge records of this model into a survivor through one template.

    :meth:`merge` locks the survivor and the merged records, authorizes the
    actor, checks :meth:`validate_merge`, lets :meth:`merge_fields` carry values
    over, re-points references (:meth:`merge_references`) as named system work
    and retires each merged record (:meth:`retire_merged`). A model adapts the
    merge by overriding those hooks and calling ``super()``.
    """

    MERGE_PERMISSION: ClassVar[str] = "write"
    """The survivor permission a merge needs."""

    MERGED_PERMISSION: ClassVar[str] = "delete"
    """The permission a merge needs on each merged record."""

    class Meta:
        """Django model options for merge-only abstract inheritance."""

        abstract = True

    @classmethod
    def merge_blockers(cls) -> tuple[tuple[Q, str], ...]:
        """Return each condition on a merged record that refuses its merge, with its message.

        A verb and a batched projection share these: a row matching any of them
        cannot be merged. The default refuses while a ``BLOCK`` reference points
        at the record. Overrides add the model's own conditions to ``super()``'s.
        """

        return tuple(
            (Q(reference.pointing(cls)), f"{reference.label} still refer to it.")
            for reference in references(cls)
            if reference.policy == MergePolicy.BLOCK
        )

    def merge(self, *, records: Sequence[str]) -> Self:
        """Merge the records named by public id into this one and return it.

        A record the actor cannot read reads as gone. The whole merge commits or
        none of it does.
        """

        model = type(self)
        actor = instance_actor(self)
        if not records:
            raise ValidationError("Choose the records to merge into this one.")
        if len(records) > MERGE_LIMIT:
            raise ValidationError(f"Merge at most {MERGE_LIMIT} records at once.")
        with transaction.atomic(), actor_context(actor):
            found = instances_from_public_ids(model, records, queryset=model._default_manager.all())
            gone = sorted(set(records) - set(found))
            merged_ids = sorted({row.pk for row in found.values()})
            if self.pk in merged_ids:
                raise ValidationError("A record cannot be merged into itself.")
            rows = lock_if_supported(model._base_manager.filter(pk__in=(self.pk, *merged_ids)).order_by("pk"))
            locked = {row.pk: row.with_actor(actor) for row in rows}
            gone += [public for public, row in found.items() if row.pk not in locked]
            if gone or self.pk not in locked:
                raise ValidationError(f"These records no longer exist: {', '.join(sorted(gone)) or 'the kept one'}.")
            survivor = locked[self.pk]
            merged = [locked[pk] for pk in merged_ids]
            if not survivor.has_access(self.MERGE_PERMISSION):
                raise PermissionDenied("You are not allowed to change the record that is kept.")
            if not all(record.has_access(self.MERGED_PERMISSION) for record in merged):
                raise PermissionDenied("You are not allowed to remove every record being merged.")
            survivor.validate_merge(merged)
            survivor.merge_fields(merged)
            with system_context(reason=MERGE_REASON):
                for record in merged:
                    survivor.merge_references(record)
            for record in merged:
                survivor.retire_merged(record)
        self.refresh_from_db()
        return self

    def validate_merge(self, merged: Sequence[Self]) -> None:
        """Refuse when any merged record matches a :meth:`merge_blockers` condition.

        Overrides add rules about the pair, such as a recorded "keep separate".
        """

        model = type(self)
        with system_context(reason=MERGE_REASON):
            for condition, message in model.merge_blockers():
                refused = model._base_manager.filter(pk__in=[record.pk for record in merged]).filter(condition)
                if refused.exists():
                    raise ValidationError(message)

    def merge_fields(self, merged: Sequence[Self]) -> None:
        """Carry values over from the merged records; the default keeps this record's own."""

        del merged

    def merge_references(self, merged: Self) -> None:
        """Re-point what refers to ``merged`` to this record, by each reference's policy.

        A reference from this record itself to ``merged`` refuses the merge:
        moving it would point the record at itself.
        """

        for reference in references(type(merged)):
            if reference.policy != MergePolicy.MOVE:
                continue
            rows = reference.rows(merged)
            if isinstance(self, reference.model) and rows.filter(pk=self.pk).exists():
                raise ValidationError(f"{self} refers to {merged} through {reference.label}; change that first.")
            if rows.exists():
                reference.move(rows, survivor=self)

    def retire_merged(self, merged: Self) -> None:
        """Remove ``merged`` through the model's delete lock, as the actor; the default deletes it."""

        locked = merged.lock_for_delete()
        if locked is None:
            raise ValidationError(f"{merged} no longer exists.")
        try:
            locked.with_actor(instance_actor(merged)).delete()
        except (ProtectedError, RestrictedError) as error:
            raise ValidationError(f"{merged} is still referred to and cannot be removed.") from error


def _descendants(model: type[models.Model]) -> Iterator[type[models.Model]]:
    """Yield the installed multi-table descendants of ``model``, which share its key."""

    for child in concrete_child_models(model):
        yield child
        yield from _descendants(child)


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
