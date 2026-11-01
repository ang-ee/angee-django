"""Record merge: fold records into a survivor and re-point what refers to them.

A model composing :class:`MergeableMixin` merges through one template,
:meth:`MergeableMixin.merge`, called on the survivor. Every row that points at a
merged record is a :class:`Reference` with a :class:`MergePolicy`,
much as Django's ``on_delete`` decides what a delete does to such rows:

- a foreign key Django's delete collects moves to the survivor, except one on an
  append-only model, which can neither move nor outlive the record and so
  blocks, and a ``DO_NOTHING`` key (history), which keeps pointing at the record;
  the pointing model's ``merge_policies`` names a key whose policy differs;
- a polymorphic edge (:class:`~angee.base.models.refs.RecordRefMixin`) follows its
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

Every merge shares one system fold, :meth:`MergeableMixin._fold`: its refusals, the
moves (and the identity copies they drop) as named system work, and the merge
changeset. :meth:`MergeableMixin.merge` calls it once it has authorized the
actor, whose own delete retires each merged record; under an actor REBAC only
deletes and recreates an edge, which would cost a row its identity. A model's own
system verb that folds a duplicate has no actor to authorize and calls it directly.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import ExitStack
from typing import Any, ClassVar, Self, cast

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import ProtectedError, Q, RestrictedError
from rebac import PermissionDenied, actor_context, system_context
from rebac.actors import is_sudo

from angee.base.hooks import resolve_hooks
from angee.base.managers import MergeableUnscopedManager
from angee.base.models.base import AngeeModel
from angee.base.models.identity import instances_from_public_ids
from angee.base.models.merge_references import MergePolicy, named_in_relationships, references
from angee.base.models.trash import TrashMixin
from angee.base.permissions import instance_actor
from angee.base.signals import pre_merge

MERGE_REASON = "merge"
"""The system-context reason a merge's moves record."""

MERGE_LIMIT = 100
"""The most records one merge folds into its survivor."""

class MergeableMixin(AngeeModel):
    """Merge records of this model into a survivor through one template.

    :meth:`merge` locks the survivor and the merged records and authorizes the
    actor, then folds them (:meth:`MergeableMixin._fold`): the fold refuses a record a
    :meth:`merge_blockers` condition, a stored relationship or a merge guard
    holds, :meth:`validate_merge` adds the model's own refusals,
    :meth:`merge_fields` carries values over, references move as named system
    work and :meth:`retire_merged` retires each merged record. A model adapts the
    merge by overriding those hooks and calling ``super()``.
    """

    _angee_base: ClassVar[models.Manager[Self]] = MergeableUnscopedManager()
    _base_manager: ClassVar[MergeableUnscopedManager[Self]]

    retains_merged: ClassVar[bool] = False
    """Keep every merged record as a redirect, even one no other row names."""
    merge_moves_references: ClassVar[bool] = True
    """Whether a fold re-points ``MOVE`` references.

    ``False`` keeps them on the retired record, which readers resolve through
    :meth:`canonical`.
    """

    merged_into_id: object | None
    merged_into = models.ForeignKey(
        "self", null=True, blank=True, editable=False, on_delete=models.PROTECT, related_name="merged_from",
    )

    def canonical(self) -> Self:
        """Resolve the operational survivor; corrupt redirect cycles fail closed.

        A redirect is identity, not content: it resolves outside any actor scope,
        and the caller gates what it then reads from the survivor.
        """

        if self.merged_into_id is None:
            return self
        pk = type(self).canonical_ids((self.merged_into_id,), using=self._state.db).get(self.merged_into_id)
        if pk is None:
            raise type(self).DoesNotExist("The merge survivor no longer exists.")
        return type(self)._base_manager.using(self._state.db).get(pk=pk)

    @classmethod
    def canonical_ids(cls, ids: Iterable[Any], *, using: str | None = None) -> dict[Any, Any]:
        """Resolve existing identities in batches, without loading record bodies.

        Missing records are omitted; corrupt redirects fail closed, as on the
        instance read. One query per redirect depth serves the entire input.
        """

        pending = set(ids)
        redirects: dict[Any, Any] = {}
        roots = pending.copy()
        rows = cls._base_manager.using(using)
        while pending:
            found = dict(rows.filter(pk__in=pending).values_list("pk", "merged_into_id"))
            redirects.update(found)
            pending = {pk for pk in found.values() if pk is not None and pk not in redirects}
        result = {}
        for root in roots:
            pk, seen = root, set()
            while pk in redirects:
                if pk in seen:
                    raise ValidationError("A merge redirect contains a cycle.")
                seen.add(pk)
                target = redirects[pk]
                if target is None:
                    result[root] = pk
                    break
                pk = target
        return result

    def retains(self, merged: Self) -> bool:
        """Whether retiring ``merged`` keeps it as a redirect rather than deleting it.

        The model's policy (:attr:`retains_merged`), a trash owner (removal is a
        redirect it can restore) or the record's own evidence
        (:meth:`~angee.base.models.base.AngeeModel.retained`) each retain it.
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
    def merge_blockers(cls) -> tuple[tuple[Q, str], ...]:
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
        return reference_blockers(cls)

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
                model, records, queryset=model._default_manager.with_actor(actor).with_action("read").using(using),
            )
            gone = sorted(set(records) - set(found))
            merged_ids = sorted({row.pk for row in found.values()})
            if self.pk in merged_ids:
                raise ValidationError("A record cannot be merged into itself.")
            model.lock_merge_records((self, *found.values()))
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

            survivor._fold(merged, join=False, **options)
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
            model.lock_merge_records((self, *merged))
            rows = model._base_manager.db_manager(hints={"instance": self}).filter(
                pk__in=[self.pk, *ids]
            ).order_by("pk")
            locked = {row.pk: row for row in rows.lock_if_supported()}
            if len(locked) != len(ids) + 1:
                raise ValidationError("A record being folded no longer exists.")
            survivor = locked[self.pk]
            duplicates = [locked[pk] for pk in ids]

            survivor._fold(duplicates, join=True, **options)
        self.refresh_from_db()
        return self

    def _fold(self, merged: Sequence[Self], *, join: bool, **options: Any) -> None:
        """Fold already locked rows through this model's guards, carry and retirement."""

        survivor = self
        model = type(survivor)
        using = survivor._state.db
        with ExitStack() as contexts:
            pre_merge.send(sender=model, survivor=survivor, join=join, contexts=contexts)
            self.validate_merge(merged)
            self.merge_fields(merged, **options)
            moving = [
                reference for reference in references(cast(Any, model))
                if self.merge_moves_references and reference.policy == MergePolicy.MOVE
            ]
            with system_context(reason=MERGE_REASON):
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
                self.retire_merged(record)

    @classmethod
    def lock_merge_records(cls, records: Sequence[Self]) -> None:
        """Acquire domain locks for the complete batch before core record locks."""

        for row in sorted(records, key=lambda item: item.pk):
            row.lock_for_merge()

    def lock_for_merge(self) -> None:
        """Acquire domain locks that must precede the core record locks."""

    def validate_merge(self, merged: Sequence[Self]) -> None:
        """Read canonical, reference and addon refusals without mutating records."""

        if any(
            row.merged_into_id is not None or (isinstance(row, TrashMixin) and row.is_trashed)
            for row in (self, *merged)
        ):
            raise ValidationError("Only canonical records can be merged.")

        retained = all(self.retains(record) for record in merged)
        with system_context(reason=MERGE_REASON):
            rows = type(self)._base_manager.db_manager(hints={"instance": self}).filter(
                pk__in=[record.pk for record in merged],
            )
            for condition, message in type(self).merge_blockers():
                if rows.filter(condition).exists():
                    raise ValidationError(message)
            guards = resolve_hooks("ANGEE_MERGE_GUARDS")
            for record in merged:
                if not retained and named_in_relationships(record):
                    raise ValidationError(f"{record} is shared or named in access rules; remove that first.")
                for guard in guards:
                    if refusal := guard(record):
                        raise ValidationError(refusal)

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


def reference_blockers(model: type[models.Model]) -> tuple[tuple[Q, str], ...]:
    """Return a refusal condition on ``model`` rows per ``BLOCK`` reference, with its message.

    A row a ``BLOCK`` reference points at cannot be merged: the refusal every
    merge shares (:meth:`MergeableMixin._fold`) and the default of
    :meth:`MergeableMixin.merge_blockers`.
    """

    return tuple(
        (Q(reference.pointing(model)), f"{reference.label} still refer to it.")
        for reference in references(cast(Any, model))
        if reference.policy == MergePolicy.BLOCK
    )
