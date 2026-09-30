"""Abstract definitions and retained workflow execution rows."""

from __future__ import annotations

from typing import Any, cast

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, transaction
from django.db.models.functions import Coalesce, Now
from django.utils.functional import cached_property
from rebac import system_context, to_subject_ref
from rebac.models import active_relationship_model

from angee.base.fields import DiagnosticTextField, ModelLabelField, StateField
from angee.base.impl import ImplClassField
from angee.base.mixins import AppendOnlyModel, AppendOnlyQuerySet, AuditMixin
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet
from angee.base.refs import RecordRefMixin
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.graphql.schema import GraphQLSchemas
from angee.iam.service_users import sync_service_user
from angee.resources.mixins import ResourceLoadMixin
from angee.workflows.definition import MAP_BODY_SUFFIX, Definition
from angee.workflows.managers import (
    StepAttemptQuerySet,
    StepRunManager,
    StepWatchManager,
    WorkflowManager,
    WorkflowRunManager,
)
from angee.workflows.resources import TriggerResource, WorkflowDefinitionResource
from angee.workflows.states import (
    NAME_MAX_LENGTH,
    AttemptResult,
    RunOrigin,
    RunRelation,
    RunStatus,
    StepRunStatus,
    WaitingKind,
)
from angee.workflows.steps import Step
from angee.workflows.triggers import TriggerEventManager, TriggerManager, TriggerSource


class Workflow(ResourceLoadMixin, AuditMixin, AngeeDataModel):
    """Editable identity and draft, pointing to one immutable published version."""

    runtime = True
    resource_class = WorkflowDefinitionResource
    rebac_grantable = {"editor": "write", "viewer": "write", "starter": "write", "operator": "write"}
    sqid_prefix = "wfl_"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        editable=False, related_name="workflow_principal",
    )
    key = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    subject_model = ModelLabelField(max_length=200, blank=True, default="")
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
            subject is None or subject._meta.label != self.subject_model
        ):
            raise ValidationError("The workflow subject has the wrong model.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep the service principal's name synchronized with its workflow."""

        creating = self._state.adding
        update_fields = kwargs.get("update_fields")
        name_written = update_fields is None or "name" in update_fields
        previous_name = None
        if not creating and name_written:
            previous_name = type(self)._base_manager.filter(pk=self.pk).values_list("name", flat=True).first()
        with transaction.atomic():
            super().save(*args, **kwargs)
            if creating or (name_written and previous_name != self.name):
                sync_service_user(self, prefix="workflow")

    def __str__(self) -> str:
        """Return the authored workflow name."""

        return self.name


class WorkflowVersionQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet[Any]):
    """Prevent collection mutation of published graph documents."""


WorkflowVersionManager = AngeeManager.from_queryset(WorkflowVersionQuerySet)


class WorkflowVersion(AppendOnlyModel, AngeeDataModel):
    """Immutable normalized graph document selected when a run starts."""

    runtime = True
    sqid_prefix = "wfv_"

    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField()
    document = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)

    objects = WorkflowVersionManager()

    def __str__(self) -> str:
        """Identify the published version within its workflow."""
        return f"Version {self.number}"

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

class WorkflowRun(RecordRefMixin, AngeeDataModel):
    """One actor's execution of one immutable graph against an optional record."""

    runtime = True
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
    error = DiagnosticTextField(max_length=8192, blank=True, default="")
    request_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    parent_step = models.ForeignKey(
        "workflows.StepRun", on_delete=models.PROTECT, null=True, blank=True, related_name="child_runs",
    )
    relation = StateField(choices_enum=RunRelation, null=True, blank=True)
    reprocess_of = models.ForeignKey(
        "workflows.WorkflowRun", on_delete=models.PROTECT, null=True, blank=True, related_name="reprocesses",
    )
    trigger_event = models.OneToOneField(
        "workflows.TriggerEvent", on_delete=models.PROTECT, null=True, blank=True, related_name="started_run",
    )
    origin = StateField(choices_enum=RunOrigin, editable=False)
    finished_at = models.DateTimeField(null=True, blank=True)
    prune_after = models.DateTimeField(null=True, blank=True, editable=False)
    prune_reason = DiagnosticTextField(max_length=255, blank=True, default="", editable=False)

    objects = WorkflowRunManager()

    def __str__(self) -> str:
        """Use the public execution identity without loading its workflow."""
        return str(self.sqid)

    @cached_property
    def policy_version(self) -> Any:
        """Reuse loaded policy, resolving a viewer-redacted relation by its pinned ID."""
        if version := self._state.fields_cache.get("version"):
            return version
        if "version" not in self._state.fields_cache:
            with system_context(reason="workflows.execution_policy"):
                return self.version
        model = self._meta.get_field("version").related_model
        return system_queryset(model).get(pk=self.version_id)

    @property
    def subject_model_class(self) -> type[models.Model] | None:
        """Resolve the declared execution type, falling back to the canonical record."""
        if self.subject_object_id is None:
            return None
        with system_context(reason="workflows.subject_policy"):
            label = self.policy_version.workflow.subject_model or self.record_model_label
        return apps.get_model(label)

    @property
    def is_terminal(self) -> bool:
        """Whether this run has finished its lifecycle."""
        return self.status in RunStatus.terminal_values()

    def can_cancel(self, actor: Any) -> bool:
        """A writer may cancel active execution or clean up a terminal run's open rows."""
        return self.with_actor(actor).has_access("write") and self._has_open_owned_work

    def check_await(self, observer: Any, *, expects: str) -> None:
        """Require an observed run to match the pinned contract without a self-wait."""
        with system_context(reason="workflows.await_policy"):
            expected = self.policy_version.workflow.key == expects
        if not expected:
            raise ValidationError("The awaited run belongs to a different workflow.")
        if self.pk == observer.pk:
            raise ValidationError("A run cannot await itself.")

    @property
    def _has_open_owned_work(self) -> bool:
        """Cancellation owns open rows throughout the tree, even below final descendants."""
        return (
            not self.is_terminal
            or system_queryset(self.step_runs.model).filter(run=self)
            .exclude(status__in=StepRunStatus.terminal_values()).exists()
            or any(child._has_open_owned_work for child in system_queryset(type(self)).filter(
                parent_step__run=self, relation=RunRelation.OWNED,
            ))
        )

    def can_reprocess(self, actor: Any) -> bool:
        """Reprocessing needs terminal execution, run write and workflow start access."""
        if not self.is_terminal or not self.with_actor(actor).has_access("write"):
            return False
        with system_context(reason="workflows.reprocess_policy"):
            workflow = self.policy_version.workflow
        return bool(workflow.with_actor(actor).has_access("start"))

    class Meta:
        """Django options for execution identity and terminal timestamps."""

        abstract = True
        rebac_resource_type = "workflows/run"
        constraints = [
            models.CheckConstraint(condition=(
                models.Q(origin=RunOrigin.MANUAL, parent_step__isnull=True, reprocess_of__isnull=True,
                         trigger_event__isnull=True)
                | models.Q(origin=RunOrigin.WORKFLOW, parent_step__isnull=False, reprocess_of__isnull=True,
                           trigger_event__isnull=True)
                | models.Q(origin=RunOrigin.REPROCESS, parent_step__isnull=True, reprocess_of__isnull=False,
                           trigger_event__isnull=True)
                | models.Q(origin=RunOrigin.TRIGGER, parent_step__isnull=True, reprocess_of__isnull=True,
                           trigger_event__isnull=False)
            ), name="workflows_run_origin_cause"),
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
    """One graph node's state and input, with its claim counter as the fence.

    ``rank`` stores graph order for query sorting without reparsing the version.
    """

    runtime = True
    decision_group = models.OneToOneField(
        "decisions.DecisionGroup", on_delete=models.PROTECT, null=True, blank=True, related_name="step_run",
    )
    awaited_run = models.ForeignKey(
        "workflows.WorkflowRun", on_delete=models.PROTECT, null=True, blank=True, related_name="waiters",
    )
    sqid_prefix = "wsr_"

    run = models.ForeignKey("workflows.WorkflowRun", on_delete=models.CASCADE, related_name="step_runs")
    node_key = models.CharField(
        max_length=NAME_MAX_LENGTH + len(MAP_BODY_SUFFIX),
        help_text="A declared node key, with room for a map item's .body suffix.",
    )
    rank = models.PositiveIntegerField(editable=False, help_text="Execution order assigned once by the graph planner.")
    map_index = models.PositiveIntegerField(default=0)
    status = StateField(choices_enum=StepRunStatus, default=StepRunStatus.READY, db_index=False)
    waiting_kind = StateField(choices_enum=WaitingKind, null=True, blank=True, db_index=False)
    wait_reason = DiagnosticTextField(blank=True, default="")
    attempt = models.PositiveIntegerField(default=0)
    idempotency_token = models.UUIDField(null=True, blank=True, editable=False)
    page_index = models.PositiveIntegerField(default=0, editable=False)
    retries = models.PositiveIntegerField(default=0)
    retry_acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="Pending duplicate-effect acknowledgement, transferred to the next committed attempt.",
    )
    dispatches = models.PositiveIntegerField(default=0)
    deadline_at = models.DateTimeField(null=True, blank=True)
    wake_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(db_default=Now())
    input = models.JSONField(default=dict)
    output = models.JSONField(default=dict)
    outcome = models.CharField(max_length=NAME_MAX_LENGTH, blank=True, default="")
    state = models.JSONField(default=dict)

    objects = StepRunManager()

    def __str__(self) -> str:
        """Distinguish mapped items while preserving the authored node name."""
        return f"{self.node_key} [{self.map_index}]" if self.is_mapped else self.node_key

    @property
    def is_mapped(self) -> bool:
        """Identify a map body row by its node identity, including item index zero."""
        return self.node_key.endswith(MAP_BODY_SUFFIX)

    @property
    def is_map(self) -> bool:
        """Identify a map parent from its declaration, including an empty map."""
        return not self.is_mapped and self.run.policy_version.definition.nodes[self.node_key].body is not None

    def map_rows(self) -> Any:
        """Select this map's body rows; ordinary nodes have no matching items."""
        return self.run.step_runs.for_map(self.run_id, self.node_key)

    @property
    def map_total(self) -> int:
        """Count admitted items for a map, including after its wait has ended."""
        return len(self.input["items"]) if self.input and self.is_map else 0

    @classmethod
    def map_settled_expression(cls) -> Coalesce:
        """Count terminal body rows in one correlated expression for bulk reads."""
        rows = cast(Any, system_queryset(cls)).for_map(models.OuterRef("run_id"), models.OuterRef("node_key"))
        counts = (rows.filter(status__in=StepRunStatus.terminal_values()).order_by().values("run_id")
                  .annotate(total=models.Count("pk")).values("total"))
        return Coalesce(models.Subquery(counts), 0, output_field=models.IntegerField())

    @property
    def map_settled(self) -> int:
        """Use the same progress expression for ordinary model and optimized reads."""
        if "_map_settled" in self.__dict__:
            return int(self.__dict__["_map_settled"])
        return int(system_queryset(type(self)).filter(pk=self.pk)
                   .annotate(_map_settled=self.map_settled_expression()).values_list("_map_settled", flat=True).get())

    @property
    def step(self) -> type[Step]:
        """Resolve the class once from this run's immutable node declaration."""
        return self.run.policy_version.definition.step(self.node_key)

    @property
    def requires_duplicate_acknowledgement(self) -> bool:
        """Whether any attempt on this page may have made a non-idempotent effect."""
        with system_context(reason="workflows.effect_policy"):
            return not self.step.effect_idempotent and self.attempts.filter(
                page_index=self.page_index, effect_started_at__isnull=False,
            ).exists()

    @property
    def retry_blocker(self) -> str | None:
        """State-only retry eligibility, shared by the locked action and read surface."""
        run = self.run
        definition = run.policy_version.definition
        if (
            run.status not in {RunStatus.FAILED, RunStatus.WAITING, RunStatus.RUNNING}
            or not (self.status == StepRunStatus.FAILED
                    or self.status == StepRunStatus.WAITING and self.waiting_kind == WaitingKind.OPERATOR)
            or self.status == StepRunStatus.FAILED and not definition.retry_allowed(self.node_key)
        ):
            return "This step cannot be retried in place."
        others = sorted(row.node_key for row in run.step_runs.all()
                        if row.pk != self.pk and definition.unrouted_failure([row]))
        return f"Other unrouted failures remain: {', '.join(others)}." if others else None

    def can_retry(self, actor: Any) -> bool:
        """Expose retry eligibility without granting generic writes to engine rows."""
        if not self.run.with_actor(actor).has_access("write"):
            return False
        with system_context(reason="workflows.retry_policy"):
            return self.retry_blocker is None

    class Meta:
        """Django options for node identity, fences and tick predicates."""

        abstract = True
        rebac_resource_type = "workflows/step_run"
        ordering = ("rank", "map_index", "pk")
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
            models.CheckConstraint(
                condition=(
                    models.Q(status=StepRunStatus.WAITING, waiting_kind=WaitingKind.TIME,
                             wake_at__isnull=False, wait_reason="")
                    | models.Q(status=StepRunStatus.WAITING, waiting_kind=WaitingKind.RECORD, wait_reason="")
                    | models.Q(status=StepRunStatus.WAITING, waiting_kind=WaitingKind.DECISION,
                               decision_group__isnull=False, wake_at__isnull=True, wait_reason="")
                    | models.Q(status=StepRunStatus.WAITING, waiting_kind=WaitingKind.RUN,
                               awaited_run__isnull=False, wake_at__isnull=True, wait_reason="")
                    | models.Q(status=StepRunStatus.WAITING, waiting_kind=WaitingKind.MAP,
                               wake_at__isnull=True, wait_reason="")
                    | (models.Q(status=StepRunStatus.WAITING, waiting_kind=WaitingKind.OPERATOR,
                                wake_at__isnull=True) & ~models.Q(wait_reason=""))
                    | (~models.Q(status=StepRunStatus.WAITING)
                       & models.Q(wake_at__isnull=True, wait_reason=""))
                ),
                name="workflows_step_wait_columns",
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
    page_index = models.PositiveIntegerField(default=0, editable=False)
    started_at = models.DateTimeField(db_default=Now())
    finished_at = models.DateTimeField(null=True, blank=True)
    result = StateField(choices_enum=AttemptResult, null=True, blank=True, db_index=False)
    error = DiagnosticTextField(max_length=8192, blank=True, default="")
    stacktrace = DiagnosticTextField(max_length=65536, blank=True, default="")
    effect_started_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )

    objects = AngeeManager.from_queryset(StepAttemptQuerySet)()

    def __str__(self) -> str:
        """Identify this retained claim within its step."""
        return f"Attempt {self.number}"

    class Meta:
        """Django options for one attempt per claim number."""

        abstract = True
        rebac_resource_type = "workflows/step_attempt"
        constraints = [
            models.UniqueConstraint(fields=("step_run", "number"), name="workflows_attempt_number_unique"),
            models.CheckConstraint(
                condition=(models.Q(finished_at__isnull=True, result__isnull=True)
                           | models.Q(finished_at__isnull=False, result__isnull=False)),
                name="workflows_attempt_finished_result",
            ),
        ]


class StepArtifact(RecordRefMixin, AngeeDataModel):
    """One record retained as a step's result evidence without copying its data."""

    runtime = True
    sqid_prefix = "wfa_"

    step_run = models.ForeignKey("workflows.StepRun", on_delete=models.CASCADE, related_name="artifacts")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")
    label = models.CharField(max_length=200, blank=True, default="")

    def __str__(self) -> str:
        """Use the authored evidence label, falling back to its public identity."""
        return self.label or str(self.sqid)

    class Meta:
        """Django options for actor-readable execution artifacts."""

        abstract = True
        rebac_resource_type = "workflows/step_artifact"


class StepWatch(RecordRefMixin, AngeeDataModel):
    """One transactional observation and its durable, coalesced wake obligation."""

    runtime = True
    sqid_prefix = "wsw_"
    step_run = models.ForeignKey("workflows.StepRun", on_delete=models.CASCADE, related_name="watches")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")
    pending = models.BooleanField(default=False, editable=False)
    objects = StepWatchManager()

    class Meta:
        """Canonical record uniqueness and source capture lookup."""

        abstract = True
        verbose_name_plural = "step watches"
        rebac_resource_type = "workflows/step_watch"
        constraints = [models.UniqueConstraint(
            fields=("step_run", "content_type", "object_id"), name="workflows_watch_record_unique",
        )]
        indexes = [models.Index(fields=("content_type", "object_id"), name="workflows_watch_record")]


class DecisionWorkflow(models.Model):
    """Contribute execution query axes without coupling decisions to its waiter."""

    extends: str | None = "decisions.Decision"
    hasura_filterable_fields = (
        "group__step_run", "group__step_run__run", "group__step_run__run__version__workflow",
        "group__step_run__run__version__workflow__key",
    )
    hasura_filter_aliases = {
        "workflow_name": "group__step_run__run__version__workflow__name",
        "node_key": "group__step_run__node_key",
    }

    class Meta:
        """Compose declarations onto the decision row without another table."""

        abstract = True


class Trigger(ResourceLoadMixin, AngeeDataModel):
    """Disabled-by-default admission policy with source-scoped domain hooks.

    An ``extends`` donor implementing ``trigger_input`` or ``check_admission``
    declares ``trigger_sources`` as registered source keys. Admission runs all
    matching guards and accepts at most one matching input builder.
    """

    runtime = True
    resource_class = TriggerResource
    sqid_prefix = "wft_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="triggers")
    source = ImplClassField(TriggerSource)
    model_label: str = ModelLabelField(max_length=200, blank=True, default="")
    condition = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=False, editable=False)
    granted_targets = models.JSONField(default=list, editable=False)
    """Targets this trigger contributes to the workflow principal's direct tuples."""
    run_as = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, editable=False,
    )
    disabled_reason: str = DiagnosticTextField(blank=True, default="", editable=False)
    objects = TriggerManager()

    @classmethod
    def can_read_impl_choices(cls, field_name: str, actor: Any) -> bool:
        """Workflow authors can configure sources without platform administration."""
        workflows = read_scoped_queryset(cls._meta.get_field("workflow").related_model, actor, action="write")
        return field_name == "source" and workflows is not None and workflows.exists()

    @property
    def source_class(self) -> type[TriggerSource]:
        """Resolve source behavior through its declared implementation field."""
        try:
            return cast(type[TriggerSource], self._meta.get_field("source").resolve_for(self))
        except ImportError as error:
            raise ImproperlyConfigured(f"Trigger source {self.source!r} cannot be loaded: {error}") from error

    @property
    def source_model(self) -> str:
        """Expose the resolved model's canonical label, retaining unavailable-source configuration."""
        try:
            return self.source_class.model(self)._meta.label
        except ImproperlyConfigured:
            return self.model_label

    def granted_relationships(self, *, actor: Any) -> tuple[Any, ...]:
        """List the currently stored direct tuples contributed by this trigger."""

        self.workflow.require_access("write", actor)
        if not self.workflow.user_id or not self.granted_targets:
            return ()
        subject = to_subject_ref(self.workflow.user)
        keys = {
            (value["resource_type"], value["resource_id"], value["relation"])
            for value in self.granted_targets
        }
        rows = active_relationship_model().objects.filter(
            subject_type=subject.subject_type, subject_id=subject.subject_id,
            optional_subject_relation=subject.optional_relation,
        )
        return tuple(row for row in rows if (row.resource_type, str(row.resource_id), row.relation) in keys)

    def validate_configuration(self) -> tuple[Any, Any]:
        """Use the model's final resource input and native filter compiler."""
        self._trigger_hooks("trigger_input")
        self._trigger_hooks("check_admission")
        model = self.source_class.model(self)
        if self.workflow.subject_model and self.workflow.subject_model != model._meta.label:
            raise ValidationError("The trigger source does not match the workflow subject model.")
        return model, GraphQLSchemas.from_discovery().resource_filter(model, self.condition)

    def trigger_input(self, record: Any) -> dict[str, Any]:
        """Domain extensions build admitted input; source adapters never do."""
        return {}

    def check_admission(self, record: Any, *, actor: Any) -> None:
        """Domain extensions may reject this record without disabling the trigger."""

    def _trigger_hooks(self, name: str) -> list[Any]:
        """Select only source-declared donor hooks in stable order; reject ambiguous input."""
        hooks = []
        for donor in type(self).mro():
            hook = donor.__dict__.get(name)
            if hook is None or donor is Trigger:
                continue
            sources = donor.__dict__.get("trigger_sources")
            if not isinstance(sources, tuple) or any(not isinstance(source, str) for source in sources):
                raise ImproperlyConfigured(f"{donor.__name__}.{name} must declare trigger_sources as a tuple of keys.")
            if self.source in sources:
                hooks.append((donor, hook))
        hooks.sort(key=lambda item: (item[0].__module__, item[0].__qualname__))
        if name == "trigger_input" and len(hooks) > 1:
            raise ImproperlyConfigured("Multiple trigger_input hooks contribute to this source.")
        return [hook for _, hook in hooks]

    def admission_input(self, record: Any) -> dict[str, Any]:
        """Build source-specific input from at most one declared domain contributor."""
        hooks = self._trigger_hooks("trigger_input")
        return hooks[0](self, record) if hooks else Trigger.trigger_input(self, record)

    def admit_record(self, record: Any, *, actor: Any) -> None:
        """Apply every source-specific domain admission policy."""
        hooks = self._trigger_hooks("check_admission")
        if hooks:
            for hook in hooks:
                hook(self, record, actor=actor)
        else:
            Trigger.check_admission(self, record, actor=actor)

    def clean(self) -> None:
        """Reject invalid authoring at save and preserve the server-owned actor."""
        super().clean()
        self.model_label = "" if self.source_class.model_label else ModelLabelField.normalize(self.model_label)
        self.validate_configuration()
        previous = system_queryset(type(self)).filter(pk=self.pk).first() if self.pk else None
        self.granted_targets = previous.granted_targets if previous is not None else []
        if previous is not None and self.workflow_id != previous.workflow_id:
            raise ValidationError("A trigger's workflow cannot be changed.")
        if self.enabled and (previous is None or not previous.enabled or self.run_as_id != previous.run_as_id):
            raise ValidationError("Enable triggers through the manager action.")
        fields = ("condition", "source", "model_label", *self.source_class.scope_fields)
        if previous is not None and any(
            getattr(self, self._meta.get_field(name).attname) != getattr(previous, self._meta.get_field(name).attname)
            for name in fields
        ):
            self.enabled = False
            self.disabled_reason = "Trigger configuration changed; enable it again."
        if not self.enabled:
            self.run_as = None

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Validate native saves as well as resource writes."""
        with transaction.atomic():
            if self.pk:
                type(self).objects._lock_workflow(self.workflow_id)
            previous = None
            if not self._state.adding:
                previous = system_queryset(type(self)).filter(pk=self.pk).lock_if_supported(no_key=True).get()
            self.clean()
            self.workflow.require_access("write", self.actor())
            if not self.enabled and kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "enabled", "run_as", "disabled_reason"}
            super().save(*args, **kwargs)
            if previous is not None and previous.granted_targets and not self.enabled:
                type(self).objects._set_grants(self, ())

    def __str__(self) -> str:
        """Identify configuration without loading permission-sensitive relations."""
        try:
            source = self.source_class.display_label()
        except ImproperlyConfigured:
            source = str(self.source)
        model = self.source_model
        return f"{source}: {model}" if model else source

    class Meta:
        """Compose admission configuration and its permission identity."""

        abstract = True
        rebac_resource_type = "workflows/trigger"
        constraints = [models.CheckConstraint(
            condition=(models.Q(enabled=True, run_as__isnull=False) | models.Q(enabled=False, run_as__isnull=True)),
            name="workflows_trigger_enabled_actor",
        )]


class TriggerEvent(RecordRefMixin, AngeeDataModel):
    """One retained admission fact per trigger and concrete record reference."""

    runtime = True
    sqid_prefix = "wfe_"
    trigger = models.ForeignKey("workflows.Trigger", on_delete=models.PROTECT, related_name="events")
    record_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    record_object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("record_content_type", "record_object_id")
    changed_at = models.DateTimeField(db_default=Now())
    evaluated_at = models.DateTimeField(null=True, blank=True)
    admitted_at = models.DateTimeField(null=True, blank=True)
    rejection = DiagnosticTextField(blank=True, default="")
    objects = TriggerEventManager()

    def __str__(self) -> str:
        """Use the public ledger identity without resolving its protected record."""
        return str(self.sqid)

    class Meta:
        """Enforce concrete ledger identity independently of retained runs."""

        abstract = True
        rebac_resource_type = "workflows/trigger_event"
        constraints = [models.UniqueConstraint(
            fields=("trigger", "record_content_type", "record_object_id"), name="workflows_trigger_record_unique",
        ), models.CheckConstraint(
            condition=models.Q(admitted_at__isnull=True) | models.Q(evaluated_at__isnull=False),
            name="workflows_event_admission_evaluated",
        )]
        indexes = [models.Index(
            fields=("changed_at", "id"), condition=models.Q(admitted_at__isnull=True),
            name="workflows_event_pending",
        )]
