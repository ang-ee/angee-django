"""Changeset metadata; concrete classes are emitted by the composer."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from graphlib import CycleError, TopologicalSorter
from itertools import groupby
from typing import Any, ClassVar, cast

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, connections, models, transaction
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

from angee.actors import instance_actor
from angee.changesets import changeset, current_changeset, discard_changeset_claim
from angee.errors import CreationKeyConflict
from angee.fields import StateField
from angee.graphql.publishing import publish_change
from angee.models import (
    AngeeDataModel,
    AngeeManager,
    AngeeUnscopedManager,
    ChangesetQuerySet,
    HistoryMixin,
    SourceKind,
    SqidMixin,
    StatementSourceMixin,
    model_history,
    role_anchor,
)
from angee.refs import MergePolicy, RecordRefMixin
from angee.serialization import canonical_json, canonical_json_sha256, json_safe

ProvenanceRole = role_anchor("provenance/role")


class ChangesetManager(AngeeManager.from_queryset(ChangesetQuerySet)):  # type: ignore[misc]
    """Atomic idempotency claims on logical write boundaries."""

    def claim(self, scope: str, key: str, *, fingerprint: str) -> Any | None:
        """Claim ``(scope, key)`` on the fresh current boundary or return its completed match."""

        for name, value in (("scope", scope), ("key", key), ("fingerprint", fingerprint)):
            error_name = "idempotency_key" if name == "key" else name
            try:
                self.model._meta.get_field(name).clean(value, None)
            except ValidationError as error:
                raise ValidationError({error_name: error}) from error
            if not value.strip():
                raise ValidationError({error_name: f"A changeset {name} must not be blank."})
        if not connections[self.db].in_atomic_block:
            raise RuntimeError("Changeset claims require an outer transaction.atomic() boundary.")
        claimed = cast(Any, current_changeset(required=True))
        if not claimed._state.adding or claimed.pk is not None:
            raise RuntimeError("Claim an idempotency key before the boundary records any writes.")
        claimed.scope = scope
        claimed.key = key
        claimed.fingerprint = fingerprint
        try:
            with transaction.atomic(using=self.db):
                current_changeset(required=True, record=True)
        except IntegrityError:
            discard_changeset_claim(claimed)
            existing = self.model._base_manager.using(self.db).filter(scope=scope, key=key).first()
            if existing is None:
                raise
            if existing.fingerprint != fingerprint:
                raise CreationKeyConflict("This idempotency key was already used for different content.")
            return existing
        return None


class StatementPrincipal(StatementSourceMixin, AngeeDataModel):
    """Persisted statement-source principal keyed by its declared identity."""

    key = models.CharField(max_length=255, unique=True)
    statement_source_kind: ClassVar[SourceKind]

    @property
    def source_kind(self) -> SourceKind:
        return self.statement_source_kind

    class Meta:
        abstract = True


class Rule(StatementPrincipal):
    """Persisted principal for one registered rule or matcher, keyed by its name."""

    runtime = True
    sqid_prefix = "rul_"
    statement_source_kind = cast(SourceKind, SourceKind.RULE)

    class Meta(StatementPrincipal.Meta):
        abstract = True
        rebac_resource_type = "provenance/rule"


class Seed(StatementPrincipal):
    """Persisted principal for one seed load, keyed by that load's identity."""

    runtime = True
    sqid_prefix = "sed_"
    statement_source_kind = cast(SourceKind, SourceKind.SEED)

    class Meta(StatementPrincipal.Meta):
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
    objects = ChangesetManager()
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
        RUN = "run", "Run"

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
    scope = models.CharField(max_length=255, blank=True, editable=False)
    key = models.CharField(max_length=128, blank=True, editable=False)
    fingerprint = models.CharField(max_length=64, blank=True, editable=False)
    origin_content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    origin_object_id = models.CharField(max_length=1024, null=True, blank=True)
    origin = GenericForeignKey("origin_content_type", "origin_object_id")
    created_content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_object_id = models.CharField(max_length=1024, null=True, blank=True)
    created_row = GenericForeignKey("created_content_type", "created_object_id")
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
        constraints = (
            models.UniqueConstraint(
                fields=("scope", "key"),
                condition=~models.Q(scope="") & ~models.Q(key=""),
                name="%(app_label)s_%(class)s_claim",
            ),
        )

    @classmethod
    def record_ref_field(cls) -> GenericForeignKey:
        """Keep the authored origin as this edge's canonical record reference."""

        return cast(GenericForeignKey, cls._meta.get_field("origin"))

    def record_created(self, row: models.Model) -> None:
        """Attach the claimed create result inside the claim's transaction."""

        if not self.scope or not self.key:
            raise RuntimeError("Only a claimed changeset can record a created row.")
        self.created_row = row
        type(self)._base_manager.db_manager(hints={"instance": self}).filter(pk=self.pk).update(
            created_content_type_id=self.created_content_type_id,
            created_object_id=self.created_object_id,
        )

    def revertable(self, record: models.Model, *, actor: Any = None) -> bool:
        """Return whether ``record`` admits this boundary under its declared permission."""

        if not isinstance(record, HistoryMixin):
            return False
        return self.pk in record.revertable_changesets((self,), actor=actor)

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
        manager = type(self)._base_manager.db_manager(hints={"instance": self})
        alias = manager.db
        recording = inserting and current_changeset() is self
        with (
            transaction.atomic(using=alias),
            system_context(reason="changeset.record") if recording else nullcontext(),
        ):
            kwargs["using"] = alias
            super().save(*args, **kwargs)
            if inserting:
                write_relationships(
                    [
                        RelationshipTuple(
                            resource=to_object_ref(self),
                            relation=relation,
                            subject=subject,
                        )
                        for relation, subjects in self.proposed_relationships().items()
                        for subject in subjects
                    ]
                )

    def close(self, outcome: str) -> None:
        """Close persisted attribution as model bookkeeping, outside actor gates.

        Closing writes one row and never re-reads the boundary's history: the
        document digest belongs to export (:meth:`document`). The published
        change lets observers queue independent projections after commit.
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
        publish_change(self, action="update", update_fields=("state", "outcome", "touched", "counts"), broadcast=False)

    def history_rows(self) -> Any:
        """Yield touched owners and their native versions in deterministic order."""

        using = type(self)._base_manager.db_manager(hints={"instance": self}).db
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

        using = type(self)._base_manager.db_manager(hints={"instance": self}).db
        actor = instance_actor(self) or current_actor()
        if self.state != self.State.CLOSED or self.outcome != self.Outcome.SUCCESS:
            raise ValidationError("Only a successfully closed changeset can be reverted.")
        with (
            actor_context(actor) if actor is not None else nullcontext(),
            transaction.atomic(using=using),
            changeset(fresh=True, activity="revert", origin=self),
        ):
            histories = []
            subjects: dict[tuple[type[models.Model], Any], Any] = {}
            for model, _ in self.history_rows():
                history = model_history(model)
                if history is None:
                    raise ValidationError(f"{model._meta.label}: no history owner.")
                histories.append((model, history))
                for version in model.history.using(using).filter(
                    changeset_id=self.pk,
                ).order_by("history_id").iterator():
                    subject = version.history_object
                    subjects.setdefault((type(subject), subject.pk), subject)
            for subject in sorted(subjects.values(), key=lambda row: (row._meta.label_lower, str(row.pk))):
                try:
                    subject.lock_subject()
                except ObjectDoesNotExist:
                    # A deleted row has no operational lock to acquire; its retained
                    # snapshot remains the restoration input below.
                    pass
            states = []
            for model, history in histories:
                for first, last, previous in history.changeset_states(model, self, using=using):
                    history.authorize_revert(first, last, previous, actor)
                    states.append((history, first, last, previous))
            affected = {}
            with system_context(reason="changeset.revert"):
                for history, first, last, previous in states:
                    history.stage_revert(first, last, previous)
                for history, first, last, previous in self._revert_states(states):
                    for subject in history.revert_row(first, last, previous):
                        affected[(type(subject), subject.pk)] = subject
                for subject in affected.values():
                    if type(subject)._base_manager.using(using).filter(pk=subject.pk).exists():
                        for manager in subject.statement_managers():
                            manager.resolve()
                result = current_changeset(required=True, record=True)
        return result

    @staticmethod
    def _revert_states(states: list[Any]) -> Any:
        """Keep replay phases and order each phase by native FK dependencies.

        MTI and ordinary document lines use the same graph. Restorations precede
        their dependents; removing creations reverses that order. A cyclic
        restoration refuses atomically instead of depending on table names.
        """

        ordered = sorted(states, key=lambda state: state[0].revert_order(*state[1:]))
        for phase, entries in groupby(ordered, key=lambda state: state[0].revert_order(*state[1:])[0]):
            batch = list(entries)
            snapshots = [(state[3] or state[2]).instance for state in batch]
            identities = {
                (type(row), field.name, getattr(row, field.attname)): index
                for index, row in enumerate(snapshots)
                for field in row._meta.concrete_fields if field.unique
            }
            graph: dict[int, set[int]] = {index: set() for index in range(len(batch))}
            for index, row in enumerate(snapshots):
                for field in row._meta.concrete_fields:
                    if not field.is_relation or not field.many_to_one and not field.one_to_one:
                        continue
                    target = identities.get((field.related_model, field.target_field.name, getattr(row, field.attname)))
                    if target is not None and target != index:
                        if phase == 2:
                            graph[target].add(index)
                        elif batch[target][2].history_type == "-" or issubclass(type(row), field.related_model):
                            # A row saves against the restored state of a row of its own
                            # model it names (a hierarchy parent, a merge redirect).
                            # Other existing rows already satisfy each other's FKs.
                            graph[index].add(target)
            ordering = TopologicalSorter(graph)
            try:
                ordering.prepare()
            except CycleError as error:
                raise ValidationError("The changeset contains cyclic restoration dependencies.") from error
            while ordering.is_active():
                for index in sorted(ordering.get_ready()):
                    yield batch[index]
                    ordering.done(index)
