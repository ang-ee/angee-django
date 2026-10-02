"""Workflow admission, publication, fenced execution and operator recovery."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property
from typing import Any, Literal, cast
from uuid import uuid4

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import Exists, F, Max, OuterRef, Q, Value
from django.db.models.deletion import ProtectedError, RestrictedError
from django.db.models.functions import Concat, Least, Now
from pydantic import ValidationError as PydanticValidationError
from rebac import actor_context, system_context, to_subject_ref

from angee.base.actors import actor_user_id
from angee.base.evidence import EvidenceReference, readable_records
from angee.base.fields import ModelLabelField
from angee.base.identity import public_id_of
from angee.base.mixins import AppendOnlyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.refs import canonical_record_target
from angee.base.scoping import lock_if_supported, read_scoped_queryset, system_queryset
from angee.base.serialization import canonical_json_sha256, strip_null_bytes
from angee.graphql.publishing import publish_change
from angee.jobs.enqueue import enqueue_task
from angee.workflows.definition import MAP_BODY_SUFFIX, Definition, DefinitionInvalid, Issue
from angee.workflows.states import (
    CANCELED_OUTCOME,
    AttemptResult,
    RunOrigin,
    RunRelation,
    RunStatus,
    StepRunStatus,
    WaitingKind,
)
from angee.workflows.steps import StepMode, Superseded, io_timeout_budget
from angee.workflows.triggers import TriggerGrantTarget, TriggerSource

logger = logging.getLogger(__name__)
RETRYABLE_SQLSTATES = frozenset({"57014", "40P01", "55P03"})
PRUNE_BATCH_LIMIT = 25
"""Prune examines at most 25 roots per tick; blocked roots retry after one day."""
_held_runs: ContextVar[set[int] | None] = ContextVar("workflows_held_runs", default=None)


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
class PublishResult:
    """The published version and readable parents that need republishing."""

    version: Any
    dependents: tuple[str, ...]


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

    def _resolved_document(self, draft: Any, actor: Any) -> tuple[Any, list[Issue]]:
        """Snapshot each awaited contract through the author's workflow read scope."""
        try:
            definition = Definition.model_validate(draft)
        except PydanticValidationError:
            return draft, []
        document = definition.model_dump(mode="json", by_alias=True)
        issues: list[Issue] = []
        readable = read_scoped_queryset(self.model, actor) if actor is not None else system_queryset(self.model)
        for key, node, path in definition.declarations():
            if node.step != "await_run":
                continue
            config = document["nodes"][key.partition(".")[0]]
            if key.endswith(MAP_BODY_SUFFIX):
                config = config["body"]
            config = config["config"]
            expects = config.get("expects")
            config.pop("outcomes", None)
            if not isinstance(expects, str) or not expects:
                continue
            expected = readable.select_related("published").filter(key=expects, published__isnull=False).first()
            if expected is None:
                issues.append(Issue(
                    node=key, path=[*path, "config", "expects"], code="expected_workflow",
                    message=f"Expected workflow {expects!r} must be readable and published.",
                ))
                continue
            config["outcomes"] = expected.published.definition.output_schemas
        return document, issues

    def _published_dependents(self, workflow: Any, actor: Any) -> tuple[str, ...]:
        """Name readable published parents whose frozen contract names this workflow."""
        readable = read_scoped_queryset(self.model, actor) if actor is not None else system_queryset(self.model)
        parents = readable.exclude(pk=workflow.pk).filter(published__isnull=False).values_list(
            "key", "published__document",
        )
        return tuple(sorted(
            key for key, document in parents
            if any(
                node.step == "await_run" and node.config.get("expects") == workflow.key
                for _, node, _ in Definition.model_validate(document).declarations()
            )
        ))

    def save_identity(
        self, *, key: str, name: str, description: str = "", subject_model: str = "", actor: Any = None,
    ) -> Any:
        """Save an authorized identity with a canonical, version-stable subject model."""
        if subject_model:
            try:
                subject_model = ModelLabelField.normalize(subject_model)
            except ValidationError as error:
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
                workflow.name = name
                workflow.description = description
                workflow.subject_model = subject_model
                workflow.save(update_fields={"name", "description", "subject_model", "updated_at"})
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

        actor = workflow.require_access("write", actor)
        resolved, resolution_issues = self._resolved_document(draft, actor)
        with system_context(reason="workflows.save_draft"):
            definition, issues = Definition.check(resolved, subject_model=workflow.subject_model)
            issues.extend(resolution_issues)
            if definition is None or any(issue.blocks_draft for issue in issues):
                return DraftSave("invalid", expected_revision, issues)
            values = {"draft": draft, "draft_revision": F("draft_revision") + 1, "updated_at": Now()}
            if layout is not None:
                values["layout"] = layout
            changed = self.filter(pk=workflow.pk, draft_revision=expected_revision).update(**values)
            revision = self.values_list("draft_revision", flat=True).get(pk=workflow.pk)
        return DraftSave("saved" if changed else "conflict", revision, issues)

    def publish(self, workflow: Any, *, actor: Any = None) -> PublishResult:
        """Publish a frozen document and report readable dependents to republish."""

        actor = workflow.require_access("write", actor)
        with transaction.atomic():
            with system_context(reason="workflows.publish"):
                current = self.filter(pk=workflow.pk).lock_if_supported(no_key=True).get()
            resolved, resolution_issues = self._resolved_document(current.draft, actor)
            definition, issues = Definition.check(resolved, subject_model=current.subject_model)
            issues.extend(resolution_issues)
            if issues:
                raise DefinitionInvalid(issues)
            assert definition is not None
            document = definition.published_document()
            digest = canonical_json_sha256(document)
            if current.published_id and current.published.content_hash == digest:
                version = current.published
            else:
                with system_context(reason="workflows.publish"):
                    number = current.versions.aggregate(number=Max("number"))["number"] or 0
                    version = current.versions.create(
                        number=number + 1,
                        document=document,
                        content_hash=digest,
                        published_by_id=actor_user_id(to_subject_ref(actor)) if actor is not None else None,
                    )
                    self.filter(pk=current.pk).update(published=version, updated_at=Now())
            return PublishResult(version, self._published_dependents(current, actor))

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
        """Lock one run and dispatch once for the outermost hold after commit.

        Register at context exit. Robust callbacks log delivery failures without
        suppressing later publications from enclosing parent/child transactions;
        the tick recovers the durable ready rows.
        """
        held = _held_runs.get()
        outermost = held is None
        if held is None:
            held = set()
            token = _held_runs.set(held)
        else:
            token = None
        previous = held.copy()
        try:
            with transaction.atomic():
                with system_context(reason="workflows.hold"), (
                    _database_timeout(timeout, setting="lock_timeout") if timeout is not None else nullcontext()
                ), (transaction.atomic() if timeout is not None else nullcontext()):
                    run = self.filter(pk=run_id).lock_if_supported(no_key=True, skip_locked=skip_locked).first()
                if run is not None:
                    held.add(run.pk)
                try:
                    yield run
                finally:
                    if outermost and held:
                        ids = sorted(held)
                        transaction.on_commit(
                            lambda: apps.get_model("workflows", "StepRun").objects.filter(run_id__in=ids).dispatch(),
                            robust=True,
                        )
        except BaseException:
            held.clear()
            held.update(previous)
            raise
        finally:
            if token is not None:
                _held_runs.reset(token)

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


class WorkflowRunEvidenceQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet[Any]):
    """Keep retained admission references immutable until their run is pruned."""


WorkflowRunEvidenceManager = AngeeManager.from_queryset(WorkflowRunEvidenceQuerySet)


class WorkflowRunManager(AngeeManager.from_queryset(WorkflowRunQuerySet)):  # type: ignore[misc]
    """Own run admission, cancellation and retained row updates under the run lock."""

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
        trigger_event: Any = None,
    ) -> Any:
        """Insert the pinned version, origin and sole cause together, or replay the same request."""

        actor = workflow.require_access("start", actor)
        if actor is None:
            raise PermissionDenied("A run requires an actor.")
        if (parent_step is None and relation is not None) or (
            parent_step is not None and relation not in RunRelation.values
        ):
            raise ValidationError("A child requires an owned or continuation relation and a parent step.")
        if sum(cause is not None for cause in (parent_step, reprocess_of, trigger_event)) > 1:
            raise ValidationError("A run can have only one cause.")
        origin = (RunOrigin.WORKFLOW if parent_step is not None else
                  RunOrigin.REPROCESS if reprocess_of is not None else
                  RunOrigin.TRIGGER if trigger_event is not None else RunOrigin.MANUAL)
        payload = {} if input is None else input
        with transaction.atomic(), (
            self.hold(parent_step.run_id) if parent_step is not None else nullcontext(None)
        ) as parent, system_context(reason="workflows.start"):
            if parent_step is not None:
                if parent is None:
                    raise ValidationError("The parent run no longer exists.")
                parent.require_access("write", actor)
                if request_key is None:
                    request_key = f"child:{parent_step.sqid}:{parent_step.page_index}"
            workflow.refresh_from_db()
            workflow.validate_subject(subject)
            target = canonical_record_target(subject) if subject is not None else None
            identity = dict(
                run_as_id=actor_user_id(to_subject_ref(actor)),
                subject_content_type_id=target.content_type.pk if target is not None else None,
                subject_object_id=target.object_id if target is not None else None,
                parent_step_id=parent_step.pk if parent_step is not None else None,
                reprocess_of_id=reprocess_of.pk if reprocess_of is not None else None,
                trigger_event_id=trigger_event.pk if trigger_event is not None else None,
                origin=origin,
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
            principal_workflow = system_queryset(apps.get_model("workflows", "Workflow")).filter(
                user_id=identity["run_as_id"],
            ).first()
            if principal_workflow is not None and version.published_by_id is not None:
                publisher = version.published_by
                granted = system_queryset(apps.get_model("workflows", "Trigger")).filter(
                    workflow=principal_workflow, enabled=True,
                ).order_by("pk").values_list("granted_targets", flat=True)
                for values in granted:
                    for value in values:
                        TriggerGrantTarget.from_stored(value).require_publisher_access(publisher, version)
            normalized = version.definition.validate_input(payload)
            references = list(version.definition.input_evidence(normalized))
            if subject is not None:
                references.append(EvidenceReference(model=subject._meta.label, id=public_id_of(subject)))
            records = readable_records(references, (actor,))
            targets = {canonical_record_target(record) for record in records}
            try:
                with transaction.atomic():
                    run = self.create(
                        version=version, input=normalized, request_key=request_key, **identity,
                    )
            except IntegrityError:
                existing = self.filter(request_key=request_key).first() if request_key is not None else None
                if existing is None:
                    raise
                return replay(existing)
            evidence_model = apps.get_model("workflows", "WorkflowRunEvidence")
            evidence_model.objects.bulk_create(
                evidence_model(run=run, content_type=source.content_type, object_id=source.object_id)
                for source in sorted(targets, key=lambda item: (item.content_type.pk, item.object_id))
            )
            with self.hold(run.pk) as locked:
                from angee.workflows.runner import runner

                runner.advance(locked)
            return locked.with_actor(actor)

    def _write_state(self, run: Any, *, status: str, outcome: str, output: Any, error: str = "") -> None:
        self.filter(pk=run.pk).update(
            status=status, outcome=outcome, output=strip_null_bytes(output),
            error=error,
            finished_at=Now() if status in RunStatus.terminal_values() else None, updated_at=Now(),
        )
        if status in RunStatus.terminal_values() and not run.is_terminal:
            if status != RunStatus.CANCELED:
                def cancel_children() -> None:
                    self.cancel_abandoned(run.pk)

                transaction.on_commit(cancel_children, robust=True)
            enqueue_task("workflows.wake_run", kwargs={"run_id": run.pk}, robust=True)

    def cancel_abandoned(self, run_id: int) -> None:
        """Close unawaited owned children after the parent's terminal write commits."""
        with system_context(reason="workflows.cancel_abandoned"):
            parent = self.filter(pk=run_id, status__in=RunStatus.terminal_values()).first()
            if parent is None:
                return
            awaited = parent.step_runs.exclude(awaited_run_id=None).values("awaited_run_id")
            abandoned = self.filter(parent_step__run=parent, relation=RunRelation.OWNED).exclude(pk__in=awaited)
            for child_id in abandoned.order_by("pk").values_list("pk", flat=True):
                with self.hold_owned(child_id) as children:
                    self._cancel_locked(children)

    def _cancel_locked(self, runs: list[Any]) -> Cancellation:
        """Cancel open rows in an already-held tree without rewriting terminal facts."""
        steps = reviews = children = 0
        canceled = bool(runs and not runs[0].is_terminal)
        for index, run in enumerate(runs):
            changed, closed = run.step_runs.cancel_open()
            steps, reviews = steps + changed, reviews + closed
            if not run.is_terminal:
                children += bool(index)
                self._write_state(run, status=str(RunStatus.CANCELED), outcome=CANCELED_OUTCOME, output={})
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
        self._write_state(run, status=str(RunStatus.RUNNING), outcome="", output={})
        run.refresh_from_db()
        from angee.workflows.runner import runner

        runner.advance(run)

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
                    except (ProtectedError, RestrictedError) as error:
                        self.filter(pk=run_id).update(
                            prune_after=Now() + timedelta(days=1),
                            prune_reason=str(error.args[0]),
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
        with system_context(reason="workflows.watch_register"):
            for _, target in sorted(targets.items()):
                rows = system_queryset(target.content_type.model_class()).filter(pk=target.object_id)
                lock_if_supported(rows, no_key=True).get()
            for record in records:
                readable = read_scoped_queryset(type(record), actor)
                if not readable.filter(pk=record.pk).exists():
                    raise PermissionDenied("Read access to a saved watched record is required.")
            for target in targets.values():
                self.get_or_create(step_run=step_run, content_type=target.content_type, object_id=target.object_id)

    def wait_kind(self, step_run: Any, until: datetime | None) -> WaitingKind:
        """Admit a record wait, or require the time wait's explicit deadline."""
        with system_context(reason="workflows.watch_wait"):
            if self.filter(step_run=step_run).exists():
                return cast(WaitingKind, WaitingKind.RECORD)
        if until is None:
            raise ValidationError("A wait requires a deadline or a watched record.")
        return cast(WaitingKind, WaitingKind.TIME)

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
        return self.filter(run_id=run_id, node_key=Concat(key, Value(MAP_BODY_SUFFIX)))

    def collect_map(self, expected_count: int) -> list[dict[str, Any]] | None:
        """Collect ordered terminal evidence, or None while body rows remain unsettled."""
        rows = list(self.order_by("map_index"))
        if len(rows) != expected_count or any(row.status not in StepRunStatus.terminal_values() for row in rows):
            return None
        if any(row.status not in (StepRunStatus.SUCCEEDED, StepRunStatus.FAILED) for row in rows):
            raise ValidationError("A canceled or skipped map body cannot produce a result.")
        return [{
            "index": row.map_index, "outcome": row.outcome,
            **(
                {"error": row.output.get("error", "")}
                if row.status == StepRunStatus.FAILED else {"output": row.output}
            ),
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
                if step.mode == StepMode.IO:
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

    def settle(self, step_run: Any, attempt: Any) -> None:
        """Record a settlement only while its numbered claim is live."""
        self.fenced(step_run)._record_settlement(step_run, attempt)

    def expire(self, step_run: Any, attempt: Any) -> None:
        """Recover an expired claim under the tick's run and step locks."""
        self.expired().filter(pk=step_run.pk, attempt=step_run.attempt)._record_settlement(
            step_run, attempt,
        )

    def _record_settlement(self, step_run: Any, attempt: Any) -> None:
        """Persist one eligible claim and close its attempt through shared policy."""
        changed = attempt.settlement.transition(self, step_run, attempt)
        if changed != 1:
            raise Superseded
        if not attempt.settlement.keeps_watches:
            step_run.watches.all().delete()
        if step_run.attempts.filter(number=step_run.attempt).close(
            attempt.result, attempt.diagnostic_error or attempt.error, attempt.stacktrace,
        ) != 1:
            raise Superseded

    def to_waiting(
        self, *, until: Any = None, state: Any = None, retries: int | None = None,
        kind: WaitingKind = cast(WaitingKind, WaitingKind.TIME), reason: str = "",
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
        acknowledged_by_id: Any = None, next_page: bool = False, reset_dispatches: bool = False,
    ) -> int:
        """Wake waiting rows and clear wait/deadline state; delivery is commit-owned."""
        return self.filter(status__in=(StepRunStatus.WAITING, StepRunStatus.FAILED, StepRunStatus.RUNNING)).update(
            **self._cleared_wait(), status=StepRunStatus.READY, dispatched_at=Now(),
            state=F("state") if state is None else state, retries=0 if reset_retries else F("retries"),
            outcome="", output={}, retry_acknowledged_by_id=acknowledged_by_id,
            dispatches=0 if reset_dispatches else F("dispatches"),
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
            error=error,
            stacktrace=stacktrace, updated_at=Now(),
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
    """Own step row updates and operator retry under the run lock."""

    @cached_property
    def run_model(self) -> Any:
        """Resolve the related model for entrypoints that start from bare row ids."""
        return self.model._meta.get_field("run").related_model

    def record_await(self, step_run: Any, run_id: int) -> None:
        """Retain observed-run evidence inside an already-held DATABASE claim."""
        with system_context(reason="workflows.record_await"):
            if self.fenced(step_run).update(awaited_run_id=run_id) != 1:
                raise Superseded

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
                    reset_dispatches=True,
                )
                self.run_model.objects.reopen(run)
                current.refresh_from_db()
                return current.with_actor(actor)
