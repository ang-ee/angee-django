"""Angee provenance entities and statement-principal declarations."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from typing import Any, ClassVar, cast

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from rebac import (
    RebacMixin,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    app_settings,
    backend,
    current_actor,
    database_context,
    is_anonymous_actor,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.actors import current_sudo_reason
from rebac.resources import model_resource_type
from rebac.schema.walker import subject_allowed_by_relation

from angee.base.fields.state import StateField
from angee.base.models.identity import SqidMixin
from angee.base.models.merge_references import MergePolicy
from angee.base.serialization import json_safe
from angee.base.signals import bulk_updated
from angee.iam.managers import ProtectedUnscopedManager
from angee.iam.models.refs import ProtectedRecordRefMixin
from angee.iam.permissions import actor_user_id, instance_actor
from angee.provenance.context import actor_context as provenance_actor_context
from angee.provenance.context import is_current_changeset
from angee.provenance.contrib.angee.exceptions import ChangesetRequired
from angee.provenance.contrib.angee.history import HistoryMixin, ModelHistory
from angee.provenance.contrib.angee.policy import angee_policy
from angee.provenance.engine.base import AbstractChangeset
from angee.provenance.engine.replay import ordered_states
from angee.provenance.managers import ChangesetManager
from angee.provenance.querysets import model_history

MACHINE_REASON = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)+")


class Changeset(AbstractChangeset, ProtectedRecordRefMixin, SqidMixin, RebacMixin, concrete=True):
    """One logical write's attribution and touched-model index; never historized.

    ``actor_ref`` stores the native SubjectRef wire representation in the historical
    ``actor`` database column. Its relationship
    tuple is a permission projection, inserted atomically through REBAC's trusted
    proposed-relationships contract. History user stamps derive from the column.
    """

    required_context_error: ClassVar[type[Exception]] = ChangesetRequired

    sqid_prefix = "cst_"
    merge_policy: ClassVar[MergePolicy] = MergePolicy.KEEP
    objects = ChangesetManager()
    _angee_base = ProtectedUnscopedManager()

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

    actor_ref = models.TextField(db_column="actor", blank=True, editable=False)
    action = StateField(choices_enum=Action, default=Action.UPDATE)
    activity = StateField(choices_enum=Activity, default=Activity.MANUAL)
    state = StateField(choices_enum=State, default=State.OPEN)
    outcome = StateField(choices_enum=Outcome, null=True, blank=True)

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
    def ambient_attribution(cls) -> tuple[str, str]:
        """Project REBAC's ambient facts without changing its actor or authority."""

        actor = current_actor()
        return str(actor) if actor is not None else "", current_sudo_reason() or ""

    @classmethod
    def context_required(cls) -> bool:
        """Retain REBAC's configured strict posture for captured Angee writes."""

        return bool(app_settings.REBAC_STRICT_MODE)

    def history_user(self) -> models.Model | None:
        """Construct the native history user stamp from persisted attribution."""

        actor = SubjectRef.parse(self.actor_ref) if self.actor_ref else None
        user_id = actor_user_id(actor)
        return get_user_model()(pk=user_id) if user_id is not None else None

    def offset_in(self, queryset: models.QuerySet[Any]) -> int:
        """Position in a scoped changeset sequence, using this owner's newest-first order."""

        return queryset.filter(
            models.Q(recorded_at__gt=self.recorded_at) | models.Q(recorded_at=self.recorded_at, pk__gt=self.pk)
        ).count()

    @property
    def reason_display(self) -> str:
        """The reason as user text: a human sentence passes through, a machine reason is silent.

        Automated changes record the ``system_context(reason=...)`` code (``integrate.stream.advance``)
        as their reason; the action and activity already say what happened, so the code never
        reaches a user. Every presentation (API, timeline) reads this, not ``change_reason``.
        """

        reason = self.change_reason.strip()
        return "" if MACHINE_REASON.fullmatch(reason) else reason

    @classmethod
    def record_ref_field(cls) -> GenericForeignKey:
        """Keep the authored origin as this edge's canonical record reference."""

        return cast(GenericForeignKey, cls._meta.get_field("origin"))

    def revertable(self, record: models.Model, *, actor: Any = None) -> bool:
        """Return whether ``record`` admits this boundary under its declared permission."""

        if not isinstance(record, HistoryMixin):
            return False
        return self.pk in record.revertable_changesets((self,), actor=actor)

    def proposed_relationships(self, *, using: str | None = None) -> Mapping[str, tuple[SubjectRef, ...]]:
        """Promise the actor tuple that this owner persists immediately after insert."""

        if not self.actor_ref:
            return {}
        actor = SubjectRef.parse(self.actor_ref)
        if is_anonymous_actor(actor):
            return {}
        alias = using or self._state.db or router.db_for_write(type(self), instance=self)
        with database_context(alias):
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
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        recording = inserting and is_current_changeset(self)
        with (
            database_context(alias),
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
                        for relation, subjects in self.proposed_relationships(using=alias).items()
                        for subject in subjects
                    ]
                )

    def close(self, outcome: str) -> None:
        """Close persisted attribution as model bookkeeping, outside actor gates.

        Closing writes one row and never re-reads the boundary's history: the
        document digest belongs to export (:meth:`document`). The published
        change lets observers queue independent projections after commit.
        """

        super().close(self.Outcome(outcome))
        self.state = self.State(self.State.CLOSED)
        self.outcome = self.Outcome(outcome)
        bulk_updated.send(
            sender=type(self),
            instances=(self,),
            update_fields=("state", "outcome", "touched", "counts"),
            using=self._state.db,
        )

    def authorize_document(self, actor: Any) -> None:
        """Lossless retained export requires the existing trusted system boundary."""
        angee_policy().authorize(actor, "export", self)

    @staticmethod
    def document_value(value: Any) -> Any:
        """Preserve the historical Angee cold-store byte representation."""
        return json_safe(value)

    @contextmanager
    def revert_context(self, actor: Any, *, using: str) -> Iterator[None]:
        with (
            database_context(using),
            actor_context(actor) if actor is not None else nullcontext(),
            provenance_actor_context(str(to_subject_ref(actor)) if actor is not None else None),
        ):
            yield

    def revert_application(self) -> Any:
        return system_context(reason="changeset.revert")

    def revert(self, *, actor: Any = None) -> Any:
        """Replay native retained states through each record's protected policy."""
        for model, _rows in self.history_rows():
            if not isinstance(model_history(model), ModelHistory):
                raise ValidationError(f"{model._meta.label}: no record history policy owner.")
        return super().revert(actor=actor if actor is not None else instance_actor(self) or current_actor())

    # Historical private import/caller compatibility; native replay owns ordering.
    _revert_states = staticmethod(ordered_states)
