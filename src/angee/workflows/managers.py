"""Workflow admission, publication, fenced execution and operator recovery."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from contextlib import ExitStack, nullcontext
from contextvars import Context
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property
from itertools import batched
from typing import TYPE_CHECKING, Annotated, Any, cast

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, OperationalError, transaction
from django.db.models import F, Max
from django.db.models.deletion import ProtectedError, RestrictedError
from django.db.models.functions import Now
from pydantic import Field, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from rebac import (
    ObjectRef,
    RelationshipTuple,
    actor_context,
    database_context,
    generic_target,
    resolve_subjects,
    system_context,
    to_object_ref,
    to_subject_ref,
    user_subject_type,
)
from rebac.actors import is_sudo
from rebac.backends import backend
from rebac.database import write_alias
from rebac.errors import MissingActorError, NoActorResolvedError
from rebac.relationships import delete_relationship, write_relationships

from angee.base.exceptions import exception_text
from angee.base.fields.implementation import ImplClassField
from angee.base.fields.text import ModelLabelField
from angee.base.models.identity import public_id_of
from angee.base.models.locks import StaleRevisionError, require_revision
from angee.base.querysets import query_batch_size, row_lock_paths
from angee.base.serialization import canonical_json_sha256
from angee.iam.identity import group_subject_type, user_label
from angee.iam.managers import ProtectedManager
from angee.iam.permissions import actor_user_id
from angee.jobs.enqueue import enqueue_task
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.evidence import EvidenceReference, readable_records
from angee.workflows.definition import (
    Body,
    Definition,
    DefinitionInvalid,
    Issue,
    SourcedWorkflowExtension,
)
from angee.workflows.querysets import (
    RETRYABLE_SQLSTATES as RETRYABLE_SQLSTATES,
)
from angee.workflows.querysets import (
    StepAttemptQuerySet as StepAttemptQuerySet,
)
from angee.workflows.querysets import (
    StepRecordQuerySet as StepRecordQuerySet,
)
from angee.workflows.querysets import (
    StepRunQuerySet as StepRunQuerySet,
)
from angee.workflows.querysets import (
    TriggerEventQuerySet,
    TriggerQuerySet,
)
from angee.workflows.querysets import (
    WorkflowRunQuerySet as WorkflowRunQuerySet,
)
from angee.workflows.querysets import (
    _record_failure as _record_failure,
)
from angee.workflows.querysets import (
    _sqlstate as _sqlstate,
)
from angee.workflows.states import (
    INTERRUPTED_OUTCOME,
    RunOrigin,
    RunRelation,
    RunStatus,
    WaitingKind,
)
from angee.workflows.steps import Superseded
from angee.workflows.triggers import PublisherAuthorityDenied, TriggerEnablePreview, TriggerGrantTarget, TriggerSource

if TYPE_CHECKING:
    from angee.workflows.models import StepRun, StepWatch, Trigger, TriggerEvent, Workflow, WorkflowRun, WorkflowVersion

logger = logging.getLogger(__name__)


PRUNE_BATCH_LIMIT = 25
TRIGGER_DRAIN_LIMIT = 100


"""Prune examines at most 25 roots per tick; blocked roots retry after one day."""


@dataclass(frozen=True)
class DraftSave:
    """A conditional draft save and its complete validation diagnostics."""

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
    children: int = 0

    @property
    def message(self) -> str:
        """Describe the actual transition, including terminal-run cleanup."""
        changes = []
        if self.steps:
            noun = "step" if self.steps == 1 else "steps"
            changes.append(f"{self.steps} open {noun} canceled")
        if self.children:
            noun = "run" if self.children == 1 else "runs"
            changes.append(f"{self.children} child {noun} canceled")
        if not self.canceled and not changes:
            return "Nothing to cancel."
        return "; ".join(["Run canceled" if self.canceled else "Run already finished", *changes]) + "."


class StepConfiguration(Body):
    """A client identity and typed declaration for rowless outcome authoring."""

    node: str


_CONFIGURATIONS: TypeAdapter[list[StepConfiguration]] = TypeAdapter(
    Annotated[list[StepConfiguration], Field(max_length=100)]
)


class WorkflowManager[T: Workflow](AngeeHistoryManager[T]):
    """Own the editable document and the immutable publication sequence."""

    def _resolved_document(self, draft: Any, actor: Any) -> tuple[Any, list[Issue]]:
        """Snapshot awaited contracts and config references through the publishing actor."""
        try:
            definition = Definition.model_validate(draft)
        except PydanticValidationError:
            return draft, []
        issues = self._resolve_awaits(list(definition.declarations()), actor)
        document = definition.model_dump(mode="json", by_alias=True)
        for key, path, relation, item in definition.config_references():
            try:
                with actor_context(actor) if actor is not None else nullcontext():
                    resolved = (
                        apps.get_model("resources", "Resource")
                        .objects.db_manager(self.db)
                        .resolve_config_reference(
                            item,
                            relation=relation,
                        )
                    )
                # Relation paths index both objects (str) and arrays (int).
                parent: Any = document
                for part in path[:-1]:
                    parent = parent[part]
                parent[path[-1]] = resolved
            except (
                ValueError,
                LookupError,
                ValidationError,
                ImproperlyConfigured,
                PermissionDenied,
                MissingActorError,
                NoActorResolvedError,
            ) as error:
                issues.append(Issue(node=key, path=list(path), code="config_reference", message=str(error)))
        return document, issues

    def _resolve_awaits(self, declarations: list[tuple[str, Body, list[str | int]]], actor: Any) -> list[Issue]:
        """Resolve all awaited contracts in one read-scoped query, with per-node failures."""
        issues: list[Issue] = []
        readable = (
            self.model.objects.db_manager(self.db).with_actor(actor)
            if actor is not None
            else self.model.system_queryset().using(self.db)
        )
        awaited = [(key, node, path) for key, node, path in declarations if node.step == "await_run"]
        expects_keys = {
            expects for _, node, _ in awaited if isinstance(expects := node.config.get("expects"), str) and expects
        }
        expected_by_key = (
            {
                expected.key: expected
                for expected in readable.select_related("published").filter(
                    key__in=expects_keys, published__isnull=False
                )
            }
            if expects_keys
            else {}
        )
        for key, node, path in awaited:
            config = node.config
            expects = config.get("expects")
            config.pop("outcomes", None)
            if not isinstance(expects, str) or not expects:
                continue
            expected = expected_by_key.get(expects)
            if expected is None or expected.published is None:
                issues.append(
                    Issue(
                        node=key,
                        path=[*path, "config", "expects"],
                        code="expected_workflow",
                        message=f"Expected workflow {expects!r} must be readable and published.",
                    )
                )
                continue
            try:
                config["outcomes"] = expected.published.definition.output_schemas
            except ImproperlyConfigured, ValidationError, PydanticValidationError:
                issues.append(
                    Issue(
                        node=key,
                        path=[*path, "config", "expects"],
                        code="expected_workflow",
                        message=f"Expected workflow {expects!r} has an invalid published contract.",
                    )
                )
        return issues

    def authoring_outcomes(
        self, configurations: list[dict[str, Any]], *, actor: Any
    ) -> list[tuple[str, dict[str, str], list[Issue]]]:
        """Resolve each unfinished node independently under the viewer's read scope."""
        try:
            entries = _CONFIGURATIONS.validate_python(configurations)
        except PydanticValidationError as error:
            raise ValidationError(
                {
                    ".".join(["configurations", *(str(part) for part in issue["loc"])]): [issue["msg"]]
                    for issue in error.errors()
                }
            ) from error
        declarations: list[tuple[str, Body, list[str | int]]] = [
            (entry.node, entry, ["nodes", entry.node]) for entry in entries
        ]
        resolved_issues = self._resolve_awaits(declarations, actor)
        projected = []
        for key, node, path in declarations:
            outcomes, issues = Definition.node_outcomes(key, node, path)
            projected.append((key, outcomes, [issue for issue in resolved_issues if issue.node == key] + issues))
        return projected

    def _published_dependents(self, workflow: Any, actor: Any) -> tuple[str, ...]:
        """Name readable published parents whose frozen contract names this workflow."""
        readable = (
            self.model.objects.db_manager(self.db).with_actor(actor)
            if actor is not None
            else self.model.system_queryset().using(self.db)
        )
        parents = (
            readable.exclude(pk=workflow.pk)
            .filter(published__isnull=False)
            .values_list(
                "key",
                "published__document",
            )
        )
        return tuple(
            sorted(
                key
                for key, document in parents
                if any(
                    node.step == "await_run" and node.config.get("expects") == workflow.key
                    for _, node, _ in Definition.model_validate(document).declarations()
                )
            )
        )

    def save_identity(
        self,
        *,
        key: str,
        name: str,
        description: str = "",
        subject_model: str = "",
        actor: Any = None,
    ) -> Any:
        """Save an authorized identity with a canonical, version-stable subject model."""
        owner = self.db_manager(write_alias(self.model, using=self._db))

        if subject_model:
            try:
                subject_model = ModelLabelField.normalize(subject_model)
            except ValidationError as error:
                raise DefinitionInvalid(
                    [
                        Issue(
                            path=["subject_model"],
                            code="subject_model",
                            message=f"Unknown subject model {subject_model!r}.",
                        ),
                    ]
                ) from error
        with database_context(owner.db), transaction.atomic(using=owner.db):
            with system_context(reason="workflows.save_identity locate target"):
                workflow = owner.filter(key=key).first()
            identity = {"name": name, "description": description, "subject_model": subject_model}
            # A new workflow asks create about the row get_or_create would insert, the candidate its save checks.
            candidate = workflow or owner.model(key=key, **identity)
            actor = candidate.require_access("write" if workflow else "create", actor)
            with actor_context(actor) if actor is not None else nullcontext():
                workflow, _ = owner.get_or_create(key=key, defaults=identity)
                workflow.require_access("write", actor)
                workflow = owner.filter(pk=workflow.pk).lock_if_supported(no_key=True).get()
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
        node_keys: object = None,
        layout: Any = None,
        actor: Any = None,
    ) -> DraftSave:
        """Save one parsable registered document with optimistic concurrency."""

        owner = self.db_manager(write_alias(self.model, using=self._db or workflow._state.db, instance=workflow))

        actor = workflow.require_access("write", actor)
        with system_context(reason="workflows.save_draft revision preflight"):
            current = owner.filter(pk=workflow.pk).values_list("draft_revision", flat=True).first()
        require_revision(expected=expected_revision, current=current, minimum=0)
        draft, layout = Definition.rekey(draft, keys=node_keys, layout=layout)
        resolved, resolution_issues = owner._resolved_document(draft, actor)
        with system_context(reason="workflows.save_draft"):
            definition, issues = Definition.check(resolved, subject_model=workflow.subject_model)
            issues.extend(resolution_issues)
            if definition is None or any(issue.blocks_draft for issue in issues):
                raise DefinitionInvalid(issues)
            values = {"draft": draft, "draft_revision": F("draft_revision") + 1, "updated_at": Now()}
            if layout is not None:
                values["layout"] = layout
            changed = owner.filter(pk=workflow.pk, draft_revision=expected_revision).update(**values)
            if not changed:
                current = owner.filter(pk=workflow.pk).values_list("draft_revision", flat=True).first()
                raise StaleRevisionError(expected_revision, current)
        return DraftSave(expected_revision + 1, issues)

    def publish(
        self,
        workflow: Any,
        *,
        expected_revision: int,
        actor: Any = None,
        extensions: Sequence[SourcedWorkflowExtension] | None = None,
    ) -> PublishResult:
        """Publish as a user, or install without an actor; report readable dependents."""

        owner = self.db_manager(write_alias(self.model, using=self._db or workflow._state.db, instance=workflow))

        actor = workflow.require_access("write", actor)
        publisher_id = actor_user_id(to_subject_ref(actor)) if actor is not None else None
        if actor is not None and publisher_id is None:
            raise ValidationError("Workflow publication requires a user-backed actor.")
        with database_context(owner.db), transaction.atomic(using=owner.db):
            with system_context(reason="workflows.publish"):
                current = owner.filter(pk=workflow.pk).lock_if_supported(no_key=True).get()
            require_revision(expected=expected_revision, current=current.draft_revision, minimum=0)
            published = current.published
            if extensions is None:
                extensions = published.definition.retained_extensions() if published is not None else ()
            source = current.draft
            try:
                source = (
                    Definition.model_validate(source)
                    .with_extensions(extensions)
                    .model_dump(
                        mode="json",
                        by_alias=True,
                    )
                )
            except PydanticValidationError:
                pass
            resolved, resolution_issues = owner._resolved_document(source, actor)
            definition, issues = Definition.check(resolved, subject_model=current.subject_model)
            issues.extend(resolution_issues)
            if issues:
                raise DefinitionInvalid(issues)
            assert definition is not None
            document = definition.published_document()
            digest = canonical_json_sha256(document)
            version: WorkflowVersion
            if published is not None and published.content_hash == digest:
                version = published
            else:
                with system_context(reason="workflows.publish"):
                    number = current.versions.aggregate(number=Max("number"))["number"] or 0
                    version = current.versions.create(
                        number=number + 1,
                        document=document,
                        content_hash=digest,
                        published_by_id=publisher_id,
                    )
                    owner.filter(pk=current.pk).update(published=version, updated_at=Now())
            return PublishResult(version, owner._published_dependents(current, actor))

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
        extensions: Sequence[SourcedWorkflowExtension] | None = (),
    ) -> Any:
        """Install a resource document through the same draft and publish verbs."""

        owner = self.db_manager(write_alias(self.model, using=self._db))

        with (
            database_context(owner.db),
            transaction.atomic(using=owner.db),
            actor_context(actor) if actor is not None else nullcontext(),
        ):
            workflow = owner.save_identity(
                key=key,
                name=name,
                description=description,
                subject_model=subject_model,
                actor=actor,
            )
            saved = owner.save_draft(
                workflow,
                draft=draft,
                expected_revision=workflow.draft_revision,
                layout=layout,
                actor=actor,
            )
            if publish:
                owner.publish(
                    workflow,
                    expected_revision=saved.revision,
                    actor=actor,
                    extensions=extensions,
                )
            elif extensions:
                Definition.model_validate(draft).with_extensions(extensions)
            workflow.refresh_from_db()
            return workflow


StepRecordManager = ProtectedManager.from_queryset(StepRecordQuerySet)


StepAttemptManager = ProtectedManager.from_queryset(StepAttemptQuerySet)


_WorkflowRunManagerBase = ProtectedManager.from_queryset(WorkflowRunQuerySet)


class WorkflowRunManager[T: WorkflowRun](_WorkflowRunManagerBase[T]):
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

        owner = self.db_manager(write_alias(self.model, using=self._db or workflow._state.db, instance=workflow))

        actor = workflow.require_access("start", actor)
        if actor is None:
            raise PermissionDenied("A run requires an actor.")
        if (parent_step is None and relation is not None) or (
            parent_step is not None and relation not in RunRelation.values
        ):
            raise ValidationError("A child requires an owned or continuation relation and a parent step.")
        if sum(cause is not None for cause in (parent_step, reprocess_of, trigger_event)) > 1:
            raise ValidationError("A run can have only one cause.")
        origin = (
            RunOrigin.WORKFLOW
            if parent_step is not None
            else RunOrigin.REPROCESS
            if reprocess_of is not None
            else RunOrigin.TRIGGER
            if trigger_event is not None
            else RunOrigin.MANUAL
        )
        payload = {} if input is None else input
        with (
            database_context(owner.db),
            transaction.atomic(using=owner.db),
            owner.hold(parent_step.run_id) if parent_step is not None else nullcontext(None) as parent,
            system_context(reason="workflows.start"),
        ):
            if parent_step is not None:
                if parent is None:
                    raise ValidationError("The parent run no longer exists.")
                parent.require_access("write", actor)
                if request_key is None:
                    request_key = f"child:{parent_step.sqid}:{parent_step.page_index}"
            workflow.refresh_from_db()
            workflow.validate_subject(subject)
            target = generic_target(subject, using=owner.db) if subject is not None else None
            principal_id = actor_user_id(to_subject_ref(actor))
            identity = dict(
                run_as_id=principal_id,
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

            if request_key is not None and (existing := owner.filter(request_key=request_key).first()) is not None:
                return replay(existing)
            if parent is not None and (
                parent.is_terminal or not parent.step_runs.fenced(parent_step).lock_if_supported(no_key=True).exists()
            ):
                raise Superseded("The parent attempt cannot admit a child.")
            version = version or workflow.published
            if version is None or version.workflow_id != workflow.pk:
                raise ValidationError("A published version of this workflow is required.")
            principal_workflow = (
                apps.get_model("workflows", "Workflow")
                .system_queryset()
                .using(owner.db)
                .filter(user_id=principal_id)
                .first()
                if principal_id is not None
                else None
            )
            if principal_workflow is not None:
                granted = (
                    apps.get_model("workflows", "Trigger")
                    .system_queryset()
                    .using(owner.db)
                    .filter(
                        workflow=principal_workflow,
                        enabled=True,
                    )
                    .order_by("pk")
                    .values_list("granted_targets", flat=True)
                )
                version.require_publisher_access(
                    TriggerGrantTarget.from_stored(value) for values in granted for value in values
                )
            normalized = version.definition.validate_input(payload)
            references = list(version.definition.input_evidence(normalized))
            if subject is not None:
                references.append(EvidenceReference(model=subject._meta.label, id=public_id_of(subject)))
            records = readable_records(references, (actor,), using=owner.db)
            targets = {generic_target(record, using=owner.db) for record in records}
            try:
                with database_context(owner.db), transaction.atomic(using=owner.db):
                    run = owner.create(
                        version=version,
                        input=normalized,
                        request_key=request_key,
                        **identity,
                    )
            except IntegrityError:
                existing = owner.filter(request_key=request_key).first() if request_key is not None else None
                if existing is None:
                    raise
                return replay(existing)
            evidence_model = apps.get_model("workflows", "StepRecord")
            evidence_model.objects.db_manager(owner.db).bulk_create(
                evidence_model(run=run, content_type=source.content_type, object_id=source.object_id)
                for source in sorted(targets, key=lambda item: (item.content_type.pk, item.object_id))
            )
            with owner.hold(run.pk) as locked:
                if (subject := locked._lock_run_subject()) is not None:
                    subject.admit_run(locked)
                locked.advance()
            return locked.with_actor(actor)

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
                    self._cancel_locked(children, actor=parent.run_as)

    def reap_interrupted(self) -> int:
        """Settle runs left running across a process restart and close their work."""

        owner = self.db_manager(write_alias(self.model, using=self._db))

        interrupted = 0
        with system_context(reason="workflows.reap_interrupted"):
            candidates = list(owner.filter(status=RunStatus.RUNNING).order_by("-pk").values_list("pk", flat=True))
            for run_id in candidates:
                with owner.hold(run_id, skip_locked=True) as run:
                    if run is None or run.status != RunStatus.RUNNING:
                        continue
                    run._write_state(
                        status=str(RunStatus.INTERRUPTED),
                        outcome=INTERRUPTED_OUTCOME,
                        output={},
                        actor=run.run_as,
                    )
                    run.step_runs.cancel_open()
                    interrupted += 1
        return interrupted

    def _cancel_locked(self, runs: list[Any], *, actor: Any) -> Cancellation:
        """Cancel open rows in an already-held tree without rewriting terminal facts."""
        steps = children = 0
        canceled = bool(runs and not runs[0].is_terminal)
        for index, run in enumerate(runs):
            transitioning, changed = run._cancel_owned_work(actor=actor)
            children += bool(index) if transitioning else 0
            steps += changed
        return Cancellation(canceled, steps, children)

    def cancel(self, run: Any, *, actor: Any = None, timeout: timedelta | None = timedelta(seconds=5)) -> Cancellation:
        """Cancel open rows after DATABASE work releases its lock, retaining terminal run facts."""

        owner = self.db_manager(write_alias(self.model, using=self._db or run._state.db, instance=run))

        if not owner.model.system_queryset().using(owner.db).filter(pk=run.pk).exists():
            return Cancellation(False, 0)
        actor = run.require_access("write", actor)
        try:
            with owner.hold_owned(run.pk, timeout=timeout) as runs:
                return owner._cancel_locked(runs, actor=actor or runs[0].run_as)
        except OperationalError as error:
            if _sqlstate(error) == "55P03":
                raise ValidationError("The run is still running; retry cancellation.") from error
            raise

    def reprocess(self, run: Any, *, actor: Any = None) -> Any:
        """Reprocess a terminal run on the current publication as its requesting operator."""

        owner = self.db_manager(write_alias(self.model, using=self._db or run._state.db, instance=run))

        actor = run.require_access("write", actor)
        with owner.hold(run.pk) as retained:
            with system_context(reason="workflows.reprocess"):
                if not retained.is_terminal:
                    raise ValidationError("Only terminal runs can be reprocessed.")
                workflow = retained.version.workflow
                model = retained.subject_model_class
                subject = (
                    model.system_queryset().using(owner.db).get(pk=retained.subject_object_id)
                    if model is not None
                    else None
                )
            return owner.start(workflow, actor=actor, subject=subject, input=retained.input, reprocess_of=retained)

    def prune(self) -> int:
        """Delete bounded old owned trees, marking protected roots for a later retry."""
        owner = self.db_manager(write_alias(self.model, using=self._db))

        count = 0
        with system_context(reason="workflows.retention"):
            candidates = list(
                owner.retention_candidates()
                .order_by("finished_at", "pk")
                .values_list("pk", flat=True)[:PRUNE_BATCH_LIMIT]
            )
            for run_id in candidates:
                with (
                    _record_failure(f"prune candidate {run_id}", using=owner.db),
                    owner.hold_owned(run_id, skip_locked=True) as runs,
                ):
                    if not runs or not owner.retention_candidates().filter(pk=run_id).exists():
                        continue
                    try:
                        with (
                            database_context(owner.db),
                            transaction.atomic(using=owner.db),
                            ExitStack() as continuation_locks,
                        ):
                            ids = [run.pk for run in runs]
                            if (
                                apps.get_model("decisions", "Decision")
                                .objects.db_manager(owner.db)
                                .filter(
                                    requesting_steps__run_id__in=ids,
                                )
                                .open()
                                .exists()
                            ):
                                raise ProtectedError("A decision is still open.", runs)
                            continuation_ids = (
                                owner.filter(
                                    parent_step__run_id__in=ids,
                                    relation=RunRelation.CONTINUATION,
                                )
                                .order_by("pk")
                                .values_list("pk", flat=True)
                            )
                            continuations = [
                                continuation_locks.enter_context(owner.hold(pk, skip_locked=True))
                                for pk in continuation_ids
                            ]
                            if any(run is None or not run.is_terminal for run in [*runs, *continuations]):
                                raise ProtectedError("A descendant is still running.", runs)
                            steps = runs[0].step_runs.model.objects.db_manager(owner.db).filter(run_id__in=ids)
                            # Internal wait references must not protect rows in the same deleted tree.
                            steps.update(awaited_run=None)
                            for run in reversed(runs):
                                owner.filter(pk=run.pk).delete()
                        count += len(runs)
                    except (ProtectedError, RestrictedError) as error:
                        owner.filter(pk=run_id).update(
                            prune_after=Now() + timedelta(days=1),
                            prune_reason=str(error.args[0]),
                            updated_at=Now(),
                        )
        return count


def unfinished_run_merge_guard(record: Any) -> str | None:
    """Refuse merging away a record an unfinished run works on (an ``ANGEE_MERGE_GUARDS`` entry).

    Retiring the merged record would cancel those runs while their watches move to
    the record that is kept.
    """
    with system_context(reason="workflows.unfinished_run_merge_guard"):
        if not apps.get_model("workflows", "WorkflowRun").objects.working_on(record).exists():
            return None
    return f"Workflow runs still working on {record} must finish or be canceled first."


class StepWatchManager[T: StepWatch](AngeeHistoryManager[T]):
    """Own transactional observation, durable change capture and wait admission."""

    def register(self, step_run: Any, records: tuple[Any, ...], *, actor: Any) -> None:
        """Observe saves after ordered target locks; precondition reads must also lock.

        Called inside a DATABASE body savepoint. Registration is immediately
        visible to that body's later saves and rolls back with a failed body.
        Callers needing an atomic predicate use ctx.load(..., lock=True) first.
        """
        owner = self.db_manager(write_alias(self.model, using=self._db or step_run._state.db, instance=step_run))

        targets = {}
        for record in records:
            TriggerSource.check_model(type(record))
            if record.pk is None:
                raise PermissionDenied("Read access to a saved watched record is required.")
            target = generic_target(record, using=owner.db)
            targets[(target.content_type.pk, target.object_id)] = target
        with system_context(reason="workflows.watch_register"):
            for _, target in sorted(targets.items()):
                model = target.content_type.model_class()
                assert model is not None
                model._default_manager.db_manager(owner.db).filter(pk=target.object_id).select_for_update(
                    no_key=True,
                    of=row_lock_paths(model),
                ).get()
            for record in records:
                readable = type(record).objects.db_manager(owner.db).with_actor(actor)
                if not readable.filter(pk=record.pk).exists():
                    raise PermissionDenied("Read access to a saved watched record is required.")
            for target in targets.values():
                owner.get_or_create(step_run=step_run, content_type=target.content_type, object_id=target.object_id)

    def wait_kind(self, step_run: Any, until: datetime | None) -> WaitingKind:
        """Admit a record wait, or require the time wait's explicit deadline."""
        with system_context(reason="workflows.watch_wait"):
            if self.filter(step_run=step_run).exists():
                return cast(WaitingKind, WaitingKind.RECORD)
        if until is None:
            raise ValidationError("A wait requires a deadline or a watched record.")
        return cast(WaitingKind, WaitingKind.TIME)

    def record_changes(self, records: tuple[Any, ...]) -> None:
        """Bound the existence probe, then retain each watched target's capture."""
        if not records:
            return
        owner = self.db_manager(write_alias(self.model, using=self._db or records[0]._state.db, instance=records[0]))

        if len(records) > 1:
            try:
                groups: dict[int, set[Any]] = {}
                for record in records:
                    target = generic_target(record, using=owner.db)
                    groups.setdefault(target.content_type.pk, set()).add(target.object_id)
                rows = owner.get_queryset()
                rows._for_write = True
                with system_context(reason="workflows.watch_capture"):
                    for content_type_id, keys in sorted(groups.items()):
                        watches = rows.filter(content_type_id=content_type_id).values_list("pk", flat=True)
                        if any(
                            watches.filter(object_id__in=chunk).first() is not None
                            for chunk in batched(sorted(keys), query_batch_size(watches))
                        ):
                            break
                    else:
                        return
            except Exception:
                logger.exception("Workflow watch capture failed.")
                return
        for record in records:
            owner.record_change(record)

    def record_change(self, record: Any) -> None:
        """Capture under the record lock, isolating failure from the source write."""
        owner = self.db_manager(write_alias(self.model, using=self._db or record._state.db, instance=record))

        try:
            try:
                target = generic_target(record, using=owner.db)
            except ValueError:
                return  # Unsaved or untyped records cannot have watches.
            with (
                database_context(owner.db),
                transaction.atomic(using=owner.db),
                system_context(reason="workflows.watch_capture"),
            ):
                model = target.content_type.model_class()
                assert model is not None
                rows = model._default_manager.db_manager(owner.db).filter(pk=target.object_id)
                if rows.select_for_update(no_key=True, of=row_lock_paths(model)).first() is None:
                    return
                if owner.filter(**target.lookups(owner.model, "record")).update(pending=True):
                    payload = {"content_type_id": target.content_type.pk, "object_id": target.object_id}
                    enqueue_task("workflows.wake_records", kwargs=payload, robust=True, using=owner.db)
        except Exception:
            logger.exception("Workflow watch capture failed.")


_StepRunManagerBase = ProtectedManager.from_queryset(StepRunQuerySet)


class StepRunManager[T: StepRun](_StepRunManagerBase[T]):
    """Own step row updates and operator retry under the run lock."""

    @cached_property
    def run_model(self) -> Any:
        """Resolve the related model for entrypoints that start from bare row ids."""
        return self.model._meta.get_field("run").related_model

    def record_await(self, step_run: Any, run_id: int) -> None:
        """Retain observed-run evidence inside an already-held DATABASE claim."""
        owner = self.db_manager(write_alias(self.model, using=self._db or step_run._state.db, instance=step_run))

        with system_context(reason="workflows.record_await"):
            if owner.fenced(step_run).update(awaited_run_id=run_id) != 1:
                raise Superseded

    def retry_step(self, step_run: Any, *, actor: Any = None, accept_duplicate: bool = False) -> Any:
        """Retry one failed or operator-waiting node, retaining all settled evidence."""
        owner = self.db_manager(write_alias(self.model, using=self._db or step_run._state.db, instance=step_run))

        with owner.run_model.objects.db_manager(owner.db).hold(step_run.run_id) as run:
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
                run.reopen()
                current.refresh_from_db()
                return current.with_actor(actor)


_TriggerEventManagerBase = ProtectedManager.from_queryset(TriggerEventQuerySet)


class TriggerEventManager[T: TriggerEvent](_TriggerEventManagerBase[T]):
    """Capture without compromising the transaction that changed the record."""

    def record_change(self, model: Any, record: Any, *, source: str = "record_changed") -> None:
        """Capture one source record through the same batch-aware owner."""
        owner = self.db_manager(write_alias(self.model, using=self._db or record._state.db, instance=record))

        owner.record_changes(model, (record,), source=source)

    def record_changes(self, model: Any, records: tuple[Any, ...], *, source: str = "record_changed") -> None:
        """Resolve a batch's source once; retain scoped, failure-isolated upserts."""
        if not records:
            return
        owner = self.db_manager(write_alias(self.model, using=self._db or records[0]._state.db, instance=records[0]))

        records = tuple(record for record in records if record.pk is not None)
        if not records:
            return
        try:
            triggers = apps.get_model("workflows", "Trigger")
            field = triggers._meta.get_field("source")
            if not isinstance(field, ImplClassField):
                raise ImproperlyConfigured("Workflow trigger sources require an ImplClassField.")
            implementation = cast(ImplClassField[TriggerSource], field).resolve_class(source)
            try:
                implementation.validate_model(model)
            except ValidationError:
                return
            queryset = triggers.system_queryset().using(owner.db).filter(enabled=True, source=source)
            if not implementation.model_label:
                queryset = queryset.filter(model_label=model._meta.label)
            if len(records) > 1:
                with system_context(reason="workflows.capture"):
                    if not queryset.exists():
                        return
            content_type = ContentType.objects.db_manager(owner.db).get_for_model(model)
        except Exception:
            logger.exception("Workflow trigger capture failed.")
            return
        for record in records:
            try:
                with (
                    database_context(owner.db),
                    transaction.atomic(using=owner.db),
                    system_context(reason="workflows.capture"),
                ):
                    for trigger in implementation.matching_triggers(queryset, record).order_by("pk"):
                        owner.model.system_queryset().using(owner.db).update_or_create(
                            trigger=trigger,
                            record_content_type=content_type,
                            record_object_id=record.pk,
                            defaults={"changed_at": Now()},
                        )
            except Exception:
                logger.exception("Workflow trigger capture failed.")


_TriggerManagerBase = ProtectedManager.from_queryset(TriggerQuerySet)


class TriggerManager[T: Trigger](_TriggerManagerBase[T]):
    """Enablement and admission serialize on the trigger before its ledger row."""

    def lock_grants(self, workflow_id: Any, *, skip_locked: bool = False) -> Any:
        """Serialize grants shared by triggers of one workflow."""
        workflow = apps.get_model("workflows", "Workflow")
        rows = (
            workflow.system_queryset()
            .using(self.db)
            .filter(pk=workflow_id)
            .lock_if_supported(
                no_key=True,
                skip_locked=skip_locked,
            )
        )
        return rows.first() if skip_locked else rows.get()

    def reconcile_grants(self, trigger: Any, targets: tuple[TriggerGrantTarget, ...]) -> None:
        """Reconcile this trigger's contributed tuples without dropping shared grants."""
        owner = self.db_manager(write_alias(self.model, using=self._db or trigger._state.db, instance=trigger))

        old = {TriggerGrantTarget.from_stored(value) for value in trigger.granted_targets}
        new = set(targets)
        workflow = apps.get_model("workflows", "Workflow").system_queryset().using(owner.db).get(pk=trigger.workflow_id)
        user = workflow.sync_service_user()
        subject = to_subject_ref(user)
        retained = {
            TriggerGrantTarget.from_stored(value)
            for values in owner.model.system_queryset()
            .using(owner.db)
            .filter(
                workflow_id=trigger.workflow_id,
                enabled=True,
            )
            .exclude(pk=trigger.pk)
            .values_list("granted_targets", flat=True)
            for value in values
        }

        def order(item: TriggerGrantTarget) -> tuple[str, str, str]:
            return item.resource.resource_type, str(item.resource.resource_id), item.relation

        with system_context(reason="workflows.trigger_grants"):
            if new:
                write_relationships(
                    [
                        RelationshipTuple(resource=target.resource, relation=target.relation, subject=subject)
                        for target in sorted(new, key=order)
                    ]
                )
            for target in sorted(old - new - retained, key=order):
                delete_relationship(
                    RelationshipTuple(
                        resource=target.resource,
                        relation=target.relation,
                        subject=subject,
                    )
                )
            stored = [target.stored() for target in sorted(new, key=order)]
            owner.model.system_queryset().using(owner.db).filter(pk=trigger.pk).update(granted_targets=stored)
            trigger.granted_targets = stored

    def _enable_targets(self, trigger: Any, actor: Any) -> tuple[TriggerGrantTarget, ...]:
        """Use one permission and source check for preview and enablement."""
        trigger.workflow.require_access("write", actor)
        trigger.validate_configuration()
        if not trigger.workflow.published_id:
            raise ValidationError("Publish the workflow before enabling its trigger.")
        trigger.source_class.check_access(trigger, actor)
        targets = tuple(trigger.source_class.grant_targets(trigger))
        if not targets:
            raise ValidationError("The trigger source must declare at least one grant target.")
        if len(set(targets)) != len(targets):
            raise ValidationError("The trigger source declared a duplicate grant target.")
        for target in targets:
            target.require_grant_access(actor)
        return targets

    def enable_preview(self, trigger: Any, *, actor: Any) -> TriggerEnablePreview | None:
        """Disclose source grants and workflow run readers only to eligible enablers."""
        owner = self.db_manager(write_alias(self.model, using=self._db or trigger._state.db, instance=trigger))

        if actor is None:
            return None
        try:
            targets = owner._enable_targets(trigger, actor)
        except PermissionDenied, ValidationError, ImproperlyConfigured:
            return None
        resource = to_object_ref(trigger.workflow)
        readers: list[str] = []
        for subject_type, label in ((user_subject_type(), "User"), (group_subject_type(), "Group")):
            refs = backend().lookup_subjects(resource=resource, action="monitor", subject_type=subject_type)
            with system_context(reason="workflows.enable_preview readers"):
                resolved = resolve_subjects(refs)
            readers.extend(
                f"{label}: {user_label(row) if subject_type == user_subject_type() else row}"
                for row in resolved.values()
            )
        return TriggerEnablePreview(
            grants=tuple(
                sorted(f"{target.relation_label()} on {target.target_label(actor=actor)}" for target in targets)
            ),
            run_readers=tuple(sorted(set(readers))),
        )

    def enable(self, trigger: Any, *, actor: Any) -> Any:
        """Require workflow write and source-declared delegation authority."""
        owner = self.db_manager(write_alias(self.model, using=self._db or trigger._state.db, instance=trigger))

        if is_sudo():
            return Context().run(owner.enable, trigger, actor=actor)
        if actor is None:
            raise PermissionDenied("Enabling a trigger requires an acting user.")
        trigger.workflow.require_access("write", actor)
        with database_context(owner.db), transaction.atomic(using=owner.db), actor_context(actor):
            owner.lock_grants(trigger.workflow_id)
            current = (
                owner.model.system_queryset().using(owner.db).filter(pk=trigger.pk).lock_if_supported(no_key=True).get()
            )
            current.with_actor(actor)
            targets = owner._enable_targets(current, actor)
            owner.reconcile_grants(current, targets)
            owner.model.system_queryset().using(owner.db).filter(pk=current.pk).update(
                enabled=True,
                disabled_reason="",
            )
            current.refresh_from_db()
            return current

    def disable(self, trigger: Any, *, actor: Any) -> Any:
        """Stop admission and release this trigger's source grants."""
        owner = self.db_manager(write_alias(self.model, using=self._db or trigger._state.db, instance=trigger))

        trigger.workflow.require_access("write", actor)
        with (
            database_context(owner.db),
            transaction.atomic(using=owner.db),
            system_context(reason="workflows.disable_trigger"),
        ):
            owner.lock_grants(trigger.workflow_id)
            current = (
                owner.model.system_queryset().using(owner.db).filter(pk=trigger.pk).lock_if_supported(no_key=True).get()
            )
            current.workflow.require_access("write", actor)
            owner._disable(current, "")
            current.refresh_from_db()
            return current

    def revoke_grant(
        self,
        trigger: Any,
        *,
        actor: Any,
        resource_type: str,
        resource_id: str,
        relation: str,
    ) -> Any:
        """Revoke one contribution; disable admission when its source still needs it."""
        owner = self.db_manager(write_alias(self.model, using=self._db or trigger._state.db, instance=trigger))

        if is_sudo():
            return Context().run(
                owner.revoke_grant,
                trigger,
                actor=actor,
                resource_type=resource_type,
                resource_id=resource_id,
                relation=relation,
            )
        trigger.workflow.require_access("write", actor)
        target = TriggerGrantTarget(ObjectRef(resource_type, resource_id), relation)
        with database_context(owner.db), transaction.atomic(using=owner.db), actor_context(actor):
            owner.lock_grants(trigger.workflow_id)
            current = (
                owner.model.system_queryset().using(owner.db).filter(pk=trigger.pk).lock_if_supported(no_key=True).get()
            )
            current.workflow.require_access("write", actor)
            held = {TriggerGrantTarget.from_stored(value) for value in current.granted_targets}
            if target not in held:
                raise ValidationError("This trigger does not hold the selected grant.")
            required = (
                next(
                    (candidate for candidate in current.source_class.grant_targets(current) if candidate == target),
                    None,
                )
                if current.enabled
                else None
            )
            if required is not None:
                required.require_grant_access(actor)
                reason = f"The workflow principal's {relation} grant was revoked; enable this trigger again."
                owner._disable(current, reason)
            else:
                owner.reconcile_grants(current, tuple(held - {target}))
            current.refresh_from_db()
            return current

    def _disable(self, trigger: Any, reason: str) -> None:
        self.model.system_queryset().using(self.db).filter(pk=trigger.pk).update(
            enabled=False,
            disabled_reason=reason,
        )
        self.reconcile_grants(trigger, ())

    def admit(self, event: Any) -> bool:
        """Lock the source record, trigger, then event and recheck current policy.

        Native saves already hold their record before capture. Sharing that order
        lets domain admission hooks write it without a record/event lock cycle.
        Busy records stay pending; deleted records proceed to a durable rejection.
        An isolated context drops a caller's ambient bypass before actor hooks.
        """
        owner = self.db_manager(write_alias(self.model, using=self._db or event._state.db, instance=event))

        if is_sudo():
            return Context().run(owner.admit, event)
        events = apps.get_model("workflows", "TriggerEvent")
        with database_context(owner.db), transaction.atomic(using=owner.db):
            event = (
                events.system_queryset()
                .using(owner.db)
                .filter(pk=event.pk)
                .select_related(
                    "record_content_type",
                    "trigger",
                )
                .first()
            )
            if event is None:
                return False
            record_model = event.record_content_type.model_class()
            if record_model is not None:
                records = record_model.system_queryset().using(owner.db).filter(pk=event.record_object_id)
                if records.lock_if_supported(no_key=True, skip_locked=True).first() is None and records.exists():
                    return False
            trigger = (
                owner.model.system_queryset()
                .using(owner.db)
                .filter(pk=event.trigger_id)
                .lock_if_supported(no_key=True, skip_locked=True)
                .first()
            )
            if trigger is None or not trigger.enabled:
                return False
            current = (
                events.objects.db_manager(owner.db)
                .system_context(reason="workflows.admit")
                .pending()
                .filter(pk=event.pk)
                .lock_if_supported(no_key=True, skip_locked=True)
                .first()
            )
            if current is None:
                return False
            try:
                model, condition = trigger.validate_configuration()
            except (ValidationError, ImproperlyConfigured, LookupError) as error:
                reason = exception_text(error)
                if owner.lock_grants(trigger.workflow_id, skip_locked=True) is None:
                    return False
                owner._disable(trigger, reason)
                events.system_queryset().using(owner.db).filter(pk=current.pk).update(
                    evaluated_at=F("changed_at"),
                    rejection=reason,
                )
                return False
            except Exception:
                logger.exception("Workflow trigger configuration failed for event %s.", current.pk)
                return False
            try:
                principal = trigger.workflow.user
                with database_context(owner.db), transaction.atomic(using=owner.db), actor_context(principal):
                    trigger.with_actor(principal)
                    if (
                        current.record_content_type_id
                        != ContentType.objects.db_manager(owner.db).get_for_model(model).pk
                    ):
                        raise ValidationError("The source model changed after this event was recorded.")
                    queryset = model.objects.db_manager(owner.db).with_actor(principal)
                    record = condition(queryset).filter(pk=current.record_object_id).first()
                    if record is None:
                        raise PermissionDenied("The record is inaccessible or no longer matches the condition.")
                    trigger.source_class.check_access(trigger, principal, record)
                    trigger.admit_record(record, actor=principal)
                    apps.get_model("workflows", "WorkflowRun").objects.db_manager(owner.db).start(
                        trigger.workflow,
                        actor=principal,
                        subject=record,
                        input=trigger.admission_input(record),
                        request_key=f"trigger:{public_id_of(trigger)}:{public_id_of(record)}",
                        trigger_event=current,
                    )
            except PublisherAuthorityDenied as error:
                reason = exception_text(error)
                if owner.lock_grants(trigger.workflow_id, skip_locked=True) is None:
                    return False
                owner._disable(trigger, reason)
                events.system_queryset().using(owner.db).filter(pk=current.pk).update(
                    evaluated_at=F("changed_at"),
                    rejection=reason,
                )
                return False
            except (ValidationError, PermissionDenied) as error:
                events.system_queryset().using(owner.db).filter(pk=current.pk).update(
                    evaluated_at=F("changed_at"),
                    rejection=exception_text(error),
                )
                return False
            except DatabaseError:
                logger.exception("Workflow trigger admission will retry event %s.", current.pk)
                return False
            except Exception:
                logger.exception("Workflow trigger admission failed for event %s.", current.pk)
                return False
            events.system_queryset().using(owner.db).filter(pk=current.pk).update(
                evaluated_at=F("changed_at"),
                admitted_at=Now(),
                rejection="",
            )
            return True

    def drain(self) -> int:
        """Examine an ordered, bounded batch; one broken event cannot stop later rows."""
        owner = self.db_manager(write_alias(self.model, using=self._db))

        events = apps.get_model("workflows", "TriggerEvent")
        candidates = list(
            events.objects.db_manager(owner.db)
            .system_context(reason="workflows.drain")
            .pending()
            .filter(trigger__enabled=True)
            .order_by("pk")[:TRIGGER_DRAIN_LIMIT]
        )
        admitted = 0
        for event in candidates:
            try:
                admitted += owner.admit(event)
            except Exception:
                logger.exception("Workflow trigger admission failed for event %s.", event.pk)
        return admitted
