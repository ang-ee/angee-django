"""Changeset metadata; concrete classes are emitted by the composer."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any, ClassVar

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models, router, transaction
from django.utils import timezone
from rebac import (
    RebacMixin,
    RelationshipTuple,
    SubjectRef,
    backend,
    is_anonymous_actor,
    system_context,
    to_object_ref,
    write_relationships,
)
from rebac.resources import model_resource_type
from rebac.schema.walker import subject_allowed_by_relation

from angee.base.changesets import current_changeset
from angee.base.fields import StateField
from angee.base.mixins import SqidMixin
from angee.base.models import AngeeManager, AngeeUnscopedManager
from angee.base.refs import MergePolicy, RecordRefMixin


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

        type(self)._base_manager.filter(pk=self.pk).update(
            state=self.State.CLOSED,
            outcome=self.Outcome(outcome),
            touched=sorted(set(self.touched)),
            counts=self.counts,
        )
