"""Record merge: fold records into a survivor and re-point what refers to them.

A model composing :class:`MergeableMixin` merges through one template,
:meth:`MergeableMixin.merge`, called on the survivor. Every row that points at a
merged record is a :class:`Reference` with a :class:`MergePolicy`,
much as Django's ``on_delete`` decides what a delete does to such rows:

- a foreign key Django's delete collects moves to the survivor, except one on an
  append-only model, which can neither move nor outlive the record and so
  blocks, and a ``DO_NOTHING`` key (history), which keeps pointing at the record;
  the pointing model's ``merge_policies`` names a key whose policy differs;
- a polymorphic edge (:class:`~angee.base.refs.RecordRefMixin`) follows its
  model's ``merge_policy``, which blocks unless the edge declares another;
- any other generic foreign key has no declared policy and blocks.

A row that names a record in a form no reference reads (a resource ledger's
public id), or work that retiring the record would cancel (an unfinished workflow
run), is its addon's to refuse, through ``ANGEE_MERGE_GUARDS``.

A move re-keys the pointing column. Where the survivor already holds an equal
row under the reference's identity (the pointing model's ``merge_identity``, an
auto-created many-to-many link's pair), the merged record's copy is dropped; any
other unique collision refuses the merge, including one under a rule no field set
describes (an expression constraint). The merged record's own many-to-many
rows are its data, not references: they leave with it unless
:meth:`MergeableMixin.merge_fields` carries them over. Stored REBAC
relationships naming a merged record (a share, a membership) refuse the merge:
retiring the record would drop them, and no merge moves them yet.

Every merge shares one system fold, :func:`fold_records`: its refusals, the
moves (and the identity copies they drop) as named system work, and the merge
changeset. :meth:`MergeableMixin.merge` calls it once it has authorized the
actor, whose own delete retires each merged record; under an actor REBAC only
deletes and recreates an edge, which would cost a row its identity. A model's own
system verb that folds a duplicate has no actor to authorize and calls it directly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import Any, ClassVar, Self, cast

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import DO_NOTHING, Exists, OuterRef, ProtectedError, Q, RestrictedError
from django.db.models.deletion import get_candidate_relations_to_delete
from django.db.models.functions import Cast
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context, to_object_ref
from rebac.actors import is_sudo, model_can_resolve_subject, to_subject_ref
from rebac.errors import NoActorResolvedError
from rebac.field_backing import canonical_model
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type

from angee.base.actors import instance_actor
from angee.base.changesets import changeset, current_changeset
from angee.base.identity import instances_from_public_ids
from angee.base.impl import resolve_hooks
from angee.base.lineage import concrete_lineage
from angee.base.models.append_only import AppendOnlyModel
from angee.base.models.identity import auto_now_stamps
from angee.base.models.ownership import AuditMixin
from angee.base.models.query import model_history
from angee.base.models.trash import TrashMixin


class MergePolicy(StrEnum):
    """What merging a record does to rows that point at it."""

    MOVE = "move"
    KEEP = "keep"
    BLOCK = "block"


def unique_field_sets(model: type[models.Model]) -> Iterator[tuple[tuple[str, ...], Q]]:
    """Yield each unique field set of ``model`` with the condition it applies under.

    An expression constraint names no field set and is not yielded.
    """

    for field in model._meta.concrete_fields:
        if field.unique and not field.primary_key:
            yield (field.name,), Q()
    for together in model._meta.unique_together:
        yield tuple(together), Q()
    for constraint in model._meta.constraints:
        if isinstance(constraint, models.UniqueConstraint) and constraint.fields:
            yield tuple(constraint.fields), constraint.condition or Q()


class MergeContractMixin(models.Model):
    """What a record merge's collision means for a model whose rows point at the merged record.

    Shared by canonical and generated models. A
    move that lands a row on one the survivor already holds under a
    ``merge_identity`` set drops the merged record's copy as the same fact,
    asking :meth:`merge_collapse` first when the two differ beyond it; any other
    unique collision refuses the merge. ``merge_contract`` replaces the whole move
    for a model that retains collisions instead (statements). A hook returns
    ``NotImplemented`` for a reference the default mover should handle.
    """

    merge_contract: ClassVar[Callable[..., Any] | None] = None

    merge_identity: ClassVar[tuple[tuple[str, ...], ...]] = ()
    """Unique field sets whose equal rows state one fact."""

    merge_policies: ClassVar[Mapping[str, Any]] = {}
    """Foreign keys whose merge policy differs from what their ``on_delete`` implies, by field name.

    A lineage key (``converted_from``) ``KEEP``\\s pointing at the merged record
    as history, where its delete behaviour alone would move it.
    """

    class Meta:
        abstract = True

    def merge_collapse(self, duplicate: Any) -> bool:
        """Fold ``duplicate``'s state into this row before a merge drops it as the same fact.

        A merge asks only when the two differ beyond their identity and
        bookkeeping. Return whether the duplicate's state is now kept; the default
        keeps nothing, so the merge refuses rather than lose it.
        """

        del duplicate
        return False

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Require every ``merge_identity`` set to be a declared unique field set."""

        errors = super().check(**kwargs)
        declared = {fields for fields, _condition in unique_field_sets(cls)}
        for fields in cls.merge_identity:
            if tuple(fields) not in declared:
                errors.append(
                    checks.Error(
                        f"{cls._meta.label}.merge_identity names {fields}, which no unique field set declares.",
                        hint="Declare the identity as a unique field, unique_together or a UniqueConstraint.",
                        obj=cls,
                        id="angee.E037",
                    )
                )
        return errors


class _RecordRefMergeContract(MergeContractMixin):
    """Merge-facing contract implemented by the record-reference owner."""

    merge_policy: ClassVar[MergePolicy] = MergePolicy.BLOCK

    class Meta:
        abstract = True

    @classmethod
    def record_ref_field(cls) -> GenericForeignKey:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _LockedFold:
    records: tuple[models.Model, ...]
    options: Mapping[str, Any]



MERGE_REASON = "merge"
"""The system-context reason a merge's moves record."""

MERGE_LIMIT = 100
"""The most records one merge folds into its survivor."""

_AUDIT_FIELDS = frozenset(field.name for field in AuditMixin._meta.local_fields)
"""Columns recording who wrote a row, which an identity duplicate may differ in."""


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

    def pointing(self, target: type[models.Model], *, using: str | None = None) -> Exists:
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

        return self.model._base_manager.using(record._state.db).filter(**self.lookup(record))

    def retains(self, record: models.Model) -> bool:
        """Include earlier pointer versions when deciding whether evidence survives."""

        if self.rows(record).exists():
            return True
        return (
            model_history(self.model) is not None
            and self.model.history.using(record._state.db).filter(**self.lookup(record)).exists()
        )

    def move(self, rows: models.QuerySet[Any], *, survivor: models.Model) -> None:
        """Re-point ``rows`` to ``survivor``, dropping copies of identity rows it already holds.

        A collision on any other unique set refuses the merge: rows that merely
        share a slot, such as a rank, are different facts.
        A pointing model may own ``merge_contract(reference, rows, survivor=)``
        to retain identity collisions; dispatch precedes ordinary deletion.
        """

        if not self.holds(survivor):
            raise ValidationError(f"The record that is kept cannot hold {self.label}.")
        if issubclass(self.model, MergeContractMixin) and self.model.merge_contract is not None:
            if self.model.merge_contract(self, rows, survivor=survivor) is not NotImplemented:
                return
        using = rows.db
        target = self.lookup(survivor)
        held = self.model._base_manager.using(using).filter(**target)
        identity = _merge_identity(self.model)
        for fields, condition in unique_field_sets(self.model):
            if not set(self.columns) <= set(fields):
                continue
            others = [name for name in fields if name not in self.columns]
            other_keys = [self.model._meta.get_field(name).attname for name in others]
            twins = held.filter(condition).filter(**{name: OuterRef(name) for name in others})
            clashing = rows.filter(condition).filter(Exists(twins))
            if fields in identity:
                for duplicate in clashing:
                    kept = held.filter(condition).get(**{key: getattr(duplicate, key) for key in other_keys})
                    if _state(kept, fields) != _state(duplicate, fields) and not _collapse(kept, duplicate):
                        raise ValidationError(
                            f"Both records have {self.label} for the same {', '.join(fields)} with different "
                            "details; resolve that before merging."
                        )
                clashing.delete()
            elif clashing.exists():
                raise ValidationError(
                    f"Both records already have {self.label} with the same {', '.join(fields)}; "
                    "resolve that before merging."
                )
        try:
            with transaction.atomic(using=using):
                # A hierarchy's ``parent`` is re-pointed by its model's ``merge_contract``
                # above (the path and cycle owner), never by this bulk update.
                rows.update(**target, **auto_now_stamps(self.model, timezone.now()))
        except IntegrityError as error:
            # A rule the field sets above cannot express (an expression constraint)
            # still refuses the merge instead of failing it.
            raise ValidationError(
                f"Moving {self.label} to the record that is kept breaks one of their rules; "
                "resolve that before merging."
            ) from error


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

    def pointing(self, target: type[models.Model], *, using: str | None = None) -> Exists:
        """Return a condition on ``target`` rows: a row's key holds the target's value."""

        column = "pk" if self.field.target_field.primary_key else self.field.target_field.attname
        return Exists(self.model._base_manager.using(using).filter(**{self.field.attname: OuterRef(column)}))

    def holds(self, survivor: models.Model) -> bool:
        """Return whether ``survivor`` has a row in the model the key points at.

        A key on a multi-table child moves only onto a survivor that is that child.
        """

        related = self.field.related_model
        return (isinstance(survivor, related)
                or related._base_manager.using(survivor._state.db).filter(pk=survivor.pk).exists())


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

        model = canonical_model(type(record)) or type(record)._meta.concrete_model or type(record)
        content_type = ContentType.objects.db_manager(record._state.db).get_for_model(model)
        return {self.pointer.ct_field: content_type, self.pointer.fk_field: record.pk}

    def pointing(self, target: type[models.Model], *, using: str | None = None) -> Exists:
        """Return a condition on ``target`` rows: an edge row names the target's type and key."""

        object_id = self.model._meta.get_field(self.pointer.fk_field)
        model = canonical_model(target) or target._meta.concrete_model or target
        content_type = ContentType.objects.db_manager(using).get_for_model(model)
        return Exists(
            self.model._base_manager.using(using).filter(
                **{
                    self.pointer.ct_field: content_type,
                    self.pointer.fk_field: Cast(OuterRef("pk"), output_field=object_id),
                }
            )
        )


def _references(model: type[models.Model]) -> Iterator[Reference]:
    """Yield every way rows can point at records of ``model``, in a stable order.

    Foreign keys are the ones Django's delete collects (hidden ones included)
    across the model's multi-table parents and children, which share its key;
    parent links are the row itself. A link row's key to the record on the
    model's own many-to-many is the record's data and is left out. The other key
    of a many-to-many to the model itself is another record pointing at it,
    unless the relation is symmetrical, whose rows mirror the record's own.
    Polymorphic edges are
    every installed :class:`RecordRefMixin` model: some accept targets their
    schema does not declare, so declarations cannot narrow the scan.
    """

    tree = concrete_lineage(model)
    own_links = {
        (field.remote_field.through._meta.label, name)
        for owner in tree
        for field in owner._meta.local_many_to_many
        for name in (
            (field.m2m_field_name(), field.m2m_reverse_field_name())
            if field.remote_field.symmetrical
            else (field.m2m_field_name(),)
        )
    }
    seen: set[tuple[str, str]] = set()
    for owner in tree:
        for relation in get_candidate_relations_to_delete(owner._meta):
            key = (relation.related_model._meta.label, relation.field.name)
            if relation.parent_link or key in own_links or key in seen:
                continue
            seen.add(key)
            declared = (
                relation.related_model.merge_policies.get(relation.field.name)
                if issubclass(relation.related_model, MergeContractMixin) else None
            )
            if declared is not None:
                policy = MergePolicy(declared)
            elif relation.on_delete == DO_NOTHING:
                policy = MergePolicy.KEEP
            elif issubclass(relation.related_model, AppendOnlyModel):
                policy = MergePolicy.BLOCK
            else:
                policy = MergePolicy.MOVE
            yield ForeignKeyReference(relation.related_model, policy, relation.field)
    for edge in sorted(apps.get_models(), key=lambda candidate: candidate._meta.label):
        declared = edge.record_ref_field() if issubclass(edge, _RecordRefMergeContract) else None
        if declared is not None:
            yield EdgeReference(edge, edge.merge_policy, declared)
        for pointer in edge._meta.private_fields:
            if isinstance(pointer, GenericForeignKey) and pointer is not declared:
                # A pointer no edge declares has no policy and refuses the merge.
                yield EdgeReference(edge, MergePolicy.BLOCK, pointer)


@cache
def references(model: type[models.Model]) -> tuple[Reference, ...]:
    """Return this model's installed references, cached for the app registry."""

    return tuple(_references(model))


class MergeableMixin(models.Model):
    """Merge records of this model into a survivor through one template.

    :meth:`merge` locks the survivor and the merged records and authorizes the
    actor, then folds them (:func:`fold_records`): the fold refuses a record a
    :meth:`merge_blockers` condition, a stored relationship or a merge guard
    holds, :meth:`validate_merge` adds the model's own refusals,
    :meth:`merge_fields` carries values over, references move as named system
    work and :meth:`retire_merged` retires each merged record. A model adapts the
    merge by overriding those hooks and calling ``super()``.
    """

    retains_merged: ClassVar[bool] = False
    """Keep every merged record as a redirect, even one no other row names."""
    merge_moves_references: ClassVar[bool] = True
    """Whether a fold re-points ``MOVE`` references.

    ``False`` keeps them on the retired record, which readers resolve through
    :meth:`canonical`.
    """

    merged_into = models.ForeignKey(
        "self", null=True, blank=True, editable=False, on_delete=models.PROTECT, related_name="merged_from",
    )

    def canonical(self) -> Self:
        """Resolve the operational survivor; corrupt redirect cycles fail closed.

        A redirect is identity, not content: it resolves outside any actor scope,
        and the caller gates what it then reads from the survivor.
        """

        rows = type(self)._base_manager.using(self._state.db)
        seen = set()
        record = self
        while record.merged_into_id is not None:
            if record.pk in seen:
                raise ValidationError("A merge redirect contains a cycle.")
            seen.add(record.pk)
            record = rows.get(pk=record.merged_into_id)
        return record

    def retains(self, merged: Self) -> bool:
        """Whether retiring ``merged`` keeps it as a redirect rather than deleting it.

        The model's policy (:attr:`retains_merged`), a trash owner (removal is a
        redirect it can restore) or the record's own evidence
        (:meth:`~angee.base.models.AngeeModel.retained`) each retain it.
        """

        return self.retains_merged or isinstance(merged, TrashMixin) or merged.retained()

    SURVIVOR_PERMISSION: ClassVar[str] = "write"
    """The permission a merge needs on the record that is kept."""

    MERGED_PERMISSION: ClassVar[str] = "delete"
    """The permission a merge needs on each merged record."""

    class Meta:
        """Django model options for merge-only abstract inheritance."""

        abstract = True

    @classmethod
    def merge_blockers(cls, *, using: str | None = None) -> tuple[tuple[Q, str], ...]:
        """Return each condition on a merged record that refuses its merge, with its message.

        A verb and a batched projection share these: a row matching any of them
        cannot be merged. The default (:func:`reference_blockers`) refuses while a
        ``BLOCK`` reference points at the record; a model whose merged records
        stay as redirects with every reference in place has nothing such a
        reference could lose, so it has none. Overrides add the model's own
        conditions to ``super()``'s.
        """

        if cls.retains_merged and not cls.merge_moves_references:
            return ()
        return reference_blockers(cls, using=using)

    def merge(self, *, records: Sequence[str], **options: Any) -> Self:
        """Merge the records named by public id into this one and return it.

        A record the actor cannot read reads as gone. The whole merge commits or
        none of it does.
        """

        model = type(self)
        using = self._state.db
        actor = instance_actor(self)
        if not records:
            raise ValidationError("Choose the records to merge into this one.")
        if len(records) > MERGE_LIMIT:
            raise ValidationError(f"Merge at most {MERGE_LIMIT} records at once.")
        with transaction.atomic(using=using), actor_context(actor):
            found = instances_from_public_ids(
                model, records, queryset=model.objects.with_actor(actor).with_action("read").using(using),
            )
            gone = sorted(set(records) - set(found))
            merged_ids = sorted({row.pk for row in found.values()})
            if self.pk in merged_ids:
                raise ValidationError("A record cannot be merged into itself.")
            for row in sorted((self, *found.values()), key=lambda item: item.pk):
                row.lock_for_merge(using)
            rows = model._base_manager.db_manager(hints={"instance": self}).filter(
                pk__in=(self.pk, *merged_ids)
            ).order_by("pk")
            # The kept record only changes, so new rows may still refer to it; a
            # merged record goes, so nothing new may refer to it before the moves.
            locked = {row.pk: row.with_actor(actor) for row in rows.lock_if_supported(no_key=True)}
            list(rows.exclude(pk=self.pk).lock_if_supported())
            gone += [public for public, row in found.items() if row.pk not in locked]
            if gone or self.pk not in locked:
                raise ValidationError(f"These records no longer exist: {', '.join(sorted(gone)) or 'the kept one'}.")
            survivor = locked[self.pk]
            merged = [locked[pk] for pk in merged_ids]
            if not survivor.has_access(self.SURVIVOR_PERMISSION):
                raise PermissionDenied("You are not allowed to change the record that is kept.")
            if not all(record.has_access(self.MERGED_PERMISSION) for record in merged):
                raise PermissionDenied("You are not allowed to remove every record being merged.")

            self._fold(survivor, _LockedFold(tuple(merged), options), join=False)
        self.refresh_from_db()
        return self

    def fold(self, merged: Sequence[Self], **options: Any) -> Self:
        """Fold as named system work, joining the caller's changeset and locks.

        This entry cannot elevate an actor. It is for admitted merge hooks and
        system producers; user entrypoints use :meth:`merge`. A joined
        changeset must use the same database as these records.
        """

        if not is_sudo():
            raise PermissionDenied("A system fold requires a system boundary.")
        if not merged or len(merged) > MERGE_LIMIT:
            raise ValidationError(f"Choose between one and {MERGE_LIMIT} records to fold.")
        model = type(self)
        if any(type(row) is not model or row._state.db != self._state.db for row in merged):
            raise ValidationError("A fold requires records of the same model and database.")
        keys = {row.pk for row in merged}
        if self.pk in keys or self.pk is None or None in keys:
            raise ValidationError("A fold requires distinct saved records.")
        ids = sorted(keys)
        using = self._state.db
        with transaction.atomic(using=using):
            for row in sorted((self, *merged), key=lambda item: item.pk):
                row.lock_for_merge(using)
            rows = model._base_manager.db_manager(hints={"instance": self}).filter(
                pk__in=[self.pk, *ids]
            ).order_by("pk")
            locked = {row.pk: row for row in rows.lock_if_supported()}
            if len(locked) != len(ids) + 1:
                raise ValidationError("A record being folded no longer exists.")
            survivor = locked[self.pk]
            duplicates = [locked[pk] for pk in ids]

            self._fold(survivor, _LockedFold(tuple(duplicates), options), join=True)
        self.refresh_from_db()
        return self

    def _fold(self, survivor: Self, locked: _LockedFold, *, join: bool) -> None:
        """Run the one fold body against already locked rows."""

        merged = cast(tuple[Self, ...], locked.records)

        def carry() -> None:
            survivor.validate_merge(merged)
            survivor.merge_fields(merged, **locked.options)

        fold_records(
            survivor,
            merged,
            carry=carry,
            retire=survivor.retire_merged,
            blockers=type(survivor).merge_blockers(using=survivor._state.db),
            retained=all(survivor.retains(record) for record in merged),
            move_references=survivor.merge_moves_references,
            join=join,
        )

    def lock_for_merge(self, using: str) -> None:
        """Acquire domain locks that must precede the core record locks."""

        del using

    def validate_merge(self, merged: Sequence[Self]) -> None:
        """Only live operational roots merge; model hooks add domain refusals."""

        if any(
            row.merged_into_id is not None or (isinstance(row, TrashMixin) and row.is_trashed)
            for row in (self, *merged)
        ):
            raise ValidationError("Only canonical records can be merged.")

    def merge_fields(self, merged: Sequence[Self], **options: Any) -> None:
        """Carry values over from the merged records; the default keeps this record's own."""

        del merged
        if options:
            raise ValidationError(f"Unsupported merge options: {', '.join(sorted(options))}.")

    def retire_merged(self, merged: Self) -> None:
        """Retain a redirect where :meth:`retains` says so; otherwise delete through its lock."""

        if self.retains(merged):
            merged.merged_into = self
            merged.save(update_fields=("merged_into",))
            if isinstance(merged, TrashMixin):
                merged.trash(reason=MERGE_REASON)
            return
        rows = type(merged)._base_manager.db_manager(hints={"instance": merged}).all()
        locked = merged.lock_for_delete(queryset=rows)
        if locked is None:
            raise ValidationError(f"{merged} no longer exists.")
        try:
            locked.with_actor(instance_actor(merged)).delete()
        except (ProtectedError, RestrictedError) as error:
            raise ValidationError(f"{merged} is still referred to and cannot be removed.") from error


def reference_blockers(
    model: type[models.Model], *, using: str | None = None,
) -> tuple[tuple[Q, str], ...]:
    """Return a refusal condition on ``model`` rows per ``BLOCK`` reference, with its message.

    A row a ``BLOCK`` reference points at cannot be merged: the refusal every
    merge shares (:func:`fold_records`) and the default of
    :meth:`MergeableMixin.merge_blockers`.
    """

    return tuple(
        (Q(reference.pointing(model, using=using)), f"{reference.label} still refer to it.")
        for reference in references(model)
        if reference.policy == MergePolicy.BLOCK
    )


def fold_records(
    survivor: models.Model,
    merged: Sequence[models.Model],
    *,
    carry: Callable[[], object],
    retire: Callable[[Any], object],
    blockers: Sequence[tuple[Q, str]],
    retained: bool = False,
    move_references: bool = True,
    join: bool = False,
) -> None:
    """Fold ``merged`` into ``survivor``, a record of their model, in one merge changeset it is the origin of.

    The system work every record merge shares; it needs no actor. The caller has
    locked the records, in the transaction this runs in, and authorized an
    actor's merge. In order, the fold:

    - refuses before anything moves while a merged record matches a ``blockers``
      condition, stored relationships
      name it (unless it is ``retained`` as a redirect: retiring it would drop
      them) or an ``ANGEE_MERGE_GUARDS`` callable refuses it. A guard takes the
      record and returns a refusal message, or ``None``: an addon whose rows
      point at records in a form no reference reads (a resource ledger's public
      id) refuses there;
    - runs ``carry``: the caller's own refusals, then the values it carries over;
    - re-points every ``MOVE`` reference to ``survivor`` as named system work;
      a reference from ``survivor`` itself to a merged record refuses, since
      moving it would point the record at itself;
    - lets ``retire`` remove each merged record, in the caller's own context;
    - records the model as touched on the caller's changeset row.
    """

    model = type(survivor)
    using = survivor._state.db
    context = current_changeset() if join else None
    if context is not None and context._state.db != using:
        raise ValidationError("A fold must share the database of its changeset.")
    boundary = {} if context is not None else {
        "fresh": True, "activity": "merge", "origin": survivor,
    }
    with changeset(**boundary):
        with system_context(reason=MERGE_REASON):
            rows = model._base_manager.db_manager(hints={"instance": survivor}).filter(
                pk__in=[record.pk for record in merged]
            )
            for condition, message in blockers:
                if rows.filter(condition).exists():
                    raise ValidationError(message)
            guards = resolve_hooks("ANGEE_MERGE_GUARDS")
            for record in merged:
                if not retained and named_in_relationships(record):
                    raise ValidationError(f"{record} is shared or named in access rules; remove that first.")
                for guard in guards:
                    if refusal := guard(record):
                        raise ValidationError(refusal)
        carry()
        moving = [
            reference for reference in references(model)
            if move_references and reference.policy == MergePolicy.MOVE
        ]
        with system_context(reason=MERGE_REASON):
            if isinstance(survivor, MergeableMixin):
                # Redirects are identity, even when a container keeps its members.
                # Tracked updates make flattening reversible with this same merge.
                model._base_manager.using(using).filter(
                    merged_into_id__in=[record.pk for record in merged],
                ).update(merged_into=survivor)
            for record in merged:
                for reference in moving:
                    pointing = reference.rows(record)
                    if isinstance(survivor, reference.model) and pointing.filter(pk=survivor.pk).exists():
                        raise ValidationError(
                            f"{survivor} refers to {record} through {reference.label}; change that first."
                        )
                    if pointing.exists():
                        reference.move(pointing, survivor=survivor)
        for record in merged:
            retire(record)
        if (context := current_changeset(required=True, record=True)) is not None:
            context.touched.append(model._meta.label_lower)


def named_in_relationships(record: models.Model) -> bool:
    """Return whether stored REBAC relationships name any row retiring ``record`` deletes.

    The delete takes the record's multi-table parents and children with it, and
    REBAC cleans every identity those rows carry, as a resource or as a subject.
    Relations backed by fields follow a moved key; stored rows (a direct share,
    a membership) would go with the retired record.
    """

    model = type(record)
    rows = active_relationship_model().objects.db_manager(hints={"instance": record})
    for owner in concrete_lineage(model):
        typed, subject = bool(model_resource_type(owner)), model_can_resolve_subject(owner)
        if not (typed or subject):
            continue
        row = owner._base_manager.db_manager(hints={"instance": record}).filter(pk=record.pk).first()
        if row is None:
            continue
        refs = [to_object_ref(row)] if typed else []
        if subject:
            try:
                refs.append(to_subject_ref(row).object)
            except NoActorResolvedError:
                pass
        for ref in refs:
            if (
                rows.filter(resource_type=ref.resource_type, resource_id=ref.resource_id).exists()
                or rows.filter(subject_type=ref.resource_type, subject_id=ref.resource_id).exists()
            ):
                return True
    return False


def _state(row: models.Model, identity: tuple[str, ...]) -> dict[str, Any]:
    """Return what ``row`` states beyond its identity, pointer and bookkeeping columns.

    Two identity duplicates are one fact only while these agree; timestamps and
    the audit columns record who wrote the row, not what it states.
    """

    return {
        field.attname: getattr(row, field.attname)
        for field in row._meta.concrete_fields
        if not field.primary_key
        and field.name not in identity
        and field.name not in _AUDIT_FIELDS
        and not getattr(field, "auto_now", False)
        and not getattr(field, "auto_now_add", False)
    }


def _collapse(kept: models.Model, duplicate: models.Model) -> bool:
    """Let the pointing model fold a differing duplicate into the row a merge keeps; plain link rows cannot."""

    return isinstance(kept, MergeContractMixin) and kept.merge_collapse(duplicate)


def _merge_identity(model: type[models.Model]) -> frozenset[tuple[str, ...]]:
    """Return the unique field sets whose equal rows of ``model`` state one fact.

    A pointing model declares its own (``MergeContractMixin.merge_identity``); an
    auto-created many-to-many link is its pair. Any other model declares none.
    """

    if issubclass(model, MergeContractMixin):
        return frozenset(model.merge_identity)
    if model._meta.auto_created:
        return frozenset(tuple(fields) for fields in model._meta.unique_together)
    return frozenset()
