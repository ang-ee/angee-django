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
from collections.abc import Mapping
from typing import Any, Self, cast

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models, transaction

from angee.base.fields import ImplClassField, StateField
from angee.base.impl import ImplDefaultsMixin
from angee.base.mixins import AuditMixin
from angee.base.models import AngeeDataModel, AngeeManager
from angee.base.transitions import StateTransitions, transition
from angee.workflows.steps import StepImpl, validate_retry_config


class WorkflowStatus(models.TextChoices):
    """Publication lifecycle for a workflow definition row."""

    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


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


def _save_workflow_status(instance: models.Model, source: Any, target: Any) -> None:
    """Persist a transition-owned workflow status change."""

    del source, target
    cast("Workflow", instance)._save_status_change()


def _save_run_status(instance: models.Model, source: Any, target: Any) -> None:
    """Persist a transition-owned workflow run status change."""

    del source, target
    cast("WorkflowRun", instance)._save_status_change()


def _save_step_run_status(instance: models.Model, source: Any, target: Any) -> None:
    """Persist a transition-owned step-run status change."""

    del source, target
    cast("StepRun", instance)._save_status_change()


def _save_decision_verdict(instance: models.Model, source: Any, target: Any) -> None:
    """Persist a transition-owned decision verdict change."""

    del source, target
    cast("Decision", instance)._save_verdict_change()


class WorkflowManager(AngeeManager):
    """Manager owning workflow lineage lookups."""

    def current_published_for(self, workflow: Any) -> Any | None:
        """Return the latest published version for ``workflow``'s lineage."""

        head = workflow if getattr(workflow, "published_from_id", None) is None else workflow.published_from
        latest = (
            self.filter(status__in=[WorkflowStatus.PUBLISHED, WorkflowStatus.ARCHIVED])
            .filter(models.Q(pk=head.pk) | models.Q(published_from=head))
            .order_by("-version", "-pk")
            .first()
        )
        if latest is None or latest.status != WorkflowStatus.PUBLISHED:
            return None
        return latest


class Workflow(AuditMixin, AngeeDataModel):
    """Editable workflow lineage head or immutable published workflow version."""

    runtime = True

    sqid_prefix = "wfl_"
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = StateField(choices_enum=WorkflowStatus, default=WorkflowStatus.DRAFT)
    version = models.PositiveIntegerField(default=0)
    published_from = models.ForeignKey(
        "workflows.Workflow",
        on_delete=models.CASCADE,
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

    def __str__(self) -> str:
        """Return the workflow's display label."""

        return self.name

    @transition(status, source=WorkflowStatus.DRAFT, target=WorkflowStatus.PUBLISHED, on_success=_save_workflow_status)
    def mark_published(self) -> None:
        """Mark this copied version as published."""

    @transition(
        status,
        source=WorkflowStatus.PUBLISHED,
        target=WorkflowStatus.ARCHIVED,
        on_success=_save_workflow_status,
    )
    def archive(self) -> None:
        """Archive a published workflow version."""

    def clean(self) -> None:
        """Validate lineage-owned workflow links."""

        super().clean()
        if self.error_workflow_id is not None and self.error_workflow.published_from_id is not None:
            raise ValidationError({"error_workflow": "Error workflow must point to a workflow lineage head."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the workflow after enforcing immutability and model validation."""

        self._raise_if_immutable_save()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only mutable workflow rows."""

        self._raise_if_immutable_save()
        return super().delete(*args, **kwargs)

    def publish(self) -> Self:
        """Copy this draft lineage head into an immutable published version."""

        self.full_clean()
        if self.published_from_id is not None:
            raise ValidationError({"published_from": "Only a workflow lineage head can be published."})
        if self.status != WorkflowStatus.DRAFT:
            raise ValidationError({"status": "Only draft workflows can be published."})
        self._validate_publishable()

        with transaction.atomic():
            draft = type(self).objects.select_for_update().get(pk=self.pk)
            draft._validate_publishable()
            version = draft._next_published_version()
            published = type(self)(
                name=draft.name,
                description=draft.description,
                status=WorkflowStatus.DRAFT,
                version=version,
                published_from=draft,
                error_workflow=draft.error_workflow,
                max_steps=draft.max_steps,
                budget=copy.deepcopy(draft.budget),
            )
            published.save()
            draft._copy_definition_to(published)
            published.mark_published()
            return cast(Self, published)

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
                join_rule=step.join_rule,
                is_entry=step.is_entry,
                position=copy.deepcopy(step.position),
            )
            copied.save()
            step_map[step.pk] = copied
        for edge in self.edges.select_related("source", "target").order_by("pk"):
            edge_model(
                workflow=published,
                source=step_map[edge.source_id],
                target=step_map[edge.target_id],
                condition=edge.condition,
            ).save()

    def _validate_publishable(self) -> None:
        """Require exactly one entry step before publishing."""

        entry_count = self.steps.filter(is_entry=True).count()
        if entry_count != 1:
            raise ValidationError({"steps": "A workflow must have exactly one entry step before publishing."})

    def _save_status_change(self) -> None:
        """Save a transition-owned status change, including immutable rows."""

        self._allow_immutable_status_save = True
        try:
            self.save(update_fields=["status"])
        finally:
            del self._allow_immutable_status_save

    def _raise_if_immutable_save(self) -> None:
        """Reject writes to persisted published or archived workflow definitions."""

        if self._state.adding or getattr(self, "_allow_immutable_status_save", False):
            return
        try:
            persisted = type(self)._base_manager.only("status").get(pk=self.pk)
        except ObjectDoesNotExist:
            return
        if persisted.status in {WorkflowStatus.PUBLISHED, WorkflowStatus.ARCHIVED}:
            raise ValidationError("Published workflow versions are immutable.")


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
    join_rule = StateField(choices_enum=JoinRule, default=JoinRule.ALL_SUCCESS)
    is_entry = models.BooleanField(default=False)
    position = models.JSONField(default=dict, blank=True)

    objects = AngeeManager()

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

    def clean(self) -> None:
        """Validate the step implementation key and config."""

        super().clean()
        try:
            impl = cast(type[StepImpl], self.resolve_impl("step_class", default="handler"))
            impl.validate_config(self.config)
            validate_retry_config(self.config)
        except ValidationError:
            raise
        except Exception as error:
            raise ValidationError({"step_class": "Unknown workflow step class."}) from error

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the step after enforcing parent immutability and validation."""

        self._raise_if_workflow_immutable()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only steps belonging to mutable workflow rows."""

        self._raise_if_workflow_immutable()
        return super().delete(*args, **kwargs)

    def _raise_if_workflow_immutable(self) -> None:
        """Reject writes when this step belongs to an immutable workflow version."""

        if self.workflow_id is None:
            return
        status = type(self.workflow)._base_manager.only("status").get(pk=self.workflow_id).status
        if status in {WorkflowStatus.PUBLISHED, WorkflowStatus.ARCHIVED}:
            raise ValidationError("Published workflow versions are immutable.")


class Edge(AuditMixin, AngeeDataModel):
    """Directed edge between two workflow steps."""

    runtime = True

    sqid_prefix = "wfe_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="edges")
    source = models.ForeignKey("workflows.Step", on_delete=models.CASCADE, related_name="outgoing_edges")
    target = models.ForeignKey("workflows.Step", on_delete=models.CASCADE, related_name="incoming_edges")
    condition = models.SlugField(max_length=100, blank=True, default="")

    objects = AngeeManager()

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

        self._raise_if_workflow_immutable()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only edges belonging to mutable workflow rows."""

        self._raise_if_workflow_immutable()
        return super().delete(*args, **kwargs)

    def _raise_if_workflow_immutable(self) -> None:
        """Reject writes when this edge belongs to an immutable workflow version."""

        if self.workflow_id is None:
            return
        status = type(self.workflow)._base_manager.only("status").get(pk=self.workflow_id).status
        if status in {WorkflowStatus.PUBLISHED, WorkflowStatus.ARCHIVED}:
            raise ValidationError("Published workflow versions are immutable.")


class Trigger(AuditMixin, AngeeDataModel):
    """Start rule attached to a workflow lineage head."""

    runtime = True

    sqid_prefix = "wft_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="triggers")
    kind = StateField(choices_enum=TriggerKind, default=TriggerKind.MANUAL)
    enabled = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)
    event_model_label = models.CharField(max_length=200, blank=True, default="", db_index=True)
    next_fire_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_fire_at = models.DateTimeField(null=True, blank=True)
    hourly_window_started_at = models.DateTimeField(null=True, blank=True)
    hourly_fire_count = models.PositiveIntegerField(default=0)

    objects = AngeeManager()

    class Meta:
        """Django model options for workflow triggers."""

        abstract = True
        ordering = ("workflow", "kind", "created_at")
        rebac_resource_type = "workflows/trigger"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the trigger's display label."""

        return f"{self.workflow_id}:{self.kind}"

    def clean(self) -> None:
        """Validate lineage ownership and trigger declaration shape."""

        super().clean()
        if self.workflow_id is not None and self.workflow.published_from_id is not None:
            raise ValidationError({"workflow": "Triggers attach only to workflow lineage heads."})
        if not isinstance(self.config, Mapping):
            raise ValidationError({"config": "Trigger config must be a JSON object."})
        if self.kind == TriggerKind.EVENT:
            if not self.event_model_label:
                raise ValidationError({"config": "Event triggers require a model label."})
            condition = self.config.get("condition", {})
            if condition is not None and not isinstance(condition, Mapping):
                raise ValidationError({"config": "Event trigger condition must be a JSON object."})

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

    def _sync_index_fields(self) -> None:
        """Mirror config-owned event declarations into indexed query fields."""

        if self.kind != TriggerKind.EVENT or not isinstance(self.config, Mapping):
            self.event_model_label = ""
            return
        self.event_model_label = str(self.config.get("model") or self.config.get("model_label") or "").lower()


class WorkflowRun(AuditMixin, AngeeDataModel):
    """One execution of a pinned published workflow version."""

    runtime = True

    sqid_prefix = "wfr_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.PROTECT, related_name="runs")
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
    artifact_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    artifact_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    artifact = GenericForeignKey("artifact_content_type", "artifact_object_id")
    dedup_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    data = models.JSONField(default=dict, blank=True)
    wake_at = models.DateTimeField(null=True, blank=True, db_index=True)
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

    objects = AngeeManager()

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
        indexes = (
            models.Index(fields=("subject_content_type", "subject_object_id"), name="idx_wfr_subject_ref"),
            models.Index(fields=("artifact_content_type", "artifact_object_id"), name="idx_wfr_artifact_ref"),
        )

    @property
    def outcome_counts(self) -> dict[str, int]:
        """Return per-outcome counts aggregated from the step-run journal."""

        return {
            str(row["outcome"] or ""): int(row["count"])
            for row in self.step_runs.values("outcome").annotate(count=models.Count("pk")).order_by("outcome")
        }

    @property
    def is_terminal(self) -> bool:
        """Return whether this run has reached a terminal status."""

        return self.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED}

    @transition(status, source=RunStatus.PENDING, target=RunStatus.RUNNING, on_success=_save_run_status)
    def mark_running(self) -> None:
        """Mark a pending run as actively orchestrating."""

    @transition(status, source=RunStatus.WAITING, target=RunStatus.RUNNING, on_success=_save_run_status)
    def resume(self) -> None:
        """Mark a waiting run as actively orchestrating again."""

    @transition(
        status,
        source=RunStatus.RUNNING,
        target=RunStatus.WAITING,
        on_success=_save_run_status,
    )
    def mark_waiting(self, *, wake_at: Any = None) -> None:
        """Mark a run as waiting on durable external or timer state."""

        self.wake_at = wake_at
        self._transition_fields = {"wake_at"}

    @transition(
        status,
        source=[RunStatus.RUNNING, RunStatus.WAITING],
        target=RunStatus.SUCCEEDED,
        on_success=_save_run_status,
    )
    def mark_succeeded(self) -> None:
        """Mark a run as successful."""

        self.wake_at = None
        self._transition_fields = {"wake_at"}

    @transition(
        status,
        source=[RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING],
        target=RunStatus.FAILED,
        on_success=_save_run_status,
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
        on_success=_save_run_status,
    )
    def mark_canceled(self) -> None:
        """Mark a run as canceled."""

        self.wake_at = None
        self._transition_fields = {"wake_at"}

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the run while keeping trigger dedup keys immutable."""

        self._raise_if_dedup_key_changed()
        super().save(*args, **kwargs)

    def _save_status_change(self) -> None:
        """Save a transition-owned status change plus fields touched by its body."""

        fields = {"status", *getattr(self, "_transition_fields", set())}
        try:
            self.save(update_fields=fields)
        finally:
            if hasattr(self, "_transition_fields"):
                del self._transition_fields

    def _raise_if_dedup_key_changed(self) -> None:
        """Reject updates that alter the immutable trigger-start dedup key."""

        if self._state.adding:
            return
        try:
            persisted = type(self)._base_manager.only("dedup_key").get(pk=self.pk)
        except ObjectDoesNotExist:
            return
        if persisted.dedup_key != self.dedup_key:
            raise ValidationError({"dedup_key": "Workflow run dedup keys are immutable."})


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
    outcome = models.SlugField(max_length=100, blank=True, default="")
    attempt = models.PositiveIntegerField(default=0)
    wait_until = models.DateTimeField(null=True, blank=True, db_index=True)
    wait_event = models.SlugField(max_length=200, blank=True, default="")
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
        },
    )

    objects = AngeeManager()

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

        return self.status in {
            StepRunStatus.SUCCEEDED,
            StepRunStatus.FAILED,
            StepRunStatus.CANCELED,
            StepRunStatus.SKIPPED,
        }

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.WAITING],
        target=StepRunStatus.STARTED,
        on_success=_save_step_run_status,
    )
    def mark_started(self, *, heartbeat_at: Any = None) -> None:
        """Claim this row for execution."""

        self.heartbeat_at = heartbeat_at
        self._transition_fields = {"heartbeat_at"}

    def record_attempt(self, *, heartbeat_at: Any = None) -> None:
        """Record one implementation invocation for this started row."""

        self.attempt += 1
        if heartbeat_at is not None:
            self.heartbeat_at = heartbeat_at
        self.save(update_fields=["attempt", "heartbeat_at", "updated_at"])

    @transition(status, source=StepRunStatus.STARTED, target=StepRunStatus.WAITING, on_success=_save_step_run_status)
    def mark_waiting(
        self,
        *,
        until: Any = None,
        event: str = "",
        resume_state: dict[str, Any] | None = None,
    ) -> None:
        """Persist durable wait conditions for this row."""

        self.wait_until = until
        self.wait_event = event
        if resume_state is not None:
            self.resume_state = resume_state
        self._transition_fields = {"wait_until", "wait_event", "resume_state"}

    @transition(
        status,
        source=[StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.SUCCEEDED,
        on_success=_save_step_run_status,
    )
    def mark_succeeded(self, *, output: Any = None, outcome: str = "") -> None:
        """Persist a successful step result."""

        self.output = output if output is not None else {}
        self.outcome = outcome
        self.error = ""
        self.stacktrace = ""
        self.wait_until = None
        self.wait_event = ""
        self._transition_fields = {"output", "outcome", "error", "stacktrace", "wait_until", "wait_event"}

    @transition(
        status,
        source=[StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.FAILED,
        on_success=_save_step_run_status,
    )
    def mark_failed(self, *, error: str = "", stacktrace: str = "", outcome: str = "failed") -> None:
        """Persist a failed step result."""

        self.error = error
        self.stacktrace = stacktrace
        self.outcome = outcome
        self.wait_until = None
        self.wait_event = ""
        self._transition_fields = {"error", "stacktrace", "outcome", "wait_until", "wait_event"}

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.WAITING],
        target=StepRunStatus.SKIPPED,
        on_success=_save_step_run_status,
    )
    def mark_skipped(self) -> None:
        """Mark this row as skipped by routing or join semantics."""

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.CANCELED,
        on_success=_save_step_run_status,
    )
    def mark_canceled(self) -> None:
        """Mark this row as canceled."""

    def _save_status_change(self) -> None:
        """Save a transition-owned status change plus fields touched by its body."""

        fields = {"status", *getattr(self, "_transition_fields", set())}
        try:
            self.save(update_fields=fields)
        finally:
            if hasattr(self, "_transition_fields"):
                del self._transition_fields


class Decision(AuditMixin, AngeeDataModel):
    """One awaited resolution slot for a suspended step-run."""

    runtime = True

    sqid_prefix = "wdc_"
    step_run = models.ForeignKey("workflows.StepRun", on_delete=models.CASCADE, related_name="decisions")
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

    objects = AngeeManager()

    class Meta:
        """Django model options for workflow decisions."""

        abstract = True
        ordering = ("step_run", "priority", "created_at", "sqid")
        rebac_resource_type = "workflows/decision"
        rebac_id_attr = "sqid"
        indexes = (
            models.Index(fields=("step_run", "verdict", "priority"), name="idx_wdc_step_verdict"),
        )

    @property
    def is_terminal(self) -> bool:
        """Return whether this decision has a terminal verdict."""

        return self.verdict in {
            Verdict.COMPLETED,
            Verdict.REJECTED,
            Verdict.ESCALATED,
            Verdict.EXPIRED,
        }

    @transition(verdict, source=Verdict.PENDING, target=Verdict.COMPLETED, on_success=_save_decision_verdict)
    def mark_completed(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as completed."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.REJECTED, on_success=_save_decision_verdict)
    def mark_rejected(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as rejected."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.ESCALATED, on_success=_save_decision_verdict)
    def mark_escalated(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as escalated."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.EXPIRED, on_success=_save_decision_verdict)
    def mark_expired(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as expired."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    def record_invalid_resolution(self) -> None:
        """Record one failed validation attempt while leaving the slot pending."""

        self.attempts += 1
        self.save(update_fields=["attempts", "updated_at"])

    def _set_resolution(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Persist normalized resolution audit fields for a terminal verdict."""

        self.resolution = resolution if resolution is not None else {}
        self.resolved_by = resolved_by
        self._transition_fields = {"resolution", "resolved_by"}

    def _save_verdict_change(self) -> None:
        """Save a transition-owned verdict change plus touched resolution fields."""

        fields = {"verdict", *getattr(self, "_transition_fields", set())}
        try:
            self.save(update_fields=fields)
        finally:
            if hasattr(self, "_transition_fields"):
                del self._transition_fields
