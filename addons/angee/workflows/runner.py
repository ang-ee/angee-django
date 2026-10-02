"""Run-locked workflow execution and recovery across queryset transition owners."""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property
from typing import TYPE_CHECKING, Any, cast

from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import OperationalError, connection, transaction
from django.db.models.functions import Now
from django.utils import timezone
from rebac import actor_context, system_context
from rebac.actors import is_sudo

from angee.base.errors import exception_text
from angee.base.refs import canonical_record_target
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.decisions.exceptions import RetryableDecisionError
from angee.graphql.publishing import publish_change
from angee.workflows.managers import RETRYABLE_SQLSTATES, _database_timeout, _record_failure, _sqlstate
from angee.workflows.states import DONE_OUTCOME, ERROR_OUTCOME, AttemptResult, RunStatus, StepRunStatus, WaitingKind
from angee.workflows.steps import Fail, Retryable, StepMode, Superseded, _Settlement, io_timeout_budget

logger = logging.getLogger(__name__)
TICK_CANDIDATE_LIMIT = 1000
"""Each tick action examines at most 1,000 candidates, including busy runs.

One wide map of 1,000 waiting items fits in a sweep. A locked early candidate
cannot hide later items within this bound; each is independently rechecked.
"""

if TYPE_CHECKING:
    from angee.workflows.context import StepContext


@dataclass(frozen=True)
class _AttemptRecord:
    """Runner-owned outcome and diagnostics for one claimed attempt."""

    settlement: _Settlement
    result: AttemptResult = cast(AttemptResult, AttemptResult.SUCCEEDED)
    error: str = ""
    diagnostic_error: str | None = None
    stacktrace: str = ""
    retryable: bool = False
    timed_out: bool = False

    @classmethod
    def from_settlement(cls, settlement: _Settlement) -> _AttemptRecord:
        if isinstance(settlement, Fail):
            return cls(settlement, result=cast(AttemptResult, AttemptResult.FAILED), error=settlement.error)
        return cls(settlement)

    @classmethod
    def failure(
        cls, error: str, *, diagnostic_error: str | None = None, stacktrace: str = "",
        retryable: bool = False, timed_out: bool = False,
    ) -> _AttemptRecord:
        return cls(
            Fail(error=error), result=cast(
                AttemptResult, AttemptResult.TIMED_OUT if timed_out else AttemptResult.FAILED,
            ),
            error=error, diagnostic_error=diagnostic_error, stacktrace=stacktrace,
            retryable=retryable, timed_out=timed_out,
        )


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
        artifacts: list[Any] | None = None,
    ) -> None:
        """Settle, plan and publish once, keeping successful body writes on data errors.

        The caller holds the run lock. A lost fence or transient database error
        escapes to roll back the transaction. Planning has its own savepoint so
        a bad plan cannot poison the transaction recording the durable failure.
        """
        step_runs = run.step_runs
        with system_context(reason="workflows.advance"):
            if run.is_terminal and settlement is None:
                return
            output: Any
            try:
                if settlement is not None:
                    with transaction.atomic():
                        step_runs.settle(step_run, settlement)
                        if not isinstance(settlement.settlement, Fail) and artifacts:
                            step_run.artifacts.bulk_create(artifacts)
                # A preserved IO sibling may settle after failure. Its evidence
                # changes, but the terminal run and graph plan stay untouched.
                if not run.is_terminal:
                    with transaction.atomic():
                        # The reverse manager needs its connecting FK to populate
                        # Django's known-related-object cache without one query per row.
                        rows = list(step_runs.only(
                            "run_id", "node_key", "map_index", "status", "outcome", "waiting_kind", "input",
                        ))
                        definition = run.version.definition
                        if definition.unrouted_failure(rows):
                            status, outcome, output = RunStatus.FAILED, ERROR_OUTCOME, {}
                        else:
                            planned = definition.ready_nodes(
                                rows, map_concurrency=settings.ANGEE_WORKFLOW_MAP_CONCURRENCY,
                            )
                            wake = [node.node_key for node in planned if node.existing]
                            step_runs.filter(node_key__in=wake).to_ready()
                            step_runs.bulk_create([
                                step_runs.model(
                                    run=run, node_key=node.node_key, map_index=node.map_index,
                                    status=node.status, rank=node.rank,
                                ) for node in planned if not node.existing
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
                        self.run_model.objects._write_state(run, status=status, outcome=outcome, output=output)
            except Superseded:
                raise
            except Exception as failure:
                if isinstance(failure, OperationalError) and (
                    _sqlstate(failure) in RETRYABLE_SQLSTATES
                    or step_run is not None and step_run.step.mode == StepMode.IO
                ):
                    raise
                logger.exception("Workflow run %s failed during advancement.", run.pk)
                run_error = exception_text(failure)
                attempt_error = exception_text(failure, diagnostic=True)
                timed_out = isinstance(failure, SoftTimeLimitExceeded)
                recorded_on_attempt = False
                if step_run is not None:
                    with _record_failure("settlement failure"):
                        # A planning error follows a completed settlement and
                        # cannot replace it. A failed settlement still owns its fence.
                        if step_runs.filter(pk=step_run.pk, status=StepRunStatus.RUNNING).exists():
                            step_runs.settle(step_run, _AttemptRecord.failure(
                                run_error, diagnostic_error=attempt_error,
                                stacktrace=traceback.format_exc(), timed_out=timed_out,
                            ))
                            recorded_on_attempt = True
                with _record_failure("attempt close"):
                    if step_run is not None:
                        closed = step_run.attempts.close(
                            AttemptResult.TIMED_OUT if timed_out else AttemptResult.FAILED,
                            attempt_error if timed_out else settlement.error if settlement else "",
                            traceback.format_exc() if timed_out else settlement.stacktrace if settlement else "",
                        )
                        if recorded_on_attempt or timed_out and closed:
                            run_error = ""
                if not run.is_terminal:
                    with _record_failure("run state"):
                        self.run_model.objects._write_state(
                            run, status=RunStatus.FAILED, outcome=ERROR_OUTCOME, output={}, error=run_error,
                        )
            with _record_failure("run change publication"):
                run.refresh_from_db()
                publish_change(run, action="update", update_fields=None)

    def execute(self, step_run_id: int) -> bool:
        """Claim once; DATABASE bodies share the claim transaction, IO bodies do not."""
        from angee.workflows.context import StepContext

        run_id = system_queryset(self.step_model).filter(
            pk=step_run_id, status=StepRunStatus.READY,
        ).values_list("run_id", flat=True).first()
        if run_id is None:
            return False
        try:
            with self.run_model.objects.hold(run_id, skip_locked=True) as run:
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
                if settlement is not None:
                    self.advance(
                        run, step_run, settlement, artifacts=ctx.pending_artifacts if ctx is not None else None,
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
            retryable=isinstance(failure, (Retryable, RetryableDecisionError)) or (
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
            ), (transaction.atomic() if database else nullcontext()), actor_context(ctx.actor):
                if is_sudo():
                    raise RuntimeError("The step body must run under its actor, outside system_context.")
                settlement = ctx.step.check(ctx.step().run(ctx), config=ctx.config)
                settlement = settlement.admit(ctx)
                if database and isinstance(settlement, Fail):
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
                        current.run, current, settlement, artifacts=ctx.pending_artifacts,
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
                step_run.step.timeout, until=attempt.started_at + io_timeout_budget(),
            ) != 1:
                raise Superseded
            step_run.refresh_from_db(fields=["deadline_at"])

    def raise_if_canceled(self, step_run: Any) -> None:
        """Cooperatively stop an attempt after cancellation or supersession."""
        with self._fenced(step_run):
            pass

    def artifact(self, step_run: Any, record: Any, *, label: str, actor: Any) -> Any:
        """Stage an actor-readable canonical reference while the attempt is live."""
        readable = read_scoped_queryset(type(record), actor)
        if not readable.filter(pk=record.pk).exists():
            raise PermissionDenied("Read access to the artifact record is required.")
        target = canonical_record_target(record)
        with self._fenced(step_run) as current:
            return current.artifacts.model(
                step_run=current, content_type=target.content_type, object_id=target.object_id, label=label,
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
        return self._each_candidate(candidates, self._wake)

    def wake_decisions(self, group_id: Any = None) -> int:
        """Signal and sweep share the run-lock owner and commit-time dispatch."""
        candidates = self.step_model.objects.settled_decisions()
        if group_id is not None:
            candidates = candidates.filter(decision_group_id=group_id)
        return self._each_candidate(candidates, self._wake)

    def wake_records(self, *, content_type_id: int | None = None, object_id: Any = None) -> int:
        """Share after-commit delivery and pending-watch tick recovery with every wake kind."""
        candidates = self.step_model.objects.changed_records(content_type_id=content_type_id, object_id=object_id)
        return self._each_candidate(candidates, self._wake)

    def _each_candidate(self, candidates: Any, action: Callable[[Any, Any], None]) -> int:
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
                                action(run, step_run)
                                count += 1
                except Superseded:
                    continue
        return count

    def wake(self) -> int:
        """Make each still-due candidate ready after locking its run, then its row."""
        return self._each_candidate(self.step_model.objects.due(), self._wake)

    def _wake(self, run: Any, step_run: Any) -> None:
        run.step_runs.filter(pk=step_run.pk).to_ready()
        step_run.watches.all().delete()
        self.advance(run)

    def redispatch(self) -> int:
        """Bound tick redelivery; exhaustion waits for an operator without routing."""
        return self._each_candidate(self.step_model.objects.undispatched(), self._redispatch)

    def _redispatch(self, run: Any, step_run: Any) -> None:
        run.step_runs.filter(pk=step_run.pk).count_redispatch()
        if step_run.dispatches + 1 >= settings.ANGEE_WORKFLOW_MAX_DISPATCHES:
            run.step_runs.filter(pk=step_run.pk).to_waiting(
                kind=WaitingKind.OPERATOR, reason="Task delivery exhausted its retry allowance.",
            )
            self.advance(run)

    def reap(self) -> int:
        """Recover expired committed claims while holding the effect marker's lock."""
        return self._each_candidate(self.step_model.objects.expired(), self._reap)

    def _reap(self, run: Any, step_run: Any) -> None:
        step_run.run = run
        try:
            with transaction.atomic():
                run.step_runs.expire(step_run, _AttemptRecord.failure(
                    "The attempt deadline expired.", retryable=True, timed_out=True,
                ))
        except ImproperlyConfigured as failure:
            step_run.attempts.filter(number=step_run.attempt).close(AttemptResult.TIMED_OUT, exception_text(failure))
            run.step_runs.filter(pk=step_run.pk).to_waiting(
                kind=WaitingKind.OPERATOR,
                reason="The step implementation is unavailable; restore its registration before retrying.",
            )
        self.advance(run)


runner = Runner()
