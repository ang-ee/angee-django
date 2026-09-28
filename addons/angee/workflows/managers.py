"""Workflow admission, publication and transactional DATABASE execution."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property
from typing import Any, Literal

from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F, Max
from django.db.models.functions import Now
from rebac import actor_context, current_actor, system_context, to_subject_ref
from rebac.actors import is_sudo

from angee.base.actors import actor_user_id
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.base.serialization import canonical_json_sha256
from angee.graphql.publishing import publish_change
from angee.jobs.enqueue import enqueue_task
from angee.workflows.context import StepContext
from angee.workflows.definition import Definition, DefinitionInvalid, Issue
from angee.workflows.states import (
    CANCELED_OUTCOME,
    DONE_OUTCOME,
    ERROR_OUTCOME,
    AttemptResult,
    RunStatus,
    StepRunStatus,
    WaitingKind,
)
from angee.workflows.steps import Fail, Retryable, Settlement, Step, Superseded

logger = logging.getLogger(__name__)


def _error_text(value: str) -> str:
    """Remove NUL bytes from unbounded diagnostic text before persistence."""
    return value.replace("\x00", "")


@contextmanager
def _record_failure(operation: str) -> Iterator[None]:
    """Isolate a failure recorder so it cannot roll back successful body work."""
    try:
        with transaction.atomic():
            yield
    except Exception:
        logger.exception("Workflow %s failed.", operation)


@dataclass(frozen=True)
class DraftSave:
    """A conditional draft save and its complete validation diagnostics."""

    status: Literal["saved", "conflict", "invalid"]
    revision: int
    issues: list[Issue]


def _authorize(instance: Any, actor: Any, permission: str = "write") -> Any:
    actor = actor if actor is not None else current_actor()
    if actor is None and is_sudo():
        return None
    if actor is None or not instance.with_actor(actor).has_access(permission):
        raise PermissionDenied(f"{permission} access is required.")
    return actor


class WorkflowManager(AngeeManager):
    """Own the editable document and the immutable publication sequence."""

    def save_identity(
        self, *, key: str, name: str, description: str = "", subject_model: str = "", actor: Any = None,
    ) -> Any:
        """Save an authorized identity with a canonical, version-stable subject model."""
        if subject_model:
            try:
                subject_model = apps.get_model(subject_model)._meta.label_lower
            except (LookupError, ValueError) as error:
                raise DefinitionInvalid([
                    Issue(
                        path=["subject_model"], code="subject_model",
                        message=f"Unknown subject model {subject_model!r}.",
                    ),
                ]) from error
        with transaction.atomic(), actor_context(actor) if actor is not None else nullcontext():
            workflow, _ = self.get_or_create(
                key=key, defaults={"name": name, "description": description, "subject_model": subject_model},
            )
            _authorize(workflow, actor)
            workflow = self.filter(pk=workflow.pk).lock_if_supported(no_key=True).get()
            if workflow.subject_model != subject_model and workflow.versions.exists():
                raise ValidationError("The subject model cannot change after a workflow version exists.")
            self.filter(pk=workflow.pk).update(
                name=name, description=description, subject_model=subject_model, updated_at=Now(),
            )
            workflow.refresh_from_db()
            return workflow

    def save_draft(
        self,
        workflow: Any,
        *,
        draft: Any,
        expected_revision: int,
        layout: Any = None,
        actor: Any = None,
    ) -> DraftSave:
        """Save one parsable registered document with optimistic concurrency."""

        _authorize(workflow, actor)
        with system_context(reason="workflows.save_draft"):
            definition, issues = Definition.check(draft, subject_model=workflow.subject_model)
            if definition is None or any(issue.blocks_draft for issue in issues):
                return DraftSave("invalid", expected_revision, issues)
            values = {"draft": draft, "draft_revision": F("draft_revision") + 1, "updated_at": Now()}
            if layout is not None:
                values["layout"] = layout
            changed = self.filter(pk=workflow.pk, draft_revision=expected_revision).update(**values)
            revision = self.values_list("draft_revision", flat=True).get(pk=workflow.pk)
        return DraftSave("saved" if changed else "conflict", revision, issues)

    def publish(self, workflow: Any, *, actor: Any = None) -> Any:
        """Validate and publish the locked draft, reusing an unchanged hash."""

        actor = _authorize(workflow, actor)
        with transaction.atomic(), system_context(reason="workflows.publish"):
            current = self.filter(pk=workflow.pk).lock_if_supported(no_key=True).get()
            definition, issues = Definition.check(current.draft, subject_model=current.subject_model)
            if issues:
                raise DefinitionInvalid(issues)
            assert definition is not None
            document = definition.model_dump(mode="json", by_alias=True)
            digest = canonical_json_sha256(document)
            if current.published_id and current.published.content_hash == digest:
                return current.published
            number = current.versions.aggregate(number=Max("number"))["number"] or 0
            version = current.versions.create(
                number=number + 1,
                document=document,
                content_hash=digest,
                published_by_id=actor_user_id(to_subject_ref(actor)) if actor is not None else None,
            )
            self.filter(pk=current.pk).update(published=version, updated_at=Now())
            return version

    def install_definition(
        self,
        *,
        key: str,
        name: str,
        draft: Any,
        description: str = "",
        subject_model: str = "",
        publish: bool = True,
        layout: Any = None,
        actor: Any = None,
    ) -> Any:
        """Install a resource document through the same draft and publish verbs."""

        with transaction.atomic(), actor_context(actor) if actor is not None else nullcontext():
            workflow = self.save_identity(
                key=key, name=name, description=description, subject_model=subject_model, actor=actor,
            )
            saved = self.save_draft(
                workflow,
                draft=draft,
                expected_revision=workflow.draft_revision,
                layout=layout,
                actor=actor,
            )
            if saved.status != "saved":
                raise DefinitionInvalid(saved.issues)
            if publish:
                self.publish(workflow, actor=actor)
            workflow.refresh_from_db()
            return workflow


class WorkflowRunQuerySet(AngeeQuerySet):
    """Own run locks and the delivery obligation every lock holder acquires."""

    @contextmanager
    def hold(self, run_id: int, *, skip_locked: bool = False) -> Iterator[Any]:
        """Lock one run and dispatch its ready rows after a successful commit.

        Register at context exit so any change publication registered while the
        lock is held runs before a broker send can fail.
        """
        with transaction.atomic():
            with system_context(reason="workflows.hold"):
                run = self.filter(pk=run_id).lock_if_supported(no_key=True, skip_locked=skip_locked).first()
            try:
                yield run
            finally:
                if run is not None:
                    transaction.on_commit(run.step_runs.dispatch)


class WorkflowRunManager(AngeeManager.from_queryset(WorkflowRunQuerySet)):  # type: ignore[misc]
    """Own run admission, graph advancement and cancellation under the run lock."""

    def start(
        self,
        workflow: Any,
        *,
        actor: Any,
        subject: Any = None,
        input: Any = None,
        request_key: str | None = None,
        version: Any = None,
    ) -> Any:
        """Start a pinned version, or replay against the existing run's version."""

        actor = _authorize(workflow, actor, "start")
        if actor is None:
            raise PermissionDenied("A run requires an actor.")
        payload = {} if input is None else input
        with transaction.atomic(), system_context(reason="workflows.start"):
            workflow.refresh_from_db()
            workflow.validate_subject(subject)
            if subject is not None:
                readable = read_scoped_queryset(type(subject), actor)
                if readable is None or not readable.filter(pk=subject.pk).exists():
                    raise PermissionDenied("Read access to the workflow subject is required.")
            subject_type = ContentType.objects.get_for_model(subject) if subject is not None else None
            identity = dict(
                run_as_id=actor_user_id(to_subject_ref(actor)),
                subject_content_type_id=subject_type.pk if subject_type is not None else None,
                subject_object_id=subject.pk if subject is not None else None,
            )

            def replay(run: Any) -> Any:
                if (
                    run.version.workflow_id != workflow.pk
                    or any(getattr(run, key) != value for key, value in identity.items())
                    or run.input != run.version.definition.validate_input(payload)
                ):
                    raise ValidationError("request_key already identifies a different request.")
                return run.with_actor(actor)

            if request_key is not None and (existing := self.filter(request_key=request_key).first()) is not None:
                return replay(existing)
            version = version or workflow.published
            if version is None or version.workflow_id != workflow.pk:
                raise ValidationError("A published version of this workflow is required.")
            normalized = version.definition.validate_input(payload)
            try:
                with transaction.atomic():
                    run = self.create(version=version, input=normalized, request_key=request_key, **identity)
            except IntegrityError:
                existing = self.filter(request_key=request_key).first() if request_key is not None else None
                if existing is None:
                    raise
                return replay(existing)
            with self.hold(run.pk) as locked:
                self.advance(locked)
            return locked.with_actor(actor)

    def advance(
        self,
        run: Any,
        step_run: Any = None,
        settlement: Settlement | None = None,
        *,
        error: str = "",
    ) -> None:
        """Settle, plan and publish once, keeping successful body writes on data errors.

        The caller holds the run lock. Only a lost fence escapes to roll back the
        body. Planning has its own savepoint so a bad plan cannot poison the
        outer transaction that records the durable run failure.
        """
        step_runs = run.step_runs
        with system_context(reason="workflows.advance"):
            if run.is_terminal:
                if settlement is not None:
                    raise Superseded
                return
            output: Any
            try:
                if settlement is not None:
                    with transaction.atomic():
                        step_runs.settle(step_run, settlement)
                with transaction.atomic():
                    rows = list(step_runs.only("node_key", "map_index", "status", "outcome"))
                    definition = run.version.definition
                    if error or definition.unrouted_failure(rows):
                        status, outcome, output = RunStatus.FAILED, ERROR_OUTCOME, {}
                    else:
                        step_runs.bulk_create([
                            step_runs.model(run=run, node_key=node.node_key, status=node.status)
                            for node in definition.ready_nodes(rows)
                        ])
                        statuses = set(step_runs.values_list("status", flat=True))
                        if statuses & {StepRunStatus.READY, StepRunStatus.RUNNING}:
                            status, outcome, output = RunStatus.RUNNING, "", {}
                        elif StepRunStatus.WAITING in statuses:
                            status, outcome, output = RunStatus.WAITING, "", {}
                        else:
                            status = RunStatus.SUCCEEDED
                            outcome, output = definition.result_for(step_runs.all(), run.input) or (
                                DONE_OUTCOME, {},
                            )
                    self._write_state(run, status=status, outcome=outcome, output=output, error=error)
                    if status == RunStatus.FAILED:
                        step_runs.cancel_open()
            except Superseded:
                raise
            except Exception as failure:
                run_error = str(failure)
                timed_out = isinstance(failure, SoftTimeLimitExceeded)
                with _record_failure("attempt close"):
                    if step_run is not None:
                        closed = step_run.attempts.close(
                            AttemptResult.TIMED_OUT if timed_out else AttemptResult.FAILED,
                            str(failure) if timed_out else settlement.error if settlement else "",
                            traceback.format_exc() if timed_out else settlement.stacktrace if settlement else "",
                        )
                        if timed_out and closed:
                            run_error = ""
                with _record_failure("run state"):
                    self._write_state(
                        run, status=RunStatus.FAILED, outcome=ERROR_OUTCOME, output={}, error=run_error,
                    )
                with _record_failure("step cancellation"):
                    step_runs.cancel_open()
            with _record_failure("run change publication"):
                run.refresh_from_db()
                publish_change(run, action="update", update_fields=None)

    def _write_state(self, run: Any, *, status: str, outcome: str, output: Any, error: str = "") -> None:
        self.filter(pk=run.pk).update(
            status=status, outcome=outcome, output=output, error=_error_text(error),
            finished_at=Now() if status in RunStatus.terminal_values() else None, updated_at=Now(),
        )

    def cancel(self, run: Any, *, actor: Any = None) -> None:
        """Cancel an active run after any executing DATABASE step releases its lock."""

        with self.hold(run.pk) as locked:
            if locked is None:
                return
            _authorize(locked, actor)
            with system_context(reason="workflows.cancel"):
                if locked.is_terminal:
                    return
                locked.step_runs.cancel_open()
                self._write_state(locked, status=RunStatus.CANCELED, outcome=CANCELED_OUTCOME, output={})
                locked.refresh_from_db()
                publish_change(locked, action="update", update_fields=None)

    def reprocess(self, run: Any, *, actor: Any = None) -> Any:
        """Reprocess a terminal run on the current publication as its requesting operator."""

        actor = _authorize(run, actor)
        with system_context(reason="workflows.reprocess"):
            run = self.get(pk=run.pk)
            if not run.is_terminal:
                raise ValidationError("Only terminal runs can be reprocessed.")
            workflow, subject = run.version.workflow, run.subject
        return self.start(workflow, actor=actor, subject=subject, input=run.input)


class StepRunQuerySet(AngeeQuerySet):
    """Own conditional step transitions and database-clock candidate scopes."""

    @staticmethod
    def _cleared_wait() -> dict[str, Any]:
        """Clear the companion columns shared by every non-waiting transition."""
        return {"waiting_kind": None, "wake_at": None, "deadline_at": None, "updated_at": Now()}

    def dispatch(self) -> None:
        """Send unlocked ready rows after refreshing their delivery timestamp."""
        with transaction.atomic(), system_context(reason="workflows.dispatch"):
            ready = self.filter(status=StepRunStatus.READY)
            selected = ready.order_by("pk").lock_if_supported(no_key=True, skip_locked=True)
            for pk in list(selected.values_list("pk", flat=True)):
                if ready.filter(pk=pk).update(dispatched_at=Now(), updated_at=Now()):
                    enqueue_task("workflows.execute", kwargs={"step_run_id": pk})

    def count_redispatch(self) -> int:
        """Count a tick redelivery without changing status or publishing a change."""
        return self.filter(status=StepRunStatus.READY).update(dispatches=F("dispatches") + 1, updated_at=Now())

    def claim(self, step_run: Any) -> Any:
        """Claim a ready row, append its attempt and persist input before the body."""
        if not connection.in_atomic_block:
            raise RuntimeError("claim requires the caller's locked transaction.")
        values = dict(
            status=StepRunStatus.RUNNING, attempt=F("attempt") + 1, deadline_at=Now() + Step.timeout,
            waiting_kind=None, wake_at=None, dispatches=0, updated_at=Now(),
        )
        changed = self.filter(pk=step_run.pk, status=StepRunStatus.READY).update(**values)
        if not changed:
            return None
        step_run.refresh_from_db(fields=list(values))
        attempt = step_run.attempts.create(number=step_run.attempt)
        with transaction.atomic():
            step_run.input = step_run.run.version.definition.input_for(
                step_run.node_key, step_run.run.input, step_run.run.step_runs.all(),
            )
            self.filter(pk=step_run.pk).update(input=step_run.input, updated_at=Now())
        return attempt

    def settle(self, step_run: Any, settlement: Settlement) -> None:
        """Write one fenced settlement and close its numbered attempt."""
        fenced = self.filter(pk=step_run.pk, status=StepRunStatus.RUNNING, attempt=step_run.attempt)
        attempt_result = AttemptResult.SUCCEEDED
        if settlement.kind == "wait":
            changed = fenced.to_waiting(until=settlement.until, state=settlement.state)
        else:
            values = self._cleared_wait()
            if settlement.kind == "done":
                values.update(
                    status=StepRunStatus.SUCCEEDED, output=settlement.output, outcome=settlement.outcome, retries=0,
                )
                changed = fenced.update(**values)
            else:
                attempt_result = AttemptResult.TIMED_OUT if settlement.timed_out else AttemptResult.FAILED
                retries = step_run.retries + 1
                if settlement.retryable and retries < step_run.step.retry.max_attempts:
                    changed = fenced.to_waiting(
                        until=Now() + step_run.step.retry.delay_for(retries), state=step_run.state, retries=retries,
                    )
                else:
                    values.update(status=StepRunStatus.FAILED, outcome=ERROR_OUTCOME, output={}, retries=retries)
                    changed = fenced.update(**values)
        if changed != 1:
            raise Superseded
        if step_run.attempts.filter(number=step_run.attempt).close(
            attempt_result, settlement.error, settlement.stacktrace,
        ) != 1:
            raise Superseded

    def to_waiting(self, *, until: Any, state: Any = None, retries: int | None = None) -> int:
        """Park running rows, preserving retries unless a failure consumed one."""
        return self.filter(status=StepRunStatus.RUNNING).update(
            **(self._cleared_wait() | {"waiting_kind": WaitingKind.TIME, "wake_at": until}),
            status=StepRunStatus.WAITING, state={} if state is None else state, outcome="",
            retries=F("retries") if retries is None else retries,
        )

    def to_ready(self) -> int:
        """Wake waiting rows and clear wait/deadline state; delivery is commit-owned."""
        return self.filter(status=StepRunStatus.WAITING).update(
            **self._cleared_wait(), status=StepRunStatus.READY, dispatched_at=Now(),
        )

    def cancel_open(self) -> int:
        """Cancel unsettled rows without disturbing completed branch evidence."""
        return self.exclude(status__in=StepRunStatus.terminal_values()).update(
            **self._cleared_wait(), status=StepRunStatus.CANCELED,
        )

    def due(self) -> Any:
        """Return time waits whose database deadline has arrived."""
        return self.filter(status=StepRunStatus.WAITING, waiting_kind=WaitingKind.TIME, wake_at__lte=Now())

    def undispatched(self) -> Any:
        """Return ready rows whose most recent delivery is old enough to retry."""
        return self.filter(status=StepRunStatus.READY, dispatched_at__lte=Now() - timedelta(seconds=60))


class StepAttemptQuerySet(AngeeQuerySet):
    """Own the one-way closure of an execution attempt."""

    def close(self, result: str, error: str = "", stacktrace: str = "") -> int:
        """Close unfinished attempts once, sanitizing their diagnostic columns."""
        return self.filter(finished_at__isnull=True).update(
            finished_at=Now(), result=result,
            error=_error_text(error), stacktrace=_error_text(stacktrace), updated_at=Now(),
        )


@contextmanager
def _statement_timeout(timeout: timedelta) -> Iterator[None]:
    """Bound each PostgreSQL statement, flooring sub-millisecond limits to 1 ms."""
    previous = None
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            previous = cursor.fetchone()[0]
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                [str(max(1, int(timeout.total_seconds() * 1000)))],
            )
    try:
        yield
    finally:
        if previous is not None:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", [previous])


class StepRunManager(AngeeManager.from_queryset(StepRunQuerySet)):  # type: ignore[misc]
    """Execute steps and recover delivery, composing the transition owners."""

    @cached_property
    def run_model(self) -> Any:
        """Resolve the related model for entrypoints that start from bare row ids."""
        return self.model._meta.get_field("run").related_model

    def execute(self, step_run_id: int) -> bool:
        """Execute one DATABASE attempt with actor-scoped body writes in a savepoint."""
        run_id = system_queryset(self.model).filter(
            pk=step_run_id, status=StepRunStatus.READY,
        ).values_list("run_id", flat=True).first()
        if run_id is None:
            return False
        try:
            with self.run_model.objects.hold(run_id, skip_locked=True) as run:
                if run is None:
                    return False
                with system_context(reason="workflows.execute.claim"):
                    step_run = self.filter(pk=step_run_id).lock_if_supported(no_key=True).get()
                    step_run.run = run
                    if run.is_terminal:
                        self.filter(pk=step_run.pk).cancel_open()
                        return False
                try:
                    with system_context(reason="workflows.claim"):
                        attempt = self.claim(step_run)
                        if attempt is None:
                            return False
                        step = step_run.step
                        actor = run.run_as
                        node = run.version.definition.node(step_run.node_key)
                    ctx = StepContext(
                        run=run, step_run=step_run, step=step, attempt=attempt, actor=actor,
                        input=step.parse_input(step_run.input), config=step.config(node.config), now=attempt.started_at,
                    )
                    with _statement_timeout(step.timeout), transaction.atomic(), actor_context(actor):
                        if is_sudo():
                            raise RuntimeError("The step body must run under its actor, outside system_context.")
                        settlement = step.check(step().run(ctx), config=ctx.config)
                        if settlement.kind == "fail":
                            transaction.set_rollback(True)
                except Superseded:
                    raise
                except Exception as failure:
                    retryable = isinstance(failure, Retryable) or (
                        isinstance(failure, OperationalError)
                        and getattr(failure.__cause__, "sqlstate", None) in {"57014", "40P01", "55P03"}
                    )
                    settlement = Fail(
                        error=str(failure), retryable=retryable, timed_out=isinstance(failure, SoftTimeLimitExceeded),
                        stacktrace=traceback.format_exc(),
                    )
                self.run_model.objects.advance(run, step_run, settlement)
            return True
        except Superseded:
            return False

    def tick(self) -> dict[str, int]:
        """Wake due waits and recover missing deliveries on a bounded batch."""
        return {"woken": self.wake(), "redispatched": self.redispatch()}

    def _each_candidate(self, candidates: Any, action: Callable[[Any, Any], None]) -> int:
        count = 0
        with system_context(reason="workflows.tick"):
            for pk, run_id in list(candidates.order_by("pk").values_list("pk", "run_id")[:100]):
                with _record_failure(f"tick candidate {pk}"):
                    with self.run_model.objects.hold(run_id, skip_locked=True) as run:
                        if run is None or run.is_terminal:
                            continue
                        step_run = candidates.filter(pk=pk).lock_if_supported(no_key=True).first()
                        if step_run is not None:
                            action(run, step_run)
                            count += 1
        return count

    def wake(self) -> int:
        """Make each still-due candidate ready after locking its run, then its row."""
        return self._each_candidate(self.due(), self._wake)

    def _wake(self, run: Any, step_run: Any) -> None:
        run.step_runs.filter(pk=step_run.pk).to_ready()
        type(run).objects.advance(run)

    def redispatch(self) -> int:
        """Bound tick redelivery; exhaustion fails L0 without domain routing."""
        return self._each_candidate(self.undispatched(), self._redispatch)

    def _redispatch(self, run: Any, step_run: Any) -> None:
        run.step_runs.filter(pk=step_run.pk).count_redispatch()
        if step_run.dispatches + 1 >= settings.ANGEE_WORKFLOW_MAX_DISPATCHES:
            type(run).objects.advance(run, error="Task delivery exhausted its retry allowance.")
