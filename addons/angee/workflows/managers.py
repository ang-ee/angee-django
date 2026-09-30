"""Workflow admission, publication, fenced execution and operator recovery."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property
from typing import Any, Literal, cast
from uuid import uuid4

from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import Exists, F, Max, OuterRef, Q, Subquery, Value
from django.db.models.deletion import ProtectedError, RestrictedError
from django.db.models.functions import Concat, Least, Now
from django.utils import timezone
from rebac import actor_context, system_context, to_subject_ref
from rebac.actors import is_sudo

from angee.base.actors import actor_user_id
from angee.base.exceptions import exception_text
from angee.base.identity import instance_from_public_id
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.refs import canonical_record_target
from angee.base.scoping import lock_if_supported, read_scoped_queryset, system_queryset
from angee.base.serialization import canonical_json_sha256, strip_null_bytes
from angee.decisions.exceptions import RetryableDecisionError
from angee.graphql.publishing import publish_change
from angee.jobs.enqueue import enqueue_task
from angee.workflows.context import StepContext
from angee.workflows.definition import Definition, DefinitionInvalid, Issue
from angee.workflows.reviews import ReviewStep
from angee.workflows.states import (
    CANCELED_OUTCOME,
    DONE_OUTCOME,
    ERROR_OUTCOME,
    AttemptResult,
    RunRelation,
    RunStatus,
    StepRunStatus,
    WaitingKind,
)
from angee.workflows.steps import Fail, Retryable, Settlement, Superseded, io_timeout_budget
from angee.workflows.triggers import TriggerSource

logger = logging.getLogger(__name__)
RETRYABLE_SQLSTATES = frozenset({"57014", "40P01", "55P03"})
TICK_CANDIDATE_LIMIT = 1000
"""Each tick action examines at most 1,000 candidates, including busy runs.

One wide map of 1,000 waiting items fits in a sweep. A locked early candidate
cannot hide later items within this bound; each is independently rechecked.
"""
PRUNE_BATCH_LIMIT = 25
"""Prune examines at most 25 roots per tick; blocked roots retry after one day."""


def _error_text(value: str, field: Any) -> str:
    """Sanitize diagnostic text to the owning model field's declared bound."""
    return cast(str, strip_null_bytes(value))[:field.max_length]


def _sqlstate(error: Exception) -> str | None:
    """Read the native database driver's SQLSTATE at one boundary."""
    return getattr(error.__cause__, "sqlstate", None)


@contextmanager
def _record_failure(operation: str) -> Iterator[None]:
    """Isolate diagnostic writes; a lost claim still aborts its execution transaction."""
    try:
        with transaction.atomic():
            yield
    except Superseded:
        raise
    except Exception:
        logger.exception("Workflow %s failed.", operation)


@dataclass(frozen=True)
class DraftSave:
    """A conditional draft save and its complete validation diagnostics."""

    status: Literal["saved", "conflict", "invalid"]
    revision: int
    issues: list[Issue]


@dataclass(frozen=True)
class Cancellation:
    """The facts observed and changed under the cancellation lock."""

    canceled: bool
    steps: int
    reviews: int = 0
    children: int = 0

    @property
    def message(self) -> str:
        """Describe the actual transition, including terminal-run cleanup."""
        changes = []
        if self.steps:
            noun = "step" if self.steps == 1 else "steps"
            changes.append(f"{self.steps} open {noun} canceled")
        if self.reviews:
            noun = "review" if self.reviews == 1 else "reviews"
            changes.append(f"{self.reviews} pending {noun} closed")
        if self.children:
            noun = "run" if self.children == 1 else "runs"
            changes.append(f"{self.children} child {noun} canceled")
        if not self.canceled and not changes:
            return "Nothing to cancel."
        return "; ".join(["Run canceled" if self.canceled else "Run already finished", *changes]) + "."


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
        with transaction.atomic():
            with system_context(reason="workflows.save_identity locate target"):
                workflow = self.filter(key=key).first()
            actor = (workflow or self.model()).require_access("write" if workflow else "create", actor)
            with actor_context(actor) if actor is not None else nullcontext():
                workflow, _ = self.get_or_create(
                    key=key, defaults={"name": name, "description": description, "subject_model": subject_model},
                )
                workflow.require_access("write", actor)
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

        workflow.require_access("write", actor)
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

        actor = workflow.require_access("write", actor)
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

    def for_subject(self, record: Any) -> Any:
        """Select the canonical subject without changing this queryset's read scope."""
        target = canonical_record_target(record)
        return self.filter(subject_content_type=target.content_type, subject_object_id=target.object_id)

    @contextmanager
    def hold(self, run_id: int, *, skip_locked: bool = False, timeout: timedelta | None = None) -> Iterator[Any]:
        """Lock one run and dispatch its ready rows after a successful commit.

        Register at context exit. Robust callbacks log delivery failures without
        suppressing later publications from enclosing parent/child transactions;
        the tick recovers the durable ready rows.
        """
        with transaction.atomic():
            with system_context(reason="workflows.hold"), (
                _database_timeout(timeout, setting="lock_timeout") if timeout is not None else nullcontext()
            ), (transaction.atomic() if timeout is not None else nullcontext()):
                run = self.filter(pk=run_id).lock_if_supported(no_key=True, skip_locked=skip_locked).first()
            try:
                yield run
            finally:
                if run is not None:
                    transaction.on_commit(run.step_runs.dispatch, robust=True)

    @contextmanager
    def hold_owned(self, run_id: int, *, skip_locked: bool = False, timeout: timedelta | None = None) -> Iterator[Any]:
        """Hold an owned tree ancestor first, admitting no new child behind a held parent."""
        with ExitStack() as stack, system_context(reason="workflows.hold_owned"):
            root = stack.enter_context(self.hold(run_id, skip_locked=skip_locked, timeout=timeout))
            runs = [] if root is None else [root]
            for run in runs:
                children = list(self.filter(parent_step__run=run, relation=RunRelation.OWNED)
                                .order_by("pk").values_list("pk", flat=True))
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
        reprocess_of: Any = None,
        parent_step: Any = None,
        relation: str | None = None,
    ) -> Any:
        """Start a pinned version, or replay against the existing run's version."""

        actor = workflow.require_access("start", actor)
        if actor is None:
            raise PermissionDenied("A run requires an actor.")
        if (parent_step is None and relation is not None) or (
            parent_step is not None and relation not in RunRelation.values
        ):
            raise ValidationError("A child requires an owned or continuation relation and a parent step.")
        payload = {} if input is None else input
        with transaction.atomic(), (
            self.hold(parent_step.run_id) if parent_step is not None else nullcontext(None)
        ) as parent, system_context(reason="workflows.start"):
            if parent_step is not None:
                if parent is None:
                    raise ValidationError("The parent run no longer exists.")
                parent.require_access("write", actor)
                if request_key is None:
                    request_key = f"child:{parent_step.sqid}"
            workflow.refresh_from_db()
            workflow.validate_subject(subject)
            if subject is not None:
                readable = read_scoped_queryset(type(subject), actor)
                if readable is None or not readable.filter(pk=subject.pk).exists():
                    raise PermissionDenied("Read access to the workflow subject is required.")
            target = canonical_record_target(subject) if subject is not None else None
            identity = dict(
                run_as_id=actor_user_id(to_subject_ref(actor)),
                subject_content_type_id=target.content_type.pk if target is not None else None,
                subject_object_id=target.object_id if target is not None else None,
                parent_step_id=parent_step.pk if parent_step is not None else None,
                relation=relation,
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
            if parent is not None and (
                parent.is_terminal or not parent.step_runs.fenced(parent_step).lock_if_supported(no_key=True).exists()
            ):
                raise Superseded("The parent attempt cannot admit a child.")
            version = version or workflow.published
            if version is None or version.workflow_id != workflow.pk:
                raise ValidationError("A published version of this workflow is required.")
            normalized = version.definition.validate_input(payload)
            try:
                with transaction.atomic():
                    run = self.create(
                        version=version, input=normalized, request_key=request_key,
                        reprocess_of=reprocess_of, **identity,
                    )
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
                        if settlement.kind != "fail" and artifacts:
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
                        self._write_state(run, status=status, outcome=outcome, output=output)
            except Superseded:
                raise
            except Exception as failure:
                if isinstance(failure, OperationalError) and (
                    _sqlstate(failure) in RETRYABLE_SQLSTATES
                    or step_run is not None and step_run.step.mode == "IO"
                ):
                    raise
                run_error = exception_text(failure)
                timed_out = isinstance(failure, SoftTimeLimitExceeded)
                if step_run is not None:
                    with _record_failure("settlement failure"):
                        # A planning error follows a completed settlement and
                        # cannot replace it. A failed settlement still owns its fence.
                        if step_runs.filter(pk=step_run.pk, status=StepRunStatus.RUNNING).exists():
                            step_runs.settle(step_run, Fail(
                                error=run_error, stacktrace=traceback.format_exc(), timed_out=timed_out,
                            ))
                with _record_failure("attempt close"):
                    if step_run is not None:
                        closed = step_run.attempts.close(
                            AttemptResult.TIMED_OUT if timed_out else AttemptResult.FAILED,
                            run_error if timed_out else settlement.error if settlement else "",
                            traceback.format_exc() if timed_out else settlement.stacktrace if settlement else "",
                        )
                        if timed_out and closed:
                            run_error = ""
                if not run.is_terminal:
                    with _record_failure("run state"):
                        self._write_state(
                            run, status=RunStatus.FAILED, outcome=ERROR_OUTCOME, output={}, error=run_error,
                        )
            with _record_failure("run change publication"):
                run.refresh_from_db()
                publish_change(run, action="update", update_fields=None)

    def _write_state(self, run: Any, *, status: str, outcome: str, output: Any, error: str = "") -> None:
        self.filter(pk=run.pk).update(
            status=status, outcome=outcome, output=strip_null_bytes(output),
            error=_error_text(error, self.model._meta.get_field("error")),
            finished_at=Now() if status in RunStatus.terminal_values() else None, updated_at=Now(),
        )
        if status in RunStatus.terminal_values() and not run.is_terminal:
            if status != RunStatus.CANCELED:
                awaited = run.step_runs.exclude(awaited_run_id=None).values("awaited_run_id")
                abandoned = self.filter(parent_step__run=run, relation=RunRelation.OWNED).exclude(pk__in=awaited)
                for child_id in abandoned.order_by("pk").values_list("pk", flat=True):
                    with self.hold_owned(child_id) as children:
                        self._cancel_locked(children)
            enqueue_task("workflows.wake_run", kwargs={"run_id": run.pk}, robust=True)

    def _cancel_locked(self, runs: list[Any]) -> Cancellation:
        """Cancel open rows in an already-held tree without rewriting terminal facts."""
        steps = reviews = children = 0
        canceled = bool(runs and not runs[0].is_terminal)
        for index, run in enumerate(runs):
            changed, closed = run.step_runs.cancel_open()
            steps, reviews = steps + changed, reviews + closed
            if not run.is_terminal:
                children += bool(index)
                self._write_state(run, status=RunStatus.CANCELED, outcome=CANCELED_OUTCOME, output={})
                changed = 1
            if changed or closed:
                run.refresh_from_db()
                publish_change(run, action="update", update_fields=None)
        return Cancellation(canceled, steps, reviews, children)

    def cancel_on_commit(self, run: Any, actor: Any) -> None:
        """Deliver an authorized cancellation after this transaction releases its locks.

        Rollback discards the request. The task calls ``cancel`` with the same
        actor, rechecking access and terminal state under the existing owner.
        Robust delivery failures are logged without interrupting other callbacks.
        """
        actor = run.require_access("write", actor)
        if actor is None:
            raise PermissionDenied("Deferred cancellation requires an actor.")
        payload = {"run_id": run.pk, "actor": str(to_subject_ref(actor))}
        enqueue_task("workflows.cancel", kwargs=payload, robust=True)

    def cancel(self, run: Any, *, actor: Any = None, timeout: timedelta | None = timedelta(seconds=5)) -> Cancellation:
        """Cancel open rows after DATABASE work releases its lock, retaining terminal run facts."""

        if not system_queryset(self.model).filter(pk=run.pk).exists():
            return Cancellation(False, 0)
        run.require_access("write", actor)
        try:
            with self.hold_owned(run.pk, timeout=timeout) as runs:
                return self._cancel_locked(runs)
        except OperationalError as error:
            if _sqlstate(error) == "55P03":
                raise ValidationError("The run is still running; retry cancellation.") from error
            raise

    def reprocess(self, run: Any, *, actor: Any = None) -> Any:
        """Reprocess a terminal run on the current publication as its requesting operator."""

        actor = run.require_access("write", actor)
        with self.hold(run.pk) as retained:
            with system_context(reason="workflows.reprocess"):
                if not retained.is_terminal:
                    raise ValidationError("Only terminal runs can be reprocessed.")
                workflow = retained.version.workflow
                model = retained.subject_model_class
                subject = system_queryset(model).get(pk=retained.subject_object_id) if model is not None else None
            return self.start(workflow, actor=actor, subject=subject, input=retained.input, reprocess_of=retained)

    def reopen(self, run: Any) -> None:
        """Clear a locked run's terminal facts and replan retained step rows."""
        self._write_state(run, status=RunStatus.RUNNING, outcome="", output={})
        run.refresh_from_db()
        self.advance(run)

    def prune(self) -> int:
        """Delete bounded old owned trees, marking protected roots for a later retry."""
        count = 0
        with system_context(reason="workflows.retention"):
            candidates = list(self.retention_candidates().order_by("finished_at", "pk")
                              .values_list("pk", flat=True)[:PRUNE_BATCH_LIMIT])
            for run_id in candidates:
                with _record_failure(f"prune candidate {run_id}"), self.hold_owned(run_id, skip_locked=True) as runs:
                    if not runs or not self.retention_candidates().filter(pk=run_id).exists():
                        continue
                    try:
                        with transaction.atomic(), ExitStack() as continuation_locks:
                            ids = [run.pk for run in runs]
                            continuation_ids = self.filter(
                                parent_step__run_id__in=ids, relation=RunRelation.CONTINUATION,
                            ).order_by("pk").values_list("pk", flat=True)
                            continuations = [continuation_locks.enter_context(self.hold(pk, skip_locked=True))
                                             for pk in continuation_ids]
                            if any(run is None or not run.is_terminal for run in [*runs, *continuations]):
                                raise ProtectedError("A descendant is still running.", runs)
                            steps = runs[0].step_runs.model.objects.filter(run_id__in=ids)
                            groups: dict[int, Any] = {}
                            for step in steps.exclude(decision_group_id=None).select_related("decision_group"):
                                for group in step.decision_group.rounds():
                                    groups.setdefault(group.pk, group)
                            # Internal wait references must not protect rows in the same deleted tree.
                            steps.update(awaited_run=None)
                            for run in reversed(runs):
                                self.filter(pk=run.pk).delete()
                            for group in groups.values():
                                if not group.is_deletable:
                                    raise ProtectedError("Decision evidence must remain retained.", [group])
                                group.delete()
                        count += len(runs)
                    except (ProtectedError, RestrictedError):
                        self.filter(pk=run_id).update(
                            prune_after=Now() + timedelta(days=1),
                            prune_reason="Active descendants or protected execution/decision evidence.",
                            updated_at=Now(),
                        )
        return count


class StepWatchManager(AngeeManager):
    """Own transactional observation, durable change capture and wait admission."""

    def register(self, step_run: Any, records: tuple[Any, ...], *, actor: Any) -> None:
        """Observe saves after ordered target locks; precondition reads must also lock.

        Called inside a DATABASE body savepoint. Registration is immediately
        visible to that body's later saves and rolls back with a failed body.
        Callers needing an atomic predicate use ctx.load(..., lock=True) first.
        """
        targets = {}
        for record in records:
            TriggerSource.check_watch_model(type(record))
            if record.pk is None:
                raise PermissionDenied("Read access to a saved watched record is required.")
            target = canonical_record_target(record)
            targets[(target.content_type.pk, target.object_id)] = target
        with type(step_run).objects._fenced(step_run):
            for _, target in sorted(targets.items()):
                rows = system_queryset(target.content_type.model_class()).filter(pk=target.object_id)
                lock_if_supported(rows, no_key=True).get()
            for record in records:
                readable = read_scoped_queryset(type(record), actor)
                if readable is None or not readable.filter(pk=record.pk).exists():
                    raise PermissionDenied("Read access to a saved watched record is required.")
            for target in targets.values():
                self.get_or_create(step_run=step_run, content_type=target.content_type, object_id=target.object_id)

    def wait_kind(self, step_run: Any, until: datetime | None) -> str:
        """Admit a record wait, or require the time wait's explicit deadline."""
        with system_context(reason="workflows.watch_wait"):
            if self.filter(step_run=step_run).exists():
                return str(WaitingKind.RECORD)
        if until is None:
            raise ValidationError("A wait requires a deadline or a watched record.")
        return str(WaitingKind.TIME)

    def record_change(self, record: Any) -> None:
        """Mark committed observation obligations while shared dispatch holds the record lock."""
        target = canonical_record_target(record)
        with system_context(reason="workflows.watch_capture"):
            if self.filter(content_type=target.content_type, object_id=target.object_id).update(pending=True):
                payload = {"content_type_id": target.content_type.pk, "object_id": target.object_id}
                enqueue_task("workflows.wake_records", kwargs=payload, robust=True)


class StepRunQuerySet(AngeeQuerySet):
    """Own conditional step transitions and database-clock candidate scopes."""

    def for_map(self, run_id: Any, node_key: Any) -> Any:
        """Select a containing map's body rows, accepting native ORM expressions."""
        key = Value(node_key) if isinstance(node_key, str) else node_key
        return self.filter(run_id=run_id, node_key=Concat(key, Value(".body")))

    def collect_map(self, expected_count: int) -> list[dict[str, Any]] | None:
        """Collect ordered terminal evidence, or None while body rows remain unsettled."""
        attempt_model = self.model._meta.get_field("attempts").related_model
        attempts = attempt_model.objects.filter(step_run_id=OuterRef("pk"), number=OuterRef("attempt"))
        rows = list(self.order_by("map_index").annotate(item_error=Subquery(attempts.values("error")[:1])))
        if len(rows) != expected_count or any(row.status not in StepRunStatus.terminal_values() for row in rows):
            return None
        if any(row.status not in (StepRunStatus.SUCCEEDED, StepRunStatus.FAILED) for row in rows):
            raise ValidationError("A canceled or skipped map body cannot produce a result.")
        return [{
            "index": row.map_index, "outcome": row.outcome,
            **({"error": row.item_error or ""} if row.status == StepRunStatus.FAILED else {"output": row.output}),
        } for row in rows]

    @staticmethod
    def _cleared_wait() -> dict[str, Any]:
        """Clear the companion columns shared by every non-waiting transition."""
        return {"waiting_kind": None, "wait_reason": "", "wake_at": None, "deadline_at": None, "updated_at": Now()}

    def dispatch(self) -> None:
        """Send unlocked ready rows after refreshing their delivery timestamp."""
        with transaction.atomic(), system_context(reason="workflows.dispatch"):
            ready = self.filter(status=StepRunStatus.READY).exclude(run__status__in=RunStatus.terminal_values())
            selected = ready.order_by("pk").lock_if_supported(no_key=True, skip_locked=True)
            selected_ids = list(selected.values_list("pk", flat=True))
            ready.filter(pk__in=selected_ids).update(dispatched_at=Now(), updated_at=Now())
            for pk in selected_ids:
                enqueue_task("workflows.execute", kwargs={"step_run_id": pk})

    def count_redispatch(self) -> int:
        """Count a tick redelivery without changing status or publishing a change."""
        return self.filter(status=StepRunStatus.READY).update(dispatches=F("dispatches") + 1, updated_at=Now())

    def claim(self, step_run: Any) -> Any:
        """Claim a ready row, append its attempt and persist input before the body."""
        if not connection.in_atomic_block:
            raise RuntimeError("claim requires the caller's locked transaction.")
        values = dict(attempt=F("attempt") + 1, idempotency_token=step_run.idempotency_token or uuid4())
        acknowledged_by_id = step_run.retry_acknowledged_by_id
        changed = self.filter(pk=step_run.pk, status=StepRunStatus.READY).update(**values)
        if not changed:
            return None
        step_run.refresh_from_db(fields=list(values))
        attempt = step_run.attempts.create(
            number=step_run.attempt, page_index=step_run.page_index, acknowledged_by_id=acknowledged_by_id,
        )
        deadline = timedelta(seconds=settings.CELERY_TASK_TIME_LIMIT)
        until = attempt.started_at + deadline
        try:
            with transaction.atomic():
                step = step_run.step
                if step.mode == "IO":
                    deadline = step.timeout
                    until = attempt.started_at + io_timeout_budget()
                step_run.input = step_run.run.version.definition.input_for(
                    step_run.node_key, step_run.run.input, step_run.run.step_runs.all(),
                    map_index=step_run.map_index,
                )
                self.filter(pk=step_run.pk).update(input=step_run.input)
        finally:
            self.filter(pk=step_run.pk).to_running(deadline, until=until)
            step_run.refresh_from_db(fields=[
                "status", "deadline_at", "waiting_kind", "wake_at", "wait_reason", "dispatches",
                "retry_acknowledged_by_id",
            ])
        return attempt

    def to_running(self, timeout: timedelta, *, until: datetime) -> int:
        """Finish a claim with one deadline write after class/input resolution."""
        return self.filter(status=StepRunStatus.READY).update(
            **(self._cleared_wait() | {"deadline_at": Least(Now() + timeout, until)}),
            status=StepRunStatus.RUNNING, dispatches=0, retry_acknowledged_by_id=None,
        )

    def extend_deadline(self, timeout: timedelta, *, until: datetime) -> int:
        """Extend a fenced claim from database time within the worker's IO budget."""
        return self.update(deadline_at=Least(Now() + timeout, until), updated_at=Now())

    def fenced(self, step_run: Any) -> Any:
        """Select this still-live claim; every live attempt write uses this predicate."""
        return self.filter(
            pk=step_run.pk, status=StepRunStatus.RUNNING, attempt=step_run.attempt, deadline_at__gt=Now(),
        )

    def settle(self, step_run: Any, settlement: Settlement) -> None:
        """Record a settlement only while its numbered claim is live."""
        self.fenced(step_run)._record_settlement(step_run, settlement)

    def expire(self, step_run: Any) -> None:
        """Recover an expired claim under the tick's run and step locks."""
        self.expired().filter(pk=step_run.pk, attempt=step_run.attempt)._record_settlement(
            step_run, Fail(error="The attempt deadline expired.", retryable=True, timed_out=True),
        )

    def _record_settlement(self, step_run: Any, settlement: Settlement) -> None:
        """Persist one eligible claim and close its attempt through shared policy."""
        attempt_result = AttemptResult.SUCCEEDED
        if (wait_parameters := settlement.wait_parameters()) is not None:
            changed = self.to_waiting(**wait_parameters)
        elif settlement.kind == "next_page":
            changed = self.to_ready(state=settlement.state, reset_retries=True, next_page=True)
        else:
            values = self._cleared_wait()
            if settlement.kind == "done":
                values.update(
                    status=StepRunStatus.SUCCEEDED, output=strip_null_bytes(settlement.output),
                    outcome=settlement.outcome, retries=0,
                )
                changed = self.update(**values)
            else:
                attempt_result = AttemptResult.TIMED_OUT if settlement.timed_out else AttemptResult.FAILED
                retries = step_run.retries + 1
                if settlement.retryable and step_run.requires_duplicate_acknowledgement:
                    changed = self.to_waiting(
                        kind=cast(str, WaitingKind.OPERATOR), reason="possible duplicate effect", retries=retries,
                    )
                elif settlement.retryable and retries < step_run.step.retry.max_attempts:
                    changed = self.to_waiting(
                        until=Now() + step_run.step.retry.delay_for(retries), state=step_run.state, retries=retries,
                    )
                else:
                    values.update(status=StepRunStatus.FAILED, outcome=ERROR_OUTCOME, output={}, retries=retries)
                    changed = self.update(**values)
        if changed != 1:
            raise Superseded
        if wait_parameters is None or wait_parameters.get("kind") != WaitingKind.RECORD:
            step_run.watches.all().delete()
        if step_run.attempts.filter(number=step_run.attempt).close(
            attempt_result, settlement.error, settlement.stacktrace,
        ) != 1:
            raise Superseded

    def to_waiting(
        self, *, until: Any = None, state: Any = None, retries: int | None = None,
        kind: str = cast(str, WaitingKind.TIME), reason: str = "",
        decision_group_id: Any = None,
    ) -> int:
        """Park running rows, preserving retries unless a failure consumed one."""
        if kind == WaitingKind.OPERATOR and not reason:
            raise ValueError("An operator wait requires its reason.")
        return self.filter(status__in=(StepRunStatus.RUNNING, StepRunStatus.READY)).update(
            **(self._cleared_wait() | {
                "waiting_kind": kind, "wake_at": until, "wait_reason": reason if kind == WaitingKind.OPERATOR else "",
            }),
            status=StepRunStatus.WAITING, state=F("state") if state is None else state, outcome="",
            retries=F("retries") if retries is None else retries,
            decision_group_id=F("decision_group_id") if decision_group_id is None else decision_group_id,
        )

    def to_ready(
        self, *, state: Any = None, reset_retries: bool = False,
        acknowledged_by_id: Any = None, next_page: bool = False,
    ) -> int:
        """Wake waiting rows and clear wait/deadline state; delivery is commit-owned."""
        return self.filter(status__in=(StepRunStatus.WAITING, StepRunStatus.FAILED, StepRunStatus.RUNNING)).update(
            **self._cleared_wait(), status=StepRunStatus.READY, dispatched_at=Now(),
            state=F("state") if state is None else state, retries=0 if reset_retries else F("retries"),
            outcome="", output={}, retry_acknowledged_by_id=acknowledged_by_id,
            page_index=F("page_index") + 1 if next_page else F("page_index"),
        )

    def cancel_open(self) -> tuple[int, int]:
        """Return canceled step and pending-review counts, preserving completed evidence."""
        opened = self.exclude(status__in=StepRunStatus.terminal_values())
        list(opened.order_by("pk").lock_if_supported(no_key=True).values_list("pk", flat=True))
        attempts = self.model._meta.get_field("attempts").related_model
        decisions = apps.get_model("decisions", "Decision").objects
        reviews = 0
        for group_id in opened.exclude(decision_group_id=None).order_by("decision_group_id").values_list(
            "decision_group_id", flat=True,
        ):
            reviews += decisions.cancel_group(group_id)
        attempts.objects.filter(step_run__in=opened).close(AttemptResult.SUPERSEDED)
        apps.get_model("workflows", "StepWatch").objects.filter(step_run__in=opened).delete()
        changed = opened.update(
            **self._cleared_wait(), status=StepRunStatus.CANCELED,
        )
        return changed, reviews

    def due(self) -> Any:
        """Return time or record waits whose optional database deadline has arrived."""
        return self.filter(status=StepRunStatus.WAITING,
                           waiting_kind__in=(WaitingKind.TIME, WaitingKind.RECORD), wake_at__lte=Now())

    def changed_records(self, *, content_type_id: int | None = None, object_id: Any = None) -> Any:
        """Select each live waiter once, even when several watched records changed."""
        watches = apps.get_model("workflows", "StepWatch")
        pending = system_queryset(watches).filter(step_run_id=OuterRef("pk"), pending=True)
        if content_type_id is not None:
            pending = pending.filter(content_type_id=content_type_id, object_id=object_id)
        return self.filter(Exists(pending), status=StepRunStatus.WAITING, waiting_kind=WaitingKind.RECORD)

    def settled_decisions(self) -> Any:
        """Select decision waiters whose retained group has durably settled."""
        return self.filter(
            status=StepRunStatus.WAITING, waiting_kind=WaitingKind.DECISION,
            decision_group__settled_at__isnull=False,
        )

    def terminal_runs(self) -> Any:
        """Select run waiters whose protected target has reached a terminal state."""
        return self.filter(status=StepRunStatus.WAITING, waiting_kind=WaitingKind.RUN,
                           awaited_run__status__in=RunStatus.terminal_values())

    def undispatched(self) -> Any:
        """Return ready rows whose most recent delivery is old enough to retry."""
        return self.filter(status=StepRunStatus.READY, dispatched_at__lte=Now() - timedelta(seconds=60))

    def expired(self) -> Any:
        """Return committed claims whose database deadline has expired."""
        return self.filter(status=StepRunStatus.RUNNING, deadline_at__lt=Now())


class StepAttemptQuerySet(AngeeQuerySet):
    """Own the one-way closure of an execution attempt."""

    def close(self, result: str, error: str = "", stacktrace: str = "") -> int:
        """Close unfinished attempts once, sanitizing their diagnostic columns."""
        return self.filter(finished_at__isnull=True).update(
            finished_at=Now(), result=result,
            error=_error_text(error, self.model._meta.get_field("error")),
            stacktrace=_error_text(stacktrace, self.model._meta.get_field("stacktrace")), updated_at=Now(),
        )


@contextmanager
def _database_timeout(timeout: timedelta, *, setting: str = "statement_timeout") -> Iterator[None]:
    """Bound each PostgreSQL statement, flooring sub-millisecond limits to 1 ms."""
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


class StepRunManager(AngeeManager.from_queryset(StepRunQuerySet)):  # type: ignore[misc]
    """Execute steps and recover delivery, composing the transition owners."""

    @cached_property
    def run_model(self) -> Any:
        """Resolve the related model for entrypoints that start from bare row ids."""
        return self.model._meta.get_field("run").related_model

    def record_await(self, step_run: Any, run_id: int) -> None:
        """Retain observed-run evidence inside an already-held DATABASE claim."""
        with system_context(reason="workflows.record_await"):
            if self.fenced(step_run).update(awaited_run_id=run_id) != 1:
                raise Superseded

    def execute(self, step_run_id: int) -> bool:
        """Claim once; DATABASE bodies share the claim transaction, IO bodies do not."""
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
                        return False
                ctx = None
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
                        input=step.parse_input(step_run.input), config=step.parse_config(node.config),
                        now=attempt.started_at,
                    )
                    if step.mode == "DATABASE":
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
                    self.run_model.objects.advance(
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
    def _failure(failure: Exception) -> Fail:
        return Fail(
            error=exception_text(failure), timed_out=isinstance(failure, SoftTimeLimitExceeded),
            retryable=isinstance(failure, (Retryable, RetryableDecisionError)) or (
                isinstance(failure, OperationalError)
                and _sqlstate(failure) in RETRYABLE_SQLSTATES
            ),
            stacktrace=traceback.format_exc(),
        )

    def _run_body(self, ctx: StepContext) -> Settlement:
        """Validate once with the body, rolling back DATABASE writes on failure."""
        database = ctx.step.mode == "DATABASE"
        try:
            with (
                _database_timeout(ctx.step.timeout) if database else nullcontext()
            ), (transaction.atomic() if database else nullcontext()), actor_context(ctx.actor):
                if is_sudo():
                    raise RuntimeError("The step body must run under its actor, outside system_context.")
                settlement = ctx.step.check(ctx.step().run(ctx), config=ctx.config)
                settlement = settlement.admit(ctx)
                if database and settlement.kind == "fail":
                    transaction.set_rollback(True)
                return settlement
        except Superseded:
            raise
        except Exception as failure:
            return self._failure(failure)

    def _settle_io(self, ctx: StepContext, settlement: Settlement) -> bool:
        """Retry a fenced result transaction until its claim's current deadline."""
        while timezone.now() < ctx.step_run.deadline_at:
            try:
                with self._fenced(ctx.step_run) as current:
                    self.run_model.objects.advance(
                        current.run, current, settlement, artifacts=ctx.pending_artifacts,
                    )
                    return True
            except Superseded:
                break
            except OperationalError:
                pass
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
                current = self.fenced(step_run).lock_if_supported(no_key=True).first()
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
            if self.fenced(step_run).extend_deadline(
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
        if readable is None or not readable.filter(pk=record.pk).exists():
            raise PermissionDenied("Read access to the artifact record is required.")
        target = canonical_record_target(record)
        with self._fenced(step_run) as current:
            return current.artifacts.model(
                step_run=current, content_type=target.content_type, object_id=target.object_id, label=label,
            )

    def retry_step(self, step_run: Any, *, actor: Any = None, accept_duplicate: bool = False) -> Any:
        """Retry one failed or operator-waiting node, retaining all settled evidence."""
        with self.run_model.objects.hold(step_run.run_id) as run:
            if run is None:
                raise ValidationError("The workflow run no longer exists.")
            actor = run.require_access("write", actor)
            if accept_duplicate and actor is None:
                raise PermissionDenied("A duplicate-effect acknowledgement requires an actor.")
            with system_context(reason="workflows.retry_step"):
                current = run.step_runs.filter(pk=step_run.pk).lock_if_supported(no_key=True).get()
                current.run = run
                if blocker := current.retry_blocker:
                    raise ValidationError(blocker)
                if current.requires_duplicate_acknowledgement and not accept_duplicate:
                    raise ValidationError("Retry requires accepting a possible duplicate effect.")
                run.step_runs.filter(pk=current.pk).to_ready(
                    acknowledged_by_id=actor_user_id(to_subject_ref(actor)) if accept_duplicate else None,
                )
                self.run_model.objects.reopen(run)
                current.refresh_from_db()
                return current.with_actor(actor)

    def resolution(self, decision_ref: str, *, run: Any, actor: Any) -> Any:
        """Resolve the prior review's action contract, then delegate answer locks and authority."""
        model = apps.get_model("decisions", "Decision")
        decision = instance_from_public_id(model, decision_ref, queryset=read_scoped_queryset(model, actor))
        if decision is None:
            raise PermissionDenied("The decision is absent or inaccessible.")
        source = next((row for row in system_queryset(self.model).filter(
            run_id=run.pk, decision_group__isnull=False,
        ).select_related("decision_group", "run__version")
            if any(group.pk == decision.group_id for group in row.decision_group.rounds())), None)
        if source is None or not issubclass(source.step, ReviewStep):
            raise ValidationError("The decision has no retained review in this run.")
        step = source.step
        config = step.parse_config(source.run.version.definition.node(source.node_key).config)
        return model.objects.resolution(
            decision.pk, actor=actor, actions=step.actions_for(config), basis_model=step.basis_model,
        )

    def tick(self) -> dict[str, int]:
        """Wake waits, reap expired claims and recover missing deliveries in bounded batches."""
        return {"woken": self.wake(), "reaped": self.reap(), "redispatched": self.redispatch(),
                "decisions": self.wake_decisions(), "runs": self.wake_runs(), "records": self.wake_records(),
                "drained": apps.get_model("workflows", "Trigger").objects.drain(),
                "pruned": self.run_model.objects.prune()}

    def wake_runs(self, run_id: Any = None) -> int:
        """After-commit delivery and tick recovery share the existing wake transition."""
        candidates = self.terminal_runs()
        if run_id is not None:
            candidates = candidates.filter(awaited_run_id=run_id)
        return self._each_candidate(candidates, self._wake)

    def wake_decisions(self, group_id: Any = None) -> int:
        """Signal and sweep share the run-lock owner and commit-time dispatch."""
        candidates = self.settled_decisions()
        if group_id is not None:
            candidates = candidates.filter(decision_group_id=group_id)
        return self._each_candidate(candidates, self._wake)

    def wake_records(self, *, content_type_id: int | None = None, object_id: Any = None) -> int:
        """Share after-commit delivery and pending-watch tick recovery with every wake kind."""
        candidates = self.changed_records(content_type_id=content_type_id, object_id=object_id)
        return self._each_candidate(candidates, self._wake)

    def _each_candidate(self, candidates: Any, action: Callable[[Any, Any], None]) -> int:
        count = 0
        with system_context(reason="workflows.tick"):
            candidates = candidates.exclude(run__status__in=RunStatus.terminal_values())
            for pk, run_id in list(candidates.order_by("pk").values_list("pk", "run_id")[:TICK_CANDIDATE_LIMIT]):
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
        step_run.watches.all().delete()
        self.run_model.objects.advance(run)

    def redispatch(self) -> int:
        """Bound tick redelivery; exhaustion waits for an operator without routing."""
        return self._each_candidate(self.undispatched(), self._redispatch)

    def _redispatch(self, run: Any, step_run: Any) -> None:
        run.step_runs.filter(pk=step_run.pk).count_redispatch()
        if step_run.dispatches + 1 >= settings.ANGEE_WORKFLOW_MAX_DISPATCHES:
            run.step_runs.filter(pk=step_run.pk).to_waiting(
                kind=WaitingKind.OPERATOR, reason="Task delivery exhausted its retry allowance.",
            )
            self.run_model.objects.advance(run)

    def reap(self) -> int:
        """Recover expired committed claims while holding the effect marker's lock."""
        return self._each_candidate(self.expired(), self._reap)

    def _reap(self, run: Any, step_run: Any) -> None:
        step_run.run = run
        try:
            with transaction.atomic():
                run.step_runs.expire(step_run)
        except ImproperlyConfigured as failure:
            step_run.attempts.filter(number=step_run.attempt).close(AttemptResult.TIMED_OUT, exception_text(failure))
            run.step_runs.filter(pk=step_run.pk).to_waiting(
                kind=WaitingKind.OPERATOR,
                reason="The step implementation is unavailable; restore its registration before retrying.",
            )
        self.run_model.objects.advance(run)
