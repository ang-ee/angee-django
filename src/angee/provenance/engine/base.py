"""Importable abstract storage contract; importing it never registers default storage."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.utils import timezone
from simple_history.utils import get_history_manager_for_model

from angee.provenance.context import (
    actor_context,
    actor_reference,
    changeset,
    changeset_model_label,
    current_actor,
    current_changeset,
    current_reason,
)
from angee.provenance.querysets import model_history

from .managers import ChangesetManager
from .policy import get_policy
from .replay import ordered_states
from .serialization import canonical_json, json_safe


class ChangesetAttribution(models.Model):
    """Retained last-writer attribution usable without historical source metadata."""

    changeset = models.ForeignKey(
        changeset_model_label(),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        abstract = True


class AbstractChangeset(models.Model):
    _provenance_fresh: bool = False
    _provenance_actor: object = None

    class Action(models.TextChoices):
        CREATE = "C", "Create"
        UPDATE = "U", "Update"
        DELETE = "D", "Delete"

    class Activity(models.TextChoices):
        MANUAL = "manual", "Manual"
        REVERT = "revert", "Revert"
        IMPORT = "import", "Import"
        SYSTEM = "system", "System"

    class State(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"

    actor_ref = models.TextField(blank=True, editable=False)
    action = models.CharField(max_length=32, default="U")
    activity = models.CharField(max_length=32, default="manual")
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
    state = models.CharField(max_length=8, default="open")
    outcome = models.CharField(max_length=8, null=True, blank=True)
    hash = models.CharField(max_length=64, blank=True, editable=False)
    counts = models.JSONField(default=dict, blank=True, editable=False)
    touched = models.JSONField(default=list, blank=True, editable=False)
    objects = ChangesetManager()

    class Meta:
        abstract = True
        ordering = ("-recorded_at", "-pk")
        permissions = (("export_changeset", "Can export lossless changeset history"),)
        indexes = (
            models.Index(fields=("-recorded_at", "-id"), name="%(app_label)s_%(class)s_recorded"),
            models.Index(
                fields=("origin_content_type", "origin_object_id"),
                name="%(app_label)s_%(class)s_origin",
            ),
        )
        constraints = (
            models.UniqueConstraint(
                fields=("scope", "key"),
                condition=~models.Q(scope="") & ~models.Q(key=""),
                name="%(app_label)s_%(class)s_claim",
            ),
            models.CheckConstraint(
                condition=models.Q(state="open", outcome__isnull=True)
                | models.Q(state="closed", outcome__in=("success", "failure")),
                name="%(app_label)s_%(class)s_closure",
            ),
        )

    @classmethod
    def ambient_attribution(cls) -> tuple[str, str]:
        return actor_reference(current_actor()), current_reason()

    @classmethod
    def context_required(cls) -> bool:
        return bool(getattr(settings, "PROVENANCE_STRICT", True))

    def history_user(self) -> models.Model | None:
        return get_policy().history_user(current_actor())

    def close(self, outcome: str) -> None:
        self.state, self.outcome = "closed", outcome
        self.touched = sorted(set(self.touched))
        type(self)._base_manager.using(self._state.db).filter(pk=self.pk).update(
            state=self.state, outcome=outcome, touched=self.touched, counts=self.counts
        )

    def record_created(self, row: models.Model) -> None:
        if not self.scope or not self.key or self.pk is None:
            raise RuntimeError("Only a persisted creation claim can record its result.")
        if row.pk is None or row._state.db != self._state.db:
            raise ValidationError("The created row must be persisted in the claim database.")
        content_type = ContentType.objects.db_manager(self._state.db).get_for_model(row)
        self.created_content_type = content_type
        self.created_row = row
        type(self)._base_manager.using(self._state.db).filter(pk=self.pk).update(
            created_content_type_id=content_type.pk, created_object_id=str(row.pk)
        )

    def history_rows(self) -> Any:
        using = type(self)._base_manager.db_manager(self._state.db, hints={"instance": self}).db
        for label in sorted(set(self.touched)):
            model = apps.get_model(label)
            if model_history(model) is None:
                raise ValidationError(f"{label}: the history owner is unavailable.")
            yield (
                model,
                get_history_manager_for_model(model)
                .using(using)
                .filter(history_changeset_id=self.pk)
                .order_by("history_id"),
            )

    def authorize_document(self, actor: Any) -> None:
        """Admit lossless export through the configured native policy."""
        get_policy().authorize(actor, "export", self)

    @staticmethod
    def document_value(value: Any) -> Any:
        """Encode a retained value; hosts may preserve an existing document format."""
        return json_safe(value)

    def _document_chunks(self) -> Iterator[str]:
        fields = {
            field.name: field.value_from_object(self) for field in self._meta.concrete_fields if field.name != "hash"
        }
        yield '{"changeset":' + canonical_json(self.document_value(fields)) + ',"history":{'
        separator = ""
        for model, history in self.history_rows():
            yield separator + canonical_json(model._meta.label_lower) + ":["
            row_separator = ""
            owner = model_history(model)
            assert owner is not None
            for row in history.iterator():
                owner.require_complete_state(row)
                values = {field.name: field.value_from_object(row) for field in row._meta.concrete_fields}
                yield row_separator + canonical_json(self.document_value(values))
                row_separator = ","
            yield "]"
            separator = ","
        yield "}}"

    def document(self, *, actor: Any = None) -> str:
        self.authorize_document(actor if actor is not None else current_actor())
        if self.state != "closed":
            raise ValidationError("Only a closed changeset can be exported.")
        text = "".join(self._document_chunks())
        digest = hashlib.sha256(text.encode()).hexdigest()
        if self.hash != digest:
            self.hash = digest
            type(self)._base_manager.using(self._state.db).filter(pk=self.pk).update(hash=digest)
        return text

    def revert_context(self, actor: Any, *, using: str) -> AbstractContextManager[Any]:
        """Host attribution/database context around native replay preflight and writes."""
        return actor_context(actor)

    def revert_application(self) -> AbstractContextManager[Any]:
        """Prevent restored derived values from becoming new authored assertions."""
        from .statements import statement_application

        return statement_application()

    def revert(self, *, actor: Any = None) -> Any:
        from angee.provenance.querysets import model_history

        actor = actor if actor is not None else current_actor()
        using = self._state.db or router.db_for_write(type(self), instance=self)
        if self.state != "closed" or self.outcome != "success":
            raise ValidationError("Only a successful closed changeset can be reverted.")
        with (
            self.revert_context(actor, using=using),
            transaction.atomic(using=using),
            changeset(fresh=True, activity="revert", origin=self, using=using),
        ):
            type(self)._base_manager.using(using).select_for_update().get(pk=self.pk)
            owners = [(model, model_history(model)) for model, _ in self.history_rows()]
            # Lock retained rows as well as live subjects: deleted rows still have
            # a stable synchronization point shared by every replay attempt.
            for model, _owner in owners:
                list(
                    get_history_manager_for_model(model)
                    .using(using)
                    .select_for_update()
                    .filter(history_changeset_id=self.pk)
                    .order_by("history_id")
                )
            states: list[Any] = []
            for model, owner in owners:
                if owner is None:
                    raise ValidationError(f"{model._meta.label}: history owner unavailable.")
                states.extend((owner, *state) for state in owner.changeset_states(model, self, using=using))
            for owner, first, last, previous in sorted(
                states,
                key=lambda entry: (
                    entry[1].history_object._meta.label_lower,
                    str(entry[1].history_object.pk),
                ),
            ):
                target = first.history_object
                target._state.db = using
                owner.lock_revert_subject(target, using=using)
                # Recheck after the live-row lock, which serializes ordinary updates.
                if (
                    get_history_manager_for_model(type(target))
                    .using(using)
                    .filter(
                        **{target._meta.pk.attname: target.pk},
                        **owner.source_database_attributes(type(first), using=using),
                        history_id__gt=last.history_id,
                    )
                    .exists()
                ):
                    raise ValidationError("A later write prevents replay.")
                owner.authorize_revert(first, last, previous, actor)
            removals = {
                (type(first.history_object), first.history_object.pk)
                for owner, first, last, previous in states
                if previous is None or previous.history_type == "-"
            }
            for owner, first, last, previous in states:
                owner.validate_revert_removal(first, last, previous, removals)
            ordered = ordered_states(states)
            subjects = {}
            with self.revert_application():
                for owner, first, last, previous in ordered:
                    owner.stage_revert(first, last, previous)
                for owner, first, last, previous in ordered:
                    for subject in owner.revert_row(first, last, previous):
                        subjects[(type(subject), subject.pk)] = subject
                for subject in subjects.values():
                    subject.resolve_statements()
            result = current_changeset(required=True, record=True, using=using)
        return result
