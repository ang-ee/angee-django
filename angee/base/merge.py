"""Record merge: fold records into a survivor and re-point what refers to them.

A model composing :class:`MergeableMixin` merges through one template,
:meth:`MergeableMixin.merge`, called on the survivor. Every row that points at a
merged record is a :class:`Reference` with a :class:`~angee.base.refs.MergePolicy`,
much as Django's ``on_delete`` decides what a delete does to such rows:

- a foreign key Django's delete collects moves to the survivor, except one on an
  append-only model, which can neither move nor outlive the record and so
  blocks, and a ``DO_NOTHING`` key (history), which keeps pointing at the record;
- a polymorphic edge (:class:`~angee.base.refs.RecordRefMixin`) follows its
  model's ``merge_policy``, which blocks unless the edge declares another.

A move re-keys the pointing column. Where the survivor already holds an equal
row under the reference's identity (an edge's ``merge_identity``, an
auto-created many-to-many link's pair), the merged record's copy is dropped; any
other unique collision refuses the merge. The merged record's own many-to-many
rows are its data, not references: they leave with it unless
:meth:`MergeableMixin.merge_fields` carries them over. Stored REBAC
relationships naming a merged record (a share, a membership) refuse the merge:
retiring the record would drop them, and no merge moves them yet.

Moves, and the identity copies they drop, run as named system work once
:meth:`MergeableMixin.merge` has authorized the actor: under an actor REBAC only
deletes and recreates an edge, which would cost a row its identity. Retiring a
merged record is the actor's own delete.
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
from django.db.models import DO_NOTHING, Exists, OuterRef, ProtectedError, Q, RestrictedError
from django.db.models.deletion import get_candidate_relations_to_delete
from django.db.models.functions import Cast
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context, to_object_ref
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type

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
    """One way rows of ``model`` point at records being merged, and what a merge does to them.

    :class:`ForeignKeyReference` and :class:`EdgeReference` say how the rows
    point; the move itself is shared.
    """

    model: type[models.Model]
    policy: MergePolicy

    @property
    def name(self) -> str:
        """Return the pointing field's name."""

        raise NotImplementedError

    @property
    def columns(self) -> tuple[str, ...]:
        """Return the model fields a move rewrites."""

        raise NotImplementedError

    def lookup(self, record: models.Model) -> dict[str, Any]:
        """Return the column values that point at ``record``."""

        raise NotImplementedError

    def pointing(self, target: type[models.Model]) -> Exists:
        """Return a condition on ``target`` rows: some row of this reference points at it."""

        raise NotImplementedError

    def holds(self, survivor: models.Model) -> bool:
        """Return whether ``survivor`` can be pointed at; the default accepts any record."""

        del survivor
        return True

    @property
    def label(self) -> str:
        """Name the pointing rows for a refusal: the model's plural and the pointing field."""

        return f"{self.model._meta.verbose_name_plural} ({self.name})"

    def rows(self, record: models.Model) -> models.QuerySet[Any]:
        """Return the rows pointing at ``record``, outside any actor scope."""

        return self.model._base_manager.filter(**self.lookup(record))

    def move(self, rows: models.QuerySet[Any], *, survivor: models.Model) -> None:
        """Re-point ``rows`` to ``survivor``, dropping copies of identity rows it already holds.

        A collision on any other unique set refuses the merge: rows that merely
        share a slot, such as a rank, are different facts.
        """

        if not self.holds(survivor):
            raise ValidationError(f"The record that is kept cannot hold {self.label}.")
        target = self.lookup(survivor)
        held = self.model._base_manager.filter(**target)
        identity = _merge_identity(self.model)
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


@dataclass(frozen=True, slots=True)
class ForeignKeyReference(Reference):
    """Rows whose foreign key points at the record, or at a multi-table parent or child sharing its key."""

    field: models.ForeignObject[Any, Any]

    @property
    def name(self) -> str:
        """Return the foreign key's name."""

        return self.field.name

    @property
    def columns(self) -> tuple[str, ...]:
        """Return the foreign key, the one field a move rewrites."""

        return (self.field.name,)

    def lookup(self, record: models.Model) -> dict[str, Any]:
        """Return the key value pointing at ``record``; a multi-table relative shares its primary key."""

        target = self.field.target_field
        return {self.field.attname: record.pk if target.primary_key else getattr(record, target.attname)}

    def pointing(self, target: type[models.Model]) -> Exists:
        """Return a condition on ``target`` rows: a row's key holds the target's value."""

        column = "pk" if self.field.target_field.primary_key else self.field.target_field.attname
        return Exists(self.model._base_manager.filter(**{self.field.attname: OuterRef(column)}))

    def holds(self, survivor: models.Model) -> bool:
        """Return whether ``survivor`` has a row in the model the key points at.

        A key on a multi-table child moves only onto a survivor that is that child.
        """

        related = self.field.related_model
        return isinstance(survivor, related) or related._base_manager.filter(pk=survivor.pk).exists()


@dataclass(frozen=True, slots=True)
class EdgeReference(Reference):
    """Rows of a :class:`~angee.base.refs.RecordRefMixin` edge whose pointer names the record."""

    pointer: GenericForeignKey

    @property
    def name(self) -> str:
        """Return the edge's pointer name."""

        return self.pointer.name

    @property
    def columns(self) -> tuple[str, ...]:
        """Return the pointer's type and id fields."""

        return (self.model._meta.get_field(self.pointer.ct_field).name, self.pointer.fk_field)

    def lookup(self, record: models.Model) -> dict[str, Any]:
        """Return the pointer's type and id for ``record``."""

        model = generic_pointer_model(type(record))
        return {self.pointer.ct_field: ContentType.objects.get_for_model(model), self.pointer.fk_field: record.pk}

    def pointing(self, target: type[models.Model]) -> Exists:
        """Return a condition on ``target`` rows: an edge row names the target's type and key."""

        object_id = self.model._meta.get_field(self.pointer.fk_field)
        return Exists(self.model._base_manager.filter(**{
            self.pointer.ct_field: ContentType.objects.get_for_model(generic_pointer_model(target)),
            self.pointer.fk_field: Cast(OuterRef("pk"), output_field=object_id),
        }))


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
        for relation in get_candidate_relations_to_delete(owner._meta):
            if relation.parent_link or relation.related_model in own_links:
                continue
            key = (relation.related_model._meta.label, relation.field.name)
            if key in seen:
                continue
            seen.add(key)
            if relation.on_delete == DO_NOTHING:
                policy = MergePolicy.KEEP
            elif issubclass(relation.related_model, AppendOnlyModel):
                policy = MergePolicy.BLOCK
            else:
                policy = MergePolicy.MOVE
            yield ForeignKeyReference(relation.related_model, policy, relation.field)
    for edge in sorted(apps.get_models(), key=lambda candidate: candidate._meta.label):
        if issubclass(edge, RecordRefMixin):
            yield EdgeReference(edge, edge.merge_policy, edge.record_ref_field())


class MergeableMixin(models.Model):
    """Merge records of this model into a survivor through one template.

    :meth:`merge` locks the survivor and the merged records, authorizes the
    actor, checks :meth:`validate_merge`, lets :meth:`merge_fields` carry values
    over, re-points references (:meth:`merge_references`) as named system work
    and retires each merged record (:meth:`retire_merged`). A model adapts the
    merge by overriding those hooks and calling ``super()``.
    """

    SURVIVOR_PERMISSION: ClassVar[str] = "write"
    """The permission a merge needs on the record that is kept."""

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
            if not survivor.has_access(self.SURVIVOR_PERMISSION):
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
        """Refuse a merged record that matches a :meth:`merge_blockers` condition or holds stored relationships.

        Overrides add rules about the pair, such as a recorded "keep separate".
        """

        model = type(self)
        with system_context(reason=MERGE_REASON):
            for condition, message in model.merge_blockers():
                refused = model._base_manager.filter(pk__in=[record.pk for record in merged]).filter(condition)
                if refused.exists():
                    raise ValidationError(message)
            for record in merged:
                if _named_in_relationships(record):
                    raise ValidationError(f"{record} is shared or named in access rules; remove that first.")

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


def _named_in_relationships(record: models.Model) -> bool:
    """Return whether stored REBAC relationships name ``record``, as their resource or subject.

    Relations backed by fields follow a moved key; stored rows (a direct share,
    a membership) would go with the retired record.
    """

    if not model_resource_type(type(record)):
        return False
    ref = to_object_ref(record)
    rows = active_relationship_model().objects
    return (
        rows.filter(resource_type=ref.resource_type, resource_id=ref.resource_id).exists()
        or rows.filter(subject_type=ref.resource_type, subject_id=ref.resource_id).exists()
    )


def _merge_identity(model: type[models.Model]) -> frozenset[tuple[str, ...]]:
    """Return the unique field sets whose equal rows of ``model`` state one fact.

    An edge declares its own (``merge_identity``); an auto-created many-to-many
    link is its pair. Any other model declares none.
    """

    if issubclass(model, RecordRefMixin):
        return frozenset(model.merge_identity)
    if model._meta.auto_created:
        return frozenset(tuple(fields) for fields in model._meta.unique_together)
    return frozenset()


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
