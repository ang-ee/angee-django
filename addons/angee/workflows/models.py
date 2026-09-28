"""Abstract definitions and retained execution rows for database workflows."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Now
from django.utils.functional import cached_property

from angee.base.fields import StateField
from angee.base.mixins import AppendOnlyQuerySet, AuditMixin
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet
from angee.base.refs import RecordRefMixin
from angee.resources.mixins import ResourceLoadMixin
from angee.workflows.definition import Definition
from angee.workflows.managers import StepAttemptQuerySet, StepRunManager, WorkflowManager, WorkflowRunManager
from angee.workflows.resources import WorkflowDefinitionResource
from angee.workflows.states import NAME_MAX_LENGTH, AttemptResult, RunStatus, StepRunStatus, WaitingKind
from angee.workflows.steps import Step


def _empty_workflow_output_schema() -> dict[str, Any]:
    """Preserve the default callable used by materialized historical migrations."""

    return {"type": "object", "properties": {}, "additionalProperties": False}


class Workflow(ResourceLoadMixin, AuditMixin, AngeeDataModel):
    """Editable identity and draft, pointing to one immutable published version."""

    runtime = True
    resource_class = WorkflowDefinitionResource
    rebac_grantable = {"editor": "write", "viewer": "write", "starter": "write"}
    sqid_prefix = "wfl_"

    key = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    subject_model = models.CharField(max_length=200, blank=True, default="")
    draft = models.JSONField(default=dict)
    draft_revision = models.PositiveIntegerField(default=0, editable=False)
    layout = models.JSONField(default=dict, blank=True)
    published = models.ForeignKey(
        "workflows.WorkflowVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    objects = WorkflowManager()

    class Meta:
        """Django options for the composed workflow identity."""

        abstract = True
        rebac_resource_type = "workflows/workflow"

    def validate_subject(self, subject: Any) -> None:
        """Require the declared concrete model when this workflow has a subject."""
        if self.subject_model and (
            subject is None or subject._meta.label_lower != self.subject_model
        ):
            raise ValidationError("The workflow subject has the wrong model.")

    def __str__(self) -> str:
        """Return the authored workflow name."""

        return self.name


class WorkflowVersionQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet[Any]):
    """Prevent collection mutation of published graph documents."""


WorkflowVersionManager = AngeeManager.from_queryset(WorkflowVersionQuerySet)


class WorkflowVersion(AngeeDataModel):
    """Immutable normalized graph document selected when a run starts."""

    runtime = True
    sqid_prefix = "wfv_"

    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField()
    document = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    objects = WorkflowVersionManager()

    class Meta:
        """Django options for immutable workflow snapshots."""

        abstract = True
        rebac_resource_type = "workflows/version"
        constraints = [
            models.UniqueConstraint(fields=("workflow", "number"), name="workflows_version_number_unique"),
        ]

    @cached_property
    def definition(self) -> Definition:
        """Parse the immutable document once for this model instance."""

        return Definition.model_validate(self.document)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Insert new snapshots and reject changes to existing instances."""

        if not self._state.adding:
            raise ValidationError("Workflow versions cannot be edited.")
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)


class WorkflowRun(RecordRefMixin, AngeeDataModel):
    """One actor's execution of one immutable graph against an optional record."""

    runtime = True
    record_ref_field_prefix = "subject"
    rebac_grantable = {"reader": "write", "operator": "write"}
    sqid_prefix = "wfr_"

    version = models.ForeignKey("workflows.WorkflowVersion", on_delete=models.PROTECT, related_name="runs")
    run_as = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    subject_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True, blank=True)
    subject_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("subject_content_type", "subject_object_id")
    status = StateField(choices_enum=RunStatus, default=RunStatus.RUNNING, db_index=False)
    input = models.JSONField(default=dict)
    output = models.JSONField(default=dict)
    outcome = models.CharField(max_length=NAME_MAX_LENGTH, blank=True, default="")
    error = models.TextField(blank=True, default="")
    request_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = WorkflowRunManager()

    @property
    def is_terminal(self) -> bool:
        """Whether this run has finished its lifecycle."""
        return self.status in RunStatus.terminal_values()

    class Meta:
        """Django options for execution identity and terminal timestamps."""

        abstract = True
        rebac_resource_type = "workflows/run"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(status__in=RunStatus.terminal_values(), finished_at__isnull=False)
                    | models.Q(status__in=(RunStatus.RUNNING, RunStatus.WAITING), finished_at__isnull=True)
                ),
                name="workflows_run_finished_terminal",
            ),
        ]
        indexes = [
            models.Index(fields=("subject_content_type", "subject_object_id"), name="workflows_run_subject"),
            models.Index(
                fields=("finished_at",),
                condition=models.Q(status__in=RunStatus.terminal_values()),
                name="workflows_run_finished",
            ),
        ]


class StepRun(AngeeDataModel):
    """One graph node's state and input, with its claim counter as the fence."""

    runtime = True
    sqid_prefix = "wsr_"

    run = models.ForeignKey("workflows.WorkflowRun", on_delete=models.CASCADE, related_name="step_runs")
    node_key = models.CharField(
        max_length=NAME_MAX_LENGTH + len(".body"),
        help_text="A declared node key, with room for a map item's .body suffix.",
    )
    map_index = models.PositiveIntegerField(default=0)
    status = StateField(choices_enum=StepRunStatus, default=StepRunStatus.READY, db_index=False)
    waiting_kind = StateField(choices_enum=WaitingKind, null=True, blank=True, db_index=False)
    attempt = models.PositiveIntegerField(default=0)
    retries = models.PositiveIntegerField(default=0)
    dispatches = models.PositiveIntegerField(default=0)
    deadline_at = models.DateTimeField(null=True, blank=True)
    wake_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(db_default=Now())
    input = models.JSONField(default=dict)
    output = models.JSONField(default=dict)
    outcome = models.CharField(max_length=NAME_MAX_LENGTH, blank=True, default="")
    state = models.JSONField(default=dict)

    objects = StepRunManager()

    @property
    def step(self) -> type[Step]:
        """Resolve the class once from this run's immutable node declaration."""
        return self.run.version.definition.step(self.node_key)

    class Meta:
        """Django options for node identity, fences and tick predicates."""

        abstract = True
        rebac_resource_type = "workflows/step_run"
        constraints = [
            models.UniqueConstraint(fields=("run", "node_key", "map_index"), name="workflows_step_node_unique"),
            models.CheckConstraint(
                condition=(
                    models.Q(status=StepRunStatus.RUNNING, deadline_at__isnull=False)
                    | (~models.Q(status=StepRunStatus.RUNNING) & models.Q(deadline_at__isnull=True))
                ),
                name="workflows_step_running_deadline",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=StepRunStatus.WAITING, waiting_kind__isnull=False)
                    | (~models.Q(status=StepRunStatus.WAITING) & models.Q(waiting_kind__isnull=True))
                ),
                name="workflows_step_waiting_kind",
            ),
        ]
        indexes = [
            models.Index(
                fields=("wake_at",), condition=models.Q(status=StepRunStatus.WAITING), name="workflows_step_wake"
            ),
            models.Index(
                fields=("deadline_at",),
                condition=models.Q(status=StepRunStatus.RUNNING),
                name="workflows_step_deadline",
            ),
            models.Index(
                fields=("dispatched_at",),
                condition=models.Q(status=StepRunStatus.READY),
                name="workflows_step_dispatch",
            ),
        ]


class StepAttempt(AngeeDataModel):
    """One committed claim, closed once the attempt finishes."""

    runtime = True
    sqid_prefix = "wsa_"

    step_run = models.ForeignKey("workflows.StepRun", on_delete=models.CASCADE, related_name="attempts")
    number = models.PositiveIntegerField()
    started_at = models.DateTimeField(db_default=Now())
    finished_at = models.DateTimeField(null=True, blank=True)
    result = StateField(choices_enum=AttemptResult, null=True, blank=True, db_index=False)
    error = models.TextField(blank=True, default="")
    stacktrace = models.TextField(blank=True, default="")

    objects = AngeeManager.from_queryset(StepAttemptQuerySet)()

    class Meta:
        """Django options for one attempt per claim number."""

        abstract = True
        rebac_resource_type = "workflows/step_attempt"
        constraints = [
            models.UniqueConstraint(fields=("step_run", "number"), name="workflows_attempt_number_unique"),
        ]
