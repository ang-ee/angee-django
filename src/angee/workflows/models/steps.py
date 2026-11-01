"""Native workflow steps owners."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, cast

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Coalesce, Concat, Length, Now, Substr
from django.db.models.lookups import IsNull
from rebac import system_context

from angee.base.exceptions import exception_text
from angee.base.fields.state import StateField
from angee.base.fields.text import DiagnosticTextField
from angee.base.models.base import AngeeDataModel
from angee.base.models.merge_references import MergePolicy
from angee.base.models.refs import RecordRefMixin
from angee.base.models.timestamps import update_fields_with_auto_now
from angee.base.refs import RecordRef
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.workflows.definition import MAP_BODY_SUFFIX, Definition
from angee.workflows.managers import (
    StepAttemptManager,
    StepRecordManager,
    StepRunManager,
    StepWatchManager,
)
from angee.workflows.models.runs import WorkflowRun
from angee.workflows.states import (
    NAME_MAX_LENGTH,
    AttemptResult,
    RecordOperation,
    RunStatus,
    StepRunStatus,
    WaitingKind,
)
from angee.workflows.steps import Step, _AttemptRecord

logger = logging.getLogger(__name__)


class _JSONArrayLength(models.Func):
    """Length of a JSON array; other JSON values have no admitted items."""

    output_field = models.IntegerField()

    def as_sqlite(self, compiler: Any, connection: Any, **extra_context: Any) -> Any:
        sql, params = super().as_sql(
            compiler,
            connection,
            function="json_array_length",
            template="CASE WHEN json_valid(%(expressions)s) THEN %(function)s(%(expressions)s) ELSE 0 END",
            **extra_context,
        )
        return sql, (*params, *params)

    def as_postgresql(self, compiler: Any, connection: Any, **extra_context: Any) -> Any:
        sql, params = super().as_sql(
            compiler,
            connection,
            function="jsonb_array_length",
            template="%(function)s(CASE WHEN jsonb_typeof(%(expressions)s) = 'array' "
            "THEN %(expressions)s ELSE '[]'::jsonb END)",
            **extra_context,
        )
        return sql, (*params, *params)


class _NodeFailureRoute(models.Func):
    """Project a frozen node's failure route without loading its graph payload."""

    output_field = models.JSONField()

    def as_postgresql(self, compiler: Any, connection: Any, **extra_context: Any) -> Any:
        document, key, port = self.get_source_expressions()
        return models.Func(
            document,
            models.Value("nodes"),
            key,
            models.Value("next"),
            port,
            function="jsonb_extract_path",
            output_field=models.JSONField(),
        ).as_sql(compiler, connection)

    def as_sqlite(self, compiler: Any, connection: Any, **extra_context: Any) -> Any:
        document, key, port = self.get_source_expressions()
        path = Concat(models.Value("$.nodes."), key, models.Value(".next."), port)
        return models.Func(document, path, function="json_extract", output_field=models.JSONField()).as_sql(
            compiler,
            connection,
        )


class StepRun(AngeeDataModel, concrete=True):
    """One graph node's state and input, with its claim counter as the fence.

    ``rank`` stores graph order for query sorting without reparsing the version.
    """

    awaited_run = models.ForeignKey(
        "workflows.WorkflowRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="waiters",
    )
    decision = models.ForeignKey(
        "decisions.Decision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="requesting_steps",
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
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
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
    notes = models.JSONField(default=list)

    objects = StepRunManager()

    @classmethod
    def change_batch_targets(
        cls, instances: Iterable[Any], *, update_fields: Iterable[str], previous_instances: Iterable[Any] = (),
    ) -> Iterable[models.Model]:
        """Publish newly retained questions; run advancement owns run publication.

        Local bulk observers coalesce the step rows to their own targets. Sending
        another run change here would publish its intermediate, unadvanced state.
        Previous reference identities do not alter these current decision targets.
        """

        rows = tuple(instances)
        if not rows:
            return ()
        using = rows[0]._state.db
        decisions = (
            apps.get_model("decisions", "Decision")
            ._base_manager.using(using)
            .filter(
                pk__in={row.decision_id for row in rows if row.decision_id},
            )
            .defer("proposal", "context", "verdict", "verdict_values")
            if "decision_id" in update_fields
            else ()
        )
        return decisions

    def redispatch(self) -> None:
        step_run = self
        run = self.run
        run.step_runs.filter(pk=step_run.pk).count_redispatch()
        if step_run.dispatches + 1 >= settings.ANGEE_WORKFLOW_MAX_DISPATCHES:
            run.step_runs.filter(pk=step_run.pk).to_waiting(
                kind=WaitingKind.ERROR,
                reason="Task delivery exhausted its retry allowance.",
            )
            run.advance()

    def reap(self) -> None:
        step_run = self
        run = self.run
        try:
            with transaction.atomic(using=self._state.db):
                run.step_runs.expire(
                    step_run,
                    _AttemptRecord.failure(
                        "The attempt deadline expired.",
                        retryable=True,
                        timed_out=True,
                    ),
                )
        except ImproperlyConfigured as failure:
            step_run.attempts.filter(number=step_run.attempt).close(AttemptResult.TIMED_OUT, exception_text(failure))
            run.step_runs.filter(pk=step_run.pk).to_waiting(
                kind=WaitingKind.ERROR,
                reason="The step implementation is unavailable; restore its registration before retrying.",
            )
        run.advance()

    def change_related_records(self) -> tuple[RecordRef, ...]:
        """A step changes both its run graph and that run's record timelines."""
        run = self._meta.get_field("run").related_model
        evidence = self._meta.get_field("records").related_model
        assert run is not None and issubclass(run, WorkflowRun)
        assert evidence is not None and issubclass(evidence, StepRecord)
        records = [
            RecordRef.from_model(run, self.run_id),
            *evidence.objects.using(self._state.db).filter(run_id=self.run_id).change_related_records(),
        ]
        if self.decision_id:
            decision = self._meta.get_field("decision").related_model
            assert decision is not None
            records.append(RecordRef.from_model(decision, self.decision_id))
        return tuple(records)

    hold_states = {
        (StepRunStatus.FAILED, None): "error",
        (StepRunStatus.WAITING, WaitingKind.ERROR): "error",
        (StepRunStatus.WAITING, WaitingKind.DECISION): "decision",
        (StepRunStatus.WAITING, WaitingKind.RUN): "run",
    }

    def __str__(self) -> str:
        """Distinguish mapped items while preserving the authored node name."""
        return f"{self.node_key} [{self.map_index}]" if self.is_mapped else self.node_key

    @property
    def hold(self) -> str | None:
        """The three reader-facing holds, independent of scheduler waits."""
        if "_hold" in self.__dict__:
            return self.__dict__["_hold"]
        if self.run.stopped_at is not None:
            return None
        if self.status == StepRunStatus.FAILED and not self.run.policy_version.definition.retry_allowed(self.node_key):
            return None
        return self.hold_states.get((self.status, self.waiting_kind))

    @classmethod
    def hold_expression(cls) -> models.Case:
        """Use the same hold rule in resource filters without loading step payloads."""
        mapped = models.Q(node_key__endswith=MAP_BODY_SUFFIX)
        key = models.Case(
            models.When(mapped, then=Substr("node_key", 1, Length("node_key") - len(MAP_BODY_SUFFIX))),
            default=models.F("node_key"),
            output_field=models.CharField(),
        )
        port = models.Case(
            models.When(mapped, then=models.Value(Definition.failure_port("map.body"))),
            default=models.Value(Definition.failure_port("node")),
            output_field=models.CharField(),
        )
        routed = IsNull(_NodeFailureRoute(models.F("run__version__document"), key, port), False)
        return models.Case(
            models.When(run__stopped_at__isnull=False, then=models.Value(None)),
            models.When(models.Q(status=StepRunStatus.FAILED) & routed, then=models.Value(None)),
            *(
                models.When(status=status, waiting_kind=kind, then=models.Value(hold))
                for (status, kind), hold in cls.hold_states.items()
            ),
            default=None,
            output_field=models.CharField(),
        )

    @property
    def node_label(self) -> str:
        """Return this row's published node label."""
        return self.run.policy_version.definition.node_label(self.node_key)

    @property
    def outcome_label(self) -> str:
        """Return this row's published outcome label."""
        return (
            self.run.policy_version.definition.node_outcome_label(self.node_key, self.outcome) if self.outcome else ""
        )

    @property
    def failure_reason(self) -> str | None:
        """Return only a failed row's retained reader-facing error."""
        if "_failure_reason" in self.__dict__:
            reason = self.__dict__["_failure_reason"]
            return reason if isinstance(reason, str) and reason else None
        if (
            self.status == StepRunStatus.FAILED
            and isinstance(self.output, dict)
            and isinstance(self.output.get("error"), str)
            and self.output["error"]
        ):
            return self.output["error"]
        return None

    @classmethod
    def failure_reason_expression(cls) -> models.Case:
        """Read a failed row's retained error without selecting its payload."""
        return models.Case(
            models.When(status=StepRunStatus.FAILED, then=KeyTextTransform("error", "output")),
            default=None,
            output_field=models.TextField(),
        )

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
        if not self.is_map:
            return 0
        if "_map_total" in self.__dict__:
            return int(self.__dict__["_map_total"])
        return len(self.input["items"]) if self.input else 0

    @classmethod
    def map_total_expression(cls) -> Coalesce:
        """Count admitted input items in one portable expression for bulk reads."""
        return Coalesce(_JSONArrayLength(KeyTransform("items", "input")), 0, output_field=models.IntegerField())

    @classmethod
    def map_settled_expression(cls) -> Coalesce:
        """Count terminal body rows in one correlated expression for bulk reads."""
        rows = cast(Any, cls.system_queryset()).for_map(models.OuterRef("run_id"), models.OuterRef("node_key"))
        counts = (
            rows.filter(status__in=StepRunStatus.terminal_values())
            .order_by()
            .values("run_id")
            .annotate(total=models.Count("pk"))
            .values("total")
        )
        return Coalesce(models.Subquery(counts), 0, output_field=models.IntegerField())

    @property
    def map_settled(self) -> int:
        """Use the same progress expression for ordinary model and optimized reads."""
        if "_map_settled" in self.__dict__:
            return int(self.__dict__["_map_settled"])
        return int(
            type(self)
            .system_queryset()
            .using(self._state.db)
            .filter(pk=self.pk)
            .annotate(_map_settled=self.map_settled_expression())
            .values_list("_map_settled", flat=True)
            .get()
        )

    @property
    def step(self) -> type[Step]:
        """Resolve the class once from this run's immutable node declaration."""
        return self.run.policy_version.definition.step(self.node_key)

    @property
    def requires_duplicate_acknowledgement(self) -> bool:
        """Whether any attempt on this page may have made a non-idempotent effect."""
        with system_context(reason="workflows.effect_policy"):
            return (
                not self.step.effect_idempotent
                and self.attempts.filter(
                    page_index=self.page_index,
                    effect_started_at__isnull=False,
                ).exists()
            )

    @property
    def retry_blocker(self) -> str | None:
        """State-only retry eligibility, shared by the locked action and read surface."""
        run = self.run
        definition = run.policy_version.definition
        if (
            run.stopped_at is not None
            or run.status not in {RunStatus.FAILED, RunStatus.WAITING, RunStatus.RUNNING}
            or not (
                self.status == StepRunStatus.FAILED
                or self.status == StepRunStatus.WAITING
                and self.waiting_kind == WaitingKind.ERROR
            )
            or self.status == StepRunStatus.FAILED
            and not definition.retry_allowed(self.node_key)
        ):
            return "This step cannot be retried in place."
        others = sorted(
            row.node_key for row in run.step_runs.all() if row.pk != self.pk and definition.unrouted_failure([row])
        )
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
                    models.Q(
                        status=StepRunStatus.WAITING,
                        waiting_kind=WaitingKind.TIME,
                        wake_at__isnull=False,
                        wait_reason="",
                    )
                    | models.Q(status=StepRunStatus.WAITING, waiting_kind=WaitingKind.RECORD, wait_reason="")
                    | models.Q(
                        status=StepRunStatus.WAITING,
                        waiting_kind=WaitingKind.DECISION,
                        decision__isnull=False,
                        wake_at__isnull=True,
                        wait_reason="",
                    )
                    | models.Q(
                        status=StepRunStatus.WAITING,
                        waiting_kind=WaitingKind.RUN,
                        awaited_run__isnull=False,
                        wake_at__isnull=True,
                        wait_reason="",
                    )
                    | models.Q(
                        status=StepRunStatus.WAITING, waiting_kind=WaitingKind.MAP, wake_at__isnull=True, wait_reason=""
                    )
                    | (
                        models.Q(status=StepRunStatus.WAITING, waiting_kind=WaitingKind.ERROR, wake_at__isnull=True)
                        & ~models.Q(wait_reason="")
                    )
                    | (~models.Q(status=StepRunStatus.WAITING) & models.Q(wake_at__isnull=True, wait_reason=""))
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


class StepAttempt(AngeeDataModel, concrete=True):
    """One committed claim, closed once the attempt finishes."""

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
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    objects = StepAttemptManager()

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
                condition=(
                    models.Q(finished_at__isnull=True, result__isnull=True)
                    | models.Q(finished_at__isnull=False, result__isnull=False)
                ),
                name="workflows_attempt_finished_result",
            ),
        ]


class StepRecord(RecordRefMixin, AngeeDataModel, concrete=True):
    """One record used by a step, or supplied by its run's trigger."""

    merge_policy = MergePolicy.KEEP
    sqid_prefix = "wsrec_"

    run = models.ForeignKey("workflows.WorkflowRun", on_delete=models.CASCADE, related_name="records")
    step_run = models.ForeignKey(
        "workflows.StepRun",
        on_delete=models.CASCADE,
        related_name="records",
        null=True,
        blank=True,
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")
    label = models.CharField(max_length=200, blank=True, default="")
    operation = models.CharField(max_length=7, choices=RecordOperation.choices, default=RecordOperation.READ)
    objects = StepRecordManager()

    def __str__(self) -> str:
        """Use the authored evidence label, falling back to its public identity."""
        return self.label or str(self.sqid)

    class Meta:
        """Django options for actor-readable execution records."""

        abstract = True
        rebac_resource_type = "workflows/step_record"
        indexes = [models.Index(fields=("content_type", "object_id"), name="workflows_step_record_target")]
        constraints = [
            models.UniqueConstraint(
                fields=("run", "step_run", "content_type", "object_id", "operation"),
                name="workflows_step_record_unique",
            )
        ]


class StepWatch(RecordRefMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """One transactional observation and its durable, coalesced wake obligation."""

    merge_policy = MergePolicy.MOVE
    merge_identity = (("step_run", "content_type", "object_id"),)
    sqid_prefix = "wsw_"
    step_run = models.ForeignKey("workflows.StepRun", on_delete=models.CASCADE, related_name="watches")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")
    pending = models.BooleanField(default=False, editable=False)
    objects = StepWatchManager()

    def merge_collapse(self, duplicate: Any) -> bool:
        """Keep the wake obligation when either watch was still pending on its record."""

        if duplicate.pending and not self.pending:
            self.pending = True
            self.save(update_fields=update_fields_with_auto_now(self, {"pending"}))
        return True

    class Meta:
        """Canonical record uniqueness and source capture lookup."""

        abstract = True
        verbose_name_plural = "step watches"
        rebac_resource_type = "workflows/step_watch"
        constraints = [
            models.UniqueConstraint(
                fields=("step_run", "content_type", "object_id"),
                name="workflows_watch_record_unique",
            )
        ]
        indexes = [models.Index(fields=("content_type", "object_id"), name="workflows_watch_record")]
