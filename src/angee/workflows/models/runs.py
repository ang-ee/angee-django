"""Native workflow runs owners."""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, OperationalError, models, transaction
from django.db.models.functions import Now
from django.utils.functional import cached_property
from rebac import database_context, system_context, to_subject_ref
from rebac.database import write_alias

from angee.base.exceptions import exception_text
from angee.base.fields.state import StateField
from angee.base.fields.text import DiagnosticTextField
from angee.base.models.merge_references import MergePolicy
from angee.base.querysets import row_lock_paths
from angee.base.refs import RecordRef
from angee.base.serialization import strip_null_bytes
from angee.base.signals import publish_change
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.refs import ProtectedRecordRefMixin
from angee.jobs.enqueue import enqueue_task
from angee.workflows.definition import GraphEdge, GraphNode
from angee.workflows.managers import WorkflowRunManager
from angee.workflows.models.definitions import WorkflowVersion
from angee.workflows.querysets import RETRYABLE_SQLSTATES, _record_failure, _sqlstate
from angee.workflows.signals import run_stopping
from angee.workflows.states import (
    CANCELED_OUTCOME,
    DONE_OUTCOME,
    ERROR_OUTCOME,
    NAME_MAX_LENGTH,
    AttemptResult,
    RunOrigin,
    RunRelation,
    RunStatus,
    StepRunStatus,
)
from angee.workflows.steps import Fail, StepMode, Superseded, _AttemptRecord
from angee.workflows.subjects import RunSubject

if TYPE_CHECKING:
    from angee.workflows.models.steps import StepRun

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StepRunStatusCount:
    """An exact count of a node's admitted items in one execution state."""

    status: StepRunStatus
    count: int


@dataclass(frozen=True)
class RunGraphNode:
    """Published topology composed with actor-readable execution progress."""

    node: GraphNode
    step_run: StepRun | None
    item_counts: tuple[StepRunStatusCount, ...]
    item_attempts: int
    plan: str


@dataclass(frozen=True)
class RunGraphEdge(GraphEdge):
    """A routed edge and whether its source retained that outcome."""

    taken: bool


@dataclass(frozen=True)
class RunGraph:
    """One run's pinned structure and bounded node summaries."""

    nodes: tuple[RunGraphNode, ...]
    edges: tuple[RunGraphEdge, ...]


class WorkflowRun(ProtectedRecordRefMixin, ProtectedDataModel, concrete=True):
    """One actor's execution of one immutable graph against an optional record."""

    merge_policy = MergePolicy.KEEP
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
        "workflows.StepRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="child_runs",
    )
    relation = StateField(choices_enum=RunRelation, null=True, blank=True)
    reprocess_of = models.ForeignKey(
        "workflows.WorkflowRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reprocesses",
    )
    trigger_event = models.OneToOneField(
        "workflows.TriggerEvent",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="started_run",
    )
    origin = StateField(choices_enum=RunOrigin, editable=False)
    finished_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True, editable=False)
    prune_after = models.DateTimeField(null=True, blank=True, editable=False)
    prune_reason = DiagnosticTextField(max_length=255, blank=True, default="", editable=False)

    objects = WorkflowRunManager()

    def change_related_records(self) -> tuple[RecordRef, ...]:
        """Invalidate the records this execution has worked on."""
        return (
            apps.get_model("workflows", "StepRecord")
            ._default_manager.db_manager(self._state.db)
            .filter(run_id=self.pk)
            .change_related_records()
        )

    def __str__(self) -> str:
        """Identify an execution by its workflow and start time."""
        if self.version_id is None:
            return str(self._meta.verbose_name)
        name = getattr(self, "_workflow_name", None)
        if name is None:
            with system_context(reason="workflows.run_label"):
                name = self.policy_version.workflow.name
        started = self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "not started"
        return f"{name} · {started}"

    @property
    def outcome_label(self) -> str:
        """Use the frozen publication's reader vocabulary for a terminal result."""
        return self.policy_version.definition.run_outcome_label(self.outcome) if self.outcome else ""

    @cached_property
    def policy_version(self) -> Any:
        """Reuse loaded policy, resolving a viewer-redacted relation by its pinned ID."""
        if version := self._state.fields_cache.get("version"):
            return version
        if "version" not in self._state.fields_cache:
            with system_context(reason="workflows.execution_policy"):
                return self.version
        model = self._meta.get_field("version").related_model
        assert model is not None and issubclass(model, WorkflowVersion)
        return model.system_queryset().get(pk=self.version_id)

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

    @property
    def failure_reason(self) -> str | None:
        """Surface the retained reader-facing failure from the run or its failed step.

        ``error`` stays out: it is gated to run writers (``read__error``).
        """
        if self.outcome != ERROR_OUTCOME:
            return None
        if isinstance(self.output, dict) and isinstance(self.output.get("error"), str) and self.output["error"]:
            return self.output["error"]
        with system_context(reason="workflows.failure_reason"):
            rows = (
                apps.get_model("workflows", "StepRun")
                ._default_manager.db_manager(self._state.db)
                .filter(run_id=self.pk, status=StepRunStatus.FAILED)
                .order_by(
                    "rank",
                    "map_index",
                    "pk",
                )
                .only("status", "output")
            )
            return next((reason for row in rows if (reason := row.failure_reason)), None)

    def _lock_run_subject(self, *, required: bool = True) -> RunSubject | None:
        """Lock an opted-in root subject after the caller has locked this run."""
        if self.parent_step_id is not None:
            return None
        model = self.subject_model_class
        if model is None or not issubclass(model, RunSubject):
            return None
        subject = (
            model._default_manager.db_manager(self._state.db)
            .filter(pk=self.subject_object_id)
            .select_for_update(
                no_key=True,
                of=row_lock_paths(model),
            )
            .first()
        )
        if subject is None and required:
            raise ValidationError("The workflow run subject no longer exists.")
        return cast(RunSubject | None, subject)

    def _write_state(self, *, status: str, outcome: str, output: Any, error: str = "", actor: Any = None) -> None:
        run = self
        rows = type(self).objects.db_manager(self._state.db)
        rows.filter(pk=run.pk).update(
            status=status,
            outcome=outcome,
            output=strip_null_bytes(output),
            error=error,
            finished_at=Now() if status in RunStatus.terminal_values() else None,
            updated_at=Now(),
        )
        if status in RunStatus.terminal_values() and not run.is_terminal:
            # The sender owns this savepoint: a receiver that poisons the
            # transaction rolls back its cleanup, never the terminal write.
            try:
                with transaction.atomic(using=self._state.db):
                    run_stopping.send_robust(sender=type(self), run=run)
            except DatabaseError:
                logger.exception("Workflow run cleanup left the transaction unusable; its cleanup was rolled back.")
            decisions = apps.get_model("decisions", "Decision").objects.db_manager(self._state.db)
            with system_context(reason="workflows.terminal.withdraw"):
                for decision in decisions.filter(requesting_steps__run=run).open().order_by("pk"):
                    decision.withdraw(actor=actor or run.run_as)
            if (subject := self._lock_run_subject(required=False)) is not None:
                subject.settle_run(rows.get(pk=run.pk), status)
            if status != RunStatus.CANCELED:

                def cancel_children() -> None:
                    rows.cancel_abandoned(run.pk)

                transaction.on_commit(cancel_children, using=self._state.db, robust=True)
            enqueue_task(
                "workflows.wake_run",
                kwargs={"run_id": run.pk, "using": self._state.db},
                robust=True,
                using=self._state.db,
            )
        with _record_failure("run change publication", using=self._state.db):
            run.refresh_from_db(using=self._state.db)
            publish_change(run, action="update", update_fields=None)

    def advance(
        self,
        step_run: Any = None,
        settlement: _AttemptRecord | None = None,
        *,
        records: list[Any] | None = None,
        notes: list[dict[str, str]] | None = None,
    ) -> None:
        """Settle, plan and publish once, keeping successful body writes on data errors.

        The caller holds the run lock. A lost fence or transient database error
        escapes to roll back the transaction. Planning has its own savepoint so
        a bad plan cannot poison the transaction recording the durable failure.
        """
        run = self
        step_runs = self.step_runs.system_context(reason="workflows.advance")
        with system_context(reason="workflows.advance"):
            terminal = run.is_terminal
            if terminal and settlement is None:
                return
            output: Any
            try:
                if settlement is not None:
                    with transaction.atomic(using=self._state.db):
                        step_runs.settle(step_run, settlement)
                        if not isinstance(settlement.settlement, Fail):
                            if records:
                                for record in records:
                                    record_model = type(record)
                                    record_model.objects.db_manager(self._state.db).get_or_create(
                                        run_id=record.run_id,
                                        step_run_id=record.step_run_id,
                                        content_type_id=record.content_type_id,
                                        object_id=record.object_id,
                                        operation=record.operation,
                                        defaults={"label": record.label},
                                    )
                            if notes:
                                step_runs.filter(pk=step_run.pk).update(notes=[*step_run.notes, *notes])
                # A preserved IO sibling may settle after failure. Its evidence
                # changes, but the terminal run and graph plan stay untouched.
                if not terminal:
                    with transaction.atomic(using=self._state.db):
                        # The reverse manager needs its connecting FK to populate
                        # Django's known-related-object cache without one query per row.
                        rows = list(
                            step_runs.only(
                                "run_id",
                                "node_key",
                                "map_index",
                                "status",
                                "outcome",
                                "waiting_kind",
                                "input",
                            )
                        )
                        definition = run.version.definition
                        if definition.unrouted_failure(rows):
                            status, outcome, output = RunStatus.FAILED, ERROR_OUTCOME, {}
                        else:
                            planned = definition.ready_nodes(
                                rows,
                                map_concurrency=settings.ANGEE_WORKFLOW_MAP_CONCURRENCY,
                            )
                            wake = [node.node_key for node in planned if node.existing]
                            if wake:
                                step_runs.filter(node_key__in=wake).to_ready()
                            new_steps = [
                                step_runs.model(
                                    run=run,
                                    node_key=node.node_key,
                                    map_index=node.map_index,
                                    status=StepRunStatus(node.status),
                                    rank=node.rank,
                                )
                                for node in planned
                                if not node.existing
                            ]
                            if new_steps:
                                step_runs.bulk_create(new_steps)
                            statuses = set(step_runs.values_list("status", flat=True))
                            if statuses & {StepRunStatus.READY, StepRunStatus.RUNNING}:
                                status, outcome, output = RunStatus.RUNNING, "", {}
                            elif StepRunStatus.WAITING in statuses:
                                status, outcome, output = RunStatus.WAITING, "", {}
                            else:
                                status = RunStatus.SUCCEEDED
                                outcome, output = definition.result_for(step_runs.all(), run.input) or (
                                    DONE_OUTCOME,
                                    {},
                                )
                        run._write_state(status=status, outcome=outcome, output=output)
            except Superseded:
                raise
            except Exception as failure:
                if isinstance(failure, OperationalError) and (
                    _sqlstate(failure) in RETRYABLE_SQLSTATES
                    or step_run is not None
                    and step_run.step.mode == StepMode.IO
                ):
                    raise
                logger.exception("Workflow run %s failed during advancement.", run.pk)
                run_error = exception_text(failure)
                attempt_error = exception_text(failure, diagnostic=True)
                timed_out = isinstance(failure, SoftTimeLimitExceeded)
                recorded_on_attempt = False
                if step_run is not None:
                    with _record_failure("settlement failure", using=self._state.db):
                        # A planning error follows a completed settlement and
                        # cannot replace it. A failed settlement still owns its fence.
                        if step_runs.filter(pk=step_run.pk, status=StepRunStatus.RUNNING).exists():
                            step_runs.settle(
                                step_run,
                                _AttemptRecord.failure(
                                    run_error,
                                    diagnostic_error=attempt_error,
                                    stacktrace=traceback.format_exc(),
                                    timed_out=timed_out,
                                ),
                            )
                            recorded_on_attempt = True
                with _record_failure("attempt close", using=self._state.db):
                    if step_run is not None:
                        closed = step_run.attempts.close(
                            AttemptResult.TIMED_OUT if timed_out else AttemptResult.FAILED,
                            attempt_error if timed_out else settlement.error if settlement else "",
                            traceback.format_exc() if timed_out else settlement.stacktrace if settlement else "",
                        )
                        if recorded_on_attempt or timed_out and closed:
                            run_error = ""
                if not terminal:
                    with _record_failure("run state", using=self._state.db):
                        run._write_state(
                            status=RunStatus.FAILED,
                            outcome=ERROR_OUTCOME,
                            output={},
                            error=run_error,
                        )
            if terminal:
                # Preserved IO evidence changed without a run state transition.
                with _record_failure("run change publication", using=self._state.db):
                    run.refresh_from_db(using=self._state.db)
                    publish_change(run, action="update", update_fields=None)

    def graph(self, actor: Any) -> RunGraph:
        """Read this run's frozen topology with two actor-scoped execution queries."""
        if actor is None:
            raise PermissionDenied("A run graph requires a reader.")
        self.require_access("read", actor)
        definition = self.policy_version.definition
        topology = definition.topology()
        steps = cast(Any, self.step_runs.model.objects.db_manager(self._state.db).with_actor(actor)).filter(
            run_id=self.pk
        )
        rows = {row.node_key: row for row in steps.nodes()}
        for row in rows.values():
            row.run = self
        plan = definition.plan(
            rows.values(),
            terminal=self.stopped_at is not None or self.is_terminal and self.status != RunStatus.FAILED,
        )
        counts: dict[str, list[StepRunStatusCount]] = {}
        attempts: dict[str, int] = {}
        for item in steps.item_counts():
            key = item["node_key"]
            counts.setdefault(key, []).append(StepRunStatusCount(StepRunStatus(item["status"]), item["count"]))
            attempts[key] = attempts.get(key, 0) + item["attempts"]
        return RunGraph(
            nodes=tuple(
                RunGraphNode(
                    node=node,
                    step_run=rows.get(node.key),
                    plan=plan.get(node.key, "not_run"),
                    item_counts=tuple(counts.get(node.body_key, ())) if node.body_key else (),
                    item_attempts=attempts.get(node.body_key, 0) if node.body_key else 0,
                )
                for node in topology.nodes
            ),
            edges=tuple(
                RunGraphEdge(
                    source=edge.source,
                    outcome=edge.outcome,
                    target=edge.target,
                    taken=edge.source in rows and definition.edge_live(rows[edge.source], {edge.outcome}),
                )
                for edge in topology.edges
            ),
        )

    def _cancel_owned_work(self, *, actor: Any) -> tuple[bool, int]:
        """Stop this locked run's open work while preserving its terminal facts."""
        stopped = self.stopped_at is None and (not self.is_terminal or self.status == RunStatus.FAILED)
        if stopped:
            type(self).objects.db_manager(self._state.db).filter(pk=self.pk).update(stopped_at=Now(), updated_at=Now())
            self.refresh_from_db(using=self._state.db, fields=["stopped_at"])
        transitioning = not self.is_terminal
        if transitioning:
            self._write_state(status=str(RunStatus.CANCELED), outcome=CANCELED_OUTCOME, output={}, actor=actor)
        changed = self.step_runs.cancel_open()
        if not transitioning and (changed or stopped):
            with _record_failure("run change publication", using=self._state.db):
                self.refresh_from_db(using=self._state.db)
                publish_change(self, action="update", update_fields=None)
        return transitioning, changed

    def cancel_on_commit(self, actor: Any) -> None:
        """Deliver an authorized cancellation after this transaction releases its locks.

        Rollback discards the request. The task calls ``cancel`` with the same
        actor, rechecking access and terminal state under the existing owner.
        Robust delivery failures are logged without interrupting other callbacks.
        """
        run = self
        using = write_alias(type(run), using=run._state.db, instance=run)
        with database_context(using):
            actor = run.require_access("write", actor)
            if actor is None:
                raise PermissionDenied("Deferred cancellation requires an actor.")
            payload = {"run_id": run.pk, "actor": str(to_subject_ref(actor)), "using": using}
            enqueue_task("workflows.cancel", kwargs=payload, robust=True, using=using)

    def reopen(self) -> None:
        """Clear a locked run's terminal facts and replan retained step rows."""
        run = self
        if (subject := run._lock_run_subject()) is not None:
            subject.admit_run(run)
        run._write_state(status=str(RunStatus.RUNNING), outcome="", output={})

        run.advance()

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
            or self.status == RunStatus.FAILED
            and self.stopped_at is None
            or self.step_runs.model.system_queryset()
            .using(self._state.db)
            .filter(run=self)
            .exclude(status__in=StepRunStatus.terminal_values())
            .exists()
            or any(
                child._has_open_owned_work
                for child in type(self)
                .system_queryset()
                .using(self._state.db)
                .filter(
                    parent_step__run=self,
                    relation=RunRelation.OWNED,
                )
            )
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
            models.CheckConstraint(
                condition=(
                    models.Q(
                        origin=RunOrigin.MANUAL,
                        parent_step__isnull=True,
                        reprocess_of__isnull=True,
                        trigger_event__isnull=True,
                    )
                    | models.Q(
                        origin=RunOrigin.WORKFLOW,
                        parent_step__isnull=False,
                        reprocess_of__isnull=True,
                        trigger_event__isnull=True,
                    )
                    | models.Q(
                        origin=RunOrigin.REPROCESS,
                        parent_step__isnull=True,
                        reprocess_of__isnull=False,
                        trigger_event__isnull=True,
                    )
                    | models.Q(
                        origin=RunOrigin.TRIGGER,
                        parent_step__isnull=True,
                        reprocess_of__isnull=True,
                        trigger_event__isnull=False,
                    )
                ),
                name="workflows_run_origin_cause",
            ),
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
