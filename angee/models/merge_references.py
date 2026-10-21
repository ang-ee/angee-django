"""Native model reference, collision and retention contracts shared by record owners."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import Any, ClassVar

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import DO_NOTHING, Exists, OuterRef, Q
from django.db.models.deletion import get_candidate_relations_to_delete
from django.db.models.functions import Cast
from django.utils import timezone
from rebac import to_object_ref
from rebac.actors import model_can_resolve_subject, to_subject_ref
from rebac.errors import NoActorResolvedError
from rebac.field_backing import canonical_model
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type
from simple_history.utils import get_history_manager_for_model

from angee.lineage import concrete_lineage
from angee.models.append_only import AppendOnlyModel
from angee.models.audit import AuditMixin
from angee.models.identity import auto_now_stamps
from angee.models.query import model_history


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

        return self.model._base_manager.using(record._state.db).filter(**self.lookup(record))

    def retains(self, record: models.Model) -> bool:
        """Include earlier pointer versions when deciding whether evidence survives."""

        if self.rows(record).exists():
            return True
        return (
            model_history(self.model) is not None
            and get_history_manager_for_model(self.model).using(record._state.db).filter(**self.lookup(record)).exists()
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
            other_keys = []
            for name in others:
                field = self.model._meta.get_field(name)
                assert isinstance(field, models.Field)
                other_keys.append(field.attname)
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

    def pointing(self, target: type[models.Model]) -> Exists:
        """Return a condition on ``target`` rows: a row's key holds the target's value."""

        column = "pk" if self.field.target_field.primary_key else self.field.target_field.attname
        return Exists(self.model._base_manager.filter(**{self.field.attname: OuterRef(column)}))

    def holds(self, survivor: models.Model) -> bool:
        """Return whether ``survivor`` has a row in the model the key points at.

        A key on a multi-table child moves only onto a survivor that is that child.
        """

        related = self.field.related_model
        return (isinstance(survivor, related)
                or related._base_manager.using(survivor._state.db).filter(pk=survivor.pk).exists())


@dataclass(frozen=True, slots=True)
class EdgeReference(Reference):
    """Rows of a :class:`~angee.refs.RecordRefMixin` edge whose pointer names the record."""

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

    def pointing(self, target: type[models.Model]) -> Exists:
        """Return a condition on ``target`` rows: an edge row names the target's type and key."""

        object_id = self.model._meta.get_field(self.pointer.fk_field)
        assert isinstance(object_id, models.Field)
        model = canonical_model(target) or target._meta.concrete_model or target
        content_type = ContentType.objects.get_for_model(model)
        return Exists(
            self.model._base_manager.filter(
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
    own_links: set[tuple[str, str]] = set()
    for owner in tree:
        for field in owner._meta.local_many_to_many:
            through = field.remote_field.through
            assert through is not None
            names = (
                (field.m2m_field_name(), field.m2m_reverse_field_name())
                if field.remote_field.symmetrical else (field.m2m_field_name(),)
            )
            own_links.update((through._meta.label, name) for name in names)
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
        declared = None
        if issubclass(edge, _RecordRefMergeContract):
            declared = edge.record_ref_field()
            yield EdgeReference(edge, edge.merge_policy, declared)
        for pointer in edge._meta.private_fields:
            if isinstance(pointer, GenericForeignKey) and pointer is not declared:
                # A pointer no edge declares has no policy and refuses the merge.
                yield EdgeReference(edge, MergePolicy.BLOCK, pointer)


@cache
def references(model: type[models.Model]) -> tuple[Reference, ...]:
    """Return this model's installed references, cached for the app registry."""

    return tuple(_references(model))


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
