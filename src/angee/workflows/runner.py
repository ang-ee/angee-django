"""Run-locked workflow execution and recovery across queryset transition owners."""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from functools import cached_property
from typing import TYPE_CHECKING, Any

from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, connection, transaction
from django.db.models.functions import Now
from django.utils import timezone
from rebac import actor_context, generic_target, system_context
from rebac.actors import is_sudo

from angee.base.exceptions import exception_text
from angee.base.signals import publish_change
from angee.jobs.timeouts import task_time_budget
from angee.provenance.context import changeset
from angee.workflows.managers import RETRYABLE_SQLSTATES, _database_timeout, _record_failure, _sqlstate
from angee.workflows.states import (
    RecordOperation,
    RunStatus,
    StepRunStatus,
)
from angee.workflows.steps import Fail, Retryable, StepMode, Superseded, _AttemptRecord

logger = logging.getLogger(__name__)
TICK_CANDIDATE_LIMIT = 1000
"""Each tick action examines at most 1,000 candidates, including busy runs.

One wide map of 1,000 waiting items fits in a sweep. A locked early candidate
cannot hide later items within this bound; each is independently rechecked.
"""

if TYPE_CHECKING:
    from angee.workflows.context import StepContext


class Runner:
    """Compose run locks, actor-scoped bodies and conditional row transitions."""

    @cached_property
    def run_model(self) -> Any:
        return apps.get_model("workflows", "WorkflowRun")

    @cached_property
    def step_model(self) -> Any:
        return apps.get_model("workflows", "StepRun")

    def advance(
        self,
        run: Any,
        step_run: Any = None,
        settlement: _AttemptRecord | None = None,
        *,
        records: list[Any] | None = None,
        notes: list[dict[str, str]] | None = None,
    ) -> None:
        """Advance a held run, classifying a failure for the run to record durably.

        The caller holds the run lock. A lost fence, a retryable database error, or
        a database error settling an IO attempt escapes to roll back the caller's
        transaction; every other failure becomes the run's, its body writes kept.
        """
        try:
            run.advance(step_run, settlement, records=records, notes=notes)
        except Superseded:
            raise
        except Exception as failure:
            if isinstance(failure, OperationalError) and (
                _sqlstate(failure) in RETRYABLE_SQLSTATES
                or step_run is not None and step_run.step.mode == StepMode.IO
            ):
                raise
            logger.exception("Workflow run %s failed during advancement.", run.pk)
            run.fail_advance(step_run, settlement, _AttemptRecord.failure(
                exception_text(failure), diagnostic_error=exception_text(failure, diagnostic=True),
                stacktrace=traceback.format_exc(), timed_out=isinstance(failure, SoftTimeLimitExceeded),
            ))

    def execute(self, step_run_id: int) -> bool:
        """Claim once; DATABASE bodies share the claim transaction, IO bodies do not."""
        from angee.workflows.context import StepContext

        run_id = self.step_model.system_queryset().filter(
            pk=step_run_id, status=StepRunStatus.READY,
        ).values_list("run_id", flat=True).first()
        if run_id is None:
            return False
        try:
            with self.run_model.objects.select_related("version", "run_as").hold(run_id, skip_locked=True) as run:
                if run is None:
                    return False
                with system_context(reason="workflows.execute.claim"):
                    step_run = self.step_model.objects.filter(pk=step_run_id).lock_if_supported(no_key=True).get()
                    step_run.run = run
                    if run.is_terminal:
                        return False
                ctx = None
                try:
                    with system_context(reason="workflows.claim"):
                        attempt = self.step_model.objects.claim(step_run)
                        if attempt is None:
                            return False
                        step = step_run.step
                        actor = run.run_as
                        node = run.version.definition.node(step_run.node_key)
                    ctx = StepContext(
                        run=run, step_run=step_run, step=step, attempt=attempt, actor=actor,
                        input=step.parse_input(step_run.input), config=step.parse_config(node.config),
                        now=attempt.started_at,
                    )
                    if step.mode == StepMode.DATABASE:
                        settlement = self._run_body(ctx)
                    else:
                        # The run lock owns this claim transaction. Leave it before
                        # any IO body executes.
                        settlement = None
                except Superseded:
                    raise
                except Exception as failure:
                    settlement = self._failure(failure)
                if settlement is None:
                    with _record_failure("run change publication"):
                        publish_change(run, action="update", update_fields=None)
                if settlement is not None:
                    self.advance(
                        run, step_run, settlement, records=ctx.pending_records if ctx is not None else None,
                        notes=ctx.pending_notes if ctx is not None else None,
                    )
            if settlement is None:
                assert ctx is not None
                if connection.in_atomic_block:
                    raise RuntimeError("An IO step cannot execute inside an enclosing transaction.")
                return self._settle_io(ctx, self._run_body(ctx))
            return True
        except Superseded:
            return False

    @staticmethod
    def _failure(failure: Exception) -> _AttemptRecord:
        return _AttemptRecord.failure(
            exception_text(failure), timed_out=isinstance(failure, SoftTimeLimitExceeded),
            retryable=isinstance(failure, (Retryable,)) or (
                isinstance(failure, OperationalError)
                and _sqlstate(failure) in RETRYABLE_SQLSTATES
            ),
            stacktrace=traceback.format_exc(),
        )

    def _run_body(self, ctx: StepContext) -> _AttemptRecord:
        """Validate once with the body, rolling back DATABASE writes on failure."""
        database = ctx.step.mode == StepMode.DATABASE
        try:
            with (
                _database_timeout(ctx.step.timeout) if database else nullcontext()
            ), (transaction.atomic() if database else nullcontext()), actor_context(ctx.actor), changeset(
                fresh=True, activity="run", origin=ctx.run,
            ) as failed:
                if is_sudo():
                    raise RuntimeError("The step body must run under its actor, outside system_context.")
                settlement = ctx.step.check(ctx.step().run(ctx), config=ctx.config)
                settlement = settlement.admit(ctx)
                if isinstance(settlement, Fail):
                    failed()
                    if database:
                        transaction.set_rollback(True)
                return _AttemptRecord.from_settlement(settlement)
        except Superseded:
            raise
        except Exception as failure:
            return self._failure(failure)

    def _settle_io(self, ctx: StepContext, settlement: _AttemptRecord) -> bool:
        """Retry a fenced result transaction until its claim's current deadline."""
        while timezone.now() < ctx.step_run.deadline_at:
            try:
                with self._fenced(ctx.step_run) as current:
                    self.advance(
                        current.run, current, settlement, records=ctx.pending_records, notes=ctx.pending_notes,
                    )
                    return True
            except Superseded:
                break
            except OperationalError:
                time.sleep(min(0.05, max(0, (ctx.step_run.deadline_at - timezone.now()).total_seconds())))
            except SoftTimeLimitExceeded as failure:
                settlement = self._failure(failure)
        logger.warning("Workflow step %s attempt %s lost its IO settlement fence.", ctx.step_run.pk, ctx.attempt.number)
        return False

    @contextmanager
    def _fenced(self, step_run: Any) -> Iterator[Any]:
        """Wait for run then step locks within the live claim's remaining time."""
        remaining = step_run.deadline_at - timezone.now()
        if remaining <= timedelta():
            raise Superseded
        with self.run_model.objects.hold(step_run.run_id, timeout=remaining) as run:
            with system_context(reason="workflows.attempt"), (
                _database_timeout(step_run.deadline_at - timezone.now())
            ), transaction.atomic():
                current = self.step_model.objects.fenced(step_run).lock_if_supported(no_key=True).first()
                if run is None or current is None:
                    raise Superseded
                current.run = run
                yield current

    def begin_effect(self, step_run: Any) -> None:
        """Mark the first possible effect under the same row lock used by the reaper."""
        with self._fenced(step_run) as current:
            current.attempts.filter(number=step_run.attempt, effect_started_at__isnull=True).update(
                effect_started_at=Now(), updated_at=Now(),
            )

    def heartbeat(self, step_run: Any) -> None:
        """Extend a live claim from database time, retaining the context's deadline."""
        with self._fenced(step_run) as current:
            attempt = current.attempts.get(number=step_run.attempt)
            if self.step_model.objects.fenced(step_run).extend_deadline(
                step_run.step.timeout, until=attempt.started_at + task_time_budget(),
            ) != 1:
                raise Superseded
            step_run.refresh_from_db(fields=["deadline_at"])

    def raise_if_canceled(self, step_run: Any) -> None:
        """Cooperatively stop an attempt after cancellation or supersession."""
        with self._fenced(step_run):
            pass

    def record(self, step_run: Any, record: Any, *, label: str, operation: str, actor: Any) -> Any:
        """Stage an actor-readable canonical reference while the attempt is live."""
        readable = type(record).objects.with_actor(actor)
        if not readable.filter(pk=record.pk).exists():
            raise PermissionDenied("Read access to the step record is required.")
        target = generic_target(record)
        if operation not in RecordOperation.values:
            raise ValidationError("Unknown step record operation.")
        with self._fenced(step_run) as current:
            return current.records.model(
                run=current.run, step_run=current, content_type=target.content_type,
                object_id=target.object_id, label=label, operation=operation,
            )

    def tick(self) -> dict[str, int]:
        """Wake waits, reap expired claims and recover missing deliveries in bounded batches."""
        return {"woken": self.wake(), "reaped": self.reap(), "redispatched": self.redispatch(),
                "decisions": self.wake_decisions(), "runs": self.wake_runs(), "records": self.wake_records(),
                "drained": apps.get_model("workflows", "Trigger").objects.drain(),
                "pruned": self.run_model.objects.prune()}

    def wake_runs(self, run_id: Any = None) -> int:
        """After-commit delivery and tick recovery share the existing wake transition."""
        candidates = self.step_model.objects.terminal_runs()
        if run_id is not None:
            candidates = candidates.filter(awaited_run_id=run_id)
        return self._each_candidate(candidates, self.step_model.wake)

    def wake_decisions(self, decision_id: Any = None) -> int:
        """Worker delivery and the tick sweep share the run-lock owner."""
        candidates = self.step_model.objects.answered_decisions()
        if decision_id is not None:
            candidates = candidates.filter(decision_id=decision_id)
        return self._each_candidate(candidates, self.step_model.wake)

    def wake_records(self, *, content_type_id: int | None = None, object_id: Any = None) -> int:
        """Share after-commit delivery and pending-watch tick recovery with every wake kind."""
        candidates = self.step_model.objects.changed_records(content_type_id=content_type_id, object_id=object_id)
        return self._each_candidate(candidates, self.step_model.wake)

    def _each_candidate(self, candidates: Any, transition: Callable[[Any], bool]) -> int:
        """Hold each bounded candidate's run, then lock its row and apply the row's transition.

        The run is advanced when the transition reports that it changed the plan.
        """
        count = 0
        with system_context(reason="workflows.tick"):
            candidates = candidates.exclude(run__status__in=RunStatus.terminal_values())
            for pk, run_id in list(candidates.order_by("pk").values_list("pk", "run_id")[:TICK_CANDIDATE_LIMIT]):
                try:
                    with _record_failure(f"tick candidate {pk}"):
                        with self.run_model.objects.hold(run_id, skip_locked=True) as run:
                            if run is None or run.is_terminal:
                                continue
                            step_run = candidates.filter(pk=pk).lock_if_supported(no_key=True).first()
                            if step_run is not None:
                                step_run.run = run
                                if transition(step_run):
                                    self.advance(run)
                                count += 1
                except Superseded:
                    continue
        return count

    def wake(self) -> int:
        """Make each still-due candidate ready after locking its run, then its row."""
        return self._each_candidate(self.step_model.objects.due(), self.step_model.wake)

    def redispatch(self) -> int:
        """Bound tick redelivery; exhaustion waits for an operator without routing."""
        return self._each_candidate(self.step_model.objects.undispatched(), self.step_model.redispatch)

    def reap(self) -> int:
        """Recover expired committed claims while holding the effect marker's lock."""
        return self._each_candidate(self.step_model.objects.expired(), self.step_model.reap)


runner = Runner()
