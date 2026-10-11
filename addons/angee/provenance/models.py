"""Changeset metadata; concrete classes are emitted by the composer."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any, ClassVar, cast

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.utils import timezone
from rebac import (
    RebacMixin,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    backend,
    current_actor,
    is_anonymous_actor,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.actors import is_sudo
from rebac.errors import PermissionDenied
from rebac.resources import model_resource_type
from rebac.schema.walker import subject_allowed_by_relation

from angee.base.actors import instance_actor
from angee.base.changesets import changeset, current_changeset
from angee.base.fields import StateField
from angee.base.merge import named_in_relationships, references
from angee.base.mixins import (
    SourceKind,
    SqidMixin,
    Statement,
    StatementPair,
    StatementsMixin,
    StatementSourceMixin,
    TrashMixin,
)
from angee.base.models import AngeeDataModel, AngeeManager, AngeeUnscopedManager
from angee.base.querysets import model_history
from angee.base.refs import MergePolicy, RecordRefMixin
from angee.base.scoping import lock_if_supported
from angee.base.serialization import canonical_json, canonical_json_sha256, json_safe


class StatementPrincipalManager(AngeeManager):
    """Resolve a producer's durable identity without creating duplicate principals."""

    def for_key(self, key: str) -> Any:
        """Return or create the principal named by the owning producer's key."""

        if not key:
            raise ValueError("A statement principal requires a nonempty key.")
        return self.get_or_create(key=key)[0]


class Rule(StatementSourceMixin, AngeeDataModel):
    """Persisted principal for one registered rule or matcher, keyed by its name."""

    runtime = True
    sqid_prefix = "rul_"
    key = models.CharField(max_length=255, unique=True)
    objects = StatementPrincipalManager()

    @property
    def source_kind(self) -> SourceKind:
        return cast(SourceKind, SourceKind.RULE)

    class Meta:
        abstract = True
        rebac_resource_type = "provenance/rule"


class Seed(StatementSourceMixin, AngeeDataModel):
    """Persisted principal for one seed load, keyed by that load's identity."""

    runtime = True
    sqid_prefix = "sed_"
    key = models.CharField(max_length=255, unique=True)
    objects = StatementPrincipalManager()

    @property
    def source_kind(self) -> SourceKind:
        return cast(SourceKind, SourceKind.SEED)

    class Meta:
        abstract = True
        rebac_resource_type = "provenance/seed"


class Changeset(RecordRefMixin, SqidMixin, RebacMixin):
    """One logical write's attribution and touched-model index; never historized.

    ``actor`` is the native SubjectRef wire representation. Its relationship
    tuple is a permission projection, inserted atomically through REBAC's trusted
    proposed-relationships contract. History user stamps derive from the column.
    """

    runtime = True
    sqid_prefix = "cst_"
    merge_policy: ClassVar[MergePolicy] = MergePolicy.KEEP
    objects = AngeeManager()
    _angee_base = AngeeUnscopedManager()

    class Action(models.TextChoices):
        CREATE = "C", "Create"
        UPDATE = "U", "Update"
        DELETE = "D", "Delete"

    class Activity(models.TextChoices):
        MANUAL = "manual", "Manual"
        PULL = "pull", "Pull"
        PUSH = "push", "Push"
        IMPORT = "import", "Import"
        MERGE = "merge", "Merge"
        REVERT = "revert", "Revert"
        SEED = "seed", "Seed"
        SYSTEM = "system", "System"

    class State(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"

    actor = models.TextField(blank=True, editable=False)
    action = StateField(choices_enum=Action, default=Action.UPDATE)
    activity = StateField(choices_enum=Activity, default=Activity.MANUAL)
    change_reason = models.TextField(blank=True)
    correlation_id = models.CharField(max_length=255, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=255, blank=True, db_index=True)
    origin_content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    origin_object_id = models.CharField(max_length=1024, null=True, blank=True)
    origin = GenericForeignKey("origin_content_type", "origin_object_id")
    occurred_at = models.DateTimeField(default=timezone.now)
    recorded_at = models.DateTimeField(default=timezone.now, editable=False)
    state = StateField(choices_enum=State, default=State.OPEN)
    outcome = StateField(choices_enum=Outcome, null=True, blank=True)
    hash = models.CharField(max_length=64, blank=True, editable=False)
    counts = models.JSONField(default=dict, blank=True, editable=False)
    touched = models.JSONField(default=list, blank=True, editable=False)

    class Meta:
        abstract = True
        base_manager_name = "_angee_base"
        swappable = "ANGEE_CHANGESET_MODEL"
        rebac_resource_type = "provenance/changeset"
        ordering = ("-recorded_at", "-pk")
        indexes = (
            models.Index(fields=("-recorded_at", "-id")),
            models.Index(fields=("origin_content_type", "origin_object_id")),
        )

    def proposed_relationships(self, *, using: str | None = None) -> Mapping[str, tuple[SubjectRef, ...]]:
        """Promise the actor tuple that this owner persists immediately after insert."""

        if not self.actor:
            return {}
        actor = SubjectRef.parse(self.actor)
        if is_anonymous_actor(actor):
            return {}
        definition = backend().schema().get_definition(model_resource_type(type(self)) or "")
        if definition is None:
            return {}
        return {
            relation.name: (actor,)
            for relation in sorted(definition.relations, key=lambda relation: relation.name)
            if relation.backing is None and subject_allowed_by_relation(relation, actor, caveat_name="")
        }

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist attribution and its native actor fact in the insert transaction.

        Recording the ambient boundary is system bookkeeping, like closure;
        direct Changeset writes retain their normal actor admission. The actor
        column is already captured before this local storage boundary opens.
        """

        inserting = self._state.adding
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        recording = inserting and current_changeset() is self
        with (
            transaction.atomic(using=using),
            system_context(reason="changeset.record") if recording else nullcontext(),
        ):
            super().save(*args, **kwargs)
            if inserting:
                write_relationships(
                    [
                        RelationshipTuple(
                            resource=to_object_ref(self),
                            relation=relation,
                            subject=subject,
                        )
                        for relation, subjects in self.proposed_relationships(using=using).items()
                        for subject in subjects
                    ]
                )

    def close(self, outcome: str) -> None:
        """Close persisted attribution as model bookkeeping, outside actor gates."""

        self.state = self.State(self.State.CLOSED)
        self.outcome = self.Outcome(outcome)
        self.touched = sorted(set(self.touched))
        with system_context(reason="changeset.close"):
            self.hash = canonical_json_sha256(self._document_values())
        type(self)._base_manager.filter(pk=self.pk).update(
            state=self.state,
            outcome=self.outcome,
            touched=self.touched,
            counts=self.counts,
            hash=self.hash,
        )

    def history_rows(self) -> Any:
        """Yield touched owners and their native versions in deterministic order."""

        using = self._state.db or router.db_for_read(type(self), instance=self)
        for label in sorted(set(self.touched)):
            model = apps.get_model(label)
            if model_history(model) is None:
                raise ValidationError(f"{label}: the history owner is unavailable.")
            yield model, model.history.using(using).filter(changeset_id=self.pk).order_by("history_id")

    def _document_values(self) -> dict[str, Any]:
        fields = {
            field.name: field.value_from_object(self) for field in self._meta.concrete_fields if field.name != "hash"
        }
        rows = {
            model._meta.label_lower: [
                {field.name: field.value_from_object(row) for field in row._meta.concrete_fields} for row in history
            ]
            for model, history in self.history_rows()
        }
        return json_safe({"changeset": fields, "history": rows})

    def document(self) -> str:
        """Canonical cold-store document, excluding its own digest.

        This lossless seam requires a trusted system boundary. User-facing export
        must authorize and redact through each touched subject, like history.
        No sink, router, pruning or external write is performed here.
        """

        if not is_sudo():
            raise PermissionDenied("Lossless history export requires a system boundary.")
        return canonical_json(self._document_values())

    def revert(self) -> Any:
        """Undo the first-to-last boundary atomically as a new changeset.

        Native version IDs settle equal timestamps. Statements retain their
        identities, including merge collisions. Canonical aspect fields rederive
        after restoration. HTTP effects remain remote; ordinary local-change
        detection queues the corrected projection on the next sync.
        """

        using = self._state.db or router.db_for_write(type(self), instance=self)
        actor = instance_actor(self) or current_actor()
        if self.state != self.State.CLOSED or self.outcome != self.Outcome.SUCCESS:
            raise ValidationError("Only a successfully closed changeset can be reverted.")
        with (
            actor_context(actor) if actor is not None else nullcontext(),
            transaction.atomic(using=using),
            changeset(fresh=True, activity="revert", origin=self),
        ):
            owners = sorted(
                self.history_rows(),
                key=lambda owner: (
                    issubclass(owner[0], Statement),
                    len(owner[0]._meta.get_parent_list()),
                    owner[0]._meta.label_lower,
                ),
            )
            states = []
            for model, versions in owners:
                keys = versions.values_list(model._meta.pk.attname, flat=True)
                list(lock_if_supported(model._base_manager.using(using).filter(pk__in=keys).order_by("pk")))
                history = model_history(model)
                if history is None:
                    raise ValidationError(f"{model._meta.label}: no history owner.")
                for first, last, previous in history.changeset_states(model, self, using=using):
                    row = model._base_manager.using(using).filter(pk=getattr(first, model._meta.pk.attname)).first()
                    subject = row or last.instance
                    if issubclass(model, Statement):
                        # Every pre/post subject needs admission, including a
                        # statement moved by a merge. Deleted subjects admit
                        # after their native restoration, before stance replay.
                        subject_ids = {version.subject_id for version in (first, last, previous) if version is not None}
                        if not is_sudo():
                            for subject in model.statement_subject_model._base_manager.using(using).filter(
                                pk__in=subject_ids
                            ):
                                subject.require_statement_write(actor)
                    elif isinstance(subject, StatementPair):
                        if not is_sudo():
                            subject.require_statement_write(to_subject_ref(actor))
                    elif row is not None:
                        subject.require_access("write", actor=actor)
                    states.append((model, first, last, previous, row))

            # Parents restore before children. Only local fields are replayed on
            # MTI children, so a flattened child cannot overwrite its parent.
            subjects = {}
            for model, first, last, previous, row in sorted(
                states,
                key=lambda state: (len(state[0]._meta.get_parent_list()), state[0]._meta.label_lower),
            ):
                if issubclass(model, Statement) or previous is None:
                    continue
                if previous.history_type == "-":
                    continue
                fields = [field.name for field in model._meta.local_concrete_fields if not field.primary_key]
                # as_of is date-based; preserve the exact native predecessor at a tie.
                if (
                    model.history.using(using)
                    .filter(
                        **{model._meta.pk.attname: getattr(previous, model._meta.pk.attname)},
                        history_date=previous.history_date,
                    )
                    .exclude(history_id=previous.history_id)
                    .exists()
                ):
                    restored = previous.instance
                else:
                    restored = (
                        model.history.db_manager(using)
                        .as_of(previous.history_date)
                        .get(pk=getattr(previous, model._meta.pk.attname))
                    )
                if row is None:
                    for parent in model._meta.get_parent_list():
                        stored = parent._base_manager.using(using).filter(pk=restored.pk).first()
                        if stored is None:
                            raise ValidationError(f"{parent._meta.label} {restored.pk}: the MTI parent is unavailable.")
                        for field in parent._meta.local_concrete_fields:
                            if not field.primary_key:
                                setattr(restored, field.attname, getattr(stored, field.attname))
                delta = last.diff_against(previous, included_fields=fields)
                changed = set(delta.changed_fields)
                target = row or restored
                for name in changed:
                    field = model._meta.get_field(name)
                    setattr(target, field.attname, getattr(previous, field.attname))
                with system_context(reason="changeset.revert"):
                    if row is not None and isinstance(target, TrashMixin) and "is_trashed" in changed:
                        # Use the removal owner, retaining its lock and stamps.
                        target.refresh_from_db(using=using)
                        if previous.is_trashed:
                            target.trash(reason=previous.trash_reason, using=using)
                        else:
                            target.restore(using=using)
                        changed.difference_update(target.TRASH_FIELDS)
                        for name in changed:
                            field = model._meta.get_field(name)
                            setattr(target, field.attname, getattr(previous, field.attname))
                    if row is None:
                        restored.save(using=using)
                        target = restored
                    elif changed:
                        target.save(using=using, update_fields=sorted(changed))
                if isinstance(target, StatementPair):
                    if not is_sudo():
                        target.require_statement_write(to_subject_ref(actor))
                else:
                    target.require_access("write", actor=actor)
                if isinstance(target, StatementsMixin):
                    subjects[(model, target.pk)] = target

            for model, _ in owners:
                if issubclass(model, Statement):
                    for subject in model.objects.db_manager(using).revert(self, resolve=False):
                        subjects[(type(subject), subject.pk)] = subject
            with system_context(reason="changeset.revert"):
                # Remove creations after restoring references, children first.
                removed = set()
                for model, first, _, previous, row in sorted(
                    states,
                    key=lambda state: (-len(state[0]._meta.get_parent_list()), state[0]._meta.label_lower),
                ):
                    if (
                        issubclass(model, Statement)
                        or (previous is not None and previous.history_type != "-")
                        or row is None
                    ):
                        continue
                    if isinstance(row, TrashMixin):
                        owner = row._meta.get_field("is_trashed").model
                        key = (owner, row.pk)
                        if key not in removed:
                            target = owner._base_manager.using(using).get(pk=row.pk)
                            if actor is not None:
                                target.with_actor(actor)
                            if not target.is_trashed:
                                target.trash(reason=f"Revert changeset {self.pk}", using=using)
                            removed.add(key)
                    else:
                        if named_in_relationships(row) or any(
                            reference.retains(row) for reference in references(model)
                        ):
                            raise ValidationError(
                                f"{model._meta.label} {row.pk}: retained rows prevent removal without a trash owner."
                            )
                        removed.add((model, row.pk))
                        row.delete(using=using)
                for (model, pk), subject in subjects.items():
                    if (model, pk) in removed and not issubclass(model, TrashMixin):
                        continue
                    for manager in subject.statement_managers():
                        manager.resolve()
                result = current_changeset(required=True, record=True)
        return result
