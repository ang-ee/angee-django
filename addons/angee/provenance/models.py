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
    write_relationships,
)
from rebac.actors import is_sudo
from rebac.errors import PermissionDenied
from rebac.resources import model_resource_type
from rebac.schema.walker import subject_allowed_by_relation

from angee.base.actors import instance_actor
from angee.base.changesets import changeset, current_changeset
from angee.base.fields import StateField
from angee.base.mixins import (
    SourceKind,
    SqidMixin,
    StatementSourceMixin,
)
from angee.base.models import AngeeDataModel, AngeeManager, AngeeUnscopedManager, role_anchor
from angee.base.querysets import model_history
from angee.base.refs import MergePolicy, RecordRefMixin
from angee.base.serialization import canonical_json, canonical_json_sha256, json_safe

ProvenanceRole = role_anchor("provenance/role")


class StatementPrincipalManager(AngeeManager):
    """Resolve a source's durable identity without creating duplicate principals."""

    def for_key(self, key: str) -> Any:
        """Return the principal bootstrapped by its owning resource declaration."""

        if not key:
            raise ValueError("A statement principal requires a nonempty key.")
        return self.get(key=key)


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
        """Close persisted attribution as model bookkeeping, outside actor gates.

        Closing writes one row and never re-reads the boundary's history: the
        document digest belongs to export (:meth:`document`).
        """

        self.state = self.State(self.State.CLOSED)
        self.outcome = self.Outcome(outcome)
        self.touched = sorted(set(self.touched))
        type(self)._base_manager.filter(pk=self.pk).update(
            state=self.state,
            outcome=self.outcome,
            touched=self.touched,
            counts=self.counts,
        )

    def history_rows(self) -> Any:
        """Yield touched owners and their native versions in deterministic order."""

        using = self._state.db or router.db_for_read(type(self), instance=self)
        for label in sorted(set(self.touched)):
            model = apps.get_model(label)
            if model_history(model) is None:
                raise ValidationError(f"{label}: the history owner is unavailable.")
            yield model, model.history.using(using).filter(changeset_id=self.pk).order_by("history_id")

    def _document_chunks(self) -> Any:
        fields = {
            field.name: field.value_from_object(self) for field in self._meta.concrete_fields if field.name != "hash"
        }
        yield '{"changeset":' + canonical_json(json_safe(fields)) + ',"history":{'
        separator = ""
        for model, history in self.history_rows():
            yield separator + canonical_json(model._meta.label_lower) + ":["
            row_separator = ""
            for row in history.iterator():
                values = {field.name: field.value_from_object(row) for field in row._meta.concrete_fields}
                yield row_separator + canonical_json(json_safe(values))
                row_separator = ","
            yield "]"
            separator = ","
        yield "}}"

    def document(self) -> str:
        """Canonical cold-store document of a closed changeset, excluding its own digest.

        Export owns the digest: the first export records ``hash`` through the one
        hash owner, and later exports reproduce it. This lossless seam requires a
        trusted system boundary. User-facing export must authorize and redact
        through each touched subject, like history. No sink, router, pruning or
        external write is performed here.
        """

        if not is_sudo():
            raise PermissionDenied("Lossless history export requires a system boundary.")
        if self.state != self.State.CLOSED:
            raise ValidationError("Only a closed changeset has a document.")
        text = "".join(self._document_chunks())
        digest = canonical_json_sha256(chunks=(text,))
        if self.hash != digest:
            self.hash = digest
            type(self)._base_manager.filter(pk=self.pk).update(hash=digest)
        return text

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
            states = []
            for model, _ in self.history_rows():
                history = model_history(model)
                if history is None:
                    raise ValidationError(f"{model._meta.label}: no history owner.")
                # Acquire subject-owned locks before checking later touches.
                for first in model.history.using(using).filter(changeset_id=self.pk).order_by("history_id").iterator():
                    history.lock_revert(first, first, None)
                for first, last, previous in history.changeset_states(model, self, using=using):
                    history.lock_revert(first, last, previous)
                    history.authorize_revert(first, last, previous, actor)
                    states.append((history, first, last, previous))
            subjects = {}
            with system_context(reason="changeset.revert"):
                for history, first, last, previous in states:
                    history.stage_revert(first, last, previous)
                for history, first, last, previous in sorted(
                    states,
                    key=lambda state: state[0].revert_order(*state[1:]),
                ):
                    for subject in history.revert_row(first, last, previous):
                        subjects[(type(subject), subject.pk)] = subject
                for subject in subjects.values():
                    if type(subject)._base_manager.using(using).filter(pk=subject.pk).exists():
                        for manager in subject.statement_managers():
                            manager.resolve()
                result = current_changeset(required=True, record=True)
        return result
