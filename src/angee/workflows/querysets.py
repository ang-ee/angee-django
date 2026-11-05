"""Native workflow populations, conditional writes and transaction boundaries."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.models import NOT_PROVIDED, Count, Exists, F, Max, OuterRef, Q, QuerySet, Sum, Value, Window
from django.db.models.functions import Concat, Least, Now, RowNumber
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from rebac import (
    database_context,
    generic_target,
    system_context,
)

from angee.base.querysets import by_key
from angee.base.refs import RecordRef, record_view_refs
from angee.base.signals import bulk_updated, changes_muted, mute_changes
from angee.iam.models.append_only import ProtectedAppendOnlyQuerySet
from angee.iam.querysets import ProtectedQuerySet
from angee.jobs.enqueue import enqueue_task
from angee.jobs.timeouts import task_time_budget
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.workflows.definition import (
    MAP_BODY_SUFFIX,
    Definition,
)
from angee.workflows.states import (
    ERROR_OUTCOME,
    AttemptResult,
    NodeKey,
    RunRelation,
    RunStatus,
    StepRunStatus,
    WaitingKind,
)
from angee.workflows.steps import StepMode, Superseded

if TYPE_CHECKING:
    from angee.workflows.models import StepAttempt, StepRecord, StepRun, Trigger, TriggerEvent, WorkflowRun

logger = logging.getLogger(__name__)


RETRYABLE_SQLSTATES = frozenset({"57014", "40P01", "55P03"})


_held_runs: ContextVar[dict[str, set[int]] | None] = ContextVar("workflows_held_runs", default=None)


_BLOCK_ACTION: TypeAdapter[NodeKey] = TypeAdapter(NodeKey)


def _sqlstate(error: Exception) -> str | None:
    """Read the native database driver's SQLSTATE at one boundary."""
    return getattr(error.__cause__, "sqlstate", None)


@contextmanager
def _record_failure(operation: str, *, using: str | None = None) -> Iterator[None]:
    """Isolate diagnostic writes; a lost claim still aborts its execution transaction."""
    try:
        with transaction.atomic(using=using):
            yield
    except Superseded:
        raise
    except Exception:
        logger.exception("Workflow %s failed.", operation)


class WorkflowRunQuerySet[T: WorkflowRun](ProtectedQuerySet[T]):
    """Own run locks and the delivery obligation every lock holder acquires."""

    def for_subject(self, record: Any) -> Any:
        """Select the canonical subject without changing this queryset's read scope."""
        return self.filter(self.model.record_filter(record))

    def about(
        self,
        records: Any,
        *,
        operations: tuple[str, ...] | None = None,
        ancestors: bool = True,
        limit: int | None = None,
    ) -> Any:
        """Runs that worked on readable records and all readable ancestors, oldest first.

        Evidence links select run identities; this queryset admits the rows.
        """
        if not isinstance(records, (list, tuple)):
            records = (records,)
        subject_filter = Q(pk__in=[])
        targets = Q(pk__in=[])
        evidence_model = apps.get_model("workflows", "StepRecord")
        for record in records:
            targets |= evidence_model.record_filter(record)
            subject_filter |= self.model.record_filter(record)
        links = evidence_model._base_manager.using(self.db).filter(targets)
        if operations is not None:
            links = links.filter(operation__in=operations)
        link_runs = links.values("run_id")
        if not ancestors and limit is None:
            # Both membership arms use this queryset's final read scope.
            return self.filter(subject_filter | Q(pk__in=link_runs)).order_by("created_at", "pk").distinct()
        subjects: QuerySet[WorkflowRun, Mapping[str, object]] = self.filter(subject_filter).values("pk")
        if limit is not None:
            subjects = (
                self.filter(subject_filter)
                .alias(
                    _rank=Window(
                        RowNumber(),
                        partition_by=["subject_content_type_id", "subject_object_id"],
                        order_by=["-created_at", "-pk"],
                    )
                )
                .filter(_rank__lte=limit)
                .values("pk")
            )
            link_runs = (
                links.values("content_type_id", "object_id", "run_id")
                .annotate(
                    _created=Max("run__created_at"),
                )
                .annotate(
                    _rank=Window(
                        RowNumber(),
                        partition_by=["content_type_id", "object_id"],
                        order_by=[F("_created").desc(), F("run_id").desc()],
                    )
                )
                .filter(_rank__lte=limit)
                .values("run_id")
            )
        if not ancestors:
            return (
                self.filter(Q(pk__in=subjects) | Q(pk__in=link_runs))
                .order_by(
                    "created_at",
                    "pk",
                )
                .distinct()
            )
        ids = set(subjects.values_list("pk", flat=True))
        ids.update(self.filter(pk__in=link_runs).values_list("pk", flat=True))
        frontier = ids.copy()
        while frontier:
            parents = set(
                self.filter(pk__in=frontier).exclude(parent_step=None).values_list("parent_step__run_id", flat=True)
            )
            frontier = set(self.filter(pk__in=parents).values_list("pk", flat=True)) - ids
            ids.update(frontier)
        return self.filter(pk__in=ids).order_by("created_at", "pk").distinct()

    def working_on(self, record: Any) -> Any:
        """Unfinished runs whose subject is ``record`` or that created or changed it.

        Deleting the record cancels these; their own ancestors are not included.
        """
        return self.exclude(status__in=RunStatus.terminal_values()).about(
            record,
            operations=("created", "changed"),
            ancestors=False,
        )

    def blocking(self, record: Any, action: str) -> Any:
        """Return open runs whose pinned definition blocks an action on ``record``."""

        try:
            action = _BLOCK_ACTION.validate_python(action, strict=True)
        except PydanticValidationError as error:
            raise ValidationError({"action": "A workflow action must be a stable lowercase identifier."}) from error
        target = generic_target(record)
        model = target.content_type.model_class()
        if model is None:
            raise ValidationError("The blocked record model is no longer installed.")
        token = f"{model._meta.label}:{action}"
        # A block is policy: it holds whether or not the caller may read the run (an import's
        # run belongs to the workflow's service user, not to the clerk posting the bill), so the
        # open runs and their pinned definitions are read under the policy's own system scope.
        version_model = apps.get_model("workflows", "WorkflowVersion")
        with system_context(reason="workflows.blocking_policy"):
            runs = (
                self.model.objects.db_manager(self.db)
                .system_context(reason="workflows.blocking_policy")
                .exclude(status__in=RunStatus.terminal_values())
                .about(record, ancestors=False)
            )
            version_ids = set(runs.order_by().values_list("version_id", flat=True))
            blocked = [
                version_id
                for version_id, document in version_model.system_queryset()
                .using(self.db)
                .filter(pk__in=version_ids)
                .values_list("pk", "document")
                if token in Definition.model_validate(document).blocks
            ]
            return (
                self.model.system_queryset()
                .using(self.db)
                .filter(
                    pk__in=list(runs.filter(version_id__in=blocked).order_by().values_list("pk", flat=True)),
                )
            )

    @contextmanager
    def hold(self, run_id: int, *, skip_locked: bool = False, timeout: timedelta | None = None) -> Iterator[Any]:
        """Lock one run and dispatch once for the outermost hold after commit.

        Register at context exit. Robust callbacks log delivery failures without
        suppressing later publications from enclosing parent/child transactions;
        the tick recovers the durable ready rows.
        """
        databases = _held_runs.get()
        token = None
        if databases is None:
            databases = {}
            token = _held_runs.set(databases)
        outermost = self.db not in databases
        held = databases.setdefault(self.db, set())
        previous = held.copy()
        try:
            with transaction.atomic(using=self.db):
                with (
                    system_context(reason="workflows.hold"),
                    (
                        _database_timeout(timeout, setting="lock_timeout", using=self.db)
                        if timeout is not None
                        else nullcontext()
                    ),
                    transaction.atomic(using=self.db) if timeout is not None else nullcontext(),
                ):
                    run = self.filter(pk=run_id).lock_if_supported(no_key=True, skip_locked=skip_locked).first()
                if run is not None:
                    held.add(run.pk)
                try:
                    yield run
                finally:
                    if outermost and held:
                        ids = sorted(held)
                        transaction.on_commit(
                            lambda: (
                                apps.get_model("workflows", "StepRun")
                                .objects.db_manager(self.db)
                                .filter(run_id__in=ids)
                                .dispatch()
                            ),
                            using=self.db,
                            robust=True,
                        )
        except BaseException:
            held.clear()
            held.update(previous)
            raise
        finally:
            if outermost:
                del databases[self.db]
            if token is not None:
                _held_runs.reset(token)

    @contextmanager
    def hold_owned(self, run_id: int, *, skip_locked: bool = False, timeout: timedelta | None = None) -> Iterator[Any]:
        """Hold an owned tree ancestor first, admitting no new child behind a held parent."""
        with ExitStack() as stack, system_context(reason="workflows.hold_owned"):
            root = stack.enter_context(self.hold(run_id, skip_locked=skip_locked, timeout=timeout))
            runs = [] if root is None else [root]
            for run in runs:
                children = list(
                    self.filter(parent_step__run=run, relation=RunRelation.OWNED)
                    .order_by("pk")
                    .values_list("pk", flat=True)
                )
                for child_id in children:
                    child = stack.enter_context(self.hold(child_id, skip_locked=skip_locked, timeout=timeout))
                    if child is None:
                        yield []
                        return
                    runs.append(child)
            yield runs

    def retention_candidates(self) -> Any:
        """Return old terminal roots, letting marked blockers yield to later batches."""
        return self.filter(
            Q(parent_step__isnull=True) | Q(relation=RunRelation.CONTINUATION),
            Q(prune_after__isnull=True) | Q(prune_after__lte=Now()),
            status__in=RunStatus.terminal_values(),
            finished_at__lt=Now() - timedelta(days=settings.ANGEE_WORKFLOW_RETENTION_DAYS),
        )


class StepRecordQuerySet[T: StepRecord](ProtectedAppendOnlyQuerySet[T], ProtectedQuerySet[T]):
    """Keep retained admission references immutable until their run is pruned."""

    def change_related_records(self) -> tuple[RecordRef, ...]:
        """Publish distinct concern identities without loading retained evidence rows."""
        with system_context(reason="workflows.change_concerns"):
            identities = self.order_by().values_list("content_type_id", "object_id").distinct()
            return record_view_refs(
                *(
                    cast(
                        T,
                        self.model.from_db(
                            self.db,
                            ["content_type_id", "object_id"],
                            [content_type_id, object_id],
                        ),
                    ).record_ref
                    for content_type_id, object_id in identities
                ),
                using=self.db,
            )


class StepRunQuerySet[T: StepRun](ProtectedQuerySet[T]):
    """Own conditional step transitions and database-clock candidate scopes."""

    def _write_transition(self, **values: Any) -> int:
        """Publish affected targets after a conditional state write, retaining its fence."""

        with transaction.atomic(using=self.db):
            keys = list(self.lock_if_supported().order_by("pk").values_list("pk", flat=True))
            changed = sum(batch.update(**values) for batch in by_key(self, keys))
            if changed and not changes_muted():
                rows = self.model._base_manager.using(self.db).defer("input", "output", "state", "notes")
                for batch in by_key(rows, keys):
                    bulk_updated.send_robust(
                        sender=self.model, instances=tuple(batch), update_fields=frozenset(values), using=self.db
                    )
            return changed

    def nodes(self) -> Any:
        """Read declared nodes with bounded map progress, excluding item rows."""
        return self.exclude(node_key__endswith=MAP_BODY_SUFFIX).defer("input", "output", "state")._graph_rows()

    def _graph_rows(self) -> Any:
        """Project node progress when a graph reader also requests map item rows."""
        return self.annotate(
            _failure_reason=self.model.failure_reason_expression(),
            _map_total=self.model.map_total_expression(),
            _map_settled=self.model.map_settled_expression(),
        )

    def item_counts(self) -> Any:
        """Aggregate every admitted map item without fetching its payload."""
        return (
            self.filter(node_key__endswith=MAP_BODY_SUFFIX)
            .values("run_id", "node_key", "status")
            .annotate(count=Count("pk"), attempts=Sum("attempt"))
            .order_by("run_id", "node_key", "status")
        )

    def body_progress(self) -> Any:
        """Count map slots and locate the next index without loading item rows."""
        return (
            self.filter(node_key__endswith=MAP_BODY_SUFFIX)
            .values("node_key")
            .annotate(
                count=Count("pk"),
                open_count=Count("pk", filter=~Q(status__in=StepRunStatus.terminal_values())),
                last_index=Max("map_index"),
                failed_count=Count("pk", filter=Q(status=StepRunStatus.FAILED)),
            )
            .order_by("node_key")
        )

    def for_map(self, run_id: Any, node_key: Any) -> Any:
        """Select a containing map's body rows, accepting native ORM expressions."""
        key = Value(node_key) if isinstance(node_key, str) else node_key
        return self.filter(run_id=run_id, node_key=Concat(key, Value(MAP_BODY_SUFFIX)))

    def collect_map(self, expected_count: int) -> list[dict[str, Any]] | None:
        """Collect ordered terminal evidence, or None while body rows remain unsettled."""
        rows = list(self.order_by("map_index"))
        if len(rows) != expected_count or any(row.status not in StepRunStatus.terminal_values() for row in rows):
            return None
        if any(row.status not in (StepRunStatus.SUCCEEDED, StepRunStatus.FAILED) for row in rows):
            raise ValidationError("A canceled or skipped map body cannot produce a result.")
        return [
            {
                "index": row.map_index,
                "outcome": row.outcome,
                **(
                    {"error": row.failure_reason or ""}
                    if row.status == StepRunStatus.FAILED
                    else {"output": row.output}
                ),
            }
            for row in rows
        ]

    @staticmethod
    def _cleared_wait() -> dict[str, Any]:
        """Clear the companion columns shared by every non-waiting transition."""
        return {"waiting_kind": None, "wait_reason": "", "wake_at": None, "deadline_at": None, "updated_at": Now()}

    def dispatch(self) -> None:
        """Send unlocked ready rows after refreshing their delivery timestamp."""
        with database_context(self.db), transaction.atomic(using=self.db), system_context(reason="workflows.dispatch"):
            ready = self.filter(status=StepRunStatus.READY).exclude(run__status__in=RunStatus.terminal_values())
            selected = ready.order_by("pk").lock_if_supported(no_key=True, skip_locked=True)
            selected_ids = list(selected.values_list("pk", flat=True))
            ready.filter(pk__in=selected_ids).update(dispatched_at=Now(), updated_at=Now())
            for pk in selected_ids:
                enqueue_task("workflows.execute", kwargs={"step_run_id": pk, "using": self.db}, using=self.db)

    def count_redispatch(self) -> int:
        """Count a tick redelivery without changing status or publishing a change."""
        return self.filter(status=StepRunStatus.READY).update(dispatches=F("dispatches") + 1, updated_at=Now())

    def claim(self, step_run: Any) -> Any:
        """Claim a ready row, append its attempt and persist input before the body."""
        if not connections[self.db].in_atomic_block:
            raise RuntimeError("claim requires the caller's locked transaction.")
        values = dict(attempt=F("attempt") + 1, idempotency_token=step_run.idempotency_token or uuid4())
        acknowledged_by_id = step_run.retry_acknowledged_by_id
        changed = self.filter(pk=step_run.pk, status=StepRunStatus.READY).update(**values)
        if not changed:
            return None
        step_run.refresh_from_db(fields=list(values))
        attempt = step_run.attempts.create(
            number=step_run.attempt,
            page_index=step_run.page_index,
            acknowledged_by_id=acknowledged_by_id,
        )
        deadline = timedelta(seconds=settings.CELERY_TASK_TIME_LIMIT)
        until = attempt.started_at + deadline
        running = False
        try:
            with transaction.atomic(using=self.db):
                step = step_run.step
                if step.mode == StepMode.IO:
                    deadline = step.timeout
                    until = attempt.started_at + task_time_budget()
                definition = step_run.run.version.definition
                parent = step_run.node_key.partition(".")[0]
                sources = definition.ancestors(parent)
                if parent != step_run.node_key:
                    sources.add(parent)
                step_run.input = definition.input_for(
                    step_run.node_key,
                    step_run.run.input,
                    step_run.run.step_runs.filter(node_key__in=sources).only("node_key", "status", "output", "input"),
                    map_index=step_run.map_index,
                )
                self.filter(pk=step_run.pk).to_running(deadline, until=until, input=step_run.input)
            running = True
        finally:
            # Failed input preparation rolls back before this durable fence,
            # allowing the caller to retain the attempt's failure.
            if not running:
                self.filter(pk=step_run.pk).to_running(deadline, until=until)
            step_run.refresh_from_db(
                fields=[
                    "status",
                    "deadline_at",
                    "waiting_kind",
                    "wake_at",
                    "wait_reason",
                    "dispatches",
                    "retry_acknowledged_by_id",
                ]
            )
        return attempt

    def to_running(self, timeout: timedelta, *, until: datetime, input: Any = NOT_PROVIDED) -> int:
        """Persist the running fence and, when prepared, its input in one write."""
        values = self._cleared_wait() | {
            "deadline_at": Least(Now() + timeout, until),
            "status": StepRunStatus.RUNNING,
            "dispatches": 0,
            "retry_acknowledged_by_id": None,
        }
        if input is not NOT_PROVIDED:
            values["input"] = input
        return self.filter(status=StepRunStatus.READY).update(**values)

    def extend_deadline(self, timeout: timedelta, *, until: datetime) -> int:
        """Extend a fenced claim from database time within the worker's IO budget."""
        return self.update(deadline_at=Least(Now() + timeout, until), updated_at=Now())

    def fenced(self, step_run: Any) -> Any:
        """Select this still-live claim; every live attempt write uses this predicate."""
        return self.filter(
            pk=step_run.pk,
            status=StepRunStatus.RUNNING,
            attempt=step_run.attempt,
            deadline_at__gt=Now(),
        )

    def settle(self, step_run: Any, attempt: Any) -> None:
        """Record a settlement only while its numbered claim is live."""
        self.fenced(step_run)._record_settlement(step_run, attempt)

    def expire(self, step_run: Any, attempt: Any) -> None:
        """Recover an expired claim under the tick's run and step locks."""
        self.expired().filter(pk=step_run.pk, attempt=step_run.attempt)._record_settlement(
            step_run,
            attempt,
        )

    def _record_settlement(self, step_run: Any, attempt: Any) -> None:
        """Persist one eligible claim and close its attempt through shared policy."""
        with mute_changes():
            changed = attempt.settlement.transition(self, step_run, attempt)
        if changed != 1:
            raise Superseded
        if not attempt.settlement.keeps_watches:
            step_run.watches.all().delete()
        if (
            step_run.attempts.filter(number=step_run.attempt).close(
                attempt.result,
                attempt.diagnostic_error or attempt.error,
                attempt.stacktrace,
            )
            != 1
        ):
            raise Superseded
        current = self.model._base_manager.using(self.db).defer("input", "output", "state", "notes").get(pk=step_run.pk)
        bulk_updated.send_robust(
            sender=self.model, instances=(current,), update_fields=frozenset(("status", "decision_id")), using=self.db
        )

    def to_succeeded(self, *, output: Any, outcome: str) -> int:
        """Complete selected claims with their validated result and clear their wait."""

        return self.update(
            **self._cleared_wait(),
            status=StepRunStatus.SUCCEEDED,
            output=output,
            outcome=outcome,
            retries=0,
        )

    def fail_attempt(self, step_run: Any, attempt: Any, *, error: str) -> int:
        """Retry or fail this selected claim under its persisted effect and retry policy."""

        retries = step_run.retries + 1
        if attempt.retryable and step_run.requires_duplicate_acknowledgement:
            return self.to_waiting(kind=WaitingKind.ERROR, reason="possible duplicate effect", retries=retries)
        if attempt.retryable and retries < step_run.step.retry.max_attempts:
            return self.to_waiting(
                until=Now() + step_run.step.retry.delay_for(retries),
                state=step_run.state,
                retries=retries,
            )
        error_field = step_run.attempts.model._meta.get_field("error")
        return self.update(
            **self._cleared_wait(),
            status=StepRunStatus.FAILED,
            outcome=ERROR_OUTCOME,
            output={"error": error_field.get_prep_value(error)},
            retries=retries,
        )

    def to_waiting(
        self,
        *,
        until: Any = None,
        state: Any = None,
        retries: int | None = None,
        kind: WaitingKind = cast(WaitingKind, WaitingKind.TIME),
        reason: str = "",
        decision_id: Any = None,
    ) -> int:
        """Park running rows, preserving retries unless a failure consumed one."""
        if kind == WaitingKind.ERROR and not reason:
            raise ValueError("An operator wait requires its reason.")
        return self.filter(status__in=(StepRunStatus.RUNNING, StepRunStatus.READY))._write_transition(
            **(
                self._cleared_wait()
                | {
                    "waiting_kind": kind,
                    "wake_at": until,
                    "wait_reason": reason if kind == WaitingKind.ERROR else "",
                }
            ),
            status=StepRunStatus.WAITING,
            state=F("state") if state is None else state,
            outcome="",
            retries=F("retries") if retries is None else retries,
            decision_id=F("decision_id") if decision_id is None else decision_id,
        )

    def to_ready(
        self,
        *,
        state: Any = None,
        reset_retries: bool = False,
        acknowledged_by_id: Any = None,
        next_page: bool = False,
        reset_dispatches: bool = False,
    ) -> int:
        """Wake waiting rows and clear wait/deadline state; delivery is commit-owned."""
        return self.filter(
            status__in=(StepRunStatus.WAITING, StepRunStatus.FAILED, StepRunStatus.RUNNING),
        )._write_transition(
            **self._cleared_wait(),
            status=StepRunStatus.READY,
            dispatched_at=Now(),
            state=F("state") if state is None else state,
            retries=0 if reset_retries else F("retries"),
            outcome="",
            output={},
            retry_acknowledged_by_id=acknowledged_by_id,
            dispatches=0 if reset_dispatches else F("dispatches"),
            page_index=F("page_index") + 1 if next_page else F("page_index"),
        )

    def cancel_open(self) -> int:
        """Cancel open steps while leaving their questions and answers intact."""
        opened = self.exclude(status__in=StepRunStatus.terminal_values())
        list(opened.order_by("pk").lock_if_supported(no_key=True).values_list("pk", flat=True))
        attempts = apps.get_model("workflows", "StepAttempt")
        attempts.objects.db_manager(self.db).filter(step_run__in=opened).close(AttemptResult.SUPERSEDED)
        apps.get_model("workflows", "StepWatch").objects.db_manager(self.db).filter(step_run__in=opened).delete()
        return opened._write_transition(
            **self._cleared_wait(),
            status=StepRunStatus.CANCELED,
        )

    def due(self) -> Any:
        """Return time or record waits whose optional database deadline has arrived."""
        return self.filter(
            status=StepRunStatus.WAITING, waiting_kind__in=(WaitingKind.TIME, WaitingKind.RECORD), wake_at__lte=Now()
        )

    def changed_records(self, *, content_type_id: int | None = None, object_id: Any = None) -> Any:
        """Select each live waiter once, even when several watched records changed."""
        watches = apps.get_model("workflows", "StepWatch")
        pending = watches.system_queryset().using(self.db).filter(step_run_id=OuterRef("pk"), pending=True)
        if content_type_id is not None:
            pending = pending.filter(content_type_id=content_type_id, object_id=object_id)
        return self.filter(Exists(pending), status=StepRunStatus.WAITING, waiting_kind=WaitingKind.RECORD)

    def answered_decisions(self) -> Any:
        """Wake an answered step without joining its question into the row lock."""
        answered = (
            apps.get_model("decisions", "Decision")
            .system_queryset()
            .using(self.db)
            .filter(
                pk=OuterRef("decision_id"),
            )
            .exclude(verdict__isnull=True)
        )
        return self.filter(
            Exists(answered),
            status=StepRunStatus.WAITING,
            waiting_kind=WaitingKind.DECISION,
            decision_id__isnull=False,
        )

    def terminal_runs(self) -> Any:
        """Select run waiters whose protected target has reached a terminal state."""
        return self.filter(
            status=StepRunStatus.WAITING,
            waiting_kind=WaitingKind.RUN,
            awaited_run__status__in=RunStatus.terminal_values(),
        )

    def undispatched(self) -> Any:
        """Return ready rows whose most recent delivery is old enough to retry."""
        return self.filter(status=StepRunStatus.READY, dispatched_at__lte=Now() - timedelta(seconds=60))

    def expired(self) -> Any:
        """Return committed claims whose database deadline has expired."""
        return self.filter(status=StepRunStatus.RUNNING, deadline_at__lt=Now())


class StepAttemptQuerySet[T: StepAttempt](ProtectedQuerySet[T]):
    """Own the one-way closure of an execution attempt."""

    def close(self, result: str, error: str = "", stacktrace: str = "") -> int:
        """Close unfinished attempts once, sanitizing their diagnostic columns."""
        return self.filter(finished_at__isnull=True).update(
            finished_at=Now(),
            result=result,
            error=error,
            stacktrace=stacktrace,
            updated_at=Now(),
        )


@contextmanager
def _database_timeout(
    timeout: timedelta,
    *,
    setting: str = "statement_timeout",
    using: str | None = None,
) -> Iterator[None]:
    """Bound each PostgreSQL statement, flooring sub-millisecond limits to 1 ms."""
    connection = connections[using or DEFAULT_DB_ALIAS]
    previous = None
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting(%s)", [setting])
            previous = cursor.fetchone()[0]
            cursor.execute(
                "SELECT set_config(%s, %s, true)",
                [setting, str(max(1, int(timeout.total_seconds() * 1000)))],
            )
    try:
        yield
    finally:
        if previous is not None:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config(%s, %s, true)", [setting, previous])


class TriggerEventQuerySet[T: TriggerEvent](ProtectedQuerySet[T]):
    """Pending admission is a re-armed, never-admitted ledger fact."""

    def pending(self) -> Any:
        """A later save retries a rejection; admission survives run pruning."""
        return self.filter(Q(evaluated_at__isnull=True) | Q(changed_at__gt=F("evaluated_at")), admitted_at=None)


class TriggerQuerySet[T: Trigger](ProtectedQuerySet[T]):
    """Keep authored writes at the validating model; engine updates use explicit scope."""

    def _check_bulk_write(self) -> None:
        if not self.is_sudo():
            raise ValidationError("Edit trigger configuration through model save().")

    def update(self, **kwargs: Any) -> int:
        """Reserve bulk transitions for the engine's explicitly elevated queryset."""
        self._check_bulk_write()
        return super().update(**kwargs)

    def bulk_create(self, *args: Any, **kwargs: Any) -> Any:
        """Keep signal-free insertion from bypassing server-owned activation."""
        self._check_bulk_write()
        return super().bulk_create(*args, **kwargs)


class WorkflowVersionQuerySet(ProtectedAppendOnlyQuerySet[Any], AngeeHistoryQuerySet[Any]):
    """Prevent collection mutation of published graph documents."""
