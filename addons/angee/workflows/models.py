"""Source models for workflow definitions.

The workflows addon owns graph definitions as data: a draft workflow lineage
head carries editable steps, edges, and triggers, while ``publish()`` copies that
draft into an immutable version. Step behavior remains in registry-selected
``StepImpl`` classes, so row data names keys and config, not Python callables.
Future runtime subject/artifact references use Django contenttypes-backed object
references; public ids stay at the transport boundary.
"""

from __future__ import annotations

import copy
import logging
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Self, cast

from croniter import CroniterBadCronError, croniter
from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_slug
from django.db import DEFAULT_DB_ALIAS, OperationalError, ProgrammingError, connections, models, router, transaction
from django.utils import timezone
from pydantic_core import PydanticSerializationError
from rebac import RelationshipTuple, SubjectRef, actor_context, system_context, write_relationships
from rebac.resources import to_object_ref

from angee.base.fields import StateField
from angee.base.impl import ImplClassField, ImplDefaultsMixin
from angee.base.mixins import AuditMixin
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet
from angee.base.refs import RecordRefMixin
from angee.base.scoping import system_queryset
from angee.base.transitions import StateTransitions, TransitionNotAllowed, save_state, transition
from angee.resources.mixins import ResourceLoadMixin, ResourceWritePreparation
from angee.workflows.attempts import (
    AttemptCause,
    AttemptClaim,
    AttemptFinalization,
    AttemptInput,
    AttemptResult,
    AttemptResultKind,
    AttemptStatus,
    DecisionSpec,
    DecisionTimerIntent,
    DecisionTimerKind,
    InvocationAdmission,
    LeaseRevocation,
    LeaseRevocationReason,
    RetryIntent,
    deserialize_decision_specs,
    serialize_decision_specs,
)
from angee.workflows.definitions import WorkflowDefinitionManagerMixin
from angee.workflows.dispatch import (
    DispatchConsumption,
    DispatchPreflight,
    DispatchPreflightDisposition,
    WorkflowDispatchEnvelope,
    WorkflowDispatchKind,
)
from angee.workflows.steps import (
    StepImpl,
    optional_non_negative_int,
    optional_positive_int,
    retry_policy_from_config,
)

logger = logging.getLogger(__name__)
_CHANGE_FEED_FIX = "declare changes() for the model to join the change feed"


@dataclass
class _DefinitionWriteSession:
    """Private state for one locked, reentrant definition transaction."""

    alias: str
    connection_id: int
    workflow_ids: frozenset[int]
    changed_head_ids: set[int]
    copy_target_ids: set[int]


_definition_write_session: ContextVar[_DefinitionWriteSession | None] = ContextVar(
    "workflow_definition_write_session", default=None
)


@dataclass(frozen=True, slots=True)
class _AttemptWriteSession:
    alias: str
    connection_id: int
    step_run_id: int


_attempt_write_session: ContextVar[_AttemptWriteSession | None] = ContextVar(
    "workflow_attempt_write_session", default=None
)


@dataclass(slots=True)
class _AttemptSaveCapability:
    alias: str
    connection_id: int
    step_run_id: int
    instance_id: int
    pk: Any
    adding: bool
    consumed: bool = False


_attempt_save_capability: ContextVar[_AttemptSaveCapability | None] = ContextVar(
    "workflow_attempt_save_capability", default=None
)


@dataclass(frozen=True, slots=True)
class _DecisionWriteSession:
    alias: str
    connection_id: int
    step_run_id: int
    attempt_id: int
    declaration_index: int


_decision_write_session: ContextVar[_DecisionWriteSession | None] = ContextVar(
    "workflow_decision_write_session", default=None
)


def _attempt_write_active(alias: str, step_run_id: int | None = None) -> bool:
    session = _attempt_write_session.get()
    connection = connections[alias]
    return (
        session is not None
        and session.alias == alias
        and session.connection_id == id(connection)
        and connection.in_atomic_block
        and (step_run_id is None or session.step_run_id == step_run_id)
    )


def _decision_write_active(alias: str, instance: Any) -> bool:
    session = _decision_write_session.get()
    connection = connections[alias]
    return (
        session is not None
        and session.alias == alias
        and session.connection_id == id(connection)
        and connection.in_atomic_block
        and instance._state.adding
        and instance.step_run_id == session.step_run_id
        and instance.suspension_attempt_id == session.attempt_id
        and instance.declaration_index == session.declaration_index
    )


def _combined_delete_results(*results: tuple[int, dict[str, int]]) -> tuple[int, dict[str, int]]:
    """Combine Django delete counts from explicitly owned cascade phases."""

    total = 0
    counts: dict[str, int] = {}
    for deleted, per_model in results:
        total += deleted
        for label, count in per_model.items():
            counts[label] = counts.get(label, 0) + count
    return total, counts


def _definition_rows(model: type[models.Model], alias: str) -> Any:
    """Return an internal unscoped queryset for ownership and lock verification."""

    return system_queryset(model, using=alias, lock=None)


def _definition_related_rows(manager: Any, owner: Any, alias: str) -> Any:
    """Bind internal child traversal to the caller's explicit actor or sudo intent."""

    queryset = manager.using(alias)
    if owner.is_sudo():
        return queryset.sudo(reason="workflows.definition_write.cascade")
    if actor := owner.actor():
        return queryset.with_actor(actor)
    return queryset


def _definition_caller_context(owner: Any) -> Any:
    """Project an explicit instance binding into Django's validation queries."""

    if owner.is_sudo():
        return system_context(reason="workflows.definition_write.validation")
    if actor := owner.actor():
        return actor_context(actor)
    return nullcontext()


def _bind_definition_caller(instance: Any, owner: Any) -> Any:
    """Carry an explicit caller binding onto a manager-fetched or copied row."""

    if owner.is_sudo():
        return instance.sudo(reason="workflows.definition_write.owner")
    if actor := owner.actor():
        return instance.with_actor(actor)
    return instance


class DefinitionQuerySet(AngeeQuerySet[Any]):
    """Definition collection writes that cannot preserve row invariants."""

    def update(self, **kwargs: Any) -> int:
        raise TypeError("Workflow definitions do not support QuerySet.update(); save instances instead.")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Workflow definitions do not support bulk_create(); save instances instead.")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        raise TypeError("Workflow definitions do not support bulk_update(); save instances instead.")

    def delete(self) -> tuple[int, dict[str, int]]:
        instances = list(self.order_by("pk"))
        if not instances:
            return (0, {})
        if isinstance(self, WorkflowQuerySet):
            workflow_model = self.model
            workflow_ids = [instance.pk for instance in instances]
        else:
            workflow_field = self.model._meta.get_field("workflow")
            workflow_model = workflow_field.remote_field.model
            workflow_ids = [instance.workflow_id for instance in instances]
        manager = workflow_model.objects.db_manager(self.db)
        with manager._definition_write(workflow_ids, using=self.db):
            total = 0
            counts: dict[str, int] = {}
            for instance in instances:
                deleted, per_model = instance.delete()
                total += deleted
                for label, count in per_model.items():
                    counts[label] = counts.get(label, 0) + count
            return total, counts


@dataclass(frozen=True, slots=True)
class StepConfigProjection:
    """Canonical authoring value and diagnostics for one persisted step config."""

    value: Any
    errors: dict[str, list[str]]


class WorkflowStatus(models.TextChoices):
    """Publication lifecycle for a workflow definition row."""

    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


class WorkflowPurpose(models.TextChoices):
    """Product purpose declared by a workflow lineage."""

    AUTOMATION = "automation", "Automation"
    AGENT_SESSION = "agent_session", "Agent session"


class RunOrigin(models.TextChoices):
    """Caller that created a workflow run."""

    UNKNOWN = "unknown", "Unknown"
    MANUAL = "manual", "Manual"
    TRIGGER = "trigger", "Trigger"
    SESSION = "session", "Session"
    ERROR_WORKFLOW = "error_workflow", "Error workflow"


class WaitingKind(models.TextChoices):
    """Runtime reason a workflow step is waiting."""

    SCHEDULED = "scheduled", "Scheduled"
    APPROVAL = "approval", "Approval"
    EXTERNAL = "external", "External input"
    CHILDREN = "children", "Child steps"


class JoinRule(models.TextChoices):
    """How a step with multiple incoming edges activates over upstream siblings."""

    ALL_SUCCESS = "all_success", "All success"
    ONE_SUCCESS = "one_success", "One success"
    ONE_DONE = "one_done", "One done"
    ALL_DONE = "all_done", "All done"
    NONE_FAILED = "none_failed", "None failed"
    NONE_FAILED_MIN_ONE_SUCCESS = "none_failed_min_one_success", "None failed, at least one success"
    ALWAYS = "always", "Always"


class TriggerKind(models.TextChoices):
    """How a workflow lineage is started."""

    MANUAL = "manual", "Manual"
    EVENT = "event", "Event"
    SCHEDULE = "schedule", "Schedule"


class RunStatus(models.TextChoices):
    """Execution lifecycle for one pinned workflow run."""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    WAITING = "waiting", "Waiting"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"


class StepRunStatus(models.TextChoices):
    """Execution lifecycle for one step-run journal row."""

    SCHEDULED = "scheduled", "Scheduled"
    STARTED = "started", "Started"
    WAITING = "waiting", "Waiting"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"
    SKIPPED = "skipped", "Skipped"


class Verdict(models.TextChoices):
    """Resolution lifecycle for one awaited decision slot."""

    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"
    ESCALATED = "escalated", "Escalated"
    EXPIRED = "expired", "Expired"


RunStatus.TERMINAL = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED})
StepRunStatus.TERMINAL = frozenset(
    {StepRunStatus.SUCCEEDED, StepRunStatus.FAILED, StepRunStatus.CANCELED, StepRunStatus.SKIPPED}
)
StepRunStatus.ACTIVE = frozenset({StepRunStatus.SCHEDULED, StepRunStatus.STARTED, StepRunStatus.WAITING})
Verdict.TERMINAL = frozenset({Verdict.COMPLETED, Verdict.REJECTED, Verdict.ESCALATED, Verdict.EXPIRED})


def _save_workflow_status(instance: models.Model, source: Any, target: Any) -> None:
    """Persist a workflow status transition through the immutable-row variant."""

    workflow = cast("Workflow", instance)
    workflow._allow_immutable_status_save = True
    try:
        save_state(workflow, source, target)
    finally:
        del workflow._allow_immutable_status_save


#: Statuses that participate in version currency: a newer ARCHIVED row
#: supersedes older PUBLISHED rows, retiring the whole lineage.
_CURRENCY_STATUSES = (WorkflowStatus.PUBLISHED, WorkflowStatus.ARCHIVED)


class WorkflowQuerySet(DefinitionQuerySet):
    """QuerySet owning subject declaration discovery and version currency."""

    def current_published(self) -> Self:
        """Return rows that are the current published version of their lineage.

        The currency rule's single owner: a row survives when it is PUBLISHED
        and no ``_CURRENCY_STATUSES`` sibling in the same lineage is newer by
        ``(version, pk)`` — so a newer ARCHIVED row retires the lineage.
        """

        workflow_model = cast(Any, self.model)
        newer_version = (
            workflow_model.objects
            .sudo(reason="workflows.subject_declaration.current")
            .filter(
                published_from_id=models.OuterRef("published_from_id"),
                status__in=_CURRENCY_STATUSES,
            )
            .filter(
                models.Q(version__gt=models.OuterRef("version"))
                | models.Q(version=models.OuterRef("version"), pk__gt=models.OuterRef("pk"))
            )
        )
        return cast(
            Self,
            self.filter(status=WorkflowStatus.PUBLISHED).filter(~models.Exists(newer_version)),
        )

    def for_subject_declaration(self, subject_declaration: str) -> Self:
        """Return current published workflows accepting ``subject_declaration``."""

        declaration = subject_declaration.strip().lower()
        return cast(
            Self,
            self.current_published()
            .filter(purpose=WorkflowPurpose.AUTOMATION)
            .filter(
                models.Q(subject_declaration="")
                | models.Q(subject_declaration=declaration)
            )
            .order_by("name", "version", "pk"),
        )

    def with_lineage_projection(self) -> Self:
        """Annotate lineage and current-publication context for every row."""

        workflow_model = cast(type[Workflow], self.model)
        return cast(Self, self.annotate(**workflow_model.lineage_projection_annotation()))


class WorkflowManager(WorkflowDefinitionManagerMixin, AngeeManager.from_queryset(WorkflowQuerySet)):  # type: ignore[misc]
    """Manager owning workflow lineage lookups."""

    @contextmanager
    def _definition_write(
        self,
        workflow_ids: Iterable[int],
        *,
        using: str | None = None,
        _allow_status_transition: bool = False,
    ) -> Iterable[list[Any]]:
        """Lock declared lineages and share one revision owner across nested writes."""

        alias = using or self.db
        ids = frozenset(workflow_ids)
        active = _definition_write_session.get()
        connection_id = id(connections[alias])
        if active is not None:
            if active.alias != alias or active.connection_id != connection_id:
                raise RuntimeError("A workflow definition write cannot span database connections.")
            undeclared = ids - active.workflow_ids - active.copy_target_ids
            if undeclared:
                raise RuntimeError("A workflow definition write cannot expand to a new lineage after locking.")
            rows = list(system_queryset(self.model, using=alias, lock=None).filter(pk__in=ids).order_by("pk"))
            if len(rows) != len(ids):
                raise ValidationError("A workflow definition parent no longer exists.")
            if not _allow_status_transition and any(
                row.is_immutable and row.pk not in active.copy_target_ids for row in rows
            ):
                raise ValidationError("Published workflow versions are immutable.")
            yield rows
            return

        ordered_ids = sorted(ids)
        with transaction.atomic(using=alias):
            rows = list(
                system_queryset(self.model, using=alias, lock=("self",))
                .filter(pk__in=ordered_ids)
                .order_by("pk")
            )
            if len(rows) != len(ordered_ids):
                raise ValidationError("A workflow definition parent no longer exists.")
            if not _allow_status_transition and any(row.is_immutable for row in rows):
                raise ValidationError("Published workflow versions are immutable.")
            session = _DefinitionWriteSession(alias, connection_id, ids, set(), set())
            token = _definition_write_session.set(session)
            try:
                yield rows
                for workflow_id in sorted(session.changed_head_ids):
                    models.QuerySet.update(
                        self.model._base_manager.using(alias).filter(
                            pk=workflow_id, published_from__isnull=True
                        ),
                        draft_revision=models.F("draft_revision") + 1,
                    )
            finally:
                _definition_write_session.reset(token)

    def mark_definition_changed(self, workflow_id: int) -> None:
        """Record one editable lineage as changed in the active write session."""

        session = _definition_write_session.get()
        if session is None or workflow_id not in session.workflow_ids:
            raise RuntimeError("Definition changes require an active locked write session.")
        session.changed_head_ids.add(workflow_id)

    def _definition_revision(self, workflow_id: int, current: int) -> int:
        """Return the exact revision this active locked session will commit."""

        session = _definition_write_session.get()
        if session is None or workflow_id not in session.workflow_ids:
            raise RuntimeError("Definition revision projection requires its active locked write session.")
        return current + int(workflow_id in session.changed_head_ids)

    @contextmanager
    def _definition_caller(self, workflow: Any) -> Iterable[None]:
        """Project a command target's explicit actor or sudo binding to nested ORM work."""

        with _definition_caller_context(workflow):
            yield

    @contextmanager
    def _definition_read(self, workflow_id: int, *, using: str | None = None) -> Iterable[Any]:
        """Lock one mutable or immutable definition for a coherent snapshot read."""

        alias = using or self.db
        with transaction.atomic(using=alias):
            yield system_queryset(self.model, using=alias, lock=("self",)).get(pk=workflow_id)

    @contextmanager
    def _copy_to(self, workflow_id: int) -> Iterable[None]:
        """Permit inserts into one new publication within the locked session."""

        session = _definition_write_session.get()
        if session is None:
            raise RuntimeError("Publication copying requires an active definition write session.")
        session.copy_target_ids.add(workflow_id)
        try:
            yield
        finally:
            session.copy_target_ids.remove(workflow_id)

    def current_published_for(self, workflow: Any) -> Any | None:
        """Return the latest published version for ``workflow``'s lineage.

        Composes the same ``_CURRENCY_STATUSES`` rule ``current_published``
        owns, scoped to one explicit lineage pool.
        """

        head = workflow if getattr(workflow, "published_from_id", None) is None else workflow.published_from
        latest = (
            self.filter(status__in=_CURRENCY_STATUSES)
            .filter(models.Q(pk=head.pk) | models.Q(published_from=head))
            .order_by("-version", "-pk")
            .first()
        )
        if latest is None or latest.status != WorkflowStatus.PUBLISHED:
            return None
        return latest


class WorkflowRunQuerySet(AngeeQuerySet[Any]):
    """QuerySet owning workflow-run subject lookups."""

    def for_subject(self, subject: Any) -> Self:
        """Return runs whose generic subject is ``subject``."""

        content_type = ContentType.objects.get_for_model(subject, for_concrete_model=False)
        return cast(
            Self,
            self.filter(
                subject_content_type=content_type,
                subject_object_id=subject.pk,
            ),
        )


class WorkflowRunManager(AngeeManager.from_queryset(WorkflowRunQuerySet)):  # type: ignore[misc]
    """Manager owning workflow-run subject lookups."""


class Workflow(ResourceLoadMixin, AuditMixin, AngeeDataModel):
    """Editable workflow lineage head or immutable published workflow version.

    A resource-assigned stable key identifies the lineage independently of its
    mutable display name and is shared by every published version.
    """

    runtime = True

    sqid_prefix = "wfl_"
    key = models.SlugField(max_length=100, blank=True, default="")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    purpose = StateField(choices_enum=WorkflowPurpose, default=WorkflowPurpose.AUTOMATION)
    subject_declaration = models.CharField(max_length=200, blank=True, default="")
    status = StateField(choices_enum=WorkflowStatus, default=WorkflowStatus.DRAFT)
    version = models.PositiveIntegerField(default=0)
    draft_revision = models.PositiveIntegerField(default=0, editable=False)
    published_from = models.ForeignKey(
        "workflows.Workflow",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="published_versions",
    )
    error_workflow = models.ForeignKey(
        "workflows.Workflow",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="error_for_workflows",
    )
    max_steps = models.PositiveIntegerField(default=1000)
    budget = models.JSONField(default=dict, blank=True)

    status_transitions = StateTransitions(
        status,
        {
            WorkflowStatus.DRAFT: [WorkflowStatus.PUBLISHED],
            WorkflowStatus.PUBLISHED: [WorkflowStatus.ARCHIVED],
        },
    )

    objects = WorkflowManager()

    class Meta:
        """Django model options for workflow definitions."""

        abstract = True
        ordering = ("name", "version")
        rebac_resource_type = "workflows/workflow"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("key",),
                condition=models.Q(published_from__isnull=True) & ~models.Q(key=""),
                name="uniq_workflows_workflow_head_key",
                violation_error_code="unique",
                violation_error_message="A workflow with this key already exists.",
            ),
        )

    def __str__(self) -> str:
        """Return the workflow's display label."""

        return self.name

    @classmethod
    def lineage_projection_annotation(cls) -> dict[str, Any]:
        """Return the ORM projection for lineage and current publication context."""

        lineage = models.Q(pk=models.OuterRef("_workflow_lineage_id")) | models.Q(
            published_from_id=models.OuterRef("_workflow_lineage_id")
        )
        current = cls.objects.current_published().filter(lineage)
        latest = cls.objects.filter(lineage, status__in=_CURRENCY_STATUSES).order_by("-version", "-pk")
        return {
            "_workflow_lineage_id": models.functions.Coalesce("published_from_id", "pk"),
            "_workflow_publication_status": models.functions.Coalesce(
                models.Subquery(latest.values("status")[:1]),
                models.Value(WorkflowStatus.DRAFT),
            ),
            "_workflow_current_published_pk": models.Subquery(current.values("pk")[:1]),
            "_workflow_current_published_version": models.Subquery(current.values("version")[:1]),
            "_workflow_current_published_subject_declaration": models.Subquery(
                current.values("subject_declaration")[:1]
            ),
        }

    @classmethod
    def resource_write_preparation(cls, resource: Any, dataset: Any) -> ResourceWritePreparation | None:
        """Declare existing lineage heads before a resource batch starts writing."""

        targets = frozenset(
            instance.pk
            for xref in dataset["_xref"]
            if (instance := resource.instance_for_xref(xref)) is not None
        )
        return ResourceWritePreparation(cls, targets) if targets else None

    @classmethod
    @contextmanager
    def prepare_resource_writes(cls, workflow_ids: Iterable[int]) -> Iterable[None]:
        """Prelock every declared resource lineage in deterministic order."""

        ids = sorted(set(workflow_ids))
        alias = router.db_for_write(cls)
        with transaction.atomic(using=alias):
            rows = list(
                system_queryset(cls, using=alias, lock=("self",))
                .filter(pk__in=ids)
                .order_by("pk")
            )
            if len(rows) != len(ids):
                raise ValidationError("A workflow definition parent no longer exists.")
            if any(row.is_immutable for row in rows):
                raise ValidationError("Published workflow versions are immutable.")
            yield

    @classmethod
    def after_resource_load(
        cls,
        instances: Iterable[Any],
        *,
        tier: str,
        source: str,
        publish: bool = False,
    ) -> None:
        """Reconcile stable keys and publish loaded drafts when requested."""

        for workflow in sorted(instances, key=lambda instance: instance.pk or 0):
            if workflow.published_from_id is not None:
                continue
            workflow._propagate_resource_key_backfill()
            if publish and workflow.status == WorkflowStatus.DRAFT:
                workflow.publish_if_changed()

        super().after_resource_load(instances, tier=tier, source=source, publish=publish)

    @transition(status, source=WorkflowStatus.DRAFT, target=WorkflowStatus.PUBLISHED, on_success=_save_workflow_status)
    def mark_published(self) -> None:
        """Mark this copied version as published."""

        session = _definition_write_session.get()
        if (
            self.published_from_id is None
            or session is None
            or self.pk not in session.copy_target_ids
        ):
            raise ValidationError("Only a new snapshot created by publish() can be marked published.")

    @transition(
        status,
        source=WorkflowStatus.PUBLISHED,
        target=WorkflowStatus.ARCHIVED,
        on_success=_save_workflow_status,
    )
    def archive(self) -> None:
        """Archive a published workflow version."""

    def clean(self) -> None:
        """Validate lineage-owned links and normalize stable and subject keys."""

        super().clean()
        self.key = (self.key or "").lower()
        if self.published_from_id is not None and self.key != self.published_from.key:
            raise ValidationError({"key": "Published workflow versions must share their lineage stable key."})
        if self.error_workflow_id is not None and self.error_workflow.published_from_id is not None:
            raise ValidationError({"error_workflow": "Error workflow must point to a workflow lineage head."})
        # Mirror Trigger.event_model_label: store the canonical label_lower form
        # and reject labels that resolve to no installed model.
        self.subject_declaration = self.subject_declaration.strip().lower()
        if self.subject_declaration:
            try:
                apps.get_model(self.subject_declaration)
            except (LookupError, ValueError) as error:
                raise ValidationError(
                    {
                        "subject_declaration": (
                            f"Subject declaration {self.subject_declaration!r} is not an installed model label."
                        )
                    }
                ) from error

    def validate_subject_declaration(self, subject: Any) -> None:
        """Raise when ``subject`` does not satisfy this workflow's subject declaration."""

        if not self.subject_declaration:
            return
        if subject is None:
            raise ValidationError(
                {"subject": f"Subject declaration {self.subject_declaration!r} requires a subject."}
            )
        if subject._meta.label_lower != self.subject_declaration:
            raise ValidationError(
                {
                    "subject": (
                        f"Subject model {subject._meta.label!r} does not satisfy "
                        f"subject declaration {self.subject_declaration!r}."
                    )
                }
            )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the workflow after enforcing immutability and model validation."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        if self._state.adding:
            if self.published_from_id is not None:
                session = _definition_write_session.get()
                if session is None or self.published_from_id not in session.workflow_ids:
                    raise ValidationError("Published workflow versions can only be created by publish().")
                if self.status != WorkflowStatus.DRAFT:
                    raise ValidationError({"status": "New publications must begin as draft snapshots."})
            elif self.status != WorkflowStatus.DRAFT or self.version != 0 or self.draft_revision != 0:
                raise ValidationError("New workflow lineage heads must begin as revision-zero drafts.")
            with _definition_caller_context(self):
                self.full_clean()
            super().save(*args, **kwargs)
            return

        manager = type(self).objects.db_manager(alias)
        with manager._definition_write(
            (self.pk,),
            using=alias,
            _allow_status_transition=getattr(self, "_allow_immutable_status_save", False),
        ):
            persisted = cast(Self, _definition_rows(type(self), alias).get(pk=self.pk))
            self._raise_if_immutable_save(persisted)
            if self.published_from_id != persisted.published_from_id:
                raise ValidationError({"published_from": "Workflow lineage ownership is immutable."})
            if self.version != persisted.version:
                raise ValidationError({"version": "Workflow publication versions are immutable."})
            if self.status != persisted.status and not getattr(self, "_allow_immutable_status_save", False):
                raise ValidationError({"status": "Workflow status changes require a declared transition."})
            # The database counter is the sole owner; a stale model can never write it backwards.
            self.draft_revision = persisted.draft_revision
            with _definition_caller_context(self):
                self.full_clean()
            self._raise_if_key_changed(persisted)
            update_fields = kwargs.get("update_fields")
            definition_fields = {
                "name",
                "description",
                "purpose",
                "subject_declaration",
                "error_workflow",
                "error_workflow_id",
                "max_steps",
                "budget",
            }
            updated = None if update_fields is None else set(update_fields)
            considered = definition_fields if updated is None else {
                field for field in definition_fields if field in updated or field.removesuffix("_id") in updated
            }
            changed = any(getattr(persisted, field) != getattr(self, field) for field in considered)
            assigns_stable_key = (
                not persisted.key
                and bool(self.key)
                and (update_fields is None or "key" in update_fields)
            )
            super().save(*args, **kwargs)
            if assigns_stable_key:
                self._propagate_resource_key_backfill()
            if changed and self.published_from_id is None:
                manager.mark_definition_changed(self.pk)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only mutable workflow rows."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        manager = type(self).objects.db_manager(alias)
        with manager._definition_write((self.pk,), using=alias):
            persisted = cast(Self, _definition_rows(type(self), alias).get(pk=self.pk))
            self._raise_if_immutable_save(persisted)
            if persisted.published_versions.exists():
                raise ValidationError("A workflow with publication history cannot be deleted.")
            # Collector skips child instance delete methods. Own the full cascade here.
            edges = _definition_related_rows(persisted.edges, self, alias).all().delete()
            steps = _definition_related_rows(persisted.steps, self, alias).all().delete()
            with _definition_caller_context(self):
                workflow = super().delete(*args, **kwargs)
            return _combined_delete_results(edges, steps, workflow)

    def publish(self) -> Self:
        """Copy this draft lineage head into an immutable published version."""

        if self.published_from_id is not None:
            raise ValidationError({"published_from": "Only a workflow lineage head can be published."})
        alias = router.db_for_write(type(self), instance=self)
        manager = type(self).objects.db_manager(alias)
        with manager._definition_write((self.pk,), using=alias):
            draft = cast(Self, _bind_definition_caller(_definition_rows(type(self), alias).get(pk=self.pk), self))
            if draft.status != WorkflowStatus.DRAFT:
                raise ValidationError({"status": "Only draft workflows can be published."})
            with _definition_caller_context(draft):
                draft._validate_publishable()
            with _definition_caller_context(draft):
                version = draft._next_published_version()
            published = type(self)(
                key=draft.key,
                name=draft.name,
                description=draft.description,
                purpose=draft.purpose,
                subject_declaration=draft.subject_declaration,
                status=WorkflowStatus.DRAFT,
                version=version,
                draft_revision=manager._definition_revision(draft.pk, draft.draft_revision),
                published_from=draft,
                error_workflow=draft.error_workflow,
                max_steps=draft.max_steps,
                budget=copy.deepcopy(draft.budget),
                created_by_id=draft.created_by_id,
                updated_by_id=draft.updated_by_id,
            )
            _bind_definition_caller(published, draft)
            published.save(using=alias)
            with manager._copy_to(published.pk):
                with _definition_caller_context(draft):
                    draft._copy_definition_to(published)
                published.mark_published()
            return cast(Self, published)

    def publish_if_changed(self) -> Self | None:
        """Publish this draft only when no current version has the same definition."""

        alias = router.db_for_write(type(self), instance=self)
        manager = type(self).objects.db_manager(alias)
        with manager._definition_write((self.pk,), using=alias):
            draft = cast(Self, _bind_definition_caller(_definition_rows(type(self), alias).get(pk=self.pk), self))
            with _definition_caller_context(draft):
                current = manager.current_published_for(draft)
                if current is not None and draft._definition_signature() == current._definition_signature():
                    return None
            return draft.publish()

    def _next_published_version(self) -> int:
        """Return the next immutable version number for this lineage head."""

        current = (
            type(self).objects.filter(published_from=self).aggregate(max_version=models.Max("version"))["max_version"]
            or 0
        )
        return int(current) + 1

    def _copy_definition_to(self, published: Workflow) -> None:
        """Copy this draft's steps and edges to ``published``."""

        step_model = self.steps.model
        edge_model = self.edges.model
        step_map: dict[int, Any] = {}
        for step in self.steps.order_by("pk"):
            copied = step_model(
                workflow=published,
                key=step.key,
                name=step.name,
                step_class=step.step_class,
                config=copy.deepcopy(step.config),
                input_binding=copy.deepcopy(step.input_binding),
                join_rule=step.join_rule,
                is_entry=step.is_entry,
                position=copy.deepcopy(step.position),
            )
            _bind_definition_caller(copied, published)
            copied.save()
            step_map[step.pk] = copied
        for edge in self.edges.select_related("source", "target").order_by("pk"):
            copied_edge = edge_model(
                workflow=published,
                source=step_map[edge.source_id],
                target=step_map[edge.target_id],
                condition=edge.condition,
            )
            _bind_definition_caller(copied_edge, published)
            copied_edge.save()

    def _definition_signature(self) -> dict[str, Any]:
        """Return the versioned definition content for publish idempotency."""

        return {
            "workflow": {
                "name": self.name,
                "description": self.description,
                "purpose": str(self.purpose),
                "subject_declaration": self.subject_declaration,
                "error_workflow_id": self.error_workflow_id,
                "max_steps": self.max_steps,
                "budget": copy.deepcopy(self.budget),
            },
            "steps": [
                {
                    "key": step.key,
                    "name": step.name,
                    "step_class": step.step_class,
                    "config": copy.deepcopy(step.config),
                    "input_binding": copy.deepcopy(step.input_binding),
                    "join_rule": str(step.join_rule),
                    "is_entry": step.is_entry,
                    "position": copy.deepcopy(step.position),
                }
                for step in self.steps.order_by("key", "pk")
            ],
            "edges": [
                {
                    "source": edge.source.key,
                    "target": edge.target.key,
                    "condition": edge.condition,
                }
                for edge in self.edges.select_related("source", "target").order_by(
                    "source__key",
                    "target__key",
                    "condition",
                    "pk",
                )
            ],
        }

    def _validate_publishable(self) -> None:
        """Validate the exact locked graph before creating an executable snapshot."""

        self.validate_readiness()

    def graph_diagnostics(self) -> tuple[Any, ...]:
        """Return readiness diagnostics for the currently persisted definition."""

        from angee.workflows.graph import WorkflowGraph

        return WorkflowGraph.from_workflow(self).diagnostics()

    def validate_readiness(self) -> None:
        """Raise all readiness diagnostics for the currently persisted definition."""

        from angee.workflows.graph import WorkflowGraph

        WorkflowGraph.from_workflow(self).validate()

    def _persisted_save_snapshot(self) -> Self | None:
        """Return the persisted status and stable key for save guards."""

        if self._state.adding:
            return None
        try:
            return cast(Self, type(self)._base_manager.only("status", "key").get(pk=self.pk))
        except ObjectDoesNotExist:
            return None

    def _raise_if_immutable_save(self, persisted: Self | None) -> None:
        """Reject writes to persisted published or archived workflow definitions."""

        if persisted is None or getattr(self, "_allow_immutable_status_save", False):
            return
        if persisted.is_immutable:
            raise ValidationError("Published workflow versions are immutable.")

    def _raise_if_key_changed(self, persisted: Self | None) -> None:
        """Keep an assigned stable key immutable while allowing legacy backfill."""

        if persisted is None:
            return
        if persisted.key and self.key != persisted.key:
            raise ValidationError({"key": "Workflow stable keys are immutable once assigned."})

    def _propagate_resource_key_backfill(self) -> None:
        """Copy a resource-assigned stable key to legacy published versions."""

        if not self.key:
            return
        versions = type(self)._base_manager.filter(published_from=self)
        if versions.exclude(key__in=("", self.key)).exists():
            raise ValidationError({"key": "Published workflow versions disagree with their lineage stable key."})
        models.QuerySet.update(
            versions.filter(key=""),
            key=self.key,
            updated_at=timezone.now(),
        )

    @property
    def is_immutable(self) -> bool:
        """Return whether this workflow version rejects definition edits."""

        return self.status in {WorkflowStatus.PUBLISHED, WorkflowStatus.ARCHIVED}


class StepQuerySet(DefinitionQuerySet):
    """Guard collection writes to workflow steps."""


class StepManager(AngeeManager.from_queryset(StepQuerySet)):  # type: ignore[misc]
    """Manager for guarded workflow-step writes."""


class Step(ImplDefaultsMixin, AuditMixin, AngeeDataModel):
    """One node in a workflow definition graph."""

    runtime = True

    sqid_prefix = "wfs_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="steps")
    key = models.SlugField(max_length=100)
    name = models.CharField(max_length=200)
    step_class = ImplClassField(
        base_class=StepImpl,
        registry_setting="ANGEE_WORKFLOW_STEP_CLASSES",
        default="handler",
    )
    config = models.JSONField(default=dict, blank=True)
    input_binding = models.JSONField(null=True, blank=True, default=None)
    join_rule = StateField(choices_enum=JoinRule, default=JoinRule.ALL_SUCCESS)
    is_entry = models.BooleanField(default=False)
    position = models.JSONField(default=dict, blank=True)

    objects = StepManager()

    class Meta:
        """Django model options for workflow steps."""

        abstract = True
        ordering = ("workflow", "key")
        rebac_resource_type = "workflows/step"
        rebac_id_attr = "sqid"
        constraints = (models.UniqueConstraint(fields=("workflow", "key"), name="uniq_workflows_step_key"),)

    def __str__(self) -> str:
        """Return the step's display label."""

        return self.name or self.key

    @classmethod
    def resource_write_preparation(cls, resource: Any, dataset: Any) -> ResourceWritePreparation | None:
        """Declare old and proposed workflow parents for a resource step batch."""

        workflows = set(resource.related_instances(dataset, "workflow"))
        workflows.update(
            instance.workflow
            for xref in dataset["_xref"]
            if (instance := resource.instance_for_xref(xref)) is not None
        )
        if not workflows:
            return None
        workflow_model = cls._meta.get_field("workflow").remote_field.model
        return ResourceWritePreparation(workflow_model, frozenset(row.pk for row in workflows))

    def config_projection(self) -> StepConfigProjection:
        """Project legacy config for repair without rewriting its stored value."""

        impl = cast(type[StepImpl], self.resolve_impl("step_class"))
        raw = copy.deepcopy(self.config)
        try:
            impl.validate_config(raw)
            value = impl.normalize_config(raw) if impl.config_model is not None else raw
            return StepConfigProjection(value=value, errors={})
        except ValidationError as error:
            return StepConfigProjection(value=raw, errors=error.message_dict)

    def clean(self) -> None:
        """Validate structural draft fields without requiring readiness."""

        super().clean()
        if not isinstance(self.config, Mapping):
            raise ValidationError({"config": "Step config must be an object."})
        if self.input_binding is not None and not isinstance(self.input_binding, Mapping):
            raise ValidationError({"input_binding": "Step input binding must be an object or null."})
        try:
            self.resolve_impl("step_class")
        except ValidationError:
            raise
        except Exception as error:
            raise ValidationError({"step_class": "Unknown workflow step class."}) from error

    def validate_impl_configs(self, *, update_fields: Any = None) -> None:
        """Canonicalize complete drafts while preserving incomplete object configs."""

        if not self._state.adding and update_fields is not None and "config" not in update_fields:
            return
        if not isinstance(self.config, Mapping):
            raise ValidationError({"config": "Step config must be an object."})
        impl = cast(type[StepImpl], self.resolve_impl("step_class"))
        if impl.config_model is None:
            return
        try:
            self.config = impl.normalize_config(self.config)
        except ValidationError:
            # Incomplete typed config is a readiness diagnostic, not a storage error.
            self.config = copy.deepcopy(dict(self.config))

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the step after enforcing parent immutability and validation."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        old_parent_id = None
        if not self._state.adding:
            old_parent_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
                "workflow_id", flat=True
            ).first()
        parent_ids = {value for value in (old_parent_id, self.workflow_id) if value is not None}
        manager = type(self.workflow).objects
        with manager._definition_write(parent_ids, using=alias):
            old = None if self._state.adding else _definition_rows(type(self), alias).filter(pk=self.pk).first()
            if not self._state.adding and (old is None or old.workflow_id != old_parent_id):
                raise ValidationError("The stored workflow step changed while it was being edited.")
            if old is not None and old.workflow_id != self.workflow_id:
                if _definition_rows(type(self), alias).filter(
                    models.Q(outgoing_edges__isnull=False) | models.Q(incoming_edges__isnull=False), pk=self.pk
                ).exists():
                    raise ValidationError({"workflow": "A connected step cannot move to another workflow."})
            with _definition_caller_context(self):
                self.full_clean()
            update_fields = kwargs.get("update_fields")
            self.validate_impl_configs(update_fields=update_fields)
            fields = {
                "workflow_id",
                "key",
                "name",
                "step_class",
                "config",
                "input_binding",
                "join_rule",
                "is_entry",
                "position",
            }
            updated = None if update_fields is None else set(update_fields)
            considered = fields if updated is None else {
                field for field in fields if field in updated or field.removesuffix("_id") in updated
            }
            changed = old is None or any(
                getattr(old, field) != getattr(self, field)
                for field in considered
            )
            super().save(*args, **kwargs)
            session = _definition_write_session.get()
            if changed and session is not None and self.workflow_id not in session.copy_target_ids:
                for workflow_id in parent_ids:
                    manager.mark_definition_changed(workflow_id)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only steps belonging to mutable workflow rows."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        workflow_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
            "workflow_id", flat=True
        ).first()
        if workflow_id is None:
            return (0, {})
        manager = type(self.workflow).objects
        with manager._definition_write((workflow_id,), using=alias):
            persisted = _definition_rows(type(self), alias).get(pk=self.pk)
            if persisted.workflow_id != workflow_id:
                raise ValidationError("The stored workflow step changed while it was being deleted.")
            edge_model = persisted.outgoing_edges.model
            edges = _definition_related_rows(edge_model.objects, self, alias).filter(
                models.Q(source_id=self.pk) | models.Q(target_id=self.pk)
            ).delete()
            with _definition_caller_context(self):
                step = super().delete(*args, **kwargs)
            session = _definition_write_session.get()
            if session is not None and workflow_id not in session.copy_target_ids:
                manager.mark_definition_changed(workflow_id)
            return _combined_delete_results(edges, step)

    def _raise_if_workflow_immutable(self) -> None:
        """Reject writes when this step belongs to an immutable workflow version."""

        if self.workflow_id is None:
            return
        workflow = type(self.workflow)._base_manager.only("status").get(pk=self.workflow_id)
        if workflow.is_immutable:
            raise ValidationError("Published workflow versions are immutable.")


class EdgeQuerySet(DefinitionQuerySet):
    """Guard collection writes to workflow edges."""


class EdgeManager(AngeeManager.from_queryset(EdgeQuerySet)):  # type: ignore[misc]
    """Manager for guarded workflow-edge writes."""


class Edge(AuditMixin, AngeeDataModel):
    """Directed edge between two workflow steps."""

    runtime = True

    sqid_prefix = "wfe_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="edges")
    source = models.ForeignKey("workflows.Step", on_delete=models.CASCADE, related_name="outgoing_edges")
    target = models.ForeignKey("workflows.Step", on_delete=models.CASCADE, related_name="incoming_edges")
    condition = models.SlugField(max_length=100, blank=True, default="")

    objects = EdgeManager()

    class Meta:
        """Django model options for workflow edges."""

        abstract = True
        ordering = ("workflow", "source", "target", "condition")
        rebac_resource_type = "workflows/edge"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(fields=("source", "target", "condition"), name="uniq_workflows_edge_condition"),
        )

    def __str__(self) -> str:
        """Return a compact edge label."""

        return f"{self.source_id}->{self.target_id}:{self.condition}"

    @classmethod
    def resource_write_preparation(cls, resource: Any, dataset: Any) -> ResourceWritePreparation | None:
        """Declare old and proposed workflow parents for a resource edge batch."""

        workflows = set(resource.related_instances(dataset, "workflow"))
        workflows.update(
            instance.workflow
            for xref in dataset["_xref"]
            if (instance := resource.instance_for_xref(xref)) is not None
        )
        if not workflows:
            return None
        workflow_model = cls._meta.get_field("workflow").remote_field.model
        return ResourceWritePreparation(workflow_model, frozenset(row.pk for row in workflows))

    def clean(self) -> None:
        """Validate that an edge is fully contained in one workflow."""

        super().clean()
        errors: dict[str, str] = {}
        if self.workflow_id is not None and self.source_id is not None and self.source.workflow_id != self.workflow_id:
            errors["source"] = "Edge source must belong to the same workflow."
        if self.workflow_id is not None and self.target_id is not None and self.target.workflow_id != self.workflow_id:
            errors["target"] = "Edge target must belong to the same workflow."
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the edge after enforcing parent immutability and validation."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        old_parent_id = None
        if not self._state.adding:
            old_parent_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
                "workflow_id", flat=True
            ).first()
        step_model = self._meta.get_field("source").remote_field.model
        parent_ids = {value for value in (old_parent_id, self.workflow_id) if value is not None}
        manager = type(self.workflow).objects
        with manager._definition_write(parent_ids, using=alias):
            old = None if self._state.adding else _definition_rows(type(self), alias).filter(pk=self.pk).first()
            if not self._state.adding and (old is None or old.workflow_id != old_parent_id):
                raise ValidationError("The stored workflow edge changed while it was being edited.")
            endpoints = dict(
                _definition_rows(step_model, alias)
                .filter(pk__in=(self.source_id, self.target_id))
                .values_list("pk", "workflow_id")
            )
            if endpoints.get(self.source_id) != self.workflow_id:
                raise ValidationError({"source": "Edge source must belong to the same workflow."})
            if endpoints.get(self.target_id) != self.workflow_id:
                raise ValidationError({"target": "Edge target must belong to the same workflow."})
            with _definition_caller_context(self):
                self.full_clean()
            update_fields = kwargs.get("update_fields")
            fields = {"workflow_id", "source_id", "target_id", "condition"}
            updated = None if update_fields is None else set(update_fields)
            considered = fields if updated is None else {
                field for field in fields if field in updated or field.removesuffix("_id") in updated
            }
            changed = old is None or any(
                getattr(old, field) != getattr(self, field)
                for field in considered
            )
            super().save(*args, **kwargs)
            session = _definition_write_session.get()
            if changed and session is not None and self.workflow_id not in session.copy_target_ids:
                for workflow_id in parent_ids:
                    manager.mark_definition_changed(workflow_id)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only edges belonging to mutable workflow rows."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        workflow_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
            "workflow_id", flat=True
        ).first()
        if workflow_id is None:
            return (0, {})
        manager = type(self.workflow).objects
        with manager._definition_write((workflow_id,), using=alias):
            persisted = _definition_rows(type(self), alias).get(pk=self.pk)
            if persisted.workflow_id != workflow_id:
                raise ValidationError("The stored workflow edge changed while it was being deleted.")
            with _definition_caller_context(self):
                result = super().delete(*args, **kwargs)
            session = _definition_write_session.get()
            if session is not None and workflow_id not in session.copy_target_ids:
                manager.mark_definition_changed(workflow_id)
            return result

    def _raise_if_workflow_immutable(self) -> None:
        """Reject writes when this edge belongs to an immutable workflow version."""

        if self.workflow_id is None:
            return
        workflow = type(self.workflow)._base_manager.only("status").get(pk=self.workflow_id)
        if workflow.is_immutable:
            raise ValidationError("Published workflow versions are immutable.")


class TriggerManager(AngeeManager):
    """Manager owning trigger row claims and due schedule priming."""

    def claim_due_event(self, trigger_id: int, *, timestamp: datetime) -> Any | None:
        """Lock and record one enabled event trigger fire if rate limits allow it."""

        with system_context(reason="workflows.event_triggers.claim"), transaction.atomic():
            trigger = (
                self.lock_if_supported()
                .select_related("workflow")
                .filter(pk=trigger_id, kind=TriggerKind.EVENT, enabled=True)
                .first()
            )
            if trigger is None or not trigger.rate_limit_allows(timestamp=timestamp):
                return None
            trigger.record_fire(timestamp=timestamp)
            return trigger

    def claim_due_schedule(self, trigger_id: int, *, timestamp: datetime) -> tuple[Any, datetime] | None:
        """Lock and advance one due schedule trigger if rate limits allow it."""

        with system_context(reason="workflows.schedule_triggers.claim"), transaction.atomic():
            trigger = (
                self.lock_if_supported()
                .select_related("workflow")
                .filter(pk=trigger_id, kind=TriggerKind.SCHEDULE, enabled=True)
                .first()
            )
            if trigger is None or trigger.next_fire_at is None or trigger.next_fire_at > timestamp:
                return None
            due_at = trigger.next_fire_at
            trigger.next_fire_at = trigger.compute_next_fire_at(after=due_at, now=timestamp)
            if not trigger.rate_limit_allows(timestamp=timestamp):
                trigger.save(update_fields={"next_fire_at", "updated_at"})
                return None
            trigger.record_fire(timestamp=timestamp, extra_update_fields=("next_fire_at",))
            return trigger, due_at

    def prime_due_schedules(self, *, timestamp: datetime) -> int:
        """Persist initial fire times for enabled schedules missing ``next_fire_at``."""

        with system_context(reason="workflows.schedule_triggers.prime"):
            trigger_ids = list(
                self.filter(
                    kind=TriggerKind.SCHEDULE,
                    enabled=True,
                    next_fire_at__isnull=True,
                )
                .order_by("pk")
                .values_list("pk", flat=True)
            )

        primed = 0
        for trigger_id in trigger_ids:
            with system_context(reason="workflows.schedule_triggers.prime"), transaction.atomic():
                trigger = (
                    self.lock_if_supported()
                    .filter(pk=trigger_id, kind=TriggerKind.SCHEDULE, enabled=True, next_fire_at__isnull=True)
                    .first()
                )
                if trigger is None:
                    continue
                try:
                    trigger.next_fire_at = trigger.initial_fire_at(now=timestamp)
                except (CroniterBadCronError, ValueError, TypeError):
                    logger.exception(
                        "Skipping workflow schedule trigger %s after initial fire calculation failed.",
                        trigger.pk,
                    )
                    continue
                if trigger.next_fire_at is None:
                    continue
                trigger.save(update_fields={"next_fire_at", "updated_at"})
                primed += 1
        return primed


def check_event_trigger_change_publishers(
    app_configs: list[object] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Report persisted event triggers targeting models outside the change feed."""

    del app_configs, kwargs
    try:
        trigger_model = apps.get_model("workflows", "Trigger")
    except LookupError:
        return []
    try:
        published_labels = _change_publisher_model_labels()
        invalid = list(
            trigger_model._base_manager.filter(kind=TriggerKind.EVENT)
            .exclude(event_model_label="")
            .exclude(event_model_label__in=published_labels)
            .order_by("pk")
            .values_list("pk", "event_model_label")[:20]
        )
    except (OperationalError, ProgrammingError):
        return []
    return [
        checks.Error(
            f"Workflow trigger {pk} targets {label!r}, which is not in the change feed; {_CHANGE_FEED_FIX}.",
            obj=trigger_model,
            id="angee.workflows.E001",
        )
        for pk, label in invalid
    ]


def _change_publisher_model_labels() -> frozenset[str]:
    """Return model labels declared into GraphQL's change feed."""

    from angee.graphql.schema import GraphQLSchemas

    return GraphQLSchemas.from_discovery().change_publisher_model_labels()


class Trigger(AuditMixin, AngeeDataModel):
    """Start rule attached to a workflow lineage head.

    Event triggers consume the GraphQL change feed: their target model must
    declare ``changes()`` so publisher wiring and workflow delivery agree.
    """

    runtime = True

    sqid_prefix = "wft_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="triggers")
    kind = StateField(choices_enum=TriggerKind, default=TriggerKind.MANUAL)
    enabled = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)
    event_model_label = models.CharField(max_length=200, blank=True, default="")
    next_fire_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_fire_at = models.DateTimeField(null=True, blank=True)
    hourly_window_started_at = models.DateTimeField(null=True, blank=True)
    hourly_fire_count = models.PositiveIntegerField(default=0)

    objects = TriggerManager()

    class Meta:
        """Django model options for workflow triggers."""

        abstract = True
        ordering = ("workflow", "kind", "created_at")
        rebac_resource_type = "workflows/trigger"
        rebac_id_attr = "sqid"
        indexes = (
            models.Index(
                fields=("event_model_label",),
                condition=models.Q(kind=TriggerKind.EVENT, enabled=True),
                name="idx_wft_event_enabled",
            ),
        )

    def __str__(self) -> str:
        """Return the trigger's display label."""

        return f"{self.workflow_id}:{self.kind}"

    def clean(self) -> None:
        """Validate lineage ownership and trigger declaration shape."""

        self._sync_index_fields()
        super().clean()
        if self.workflow_id is not None and self.workflow.published_from_id is not None:
            raise ValidationError({"workflow": "Triggers attach only to workflow lineage heads."})
        if not isinstance(self.config, Mapping):
            raise ValidationError({"config": "Trigger config must be a JSON object."})
        if self.kind == TriggerKind.EVENT:
            if not self.event_model_label:
                raise ValidationError({"config": "Event triggers require a model label."})
            if self.event_model_label not in _change_publisher_model_labels():
                raise ValidationError(
                    {
                        "event_model_label": (
                            f"Event trigger target {self.event_model_label!r} is not in the change feed; "
                            f"{_CHANGE_FEED_FIX}."
                        )
                    }
                )
            condition = self.config.get("condition", {})
            if condition is not None and not isinstance(condition, Mapping):
                raise ValidationError({"config": "Event trigger condition must be a JSON object."})
        if self.kind == TriggerKind.SCHEDULE:
            cron = str(self.config.get("cron", "") or "").strip()
            interval = self.config.get("interval_seconds")
            has_interval = interval not in (None, "")
            if bool(cron) == has_interval:
                raise ValidationError({"config": "Schedule triggers require cron or interval_seconds, but not both."})
            if has_interval:
                interval_value = cast(str | int, interval)
                try:
                    parsed_interval = int(interval_value)
                except (TypeError, ValueError) as error:
                    raise ValidationError(
                        {"config": "Schedule interval_seconds must be a positive integer."}
                    ) from error
                if parsed_interval <= 0:
                    raise ValidationError({"config": "Schedule interval_seconds must be a positive integer."})
            if cron:
                try:
                    croniter(cron)
                except CroniterBadCronError as error:
                    raise ValidationError({"config": "Schedule cron is invalid."}) from error

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the trigger after model validation."""

        self._sync_index_fields()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            fields = set(update_fields)
            if {"kind", "config"} & fields:
                fields.add("event_model_label")
                kwargs["update_fields"] = fields
        self.full_clean()
        super().save(*args, **kwargs)

    def enable(self) -> None:
        """Enable this trigger through the model owner."""

        self.enabled = True
        self.save(update_fields={"enabled", "event_model_label", "updated_at"})

    def disable(self) -> None:
        """Disable this trigger through the model owner."""

        self.enabled = False
        self.save(update_fields={"enabled", "event_model_label", "updated_at"})

    def rate_limit_allows(self, *, timestamp: datetime) -> bool:
        """Return whether this trigger can fire at ``timestamp``."""

        cooldown_seconds = optional_non_negative_int(self.config_mapping.get("cooldown_seconds"))
        if cooldown_seconds and self.last_fire_at is not None:
            if self.last_fire_at + timedelta(seconds=cooldown_seconds) > timestamp:
                return False

        hourly_cap = optional_positive_int(self.config_mapping.get("hourly_cap"))
        if hourly_cap is None:
            return True
        window_start = self.hourly_window_started_at
        if window_start is None or timestamp - window_start >= timedelta(hours=1):
            return True
        return int(self.hourly_fire_count) < hourly_cap

    def record_fire(self, *, timestamp: datetime, extra_update_fields: Iterable[str] = ()) -> None:
        """Record one trigger fire and persist rate-limit counters."""

        window_start = self.hourly_window_started_at
        if window_start is None or timestamp - window_start >= timedelta(hours=1):
            self.hourly_window_started_at = timestamp
            self.hourly_fire_count = 0
        self.hourly_fire_count += 1
        self.last_fire_at = timestamp
        self.save(
            update_fields={
                "last_fire_at",
                "hourly_window_started_at",
                "hourly_fire_count",
                "updated_at",
                *extra_update_fields,
            }
        )

    def condition_matches(self, sender: type[models.Model], instance: models.Model) -> bool:
        """Return whether this event trigger matches a saved model instance."""

        condition = self.config_mapping.get("condition", {})
        if not isinstance(condition, Mapping):
            return False
        with system_context(reason="workflows.event_triggers.condition"):
            return sender._default_manager.filter(pk=instance.pk, **dict(condition)).exists()

    def initial_fire_at(self, *, now: datetime) -> datetime | None:
        """Return the first persisted due timestamp for this schedule trigger."""

        interval = optional_positive_int(self.config_mapping.get("interval_seconds"))
        if interval is not None:
            return now + timedelta(seconds=interval)

        cron = str(self.config_mapping.get("cron", "") or "")
        if not cron:
            return None
        return cast(datetime, croniter(cron, now).get_next(datetime))

    def compute_next_fire_at(self, *, after: datetime, now: datetime) -> datetime | None:
        """Return the next scheduled occurrence after ``after`` and later than ``now``."""

        interval = optional_positive_int(self.config_mapping.get("interval_seconds"))
        if interval is not None:
            next_at = after + timedelta(seconds=interval)
            while next_at <= now:
                next_at += timedelta(seconds=interval)
            return next_at

        cron = str(self.config_mapping.get("cron", "") or "")
        if not cron:
            return None
        return cast(datetime, croniter(cron, max(after, now)).get_next(datetime))

    @property
    def config_mapping(self) -> Mapping[str, Any]:
        """Return trigger config when it is a JSON object."""

        return self.config if isinstance(self.config, Mapping) else {}

    def _sync_index_fields(self) -> None:
        """Mirror config-owned event declarations into indexed query fields."""

        if self.kind != TriggerKind.EVENT or not isinstance(self.config, Mapping):
            self.event_model_label = ""
            return
        self.event_model_label = str(self.config.get("model") or self.config.get("model_label") or "").lower()


class WorkflowRun(AuditMixin, RecordRefMixin, AngeeDataModel):
    """One execution of a pinned published workflow version."""

    runtime = True

    record_ref_field_prefix = "subject"

    sqid_prefix = "wfr_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.PROTECT, related_name="runs")
    origin = StateField(choices_enum=RunOrigin, default=RunOrigin.UNKNOWN)
    trigger = models.ForeignKey(
        "workflows.Trigger",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    parent_step_run = models.ForeignKey(
        "workflows.StepRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_runs",
    )
    status = StateField(choices_enum=RunStatus, default=RunStatus.PENDING)
    subject_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    subject_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("subject_content_type", "subject_object_id")
    dedup_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    wake_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deliveries = models.PositiveBigIntegerField(default=0)
    steps_taken = models.PositiveIntegerField(default=0)
    budget_spent = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    status_transitions = StateTransitions(
        status,
        {
            RunStatus.PENDING: [RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELED],
            RunStatus.RUNNING: [RunStatus.WAITING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED],
            RunStatus.WAITING: [RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED],
        },
    )

    objects = WorkflowRunManager()

    class Meta:
        """Django model options for workflow runs."""

        abstract = True
        ordering = ("-created_at", "sqid")
        rebac_resource_type = "workflows/run"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("parent_step_run",),
                condition=models.Q(parent_step_run__isnull=False),
                name="uniq_workflows_run_parent_step_run",
            ),
        )
        indexes = (models.Index(fields=("subject_content_type", "subject_object_id"), name="idx_wfr_subject_ref"),)

    @property
    def is_terminal(self) -> bool:
        """Return whether this run has reached a terminal status."""

        return self.status in RunStatus.TERMINAL

    @classmethod
    def waiting_projection_annotation(cls) -> dict[str, Any]:
        """Return declared active wait kind and the next genuine scheduled wake."""

        step_run = cls._meta.apps.get_model("workflows", "StepRun")
        waiting = step_run.objects.filter(run_id=models.OuterRef("pk"), status=StepRunStatus.WAITING)
        scheduled = waiting.filter(waiting_kind=WaitingKind.SCHEDULED)
        return {
            "_workflow_waiting_kind": models.Case(
                models.When(~models.Q(status=RunStatus.WAITING), then=models.Value("")),
                models.When(
                    models.Exists(waiting.filter(waiting_kind=WaitingKind.APPROVAL)),
                    then=models.Value(WaitingKind.APPROVAL),
                ),
                models.When(
                    models.Exists(waiting.filter(waiting_kind=WaitingKind.EXTERNAL)),
                    then=models.Value(WaitingKind.EXTERNAL),
                ),
                models.When(
                    models.Exists(waiting.filter(waiting_kind=WaitingKind.CHILDREN)),
                    then=models.Value(WaitingKind.CHILDREN),
                ),
                models.When(models.Exists(scheduled), then=models.Value(WaitingKind.SCHEDULED)),
                default=models.Value(""),
                output_field=models.CharField(),
            ),
            "_workflow_next_wake_at": models.Subquery(
                scheduled.filter(run__status=RunStatus.WAITING)
                .order_by("wait_until", "pk")
                .values("wait_until")[:1],
                output_field=models.DateTimeField(),
            ),
        }

    def awaiting_decision(self) -> bool:
        """Return whether this run has an unresolved workflow decision."""

        return self.step_runs.filter(decisions__verdict=Verdict.PENDING).exists()

    @transition(status, source=RunStatus.PENDING, target=RunStatus.RUNNING, on_success=save_state)
    def mark_running(self) -> None:
        """Mark a pending run as actively orchestrating."""

    @transition(status, source=RunStatus.WAITING, target=RunStatus.RUNNING, on_success=save_state)
    def resume(self) -> None:
        """Mark a waiting run as actively orchestrating again."""

    @transition(
        status,
        source=RunStatus.RUNNING,
        target=RunStatus.WAITING,
        on_success=save_state,
    )
    def mark_waiting(self, *, wake_at: Any = None) -> None:
        """Mark a run as waiting on durable external or timer state."""

        self.wake_at = wake_at
        self._transition_fields = {"wake_at"}

    @transition(
        status,
        source=[RunStatus.RUNNING, RunStatus.WAITING],
        target=RunStatus.SUCCEEDED,
        on_success=save_state,
    )
    def mark_succeeded(self) -> None:
        """Mark a run as successful."""

        self.wake_at = None
        self._transition_fields = {"wake_at"}

    @transition(
        status,
        source=[RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING],
        target=RunStatus.FAILED,
        on_success=save_state,
    )
    def mark_failed(self, error: str = "") -> None:
        """Mark a run as failed with an optional durable error message."""

        self.error = error
        self.wake_at = None
        self._transition_fields = {"error", "wake_at"}

    @transition(
        status,
        source=[RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING],
        target=RunStatus.CANCELED,
        on_success=save_state,
    )
    def mark_canceled(self) -> None:
        """Mark a run as canceled."""

        self.wake_at = None
        self._transition_fields = {"wake_at"}

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the run while keeping trigger dedup keys immutable."""

        self._raise_if_dedup_key_changed()
        super().save(*args, **kwargs)

    @classmethod
    def from_db(cls, db: str | None, field_names: list[str], values: list[Any]) -> Self:
        """Capture immutable loaded facts without a save-time SELECT."""

        instance = cast(Self, super().from_db(db, field_names, values))
        if "dedup_key" in field_names:
            instance._loaded_dedup_key = values[field_names.index("dedup_key")]
        return instance

    def _raise_if_dedup_key_changed(self) -> None:
        """Reject updates that alter the immutable trigger-start dedup key."""

        if self._state.adding:
            return
        loaded_dedup_key = getattr(self, "_loaded_dedup_key", self.dedup_key)
        if loaded_dedup_key != self.dedup_key:
            raise ValidationError({"dedup_key": "Workflow run dedup keys are immutable."})

    def debit_budget(self, delta: Mapping[str, int]) -> None:
        """Atomically add usage deltas to this run's budget ledger."""

        if not delta:
            return
        locked = type(self).objects.lock_if_supported().get(pk=self.pk)
        spent = dict(locked.budget_spent or {})
        for key, value in delta.items():
            spent[str(key)] = int(spent.get(str(key), 0)) + int(value)
        locked.budget_spent = spent
        locked.save(update_fields=["budget_spent", "updated_at"])


class StepRunQuerySet(AngeeQuerySet[Any]):
    """Step-run collection writes protecting attempt-owned execution facts."""

    _attempt_owned = frozenset({"attempt", "current_attempt", "current_attempt_id", "effect_key", "effect_generation"})

    def update(self, **kwargs: Any) -> int:
        if self._attempt_owned.intersection(kwargs) and not _attempt_write_active(self.db):
            raise TypeError("Attempt-owned StepRun fields can only be changed by StepAttemptManager.")
        return super().update(**kwargs)

    def bulk_update(self, objs: Iterable[Any], fields: Iterable[str], **kwargs: Any) -> int:
        if self._attempt_owned.intersection(fields) and not _attempt_write_active(self.db):
            raise TypeError("Attempt-owned StepRun fields can only be changed by StepAttemptManager.")
        return super().bulk_update(objs, fields, **kwargs)


class StepRunManager(AngeeManager.from_queryset(StepRunQuerySet)):  # type: ignore[misc]
    """Manager preserving existing StepRun creation with guarded attempt facts."""


class StepRun(AuditMixin, AngeeDataModel):
    """Journal row for one workflow step execution or system-injected event."""

    runtime = True

    sqid_prefix = "wsr_"
    run = models.ForeignKey("workflows.WorkflowRun", on_delete=models.CASCADE, related_name="step_runs")
    step = models.ForeignKey(
        "workflows.Step",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="step_runs",
    )
    system_kind = models.SlugField(max_length=100, blank=True, default="")
    map_index = models.IntegerField(default=-1)
    status = StateField(choices_enum=StepRunStatus, default=StepRunStatus.SCHEDULED)
    previous = models.ManyToManyField("self", symmetrical=False, blank=True, related_name="next_step_runs")
    input = models.JSONField(default=dict, blank=True)
    output = models.JSONField(default=dict, blank=True)
    resume_state = models.JSONField(default=dict, blank=True)
    claimed_deliveries = models.PositiveBigIntegerField(default=0)
    outcome = models.SlugField(max_length=100, blank=True, default="")
    attempt = models.PositiveIntegerField(default=0)
    effect_key = models.UUIDField(null=True, blank=True, editable=False)
    effect_generation = models.PositiveIntegerField(default=0, editable=False)
    current_attempt = models.OneToOneField(
        "workflows.StepAttempt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_for_step_run",
        editable=False,
    )
    wait_until = models.DateTimeField(null=True, blank=True, db_index=True)
    waiting_kind = StateField(choices_enum=WaitingKind, blank=True, default="")
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    stacktrace = models.TextField(blank=True)

    status_transitions = StateTransitions(
        status,
        {
            StepRunStatus.SCHEDULED: [
                StepRunStatus.STARTED,
                StepRunStatus.CANCELED,
                StepRunStatus.SKIPPED,
            ],
            StepRunStatus.STARTED: [
                StepRunStatus.WAITING,
                StepRunStatus.SUCCEEDED,
                StepRunStatus.FAILED,
                StepRunStatus.CANCELED,
            ],
            StepRunStatus.WAITING: [
                StepRunStatus.STARTED,
                StepRunStatus.SUCCEEDED,
                StepRunStatus.FAILED,
                StepRunStatus.CANCELED,
                StepRunStatus.SKIPPED,
            ],
            StepRunStatus.SUCCEEDED: [StepRunStatus.SCHEDULED],
            StepRunStatus.FAILED: [StepRunStatus.SCHEDULED],
            StepRunStatus.CANCELED: [StepRunStatus.SCHEDULED],
            StepRunStatus.SKIPPED: [StepRunStatus.SCHEDULED],
        },
    )

    objects = StepRunManager()

    class Meta:
        """Django model options for workflow step-run journal rows."""

        abstract = True
        ordering = ("created_at", "sqid")
        rebac_resource_type = "workflows/step_run"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(fields=("run", "step", "map_index"), name="uniq_workflows_step_run_map"),
        )

    @property
    def is_terminal(self) -> bool:
        """Return whether this journal row has reached a terminal status."""

        return self.status in StepRunStatus.TERMINAL

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep attempt-owned facts immutable outside the attempt manager."""

        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        if self._state.adding and not _attempt_write_active(alias):
            invalid = self.effect_key is not None or self.effect_generation != 0 or self.current_attempt_id is not None
            if invalid:
                raise ValidationError({"effect_key": "Attempt identity is initialized by StepAttemptManager."})
        elif not _attempt_write_active(alias):
            loaded = system_queryset(type(self), using=alias, lock=None).filter(pk=self.pk).values(
                "attempt", "current_attempt_id", "effect_key", "effect_generation"
            ).get()
            changed = {
                name
                for name in ("current_attempt_id", "effect_key", "effect_generation")
                if loaded[name] != getattr(self, name)
            }
            if loaded["attempt"] != self.attempt and (
                loaded["current_attempt_id"] is not None or loaded["effect_key"] is not None
            ):
                changed.add("attempt")
            if changed:
                raise ValidationError({name: "This field is owned by StepAttemptManager." for name in changed})
        super().save(*args, **kwargs)

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.WAITING],
        target=StepRunStatus.STARTED,
        on_success=save_state,
    )
    def mark_started(self, *, heartbeat_at: Any = None, claimed_deliveries: int = 0) -> None:
        """Claim this row for execution."""

        self.heartbeat_at = heartbeat_at
        self.claimed_deliveries = claimed_deliveries
        self.waiting_kind = ""
        self._transition_fields = {"heartbeat_at", "claimed_deliveries", "waiting_kind"}

    def record_attempt(self, *, heartbeat_at: Any = None) -> None:
        """Record one implementation invocation for this started row."""

        self.attempt += 1
        if heartbeat_at is not None:
            self.heartbeat_at = heartbeat_at
        self.save(update_fields=["attempt", "heartbeat_at", "updated_at"])

    @transition(status, source=StepRunStatus.STARTED, target=StepRunStatus.WAITING, on_success=save_state)
    def mark_waiting(
        self,
        *,
        until: Any = None,
        resume_state: dict[str, Any] | None = None,
        waiting_kind: WaitingKind = WaitingKind.SCHEDULED,
    ) -> None:
        """Persist durable wait conditions for this row."""

        self.wait_until = until
        self.waiting_kind = waiting_kind
        if resume_state is not None:
            self.resume_state = resume_state
        self._transition_fields = {"wait_until", "resume_state", "waiting_kind"}

    def wake(self, *, at: datetime) -> None:
        """Make this waiting journal row due without changing its state.

        Event delivery changes only the durable due time. The engine owns the
        later ``WAITING`` → ``STARTED`` claim and therefore remains the sole
        scheduler of implementation work.
        """

        if self.status != StepRunStatus.WAITING:
            raise TransitionNotAllowed(
                f"StepRun.wake requires status={StepRunStatus.WAITING}; found {self.status}."
            )
        self.wait_until = at
        self.save(update_fields=["wait_until", "updated_at"])

    @transition(
        status,
        source=[StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.SUCCEEDED,
        on_success=save_state,
    )
    def mark_succeeded(self, *, output: Any = None, outcome: str = "") -> None:
        """Persist a successful step result."""

        self.output = output if output is not None else {}
        self.outcome = outcome
        self.error = ""
        self.stacktrace = ""
        self.wait_until = None
        self.waiting_kind = ""
        self._transition_fields = {"output", "outcome", "error", "stacktrace", "wait_until", "waiting_kind"}

    @transition(
        status,
        source=[StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.FAILED,
        on_success=save_state,
    )
    def mark_failed(self, *, error: str = "", stacktrace: str = "", outcome: str = "failed") -> None:
        """Persist a failed step result."""

        self.error = error
        self.stacktrace = stacktrace
        self.outcome = outcome
        self.wait_until = None
        self.waiting_kind = ""
        self._transition_fields = {"error", "stacktrace", "outcome", "wait_until", "waiting_kind"}

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.WAITING],
        target=StepRunStatus.SKIPPED,
        on_success=save_state,
    )
    def mark_skipped(self) -> None:
        """Mark this row as skipped by routing or join semantics."""

        self.wait_until = None
        self.waiting_kind = ""
        self._transition_fields = {"wait_until", "waiting_kind"}

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.CANCELED,
        on_success=save_state,
    )
    def mark_canceled(self) -> None:
        """Mark this row as canceled."""

        self.wait_until = None
        self.waiting_kind = ""
        self._transition_fields = {"wait_until", "waiting_kind"}

    @transition(
        status,
        source=[StepRunStatus.SUCCEEDED, StepRunStatus.FAILED, StepRunStatus.CANCELED, StepRunStatus.SKIPPED],
        target=StepRunStatus.SCHEDULED,
        on_success=save_state,
    )
    def reschedule_for_override(self, *, input: Any = None) -> None:
        """Reset a terminal journal row so a manual override can run it again."""

        self.input = input if input is not None else {}
        self.output = {}
        self.resume_state = {}
        self.claimed_deliveries = 0
        self.outcome = ""
        self.attempt = 0
        self.wait_until = None
        self.waiting_kind = ""
        self.heartbeat_at = None
        self.error = ""
        self.stacktrace = ""
        self._transition_fields = {
            "input",
            "output",
            "resume_state",
            "claimed_deliveries",
            "outcome",
            "attempt",
            "wait_until",
            "waiting_kind",
            "heartbeat_at",
            "error",
            "stacktrace",
        }


class StepAttemptQuerySet(AngeeQuerySet[Any]):
    """Reject collection mutations that would bypass retained-evidence rules."""

    @staticmethod
    def _is_audit_nullification(values: Mapping[str, Any]) -> bool:
        audit_fields = frozenset(field.name for field in AuditMixin._meta.fields)
        return bool(values) and set(values).issubset(audit_fields) and all(value is None for value in values.values())

    def update(self, **kwargs: Any) -> int:
        if self._is_audit_nullification(kwargs):
            return super().update(**kwargs)
        raise TypeError("Step attempts do not support collection updates.")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Step attempts do not support bulk_create().")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        raise TypeError("Step attempts do not support bulk_update().")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise TypeError("Step attempts are retained execution evidence and cannot be deleted.")


class StepAttemptManager(AngeeManager.from_queryset(StepAttemptQuerySet)):  # type: ignore[misc]
    """Allocate, lease, and finalize retained attempts under ancestor locks."""

    @contextmanager
    def _write(self, alias: str, step_run_id: int) -> Iterable[None]:
        connection = connections[alias]
        if not connection.in_atomic_block:
            raise RuntimeError("Attempt writes require an active database transaction.")
        active = _attempt_write_session.get()
        if active is not None:
            if (
                active.alias != alias
                or active.connection_id != id(connection)
                or active.step_run_id != step_run_id
            ):
                raise RuntimeError("An attempt write cannot span database connections or logical step runs.")
            yield
            return
        token = _attempt_write_session.set(_AttemptWriteSession(alias, id(connection), step_run_id))
        try:
            yield
        finally:
            _attempt_write_session.reset(token)

    def _locked_ancestry(self, step_run_id: int, alias: str) -> tuple[Any, Any]:
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        run_id = system_queryset(step_run_model, using=alias, lock=None).values_list("run_id", flat=True).get(
            pk=step_run_id
        )
        run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
        step_run = system_queryset(step_run_model, using=alias, lock=("self",)).get(pk=step_run_id)
        if step_run.run_id != run.pk:
            raise OperationalError("Step run ownership changed while its attempt was being locked.")
        return run, step_run

    def _save_attempt(self, attempt: Any, *, alias: str, **kwargs: Any) -> None:
        """Authorize one exact instance save and consume authority before signals run."""

        connection = connections[alias]
        capability = _AttemptSaveCapability(
            alias=alias,
            connection_id=id(connection),
            step_run_id=attempt.step_run_id,
            instance_id=id(attempt),
            pk=attempt.pk,
            adding=attempt._state.adding,
        )
        token = _attempt_save_capability.set(capability)
        try:
            attempt.save(using=alias, **kwargs)
        finally:
            _attempt_save_capability.reset(token)

    def claim(
        self,
        step_run: Any,
        *,
        cause: AttemptCause = AttemptCause.INITIAL,
        input: AttemptInput = AttemptInput(),
        claimed_at: datetime,
    ) -> AttemptClaim:
        """Claim one logical delivery, returning the existing claim on duplicate admission."""

        self._validate_claim(cause, input)
        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run.pk),
            system_context(reason="workflows.attempt.claim"),
        ):
            run, locked = self._locked_ancestry(step_run.pk, alias)
            if run.is_terminal:
                raise ValidationError({"step_run": "A terminal workflow run cannot claim an attempt."})
            if locked.current_attempt_id is not None:
                current = system_queryset(self.model, using=alias, lock=("self",)).get(pk=locked.current_attempt_id)
                active = current.result_recorded_at is None and current.lease_revoked_at is None
                if locked.status == StepRunStatus.STARTED and active:
                    self._validate_duplicate_claim(current, cause, input)
                    return AttemptClaim(current, False)
                if active:
                    raise ValidationError({"step_run": "This step run already has an active attempt."})
            self._validate_claim_source(locked, cause)
            attempt = self._allocate_locked(locked, cause=cause, input=input, claimed_at=claimed_at, alias=alias)
            locked.mark_started(heartbeat_at=None, claimed_deliveries=run.deliveries)
            self._charge_logical_execution(run, alias=alias)
            return AttemptClaim(attempt, True)

    def fail_preparation(
        self,
        step_run: Any,
        *,
        cause: AttemptCause,
        input: AttemptInput,
        result: AttemptResult,
        claimed_at: datetime,
        recorded_at: datetime,
    ) -> Any:
        """Retain a preparation failure without describing a physical invocation."""

        self._validate_claim(cause, input)
        if result.kind != AttemptResultKind.PREPARATION_ERROR:
            raise ValidationError({"result": "Preparation failure requires a preparation-error result."})
        self._validate_result(result)
        if result.output_present or result.checkpoint_present or result.waiting_kind:
            raise ValidationError({"result": "Preparation failure cannot carry output, checkpoint, or wait state."})
        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run.pk),
            system_context(reason="workflows.attempt.prepare"),
        ):
            run, locked = self._locked_ancestry(step_run.pk, alias)
            if run.is_terminal:
                raise ValidationError({"step_run": "A terminal workflow run cannot record preparation failure."})
            if locked.current_attempt_id is not None:
                current = system_queryset(self.model, using=alias, lock=("self",)).get(pk=locked.current_attempt_id)
                if current.result_recorded_at is None and current.lease_revoked_at is None:
                    raise ValidationError({"step_run": "This step run already has an active attempt."})
            self._validate_claim_source(locked, cause)
            attempt = self._allocate_locked(locked, cause=cause, input=input, claimed_at=claimed_at, alias=alias)
            attempt.result_kind = str(result.kind)
            attempt.result_recorded_at = recorded_at
            attempt.error = result.error
            attempt.stacktrace = result.stacktrace
            attempt.outcome = result.outcome
            self._apply_result(run, locked, attempt, result)
            attempt.applied_at = recorded_at
            self._save_attempt(attempt, alias=alias)
            self._charge_logical_execution(run, alias=alias)
            return attempt

    @staticmethod
    def _charge_logical_execution(run: Any, *, alias: str) -> None:
        """Charge one newly retained logical execution under the locked run owner."""

        run.steps_taken += 1
        run.save(using=alias, update_fields=["steps_taken", "updated_at"])

    def _allocate_locked(
        self, locked: Any, *, cause: AttemptCause, input: AttemptInput, claimed_at: datetime, alias: str
    ) -> Any:
        if locked.effect_key is None:
            locked.effect_key = uuid.uuid4()
        ordinal = locked.attempt + 1
        attempt = self.model(
            step_run=locked,
            ordinal=ordinal,
            cause=str(cause),
            lease_token=uuid.uuid4(),
            input_present=input.present,
            input=input.value,
            input_provenance=input.provenance or {},
            claimed_at=claimed_at,
            effect_key=locked.effect_key,
            effect_generation=locked.effect_generation,
        )
        self._save_attempt(attempt, alias=alias, force_insert=True)
        locked.attempt = ordinal
        locked.current_attempt = attempt
        locked.save(using=alias, update_fields=["attempt", "current_attempt", "effect_key", "updated_at"])
        return attempt

    def admit_invocation(
        self, attempt_id: int, *, lease_token: uuid.UUID, at: datetime
    ) -> InvocationAdmission:
        """Admit exactly the first physical invocation for a current logical claim."""

        alias = self.db
        unresolved = system_queryset(self.model, using=alias, lock=None).get(pk=attempt_id)
        with (
            transaction.atomic(using=alias),
            self._write(alias, unresolved.step_run_id),
            system_context(reason="workflows.attempt.invoke"),
        ):
            run, step_run = self._locked_ancestry(unresolved.step_run_id, alias)
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=attempt_id)
            if (
                run.is_terminal
                or step_run.status != StepRunStatus.STARTED
                or step_run.current_attempt_id != attempt.pk
                or attempt.lease_token != lease_token
                or attempt.lease_revoked_at is not None
                or attempt.result_recorded_at is not None
            ):
                return InvocationAdmission.FENCED
            if attempt.started_at is not None:
                return InvocationAdmission.ALREADY_STARTED
            if attempt.available_at is not None and at < attempt.available_at:
                return InvocationAdmission.NOT_DUE
            timestamps = (attempt.claimed_at, attempt.heartbeat_at, at)
            freshness = max(value for value in timestamps if value)
            attempt.started_at = freshness
            attempt.heartbeat_at = freshness
            self._save_attempt(
                attempt,
                alias=alias,
                update_fields=["started_at", "heartbeat_at", "updated_at"],
            )
            return InvocationAdmission.FIRST_START

    def heartbeat(self, attempt_id: int, *, lease_token: uuid.UUID, at: datetime) -> bool:
        """Refresh a live lease without changing logical lifecycle state."""

        alias = self.db
        unresolved = system_queryset(self.model, using=alias, lock=None).get(pk=attempt_id)
        with (
            transaction.atomic(using=alias),
            self._write(alias, unresolved.step_run_id),
            system_context(reason="workflows.attempt.lease"),
        ):
            run, step_run = self._locked_ancestry(unresolved.step_run_id, alias)
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=attempt_id)
            if (
                run.is_terminal
                or step_run.current_attempt_id != attempt.pk
                or attempt.lease_token != lease_token
                or attempt.lease_revoked_at is not None
                or attempt.result_recorded_at is not None
            ):
                return False
            timestamps = (attempt.claimed_at, attempt.started_at, attempt.heartbeat_at, at)
            freshness = max(value for value in timestamps if value)
            if attempt.started_at is None:
                return False
            attempt.heartbeat_at = freshness
            self._save_attempt(
                attempt,
                alias=alias,
                update_fields=["started_at", "heartbeat_at", "updated_at"],
            )
            return True

    @staticmethod
    def _validate_claim(cause: AttemptCause, input: AttemptInput) -> None:
        if not isinstance(input, AttemptInput):
            raise ValidationError({"input": "Attempt claim requires a typed input envelope."})
        if not input.present and input.value is not None:
            raise ValidationError({"input": "An absent attempt input cannot carry a value."})
        if input.provenance is not None and not isinstance(input.provenance, dict):
            raise ValidationError({"input_provenance": "Input provenance must be a JSON object."})
        if not isinstance(cause, AttemptCause):
            raise ValidationError({"cause": "Attempt claim requires a declared cause."})

    @staticmethod
    def _validate_result(result: AttemptResult) -> None:
        if not isinstance(result.kind, AttemptResultKind):
            raise ValidationError({"result": "Attempt results require a declared result kind."})
        if not result.output_present and result.output is not None:
            raise ValidationError({"output": "An absent attempt output cannot carry a value."})
        if not result.checkpoint_present and result.checkpoint is not None:
            raise ValidationError({"checkpoint": "An absent attempt checkpoint cannot carry a value."})
        if result.waiting_kind and result.waiting_kind not in WaitingKind.values:
            raise ValidationError({"waiting_kind": "Attempt result waiting kind is not declared."})
        wait_facts = result.requested_until is not None or bool(result.decisions) or result.checkpoint_present
        if result.kind == AttemptResultKind.WAIT:
            if result.requested_until is None or result.decisions:
                raise ValidationError({"result": "Wait requires a deadline and cannot declare decisions."})
        elif result.kind == AttemptResultKind.SUSPEND:
            if result.requested_until is not None:
                raise ValidationError({"result": "Suspension cannot carry a timer deadline."})
        elif wait_facts or result.waiting_kind:
            raise ValidationError({"result": "This result kind cannot carry wait or decision facts."})
        if result.checkpoint_present and result.checkpoint is not None and not isinstance(result.checkpoint, dict):
            raise ValidationError({"checkpoint": "A non-null checkpoint must be a JSON object."})
        if result.kind == AttemptResultKind.TRANSIENT_ERROR and (
            not result.error or result.output_present or result.checkpoint_present
        ):
            raise ValidationError(
                {"result": "Transient errors require an error and cannot carry output or checkpoint."}
            )
        for declaration in result.decisions:
            if not declaration.action:
                raise ValidationError({"decisions": "Decision actions cannot be empty."})
            try:
                validate_slug(declaration.action)
                for subject in (*declaration.assignees, *declaration.escalation):
                    SubjectRef.parse(subject)
                if declaration.requester:
                    SubjectRef.parse(declaration.requester)
            except (TypeError, ValueError, ValidationError) as error:
                raise ValidationError({"decisions": "Decision declarations are invalid."}) from error

    @staticmethod
    def _validate_claim_source(step_run: Any, cause: AttemptCause) -> None:
        if step_run.status == StepRunStatus.WAITING and cause != AttemptCause.CONTINUATION:
            raise ValidationError({"cause": "A waiting step run requires a continuation attempt."})
        if step_run.status == StepRunStatus.SCHEDULED and cause != AttemptCause.INITIAL:
            raise ValidationError({"cause": "A scheduled step run requires an initial attempt."})
        if step_run.status not in {StepRunStatus.SCHEDULED, StepRunStatus.WAITING}:
            raise ValidationError({"step_run": "Only a scheduled or waiting step run can claim an attempt."})

    @staticmethod
    def _validate_duplicate_claim(attempt: Any, cause: AttemptCause, input: AttemptInput) -> None:
        if (
            attempt.cause != str(cause)
            or attempt.input_present != input.present
            or attempt.input != input.value
            or attempt.input_provenance != (input.provenance or {})
        ):
            raise ValidationError({"step_run": "The active attempt was claimed with different immutable input."})

    def revoke(
        self, attempt_id: int, *, lease_token: uuid.UUID, reason: LeaseRevocationReason, at: datetime
    ) -> LeaseRevocation:
        """Revoke a current resultless lease while preserving later evidence."""

        if not isinstance(reason, LeaseRevocationReason):
            raise ValidationError({"reason": "Lease revocation requires a declared reason."})
        alias = self.db
        unresolved = system_queryset(self.model, using=alias, lock=None).get(pk=attempt_id)
        with (
            transaction.atomic(using=alias),
            self._write(alias, unresolved.step_run_id),
            system_context(reason="workflows.attempt.revoke"),
        ):
            _, step_run = self._locked_ancestry(unresolved.step_run_id, alias)
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=attempt_id)
            if attempt.lease_token != lease_token or step_run.current_attempt_id != attempt.pk:
                return LeaseRevocation(False, attempt.result_recorded_at is not None)
            if attempt.result_recorded_at is not None:
                return LeaseRevocation(False, True)
            if attempt.lease_revoked_at is not None:
                return LeaseRevocation(attempt.lease_revocation_reason == str(reason), False)
            attempt.lease_revoked_at = at
            attempt.lease_revocation_reason = str(reason)
            self._save_attempt(
                attempt,
                alias=alias,
                update_fields=["lease_revoked_at", "lease_revocation_reason", "updated_at"],
            )
            return LeaseRevocation(True, False)

    def finalize(
        self, attempt_id: int, *, lease_token: uuid.UUID, result: AttemptResult, recorded_at: datetime
    ) -> AttemptFinalization:
        """Retain one result and atomically apply its closed legacy projection."""

        try:
            encoded_decisions = serialize_decision_specs(result.decisions)
            result = replace(result, decisions=deserialize_decision_specs(encoded_decisions))
            self._validate_result(result)
        except (TypeError, ValueError, PydanticSerializationError) as error:
            raise ValidationError({"decisions": "Decision declarations are invalid."}) from error
        alias = self.db
        unresolved = system_queryset(self.model, using=alias, lock=None).get(pk=attempt_id)
        with (
            transaction.atomic(using=alias),
            self._write(alias, unresolved.step_run_id),
            system_context(reason="workflows.attempt.finalize"),
        ):
            run, step_run = self._locked_ancestry(unresolved.step_run_id, alias)
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=attempt_id)
            if attempt.lease_token != lease_token:
                return AttemptFinalization(False, False)
            if attempt.result_recorded_at is not None:
                if not self._result_matches(attempt, result):
                    raise ValidationError({"result": "A different result is already retained for this attempt."})
                applied = attempt.applied_at is not None
                intents: tuple[DecisionTimerIntent, ...] = ()
                if applied and result.kind == AttemptResultKind.SUSPEND:
                    intents = step_run.decisions.model.objects.timer_intents_for(attempt=attempt, using=alias)
                return AttemptFinalization(
                    False,
                    applied,
                    intents,
                    self._retry_intent_for(attempt, alias=alias),
                )
            attempt.result_kind = str(result.kind)
            attempt.result_recorded_at = recorded_at
            attempt.output_present = result.output_present
            attempt.output = result.output
            attempt.checkpoint_present = result.checkpoint_present
            attempt.checkpoint = result.checkpoint
            attempt.error = result.error
            attempt.stacktrace = result.stacktrace
            attempt.outcome = result.outcome
            attempt.waiting_kind = result.waiting_kind
            attempt.result_requested_until = result.requested_until
            attempt.result_decisions = encoded_decisions
            applicable = (
                not run.is_terminal
                and step_run.current_attempt_id == attempt.pk
                and attempt.lease_revoked_at is None
            )
            timer_intents: tuple[DecisionTimerIntent, ...] = ()
            retry_intent: RetryIntent | None = None
            if applicable:
                if result.kind == AttemptResultKind.TRANSIENT_ERROR:
                    retry_intent = self._apply_transient_result(
                        step_run,
                        attempt,
                        result,
                        recorded_at=recorded_at,
                        alias=alias,
                    )
                else:
                    timer_intents = self._apply_result(run, step_run, attempt, result)
                attempt.applied_at = recorded_at
            self._save_attempt(attempt, alias=alias)
            return AttemptFinalization(True, applicable, timer_intents, retry_intent)

    def _retry_intent_for(self, attempt: Any, *, alias: str) -> RetryIntent | None:
        successor = (
            system_queryset(self.model, using=alias, lock=None)
            .filter(retry_of=attempt)
            .values("pk", "lease_token", "available_at")
            .first()
        )
        if successor is None:
            return None
        available_at = successor["available_at"]
        if available_at is None:
            raise OperationalError("An automatic retry successor has no availability time.")
        return RetryIntent(successor["pk"], successor["lease_token"], available_at)

    def _apply_transient_result(
        self,
        step_run: Any,
        attempt: Any,
        result: AttemptResult,
        *,
        recorded_at: datetime,
        alias: str,
    ) -> RetryIntent | None:
        """Project exhaustion or allocate one automatic successor under the existing locks."""

        if attempt.started_at is None or step_run.status != StepRunStatus.STARTED:
            raise ValidationError({"result": "Transient errors require a started current attempt."})
        try:
            policy = retry_policy_from_config(step_run.step.config)
            retry_index = attempt.retry_index + 1
            available_at = (
                recorded_at + timedelta(seconds=policy.delay_for(retry_index))
                if retry_index < policy.max_attempts
                else None
            )
        except (ValidationError, OverflowError) as error:
            attempt.orchestration_error = f"Retry policy could not schedule a successor: {error}"
            self._project_transient_failure(step_run, result)
            return None
        if retry_index >= policy.max_attempts:
            self._project_transient_failure(step_run, result)
            return None
        if available_at is None:
            raise OperationalError("An allowed automatic retry has no availability time.")

        successor = self._allocate_retry_locked(
            step_run,
            retry_of=attempt,
            retry_index=retry_index,
            available_at=available_at,
            claimed_at=recorded_at,
            alias=alias,
        )
        return RetryIntent(successor.pk, successor.lease_token, available_at)

    @staticmethod
    def _project_transient_failure(step_run: Any, result: AttemptResult) -> None:
        step_run.mark_failed(
            error=result.error or "",
            stacktrace=result.stacktrace or "",
            outcome=result.outcome or "failed",
        )

    def _allocate_retry_locked(
        self,
        step_run: Any,
        *,
        retry_of: Any,
        retry_index: int,
        available_at: datetime,
        claimed_at: datetime,
        alias: str,
    ) -> Any:
        """Allocate one automatic successor without charging the logical run again."""

        successor = self.model(
            step_run=step_run,
            ordinal=step_run.attempt + 1,
            cause=str(AttemptCause.AUTOMATIC_RETRY),
            retry_of=retry_of,
            retry_index=retry_index,
            available_at=available_at,
            lease_token=uuid.uuid4(),
            input_present=retry_of.input_present,
            input=copy.deepcopy(retry_of.input),
            input_provenance=copy.deepcopy(retry_of.input_provenance),
            claimed_at=claimed_at,
            effect_key=retry_of.effect_key,
            effect_generation=retry_of.effect_generation,
        )
        self._save_attempt(successor, alias=alias, force_insert=True)
        step_run.attempt = successor.ordinal
        step_run.current_attempt = successor
        step_run.heartbeat_at = None
        step_run.save(
            using=alias,
            update_fields=["attempt", "current_attempt", "heartbeat_at", "updated_at"],
        )
        return successor

    @staticmethod
    def _result_matches(attempt: Any, result: AttemptResult) -> bool:
        """Compare a retry with the retained semantic envelope, excluding delivery time."""

        return (
            attempt.result_kind == str(result.kind)
            and attempt.output_present == result.output_present
            and attempt.output == result.output
            and attempt.checkpoint_present == result.checkpoint_present
            and attempt.checkpoint == result.checkpoint
            and attempt.error == result.error
            and attempt.stacktrace == result.stacktrace
            and attempt.outcome == result.outcome
            and attempt.waiting_kind == result.waiting_kind
            and attempt.result_requested_until == result.requested_until
            and attempt.result_decisions == serialize_decision_specs(result.decisions)
        )

    def _apply_result(
        self, run: Any, step_run: Any, attempt: Any, result: AttemptResult
    ) -> tuple[DecisionTimerIntent, ...]:
        if result.kind == AttemptResultKind.PREPARATION_ERROR:
            if attempt.started_at is not None or step_run.status not in {
                StepRunStatus.SCHEDULED,
                StepRunStatus.WAITING,
            }:
                raise ValidationError({"result": "Preparation errors require an unstarted active step run."})
            step_run.error = result.error or ""
            step_run.stacktrace = result.stacktrace or ""
            step_run.outcome = result.outcome or "failed"
            step_run.wait_until = None
            step_run.waiting_kind = ""
            step_run._transition_fields = {"error", "stacktrace", "outcome", "wait_until", "waiting_kind"}
            step_run.status_transitions.force_state(
                step_run, StepRunStatus.FAILED, reason="attempt preparation failed before invocation"
            )
            return ()
        if attempt.started_at is None or step_run.status != StepRunStatus.STARTED:
            raise ValidationError({"result": "This result requires a started current attempt."})
        if result.kind == AttemptResultKind.DONE:
            step_run.mark_succeeded(output=result.output if result.output_present else None, outcome=result.outcome)
        elif result.kind in {AttemptResultKind.WAIT, AttemptResultKind.SUSPEND}:
            effective_until = result.requested_until
            if result.kind == AttemptResultKind.WAIT and run.deliveries > step_run.claimed_deliveries:
                effective_until = attempt.result_recorded_at
            resume_state = result.checkpoint if result.checkpoint_present else None
            timer_intents: tuple[DecisionTimerIntent, ...] = ()
            if result.kind == AttemptResultKind.SUSPEND:
                decision_model = step_run.decisions.model
                decisions, timer_intents = decision_model.objects.create_for_suspension(
                    step_run=step_run,
                    attempt=attempt,
                    declarations=result.decisions,
                    using=self.db,
                )
                resume_state = dict(resume_state or {})
                if decisions:
                    resume_state["_decision_ids"] = [decision.pk for decision in decisions]
                schemas = {
                    str(decision.pk): dict(spec.decision_schema)
                    for decision, spec in zip(decisions, result.decisions, strict=True)
                    if spec.decision_schema
                }
                if schemas:
                    resume_state["_decision_schemas"] = schemas
            step_run.mark_waiting(
                until=effective_until,
                resume_state=resume_state,
                waiting_kind=result.waiting_kind or WaitingKind.EXTERNAL,
            )
            return timer_intents
        elif result.kind in {AttemptResultKind.ERROR, AttemptResultKind.NO_RESULT}:
            error = result.error or (
                "Step implementation returned no result." if result.kind == AttemptResultKind.NO_RESULT else ""
            )
            step_run.mark_failed(
                error=error,
                stacktrace=result.stacktrace or "",
                outcome=result.outcome or "failed",
            )
        else:
            raise ValidationError({"result": f"Unsupported attempt result kind {result.kind!s}."})
        return ()


class StepAttempt(AuditMixin, AngeeDataModel):
    """Append-only evidence for one physical execution attempt."""

    runtime = True

    sqid_prefix = "wsa_"
    step_run = models.ForeignKey("workflows.StepRun", on_delete=models.PROTECT, related_name="attempts")
    retry_of = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="retry_successor",
        editable=False,
    )
    retry_index = models.PositiveIntegerField(default=0, editable=False)
    available_at = models.DateTimeField(null=True, blank=True, db_index=True, editable=False)
    ordinal = models.PositiveIntegerField()
    cause = models.CharField(max_length=32, choices=[(value.value, value.name.title()) for value in AttemptCause])
    lease_token = models.UUIDField(editable=False)
    effect_key = models.UUIDField(editable=False)
    effect_generation = models.PositiveIntegerField(default=0, editable=False)
    input_present = models.BooleanField(default=False)
    input = models.JSONField(null=True, blank=True)
    input_provenance = models.JSONField(default=dict, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    lease_revoked_at = models.DateTimeField(null=True, blank=True)
    lease_revocation_reason = models.CharField(
        max_length=32,
        blank=True,
        choices=[(value.value, value.name.title()) for value in LeaseRevocationReason],
    )
    result_kind = models.CharField(
        max_length=32,
        blank=True,
        choices=[(value.value, value.name.title()) for value in AttemptResultKind],
    )
    result_recorded_at = models.DateTimeField(null=True, blank=True)
    output_present = models.BooleanField(default=False)
    output = models.JSONField(null=True, blank=True)
    checkpoint_present = models.BooleanField(default=False)
    checkpoint = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    stacktrace = models.TextField(null=True, blank=True)
    outcome = models.SlugField(max_length=100, blank=True, default="")
    waiting_kind = models.CharField(max_length=32, blank=True, default="")
    result_requested_until = models.DateTimeField(null=True, blank=True)
    result_decisions = models.JSONField(default=list, blank=True)
    orchestration_error = models.TextField(blank=True, default="", editable=False)
    applied_at = models.DateTimeField(null=True, blank=True)

    objects = StepAttemptManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        ordering = ("step_run_id", "ordinal")
        constraints = (
            models.UniqueConstraint(fields=("step_run", "ordinal"), name="uniq_workflows_step_attempt_ordinal"),
            models.UniqueConstraint(fields=("lease_token",), name="uniq_workflows_step_attempt_lease"),
            models.CheckConstraint(
                condition=models.Q(input_present=True) | models.Q(input__isnull=True),
                name="chk_wsa_absent_input_null",
            ),
            models.CheckConstraint(
                condition=models.Q(output_present=True) | models.Q(output__isnull=True),
                name="chk_wsa_absent_output_null",
            ),
            models.CheckConstraint(
                condition=models.Q(checkpoint_present=True) | models.Q(checkpoint__isnull=True),
                name="chk_wsa_absent_checkpoint_null",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(lease_revoked_at__isnull=True, lease_revocation_reason="")
                    | (models.Q(lease_revoked_at__isnull=False) & ~models.Q(lease_revocation_reason=""))
                ),
                name="chk_wsa_revocation_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(result_recorded_at__isnull=True, result_kind="")
                    | (models.Q(result_recorded_at__isnull=False) & ~models.Q(result_kind=""))
                ),
                name="chk_wsa_result_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(applied_at__isnull=True)
                    | (models.Q(result_recorded_at__isnull=False) & models.Q(lease_revoked_at__isnull=True))
                ),
                name="chk_wsa_applied_result",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        cause=AttemptCause.AUTOMATIC_RETRY,
                        retry_of__isnull=False,
                        retry_index__gt=0,
                        available_at__isnull=False,
                    )
                    | (
                        ~models.Q(cause=AttemptCause.AUTOMATIC_RETRY)
                        & models.Q(retry_of__isnull=True, retry_index=0, available_at__isnull=True)
                    )
                ),
                name="chk_wsa_retry_series",
            ),
            models.CheckConstraint(
                condition=models.Q(orchestration_error="") | models.Q(result_kind=AttemptResultKind.TRANSIENT_ERROR),
                name="chk_wsa_orchestration_error",
            ),
        )

    @property
    def status(self) -> AttemptStatus:
        """Derive lifecycle display from retained lease and result evidence."""

        if self.result_recorded_at is not None:
            return AttemptStatus.COMPLETED if self.applied_at is not None else AttemptStatus.LATE_RESULT
        if self.lease_revoked_at is not None:
            return AttemptStatus.REVOKED
        if self.started_at is not None:
            return AttemptStatus.RUNNING
        if self.claimed_at is not None:
            return AttemptStatus.CLAIMED
        return AttemptStatus.ALLOCATED

    def save(self, *args: Any, **kwargs: Any) -> None:
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        capability = _attempt_save_capability.get()
        connection = connections[alias]
        if (
            capability is None
            or capability.consumed
            or capability.alias != alias
            or capability.connection_id != id(connection)
            or not connection.in_atomic_block
            or not _attempt_write_active(alias, self.step_run_id)
            or capability.step_run_id != self.step_run_id
            or capability.instance_id != id(self)
            or capability.pk != self.pk
            or capability.adding != self._state.adding
        ):
            raise TypeError("Step attempts can only be saved by StepAttemptManager.")
        capability.consumed = True
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Step attempts are retained execution evidence and cannot be deleted.")


class DecisionQuerySet(AngeeQuerySet[Any]):
    """Decision reads with protected retained-suspension provenance."""

    _PROTECTED_FIELDS = frozenset({"suspension_attempt", "suspension_attempt_id", "declaration_index"})

    def update(self, **kwargs: Any) -> int:
        if self._PROTECTED_FIELDS.intersection(kwargs):
            raise TypeError("Decision suspension provenance is owned by DecisionManager.")
        return super().update(**kwargs)

    def bulk_create(self, objs: Iterable[Any], *args: Any, **kwargs: Any) -> list[Any]:
        rows = list(objs)
        retained = any(
            row.suspension_attempt_id is not None or row.declaration_index is not None for row in rows
        )
        if retained:
            raise TypeError("Decision suspension provenance is owned by DecisionManager.")
        return super().bulk_create(rows, *args, **kwargs)

    def bulk_update(
        self,
        objs: Iterable[Any],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        field_names = tuple(fields)
        if self._PROTECTED_FIELDS.intersection(field_names):
            raise TypeError("Decision suspension provenance is owned by DecisionManager.")
        return super().bulk_update(objs, field_names, batch_size=batch_size)


class DecisionManager(AngeeManager.from_queryset(DecisionQuerySet)):  # type: ignore[misc]
    """Create actionable decisions and their authorization tuples atomically."""

    def timer_intents_for(self, *, attempt: Any, using: str) -> tuple[DecisionTimerIntent, ...]:
        """Reconstruct deterministic post-commit work for an applied suspension."""

        decisions = system_queryset(self.model, using=using, lock=None).filter(
            suspension_attempt=attempt
        ).order_by("declaration_index")
        intents: list[DecisionTimerIntent] = []
        for decision in decisions:
            if decision.escalate_at is not None:
                intents.append(
                    DecisionTimerIntent(
                        DecisionTimerKind.ESCALATE,
                        decision.pk,
                        decision.attempts,
                        decision.escalate_at,
                    )
                )
            if decision.expires_at is not None:
                intents.append(
                    DecisionTimerIntent(
                        DecisionTimerKind.EXPIRE,
                        decision.pk,
                        decision.attempts,
                        decision.expires_at,
                    )
                )
        return tuple(intents)

    def create_for_suspension(
        self,
        *,
        step_run: Any,
        attempt: Any,
        declarations: tuple[DecisionSpec, ...],
        using: str,
    ) -> tuple[tuple[Any, ...], tuple[DecisionTimerIntent, ...]]:
        """Create one validated, ordered batch for an applicable suspension.

        ORM and relationship rows share rollback on the current host's default
        database with REBAC's transactional local backend. Other aliases and
        remote backends require a durable relationship-intent contract before
        this unused API can enter the production execution cutover.
        """
        if using != DEFAULT_DB_ALIAS:
            raise ValidationError(
                {"using": "Atomic decision relationship creation currently requires the default database."}
            )
        if not _attempt_write_active(using, step_run.pk):
            raise RuntimeError("Retained decisions require an active StepAttemptManager transaction.")

        declarations = deserialize_decision_specs(serialize_decision_specs(declarations))

        prepared = tuple(
            (
                spec,
                tuple(SubjectRef.parse(subject) for subject in spec.assignees),
                SubjectRef.parse(spec.requester) if spec.requester else None,
                tuple(SubjectRef.parse(subject) for subject in spec.escalation),
            )
            for spec in declarations
        )
        if attempt.step_run_id != step_run.pk:
            raise ValidationError({"attempt": "The suspension attempt must belong to this step run."})
        if (
            step_run.current_attempt_id != attempt.pk
            or step_run.status != StepRunStatus.STARTED
            or step_run.run.is_terminal
            or attempt.started_at is None
            or attempt.lease_revoked_at is not None
            or attempt.result_kind != str(AttemptResultKind.SUSPEND)
            or attempt.result_recorded_at is None
        ):
            raise ValidationError({"attempt": "Decisions require the current applicable suspension attempt."})

        decisions: list[Any] = []
        timer_intents: list[DecisionTimerIntent] = []
        with transaction.atomic(using=using):
            connection = connections[using]
            manager = self.db_manager(using)
            for index, (spec, assignees, requester, escalation) in enumerate(prepared):
                token = _decision_write_session.set(
                    _DecisionWriteSession(using, id(connection), step_run.pk, attempt.pk, index)
                )
                try:
                    decision = manager.create(
                        step_run=step_run,
                        suspension_attempt=attempt,
                        declaration_index=index,
                        priority=spec.priority,
                        action=spec.action,
                        payload=spec.payload,
                        max_attempts=spec.max_attempts,
                        expires_at=spec.expires_at,
                        escalate_at=spec.escalate_at,
                    )
                finally:
                    _decision_write_session.reset(token)
                resource = to_object_ref(decision)
                relationships = [
                    RelationshipTuple(resource=resource, relation="assignee", subject=subject)
                    for subject in assignees
                ]
                if requester is not None:
                    relationships.append(
                        RelationshipTuple(resource=resource, relation="requester", subject=requester)
                    )
                relationships.extend(
                    RelationshipTuple(resource=resource, relation="escalation", subject=subject)
                    for subject in escalation
                )
                if relationships:
                    write_relationships(relationships)
                decisions.append(decision)
                if spec.escalate_at is not None:
                    timer_intents.append(
                        DecisionTimerIntent(
                            DecisionTimerKind.ESCALATE, decision.pk, decision.attempts, spec.escalate_at
                        )
                    )
                if spec.expires_at is not None:
                    timer_intents.append(
                        DecisionTimerIntent(
                            DecisionTimerKind.EXPIRE, decision.pk, decision.attempts, spec.expires_at
                        )
                    )
        return tuple(decisions), tuple(timer_intents)


class Decision(AuditMixin, AngeeDataModel):
    """One awaited resolution slot for a suspended step-run."""

    runtime = True
    _form_schema_state_attribute = "_workflows_form_schema_state"

    sqid_prefix = "wdc_"
    step_run = models.ForeignKey("workflows.StepRun", on_delete=models.CASCADE, related_name="decisions")
    suspension_attempt = models.ForeignKey(
        "workflows.StepAttempt", on_delete=models.PROTECT, null=True, blank=True, related_name="decisions"
    )
    declaration_index = models.PositiveIntegerField(null=True, blank=True, editable=False)
    priority = models.IntegerField(default=0)
    action = models.SlugField(max_length=100)
    payload = models.JSONField(default=dict, blank=True)
    verdict = StateField(choices_enum=Verdict, default=Verdict.PENDING)
    resolution = models.JSONField(default=dict, blank=True)
    resolved_by = models.CharField(max_length=255, blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    escalate_at = models.DateTimeField(null=True, blank=True, db_index=True)

    verdict_transitions = StateTransitions(
        verdict,
        {
            Verdict.PENDING: [
                Verdict.COMPLETED,
                Verdict.REJECTED,
                Verdict.ESCALATED,
                Verdict.EXPIRED,
            ],
        },
    )

    objects = DecisionManager()

    class Meta:
        """Django model options for workflow decisions."""

        abstract = True
        ordering = ("step_run", "priority", "declaration_index", "created_at", "sqid")
        rebac_resource_type = "workflows/decision"
        rebac_id_attr = "sqid"
        indexes = (models.Index(fields=("step_run", "verdict", "priority"), name="idx_wdc_step_verdict"),)
        constraints = (
            models.UniqueConstraint(
                fields=("suspension_attempt", "declaration_index"), name="uniq_wdc_attempt_declaration"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(suspension_attempt__isnull=True, declaration_index__isnull=True)
                    | models.Q(suspension_attempt__isnull=False, declaration_index__isnull=False)
                ),
                name="chk_wdc_declaration_source_pair",
            ),
        )

    @property
    def is_terminal(self) -> bool:
        """Return whether this decision has a terminal verdict."""

        return self.verdict in Verdict.TERMINAL

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep retained suspension provenance immutable outside its manager."""

        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        if self._state.adding:
            if (
                (self.suspension_attempt_id is not None or self.declaration_index is not None)
                and not _decision_write_active(alias, self)
            ):
                raise TypeError("Decision suspension provenance is owned by DecisionManager.")
        else:
            retained = system_queryset(type(self), using=alias, lock=None).filter(pk=self.pk).values(
                "suspension_attempt_id", "declaration_index"
            ).get()
            if retained["suspension_attempt_id"] != self.suspension_attempt_id or retained[
                "declaration_index"
            ] != self.declaration_index:
                raise TypeError("Decision suspension provenance is immutable.")
        super().save(*args, **kwargs)

    @classmethod
    def form_schema_annotation(cls) -> dict[str, Any]:
        """Return the narrow ORM projection consumed by :attr:`form_schema`."""

        return {cls._form_schema_state_attribute: models.F("step_run__resume_state")}

    @property
    def form_schema(self) -> dict[str, Any] | None:
        """Return the enforced JSON-authored form schema, excluding Python model schemas."""

        state = getattr(self, self._form_schema_state_attribute, None)
        if not isinstance(state, dict):
            state = self.step_run.resume_state
        if not isinstance(state, dict):
            return None
        schemas = state.get("_decision_schemas", {})
        if isinstance(schemas, dict) and str(self.pk) in schemas:
            schema = schemas[str(self.pk)]
            return dict(schema) if isinstance(schema, dict) and schema else None
        gate = state.get("gate")
        if isinstance(gate, dict):
            schema = gate.get("decision_schema")
            return dict(schema) if isinstance(schema, dict) and schema else None
        return None

    @transition(verdict, source=Verdict.PENDING, target=Verdict.COMPLETED, on_success=save_state)
    def mark_completed(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as completed."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.REJECTED, on_success=save_state)
    def mark_rejected(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as rejected."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.ESCALATED, on_success=save_state)
    def mark_escalated(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as escalated."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.EXPIRED, on_success=save_state)
    def mark_expired(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as expired."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    def record_invalid_resolution(self) -> None:
        """Record one failed validation attempt while leaving the slot pending."""

        self.attempts += 1
        self.save(update_fields=["attempts", "updated_at"])

    def resolve(self, verdict: Verdict, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot through the transition matching ``verdict``."""

        if verdict == Verdict.COMPLETED:
            self.mark_completed(resolution=resolution, resolved_by=resolved_by)
        elif verdict == Verdict.REJECTED:
            self.mark_rejected(resolution=resolution, resolved_by=resolved_by)
        elif verdict == Verdict.ESCALATED:
            self.mark_escalated(resolution=resolution, resolved_by=resolved_by)
        elif verdict == Verdict.EXPIRED:
            self.mark_expired(resolution=resolution, resolved_by=resolved_by)
        else:
            raise ValidationError({"verdict": "Decision verdict must be terminal."})

    def _set_resolution(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Persist normalized resolution audit fields for a terminal verdict."""

        self.resolution = resolution if resolution is not None else {}
        self.resolved_by = resolved_by
        self._transition_fields = {"resolution", "resolved_by"}


@dataclass(slots=True)
class _DispatchSaveCapability:
    alias: str
    connection_id: int
    instance_id: int
    pk: Any
    adding: bool
    kind: str
    target: tuple[int | None, int | None, int | None, int | None]
    consumed: bool = False


_dispatch_save_capability: ContextVar[_DispatchSaveCapability | None] = ContextVar(
    "workflow_dispatch_save_capability", default=None
)


@dataclass(slots=True)
class _DispatchConsumeSession:
    alias: str
    connection_id: int
    outer_atomic_id: int
    kind: str
    target_id: int
    generation: int | None
    dispatch_id: int
    lease_token: uuid.UUID | None
    consumed: bool = False


_dispatch_consume_session: ContextVar[_DispatchConsumeSession | None] = ContextVar(
    "workflow_dispatch_consume_session", default=None
)


class WorkflowDispatchQuerySet(AngeeQuerySet[Any]):
    """Read durable delivery intents without exposing collection mutation bypasses."""

    @staticmethod
    def _is_audit_nullification(values: Mapping[str, Any]) -> bool:
        audit_fields = frozenset(field.name for field in AuditMixin._meta.fields)
        return bool(values) and set(values).issubset(audit_fields) and all(value is None for value in values.values())

    def update(self, **kwargs: Any) -> int:
        if self._is_audit_nullification(kwargs):
            return super().update(**kwargs)
        raise TypeError("Workflow dispatches do not support collection updates.")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Workflow dispatches do not support bulk_create().")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        raise TypeError("Workflow dispatches do not support bulk_update().")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise TypeError("Workflow dispatches are durable delivery evidence and cannot be deleted.")


class WorkflowDispatchManager(AngeeManager.from_queryset(WorkflowDispatchQuerySet)):  # type: ignore[misc]
    """Schedule intents and own bounded publication telemetry."""

    def _save(self, dispatch: Any, *, alias: str, **kwargs: Any) -> None:
        connection = connections[alias]
        capability = _DispatchSaveCapability(
            alias,
            id(connection),
            id(dispatch),
            dispatch.pk,
            dispatch._state.adding,
            str(dispatch.kind),
            dispatch.target_identity,
        )
        token = _dispatch_save_capability.set(capability)
        try:
            dispatch.save(using=alias, **kwargs)
        finally:
            _dispatch_save_capability.reset(token)

    @contextmanager
    def _owner_transition(
        self,
        *,
        dispatch_id: int,
        lease_token: uuid.UUID | None,
        at: datetime,
        using: str,
    ) -> Iterator[DispatchPreflight]:
        """Lock canonical ancestry and preflight one exact delivery intent."""

        connection = connections[using]
        if not connection.in_atomic_block or not connection.atomic_blocks:
            raise RuntimeError("Dispatch consumption requires the owning database transaction.")
        if _dispatch_consume_session.get() is not None:
            raise RuntimeError("Dispatch consumption authority is single-use and cannot nest.")
        unresolved = system_queryset(self.model, using=using, lock=None).get(pk=dispatch_id)
        if unresolved.kind == WorkflowDispatchKind.ADVANCE:
            run_model = self.model._meta.get_field("run").remote_field.model
            locked_run = system_queryset(run_model, using=using, lock=("self",)).get(pk=unresolved.run_id)
            if locked_run.pk != unresolved.run_id:
                raise OperationalError("Advance dispatch ancestry changed while locking.")
        elif unresolved.kind == WorkflowDispatchKind.EXECUTE:
            attempt_model = self.model._meta.get_field("step_attempt").remote_field.model
            ancestry = system_queryset(attempt_model, using=using, lock=None).values(
                "step_run_id", "step_run__run_id"
            ).get(pk=unresolved.step_attempt_id)
            step_run_model = attempt_model._meta.get_field("step_run").remote_field.model
            run_model = step_run_model._meta.get_field("run").remote_field.model
            locked_run = system_queryset(run_model, using=using, lock=("self",)).get(
                pk=ancestry["step_run__run_id"]
            )
            locked_step_run = system_queryset(step_run_model, using=using, lock=("self",)).get(
                pk=ancestry["step_run_id"]
            )
            locked_attempt = system_queryset(attempt_model, using=using, lock=("self",)).get(
                pk=unresolved.step_attempt_id
            )
            if (
                locked_step_run.run_id != locked_run.pk
                or locked_attempt.step_run_id != locked_step_run.pk
            ):
                raise OperationalError("Execution dispatch ancestry changed while locking.")
        else:
            decision_model = self.model._meta.get_field("decision").remote_field.model
            ancestry = system_queryset(decision_model, using=using, lock=None).values(
                "step_run_id", "step_run__run_id", "suspension_attempt_id"
            ).get(pk=unresolved.decision_id)
            step_run_model = decision_model._meta.get_field("step_run").remote_field.model
            run_model = step_run_model._meta.get_field("run").remote_field.model
            locked_run = system_queryset(run_model, using=using, lock=("self",)).get(
                pk=ancestry["step_run__run_id"]
            )
            locked_step_run = system_queryset(step_run_model, using=using, lock=("self",)).get(
                pk=ancestry["step_run_id"]
            )
            locked_attempt = None
            if ancestry["suspension_attempt_id"] is not None:
                attempt_model = self.model._meta.get_field("step_attempt").remote_field.model
                locked_attempt = system_queryset(attempt_model, using=using, lock=("self",)).get(
                    pk=ancestry["suspension_attempt_id"]
                )
            locked_decision = system_queryset(decision_model, using=using, lock=("self",)).get(
                pk=unresolved.decision_id
            )
            if (
                locked_step_run.run_id != locked_run.pk
                or locked_decision.step_run_id != locked_step_run.pk
                or locked_decision.suspension_attempt_id != ancestry["suspension_attempt_id"]
                or (
                    locked_attempt is not None
                    and locked_attempt.step_run_id != locked_step_run.pk
                )
            ):
                raise OperationalError("Decision dispatch ancestry changed while locking.")
        dispatch = system_queryset(self.model, using=using, lock=None).select_related(
            "step_attempt"
        ).get(pk=dispatch_id)
        envelope = dispatch.envelope
        if envelope.lease_token != lease_token:
            yield DispatchPreflight(envelope, DispatchPreflightDisposition.FENCED)
            return
        if dispatch.consumed_at is not None:
            yield DispatchPreflight(envelope, DispatchPreflightDisposition.DUPLICATE)
            return
        if at < dispatch.available_at:
            yield DispatchPreflight(envelope, DispatchPreflightDisposition.EARLY)
            return
        token = _dispatch_consume_session.set(
            _DispatchConsumeSession(
                using,
                id(connection),
                id(connection.atomic_blocks[0]),
                str(dispatch.kind),
                envelope.target_id,
                dispatch.generation,
                dispatch.pk,
                lease_token,
            )
        )
        try:
            yield DispatchPreflight(envelope, DispatchPreflightDisposition.READY)
            session = _dispatch_consume_session.get()
            if session is not None and not session.consumed:
                raise RuntimeError("Ready dispatch transition exited without consuming its intent.")
        finally:
            _dispatch_consume_session.reset(token)

    def _consume_locked(
        self,
        dispatch_id: int,
        *,
        at: datetime,
        fenced: bool = False,
    ) -> DispatchConsumption:
        """Consume one exact intent last in an already locked domain transition."""

        alias = self.db
        session = _dispatch_consume_session.get()
        connection = connections[alias]
        if (
            session is None
            or session.alias != alias
            or session.connection_id != id(connection)
            or not connection.in_atomic_block
            or not connection.atomic_blocks
            or session.outer_atomic_id != id(connection.atomic_blocks[0])
            or session.consumed
            or session.dispatch_id != dispatch_id
        ):
            raise RuntimeError("Dispatch consumption requires exact domain-owner authority.")
        dispatch = system_queryset(self.model, using=alias, lock=("self",)).get(pk=dispatch_id)
        if (
            dispatch.kind != session.kind
            or dispatch.envelope.target_id != session.target_id
            or dispatch.generation != session.generation
            or dispatch.envelope.lease_token != session.lease_token
        ):
            raise RuntimeError("Dispatch consumption authority does not match this intent.")
        if dispatch.consumed_at is not None or at < dispatch.available_at:
            raise RuntimeError("Ready dispatch admission changed before consumption.")
        session.consumed = True
        dispatch.consumed_at = at
        self._save(dispatch, alias=alias, update_fields=["consumed_at", "updated_at"])
        return DispatchConsumption.FENCED if fenced else DispatchConsumption.CONSUMED

    def schedule_advance(self, run: Any, *, available_at: datetime) -> Any:
        """Create one independent run advance after locking its owner."""

        alias = self.db
        run_model = self.model._meta.get_field("run").remote_field.model
        with transaction.atomic(using=alias), system_context(reason="workflows.dispatch.schedule_advance"):
            locked = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run.pk)
            dispatch = self.model(kind=WorkflowDispatchKind.ADVANCE, run=locked, available_at=available_at)
            self._save(dispatch, alias=alias, force_insert=True)
            return dispatch

    def schedule_execute(self, attempt: Any) -> tuple[Any, bool]:
        """Ensure one execution intent using the attempt's immutable availability."""

        alias = self.db
        attempt_model = self.model._meta.get_field("step_attempt").remote_field.model
        row = system_queryset(attempt_model, using=alias, lock=None).values("step_run_id").get(pk=attempt.pk)
        step_run_model = attempt_model._meta.get_field("step_run").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        run_id = system_queryset(step_run_model, using=alias, lock=None).values_list("run_id", flat=True).get(
            pk=row["step_run_id"]
        )
        with transaction.atomic(using=alias), system_context(reason="workflows.dispatch.schedule_execute"):
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
            step_run = system_queryset(step_run_model, using=alias, lock=("self",)).get(pk=row["step_run_id"])
            locked = system_queryset(attempt_model, using=alias, lock=("self",)).get(pk=attempt.pk)
            if locked.step_run_id != step_run.pk or step_run.run_id != run.pk:
                raise OperationalError("Execution dispatch ancestry changed while locking.")
            existing = system_queryset(self.model, using=alias, lock=("self",)).filter(
                kind=WorkflowDispatchKind.EXECUTE, step_attempt=locked
            ).first()
            if existing is not None:
                return existing, False
            if (
                run.is_terminal
                or step_run.status != StepRunStatus.STARTED
                or step_run.current_attempt_id != locked.pk
                or locked.lease_revoked_at is not None
                or locked.started_at is not None
                or locked.result_recorded_at is not None
            ):
                raise ValidationError({"attempt": "Execution dispatch requires the current unfinished attempt."})
            available_at = locked.available_at or locked.claimed_at
            if available_at is None:
                raise ValidationError({"attempt": "Execution dispatch requires a claimed availability time."})
            dispatch = self.model(
                kind=WorkflowDispatchKind.EXECUTE,
                step_attempt=locked,
                available_at=available_at,
            )
            self._save(dispatch, alias=alias, force_insert=True)
            return dispatch, True

    def schedule_decision(self, kind: WorkflowDispatchKind, decision: Any) -> tuple[Any, bool]:
        """Ensure one timer intent from the locked Decision deadline and generation."""

        if kind not in {WorkflowDispatchKind.DECISION_EXPIRE, WorkflowDispatchKind.DECISION_ESCALATE}:
            raise ValidationError({"kind": "Decision dispatch kind must be expire or escalate."})
        alias = self.db
        decision_model = self.model._meta.get_field("decision").remote_field.model
        ancestry = system_queryset(decision_model, using=alias, lock=None).values(
            "step_run_id", "step_run__run_id", "suspension_attempt_id"
        ).get(pk=decision.pk)
        step_run_model = decision_model._meta.get_field("step_run").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        attempt_model = self.model._meta.get_field("step_attempt").remote_field.model
        with transaction.atomic(using=alias), system_context(reason="workflows.dispatch.schedule_decision"):
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=ancestry["step_run__run_id"])
            step_run = system_queryset(step_run_model, using=alias, lock=("self",)).get(pk=ancestry["step_run_id"])
            attempt = None
            if ancestry["suspension_attempt_id"] is not None:
                attempt = system_queryset(attempt_model, using=alias, lock=("self",)).get(
                    pk=ancestry["suspension_attempt_id"]
                )
            locked = system_queryset(decision_model, using=alias, lock=("self",)).get(pk=decision.pk)
            if locked.step_run_id != step_run.pk or step_run.run_id != run.pk or (
                attempt is not None and attempt.step_run_id != step_run.pk
            ):
                raise OperationalError("Decision dispatch ancestry changed while locking.")
            existing = system_queryset(self.model, using=alias, lock=("self",)).filter(
                kind=kind, decision=locked, generation=locked.attempts
            ).first()
            if existing is not None:
                return existing, False
            available_at = (
                locked.expires_at
                if kind == WorkflowDispatchKind.DECISION_EXPIRE
                else locked.escalate_at
            )
            if available_at is None:
                raise ValidationError({"decision": "Decision dispatch requires its declared deadline."})
            if locked.verdict != Verdict.PENDING or step_run.status != StepRunStatus.WAITING or run.is_terminal:
                raise ValidationError({"decision": "Decision timer dispatch requires a current pending decision."})
            if attempt is not None and (
                step_run.current_attempt_id != attempt.pk
                or attempt.result_kind != str(AttemptResultKind.SUSPEND)
                or attempt.applied_at is None
            ):
                raise ValidationError({"decision": "Decision timer dispatch requires its applied suspension."})
            dispatch = self.model(
                kind=kind,
                decision=locked,
                generation=locked.attempts,
                available_at=available_at,
            )
            self._save(dispatch, alias=alias, force_insert=True)
            return dispatch, True

    def due_envelopes(self, *, now: datetime, limit: int) -> tuple[WorkflowDispatchEnvelope, ...]:
        """Return a fair bounded publication snapshot without holding row locks."""

        with system_context(reason="workflows.dispatch.due"):
            rows = list(
                self.filter(
                    consumed_at__isnull=True,
                    available_at__lte=now,
                    next_send_at__lte=now,
                ).select_related("step_attempt").order_by("next_send_at", "available_at", "pk")[:limit]
            )
        return tuple(row.envelope for row in rows)

    def record_publication(self, dispatch_id: int, *, attempted_at: datetime, error: str) -> None:
        """Record bounded send telemetry after one unlocked transport call."""

        alias = self.db
        with transaction.atomic(using=alias), system_context(reason="workflows.dispatch.telemetry"):
            dispatch = system_queryset(self.model, using=alias, lock=("self",)).get(pk=dispatch_id)
            dispatch.send_count += 1
            fields = ["send_count", "updated_at"]
            if dispatch.last_sent_at is None or attempted_at >= dispatch.last_sent_at:
                dispatch.last_sent_at = attempted_at
                dispatch.last_send_error = "Transport send failed." if error else ""
                seconds = min(300, 2 ** min(dispatch.send_count, 8)) if error else 30
                dispatch.next_send_at = attempted_at + timedelta(seconds=seconds)
                fields.extend(["last_sent_at", "last_send_error", "next_send_at"])
            self._save(
                dispatch,
                alias=alias,
                update_fields=fields,
            )


class WorkflowDispatch(AuditMixin, AngeeDataModel):
    """One durable, resendable workflow delivery intent."""

    runtime = True

    sqid_prefix = "wfd_"
    kind = models.CharField(
        max_length=32,
        choices=[(value.value, value.name.title()) for value in WorkflowDispatchKind],
    )
    run = models.ForeignKey(
        "workflows.WorkflowRun", on_delete=models.PROTECT, null=True, blank=True, related_name="dispatches"
    )
    step_attempt = models.ForeignKey(
        "workflows.StepAttempt", on_delete=models.PROTECT, null=True, blank=True, related_name="dispatches"
    )
    decision = models.ForeignKey(
        "workflows.Decision", on_delete=models.PROTECT, null=True, blank=True, related_name="dispatches"
    )
    generation = models.PositiveIntegerField(null=True, blank=True, editable=False)
    available_at = models.DateTimeField(db_index=True, editable=False)
    next_send_at = models.DateTimeField(db_index=True, editable=False)
    consumed_at = models.DateTimeField(null=True, blank=True, editable=False)
    send_count = models.PositiveIntegerField(default=0, editable=False)
    last_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_send_error = models.CharField(max_length=64, blank=True, default="", editable=False)

    objects = WorkflowDispatchManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        ordering = ("available_at", "pk")
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(kind=WorkflowDispatchKind.ADVANCE, run__isnull=False, step_attempt__isnull=True,
                             decision__isnull=True, generation__isnull=True)
                    | models.Q(kind=WorkflowDispatchKind.EXECUTE, run__isnull=True, step_attempt__isnull=False,
                               decision__isnull=True, generation__isnull=True)
                    | models.Q(kind__in=[WorkflowDispatchKind.DECISION_EXPIRE,
                                         WorkflowDispatchKind.DECISION_ESCALATE], run__isnull=True,
                               step_attempt__isnull=True, decision__isnull=False, generation__isnull=False)
                ),
                name="chk_wfd_target_shape",
            ),
            models.UniqueConstraint(
                fields=("step_attempt",),
                condition=models.Q(kind=WorkflowDispatchKind.EXECUTE),
                name="uniq_wfd_execute_attempt",
            ),
            models.UniqueConstraint(
                fields=("kind", "decision", "generation"),
                condition=models.Q(kind__in=[WorkflowDispatchKind.DECISION_EXPIRE,
                                             WorkflowDispatchKind.DECISION_ESCALATE]),
                name="uniq_wfd_decision_timer",
            ),
        )

    @property
    def target_identity(self) -> tuple[int | None, int | None, int | None, int | None]:
        """Return immutable target fields used by the exact-row write guard."""

        return self.run_id, self.step_attempt_id, self.decision_id, self.generation

    @property
    def envelope(self) -> WorkflowDispatchEnvelope:
        """Return the identifier-only transport message for this validated row."""

        kind = WorkflowDispatchKind(self.kind)
        target_id = self.run_id if kind == WorkflowDispatchKind.ADVANCE else (
            self.step_attempt_id if kind == WorkflowDispatchKind.EXECUTE else self.decision_id
        )
        if target_id is None:
            raise ValueError("Workflow dispatch target does not match its kind.")
        lease_token = self.step_attempt.lease_token if kind == WorkflowDispatchKind.EXECUTE else None
        return WorkflowDispatchEnvelope(self.pk, kind, target_id, self.generation, lease_token)

    def save(self, *args: Any, **kwargs: Any) -> None:
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        capability = _dispatch_save_capability.get()
        connection = connections[alias]
        if (
            capability is None
            or capability.consumed
            or capability.alias != alias
            or capability.connection_id != id(connection)
            or not connection.in_atomic_block
            or capability.instance_id != id(self)
            or capability.pk != self.pk
            or capability.adding != self._state.adding
            or capability.kind != str(self.kind)
            or capability.target != self.target_identity
        ):
            raise TypeError("Workflow dispatches can only be saved by WorkflowDispatchManager.")
        if not self._state.adding:
            persisted = system_queryset(type(self), using=alias, lock=None).values(
                "kind", "run_id", "step_attempt_id", "decision_id", "generation", "available_at"
            ).get(pk=self.pk)
            if (
                persisted["kind"] != self.kind
                or persisted["run_id"] != self.run_id
                or persisted["step_attempt_id"] != self.step_attempt_id
                or persisted["decision_id"] != self.decision_id
                or persisted["generation"] != self.generation
                or persisted["available_at"] != self.available_at
            ):
                raise TypeError("Workflow dispatch identity and availability are immutable.")
        capability.consumed = True
        if self._state.adding and self.next_send_at is None:
            self.next_send_at = self.available_at
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Workflow dispatches are durable delivery evidence and cannot be deleted.")
