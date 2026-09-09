"""Source models for workflow definitions.

The workflows addon owns graph definitions as data: a draft workflow lineage
head carries editable steps, edges, and triggers, while ``publish()`` copies that
draft into an immutable version. Step behavior remains in registry-selected
``StepImpl`` classes, so row data names keys and config, not Python callables.
Future runtime subject/artifact references use Django contenttypes-backed object
references; public ids stay at the transport boundary.
"""

from __future__ import annotations

import copy
import logging
import uuid
from collections.abc import Collection, Iterable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Self, cast

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.validators import validate_slug
from django.db import DEFAULT_DB_ALIAS, OperationalError, ProgrammingError, connections, models, router, transaction
from django.utils import timezone
from pydantic_core import PydanticSerializationError
from rebac import RelationshipTuple, SubjectRef, actor_context, system_context, write_relationships
from rebac.actors import NoActorResolvedError, to_subject_ref
from rebac.resources import to_object_ref

from angee.base.actors import actor_user_id
from angee.base.fields import StateField
from angee.base.impl import ImplClassField, ImplDefaultsMixin
from angee.base.mixins import AuditMixin
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet
from angee.base.refs import RecordRefMixin, canonical_record_target
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.base.transitions import StateTransitions, TransitionNotAllowed, save_state, transition
from angee.resources.mixins import ResourceLoadMixin, ResourceWritePreparation
from angee.workflows.attempts import (
    ArtifactSpec,
    AttemptCause,
    AttemptClaim,
    AttemptFinalization,
    AttemptInput,
    AttemptResult,
    AttemptResultKind,
    AttemptStatus,
    DecisionSpec,
    DecisionTimerIntent,
    DecisionTimerKind,
    InvocationAdmission,
    JsonPresence,
    LeaseRevocation,
    LeaseRevocationReason,
    MapExpansionPlan,
    MapItemSource,
    RecoveryCapability,
    RecoveryPlan,
    RetryIntent,
    deserialize_decision_specs,
    json_values_equal,
    map_child_input,
    serialize_decision_specs,
    validate_json_presence,
)
from angee.workflows.definitions import StaleDefinitionError, WorkflowDefinitionManagerMixin
from angee.workflows.dispatch import (
    DispatchConsumption,
    DispatchPreflight,
    DispatchPreflightDisposition,
    WorkflowDispatchEnvelope,
    WorkflowDispatchKind,
)
from angee.workflows.steps import (
    StepImpl,
    retry_policy_from_config,
)
from angee.workflows.test_contracts import (
    TestFixtureRole,
    TestFixtureSource,
    TestFixtureSourcePage,
    TestFixtureSourceSummary,
    TestFixtureSpec,
    TestScope,
    WorkflowTestRepairContext,
    WorkflowTestSetupPlan,
    validate_test_fixture_spec,
)
from angee.workflows.trigger_declarations import (
    EventAdmissionPolicy,
    EventTriggerConfig,
    ScheduleTriggerConfig,
    TriggerConfig,
    validate_trigger_config,
)

logger = logging.getLogger(__name__)
_CHANGE_FEED_FIX = "declare changes() for the model to join the change feed"


@dataclass
class _DefinitionWriteSession:
    """Private state for one locked, reentrant definition transaction."""

    alias: str
    connection_id: int
    workflow_ids: frozenset[int]
    changed_head_ids: set[int]
    copy_target_ids: set[int]


_definition_write_session: ContextVar[_DefinitionWriteSession | None] = ContextVar(
    "workflow_definition_write_session", default=None
)


@dataclass(frozen=True, slots=True)
class _AttemptWriteSession:
    alias: str
    connection_id: int
    step_run_id: int


_attempt_write_session: ContextVar[_AttemptWriteSession | None] = ContextVar(
    "workflow_attempt_write_session", default=None
)


@dataclass(slots=True)
class _AttemptSaveCapability:
    alias: str
    connection_id: int
    step_run_id: int
    instance_id: int
    pk: Any
    adding: bool
    consumed: bool = False


_attempt_save_capability: ContextVar[_AttemptSaveCapability | None] = ContextVar(
    "workflow_attempt_save_capability", default=None
)


@dataclass(slots=True)
class _StepRunSaveCapability:
    alias: str
    connection_id: int
    outer_atomic_id: int
    run_id: int
    step_run_id: int
    instance_id: int
    consumed: bool = False


_step_run_save_capability: ContextVar[_StepRunSaveCapability | None] = ContextVar(
    "workflow_step_run_save_capability", default=None
)


@dataclass(slots=True)
class _DecisionResolutionSession:
    alias: str
    connection_id: int
    outer_atomic_id: int
    decision_id: int
    completed: bool = False


_decision_resolution_session: ContextVar[_DecisionResolutionSession | None] = ContextVar(
    "workflow_decision_resolution_session", default=None
)


@dataclass(slots=True)
class _DecisionSaveCapability:
    alias: str
    connection_id: int
    outer_atomic_id: int
    decision_id: int
    instance_id: int
    consumed: bool = False


_decision_save_capability: ContextVar[_DecisionSaveCapability | None] = ContextVar(
    "workflow_decision_save_capability", default=None
)


@dataclass(frozen=True, slots=True)
class _DecisionWriteSession:
    alias: str
    connection_id: int
    step_run_id: int
    attempt_id: int
    declaration_index: int


_decision_write_session: ContextVar[_DecisionWriteSession | None] = ContextVar(
    "workflow_decision_write_session", default=None
)


@dataclass(slots=True)
class _ArtifactWriteCapability:
    alias: str
    connection_id: int
    outer_atomic_id: int
    attempt_id: int
    declaration_index: int
    instance_id: int
    consumed: bool = False


_artifact_write_capability: ContextVar[_ArtifactWriteCapability | None] = ContextVar(
    "workflow_artifact_write_capability", default=None
)


@dataclass(slots=True)
class _ArtifactBatchCapability:
    alias: str
    connection_id: int
    outer_atomic_id: int
    attempt_id: int
    rows: tuple[tuple[int, int, str], ...]
    consumed: bool = False


_artifact_batch_capability: ContextVar[_ArtifactBatchCapability | None] = ContextVar(
    "workflow_artifact_batch_capability", default=None
)


def _attempt_write_active(alias: str, step_run_id: int | None = None) -> bool:
    session = _attempt_write_session.get()
    connection = connections[alias]
    return (
        session is not None
        and session.alias == alias
        and session.connection_id == id(connection)
        and connection.in_atomic_block
        and (step_run_id is None or session.step_run_id == step_run_id)
    )


def _decision_write_active(alias: str, instance: Any) -> bool:
    session = _decision_write_session.get()
    connection = connections[alias]
    return (
        session is not None
        and session.alias == alias
        and session.connection_id == id(connection)
        and connection.in_atomic_block
        and instance._state.adding
        and instance.step_run_id == session.step_run_id
        and instance.suspension_attempt_id == session.attempt_id
        and instance.declaration_index == session.declaration_index
    )


def _combined_delete_results(*results: tuple[int, dict[str, int]]) -> tuple[int, dict[str, int]]:
    """Combine Django delete counts from explicitly owned cascade phases."""

    total = 0
    counts: dict[str, int] = {}
    for deleted, per_model in results:
        total += deleted
        for label, count in per_model.items():
            counts[label] = counts.get(label, 0) + count
    return total, counts


def _definition_rows(model: type[models.Model], alias: str) -> Any:
    """Return an internal unscoped queryset for ownership and lock verification."""

    return system_queryset(model, using=alias, lock=None)


def _definition_related_rows(manager: Any, owner: Any, alias: str) -> Any:
    """Bind internal child traversal to the caller's explicit actor or sudo intent."""

    queryset = manager.using(alias)
    if owner.is_sudo():
        return queryset.sudo(reason="workflows.definition_write.cascade")
    if actor := owner.actor():
        return queryset.with_actor(actor)
    return queryset


def _definition_caller_context(owner: Any) -> Any:
    """Project an explicit instance binding into Django's validation queries."""

    if owner.is_sudo():
        return system_context(reason="workflows.definition_write.validation")
    if actor := owner.actor():
        return actor_context(actor)
    return nullcontext()


def _bind_definition_caller(instance: Any, owner: Any) -> Any:
    """Carry an explicit caller binding onto a manager-fetched or copied row."""

    if owner.is_sudo():
        return instance.sudo(reason="workflows.definition_write.owner")
    if actor := owner.actor():
        return instance.with_actor(actor)
    return instance


class DefinitionQuerySet(AngeeQuerySet[Any]):
    """Definition collection writes that cannot preserve row invariants."""

    def update(self, **kwargs: Any) -> int:
        raise TypeError("Workflow definitions do not support QuerySet.update(); save instances instead.")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Workflow definitions do not support bulk_create(); save instances instead.")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        raise TypeError("Workflow definitions do not support bulk_update(); save instances instead.")

    def delete(self) -> tuple[int, dict[str, int]]:
        instances = list(self.order_by("pk"))
        if not instances:
            return (0, {})
        if isinstance(self, WorkflowQuerySet):
            workflow_model = self.model
            workflow_ids = [instance.pk for instance in instances]
        else:
            workflow_field = self.model._meta.get_field("workflow")
            workflow_model = workflow_field.remote_field.model
            workflow_ids = [instance.workflow_id for instance in instances]
        manager = workflow_model.objects.db_manager(self.db)
        with manager._definition_write(workflow_ids, using=self.db):
            total = 0
            counts: dict[str, int] = {}
            for instance in instances:
                deleted, per_model = instance.delete()
                total += deleted
                for label, count in per_model.items():
                    counts[label] = counts.get(label, 0) + count
            return total, counts


@dataclass(frozen=True, slots=True)
class StepConfigProjection:
    """Canonical authoring value and diagnostics for one persisted step config."""

    value: Any
    errors: dict[str, list[str]]


class WorkflowStatus(models.TextChoices):
    """Publication lifecycle for a workflow definition row."""

    DRAFT = "draft", "Draft"
    TEST = "test", "Test"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


class WorkflowPurpose(models.TextChoices):
    """Product purpose declared by a workflow lineage."""

    AUTOMATION = "automation", "Automation"
    AGENT_SESSION = "agent_session", "Agent session"


class RunOrigin(models.TextChoices):
    """Caller that created a workflow run."""

    UNKNOWN = "unknown", "Unknown"
    MANUAL = "manual", "Manual"
    TEST = "test", "Test"
    TRIGGER = "trigger", "Trigger"
    SESSION = "session", "Session"
    ERROR_WORKFLOW = "error_workflow", "Error workflow"
    RECOVERY = "recovery", "Recovery"


class WaitingKind(models.TextChoices):
    """Runtime reason a workflow step is waiting."""

    SCHEDULED = "scheduled", "Scheduled"
    APPROVAL = "approval", "Approval"
    EXTERNAL = "external", "External input"
    CHILDREN = "children", "Child steps"


class JoinRule(models.TextChoices):
    """How a step with multiple incoming edges activates over upstream siblings."""

    ALL_SUCCESS = "all_success", "All success"
    ONE_SUCCESS = "one_success", "One success"
    ONE_DONE = "one_done", "One done"
    ALL_DONE = "all_done", "All done"
    NONE_FAILED = "none_failed", "None failed"
    NONE_FAILED_MIN_ONE_SUCCESS = "none_failed_min_one_success", "None failed, at least one success"
    ALWAYS = "always", "Always"


class TriggerKind(models.TextChoices):
    """How a workflow lineage is started."""

    MANUAL = "manual", "Manual"
    EVENT = "event", "Event"
    SCHEDULE = "schedule", "Schedule"


class RunStatus(models.TextChoices):
    """Execution lifecycle for one pinned workflow run."""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    WAITING = "waiting", "Waiting"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"


class StepRunStatus(models.TextChoices):
    """Execution lifecycle for one step-run journal row."""

    SCHEDULED = "scheduled", "Scheduled"
    STARTED = "started", "Started"
    WAITING = "waiting", "Waiting"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"
    SKIPPED = "skipped", "Skipped"


class Verdict(models.TextChoices):
    """Resolution lifecycle for one awaited decision slot."""

    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"
    ESCALATED = "escalated", "Escalated"
    EXPIRED = "expired", "Expired"


RunStatus.TERMINAL = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED})
StepRunStatus.TERMINAL = frozenset(
    {StepRunStatus.SUCCEEDED, StepRunStatus.FAILED, StepRunStatus.CANCELED, StepRunStatus.SKIPPED}
)
StepRunStatus.ACTIVE = frozenset({StepRunStatus.SCHEDULED, StepRunStatus.STARTED, StepRunStatus.WAITING})
Verdict.TERMINAL = frozenset({Verdict.COMPLETED, Verdict.REJECTED, Verdict.ESCALATED, Verdict.EXPIRED})


def _save_workflow_status(instance: models.Model, source: Any, target: Any) -> None:
    """Persist a workflow status transition through the immutable-row variant."""

    workflow = cast("Workflow", instance)
    workflow._allow_immutable_status_save = True
    try:
        save_state(workflow, source, target)
    finally:
        del workflow._allow_immutable_status_save


#: Statuses that participate in version currency: a newer ARCHIVED row
#: supersedes older PUBLISHED rows, retiring the whole lineage.
_CURRENCY_STATUSES = (WorkflowStatus.PUBLISHED, WorkflowStatus.ARCHIVED)


class WorkflowQuerySet(DefinitionQuerySet):
    """QuerySet owning subject declaration discovery and version currency."""

    def current_published(self) -> Self:
        """Return rows that are the current published version of their lineage.

        The currency rule's single owner: a row survives when it is PUBLISHED
        and no ``_CURRENCY_STATUSES`` sibling in the same lineage is newer by
        ``(version, pk)`` — so a newer ARCHIVED row retires the lineage.
        """

        workflow_model = cast(Any, self.model)
        newer_version = (
            workflow_model.objects
            .sudo(reason="workflows.subject_declaration.current")
            .filter(
                published_from_id=models.OuterRef("published_from_id"),
                status__in=_CURRENCY_STATUSES,
            )
            .filter(
                models.Q(version__gt=models.OuterRef("version"))
                | models.Q(version=models.OuterRef("version"), pk__gt=models.OuterRef("pk"))
            )
        )
        return cast(
            Self,
            self.filter(status=WorkflowStatus.PUBLISHED).filter(~models.Exists(newer_version)),
        )

    def for_subject_declaration(self, subject_declaration: str) -> Self:
        """Return current published workflows accepting ``subject_declaration``."""

        declaration = subject_declaration.strip().lower()
        return cast(
            Self,
            self.current_published()
            .filter(purpose=WorkflowPurpose.AUTOMATION)
            .filter(
                models.Q(subject_declaration="")
                | models.Q(subject_declaration=declaration)
            )
            .order_by("name", "version", "pk"),
        )

    def with_lineage_projection(self) -> Self:
        """Annotate lineage and current-publication context for every row."""

        workflow_model = cast(type[Workflow], self.model)
        return cast(Self, self.annotate(**workflow_model.lineage_projection_annotation()))


class WorkflowManager(WorkflowDefinitionManagerMixin, AngeeManager.from_queryset(WorkflowQuerySet)):  # type: ignore[misc]
    """Manager owning workflow lineage lookups."""

    @contextmanager
    def _definition_write(
        self,
        workflow_ids: Iterable[int],
        *,
        using: str | None = None,
        _allow_status_transition: bool = False,
    ) -> Iterator[list[Any]]:
        """Lock declared lineages and share one revision owner across nested writes."""

        alias = using or self.db
        ids = frozenset(workflow_ids)
        active = _definition_write_session.get()
        connection_id = id(connections[alias])
        if active is not None:
            if active.alias != alias or active.connection_id != connection_id:
                raise RuntimeError("A workflow definition write cannot span database connections.")
            undeclared = ids - active.workflow_ids - active.copy_target_ids
            if undeclared:
                raise RuntimeError("A workflow definition write cannot expand to a new lineage after locking.")
            rows = list(system_queryset(self.model, using=alias, lock=None).filter(pk__in=ids).order_by("pk"))
            if len(rows) != len(ids):
                raise ValidationError("A workflow definition parent no longer exists.")
            if not _allow_status_transition and any(
                row.is_immutable and row.pk not in active.copy_target_ids for row in rows
            ):
                raise ValidationError("Published workflow versions are immutable.")
            yield rows
            return

        ordered_ids = sorted(ids)
        with transaction.atomic(using=alias):
            rows = list(
                system_queryset(self.model, using=alias, lock=("self",))
                .filter(pk__in=ordered_ids)
                .order_by("pk")
            )
            if len(rows) != len(ordered_ids):
                raise ValidationError("A workflow definition parent no longer exists.")
            if not _allow_status_transition and any(row.is_immutable for row in rows):
                raise ValidationError("Published workflow versions are immutable.")
            session = _DefinitionWriteSession(alias, connection_id, ids, set(), set())
            token = _definition_write_session.set(session)
            try:
                yield rows
                for workflow_id in sorted(session.changed_head_ids):
                    models.QuerySet.update(
                        self.model._base_manager.using(alias).filter(
                            pk=workflow_id, published_from__isnull=True
                        ),
                        draft_revision=models.F("draft_revision") + 1,
                    )
            finally:
                _definition_write_session.reset(token)

    def mark_definition_changed(self, workflow_id: int) -> None:
        """Record one editable lineage as changed in the active write session."""

        session = _definition_write_session.get()
        if session is None or workflow_id not in session.workflow_ids:
            raise RuntimeError("Definition changes require an active locked write session.")
        session.changed_head_ids.add(workflow_id)

    def _definition_revision(self, workflow_id: int, current: int) -> int:
        """Return the exact revision this active locked session will commit."""

        session = _definition_write_session.get()
        if session is None or workflow_id not in session.workflow_ids:
            raise RuntimeError("Definition revision projection requires its active locked write session.")
        return current + int(workflow_id in session.changed_head_ids)

    @contextmanager
    def _definition_caller(self, workflow: Any) -> Iterator[None]:
        """Project a command target's explicit actor or sudo binding to nested ORM work."""

        with _definition_caller_context(workflow):
            yield

    @contextmanager
    def _definition_read(self, workflow_id: int, *, using: str | None = None) -> Iterator[Any]:
        """Lock one mutable or immutable definition for a coherent snapshot read."""

        alias = using or self.db
        with transaction.atomic(using=alias):
            yield system_queryset(self.model, using=alias, lock=("self",)).get(pk=workflow_id)

    @contextmanager
    def _copy_to(self, workflow_id: int) -> Iterator[None]:
        """Permit inserts into one new publication within the locked session."""

        session = _definition_write_session.get()
        if session is None:
            raise RuntimeError("Publication copying requires an active definition write session.")
        session.copy_target_ids.add(workflow_id)
        try:
            yield
        finally:
            session.copy_target_ids.remove(workflow_id)

    def current_published_for(self, workflow: Any) -> Any | None:
        """Return the latest published version for ``workflow``'s lineage.

        Composes the same ``_CURRENCY_STATUSES`` rule ``current_published``
        owns, scoped to one explicit lineage pool.
        """

        head = workflow if getattr(workflow, "published_from_id", None) is None else workflow.published_from
        latest = (
            self.filter(status__in=_CURRENCY_STATUSES)
            .filter(models.Q(pk=head.pk) | models.Q(published_from=head))
            .order_by("-version", "-pk")
            .first()
        )
        if latest is None or latest.status != WorkflowStatus.PUBLISHED:
            return None
        return latest

    def test_snapshot(
        self, workflow: Any, *, expected_revision: int, require_readiness: bool = True
    ) -> Any:
        """Return the immutable test copy of one exact saved draft revision."""

        self._validate_expected_revision(workflow, expected_revision)
        if workflow.published_from_id is not None:
            raise ValidationError({"workflow": "Test snapshots can only be taken from a lineage head."})
        alias = self.db
        with system_context(reason="workflows.test_snapshot"), self._definition_write(
            (workflow.pk,), using=alias
        ):
            draft = cast(
                Workflow,
                _bind_definition_caller(_definition_rows(self.model, alias).get(pk=workflow.pk), workflow),
            )
            if draft.draft_revision != expected_revision:
                raise StaleDefinitionError(expected=expected_revision, current=draft.draft_revision)
            existing = system_queryset(self.model, using=alias, lock=None).filter(
                published_from=draft,
                status=WorkflowStatus.TEST,
                draft_revision=expected_revision,
            ).first()
            if existing is not None:
                return existing
            if require_readiness:
                with _definition_caller_context(draft):
                    draft._validate_publishable()
            snapshot = draft._new_definition_copy(
                version=0,
                draft_revision=expected_revision,
            )
            _bind_definition_caller(snapshot, draft)
            snapshot.save(using=alias)
            with self._copy_to(snapshot.pk):
                with _definition_caller_context(draft):
                    draft._copy_definition_to(snapshot)
                snapshot.mark_test()
            return snapshot


class WorkflowRunQuerySet(AngeeQuerySet[Any]):
    """QuerySet owning workflow-run subject lookups."""

    def for_subject(self, subject: Any) -> Self:
        """Return runs whose generic subject is ``subject``."""

        content_type = ContentType.objects.get_for_model(subject, for_concrete_model=False)
        return cast(
            Self,
            self.filter(
                subject_content_type=content_type,
                subject_object_id=subject.pk,
            ),
        )

    def update(self, **kwargs: Any) -> int:
        """Keep creation-time input presence outside collection mutation paths."""

        if {
            "input", "input_present", "occurrence_id",
            "test_repair_source_attempt", "test_repair_source_attempt_id",
        } & kwargs.keys():
            raise TypeError("Workflow run creation facts are immutable.")
        identity_fields = {
            "workflow",
            "workflow_id",
            "origin",
            "subject_content_type",
            "subject_content_type_id",
            "subject_object_id",
            "test_request_actor_ref",
            "test_scope",
            "test_step",
            "test_step_id",
            "test_source_step_id",
            "test_repair_source_attempt",
            "test_repair_source_attempt_id",
            "recovery_source_attempt",
            "recovery_source_attempt_id",
            "recovery_request_actor_ref",
            "recovery_mode",
        }
        with system_context(reason="workflows.runs.test_identity_guard"):
            targets_test = models.QuerySet.filter(
                self, origin__in=(RunOrigin.TEST, RunOrigin.RECOVERY)
            ).exists()
        creates_test = "origin" in kwargs and str(kwargs["origin"]) in {
            str(RunOrigin.TEST), str(RunOrigin.RECOVERY)
        }
        if identity_fields & kwargs.keys() and (targets_test or creates_test):
            raise TypeError("Workflow test and recovery request identity is immutable.")
        return super().update(**kwargs)

    def bulk_update(
        self, objs: Iterable[Any], fields: Iterable[str], batch_size: int | None = None
    ) -> int:
        """Keep creation-time input presence outside bulk mutation paths."""

        field_names = tuple(fields)
        if {
            "input", "input_present", "occurrence_id",
            "test_repair_source_attempt", "test_repair_source_attempt_id",
        } & set(field_names):
            raise TypeError("Workflow run creation facts are immutable.")
        rows = list(objs)
        identity_fields = {
            "workflow",
            "workflow_id",
            "origin",
            "subject_content_type",
            "subject_content_type_id",
            "subject_object_id",
            "test_request_actor_ref",
            "test_scope",
            "test_step",
            "test_step_id",
            "test_source_step_id",
            "test_repair_source_attempt",
            "test_repair_source_attempt_id",
            "recovery_source_attempt",
            "recovery_source_attempt_id",
            "recovery_request_actor_ref",
            "recovery_mode",
        }
        targets_test = system_queryset(self.model, using=self.db, lock=None).filter(
            pk__in=[row.pk for row in rows], origin__in=(RunOrigin.TEST, RunOrigin.RECOVERY)
        ).exists()
        creates_test = "origin" in field_names and any(
            str(row.origin) in {str(RunOrigin.TEST), str(RunOrigin.RECOVERY)} for row in rows
        )
        if identity_fields & set(field_names) and (targets_test or creates_test):
            raise TypeError("Workflow test and recovery request identity is immutable.")
        return super().bulk_update(rows, field_names, batch_size=batch_size)


class WorkflowRunManager(AngeeManager.from_queryset(WorkflowRunQuerySet)):  # type: ignore[misc]
    """Own immutable publication pinning and initial durable execution state."""

    def start(
        self,
        workflow: Any,
        subject: Any,
        actor: Any,
        *,
        trigger: Any = None,
        parent_step_run: Any = None,
        dedup_key: str | None = None,
        origin: RunOrigin | None = None,
        input: JsonPresence = JsonPresence(),
        available_at: datetime | None = None,
    ) -> Any:
        """Create a pinned run, entry journal row and first ADVANCE atomically."""

        input = validate_json_presence(input, label="workflow run input")
        alias = self.db
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        head_id = workflow.pk if workflow.published_from_id is None else workflow.published_from_id
        step_run_model = self.model._meta.apps.get_model("workflows", "StepRun")
        parent_run_id = (
            None
            if parent_step_run is None
            else system_queryset(step_run_model, using=alias, lock=None)
            .values_list("run_id", flat=True)
            .get(pk=parent_step_run.pk)
        )
        with system_context(reason="workflows.runs.start"), transaction.atomic(using=alias):
            locked_parent = None
            if parent_run_id is not None:
                system_queryset(self.model, using=alias, lock=("self",)).get(pk=parent_run_id)
                locked_parent = system_queryset(step_run_model, using=alias, lock=("self",)).get(
                    pk=parent_step_run.pk
                )
                if locked_parent.run_id != parent_run_id:
                    raise OperationalError("Parent workflow step changed while locking.")
            head = system_queryset(workflow_model, using=alias, lock=("self",)).get(pk=head_id)
            locked_trigger = None
            if trigger is not None:
                trigger_model = self.model._meta.get_field("trigger").remote_field.model
                locked_trigger = system_queryset(trigger_model, using=alias, lock=("self",)).get(pk=trigger.pk)
                if locked_trigger.workflow_id != head.pk:
                    raise ValidationError({"trigger": "Workflow trigger does not belong to this lineage."})
                if not locked_trigger.enabled:
                    raise ValidationError({"trigger": "Workflow trigger is disabled."})
            return self._start_locked(
                head,
                subject,
                actor,
                trigger=locked_trigger,
                parent_step_run=locked_parent,
                dedup_key=dedup_key,
                origin=origin,
                input=input,
                available_at=available_at or timezone.now(),
                using=alias,
            )

    def _start_locked(
        self,
        head: Any,
        subject: Any,
        actor: Any,
        *,
        trigger: Any = None,
        parent_step_run: Any = None,
        dedup_key: str | None = None,
        occurrence_id: str | None = None,
        origin: RunOrigin | None = None,
        input: JsonPresence = JsonPresence(),
        available_at: datetime,
        using: str,
    ) -> Any:
        """Create initial rows after callers lock the exact lineage and trigger."""

        connection = connections[using]
        if not connection.in_atomic_block:
            raise RuntimeError("Pinned workflow start requires its owning transaction.")
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        version = workflow_model.objects.current_published_for(head)
        if version is None:
            raise ValidationError({"workflow": "Workflow has no published version to start."})
        if version.status != WorkflowStatus.PUBLISHED:
            raise ValidationError({"workflow": "Workflow runs must pin a published version."})
        return self._start_pinned_locked(
            version,
            subject,
            actor,
            trigger=trigger,
            parent_step_run=parent_step_run,
            dedup_key=dedup_key,
            occurrence_id=occurrence_id,
            origin=origin,
            input=input,
            available_at=available_at,
            using=using,
        )

    def start_test(
        self,
        workflow: Any,
        *,
        expected_revision: int,
        request_key: str,
        subject: Any,
        actor: Any,
        input: JsonPresence = JsonPresence(),
        scope: TestScope = TestScope.WHOLE,
        selected_step: Any = None,
        fixtures: tuple[TestFixtureSpec, ...] = (),
        repair_source_attempt: Any = None,
    ) -> Any:
        """Start or recover one idempotent whole-workflow test request."""

        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        workflow_model.objects._validate_expected_revision(workflow, expected_revision)
        input = validate_json_presence(input, label="workflow run input")
        if not isinstance(scope, TestScope):
            raise ValidationError({"scope": "Test launches require a declared scope."})
        if (scope is TestScope.NODE) != (selected_step is not None):
            raise ValidationError({"selected_step": "Node tests require exactly one selected step."})
        if not isinstance(request_key, str) or not request_key.strip():
            raise ValidationError({"request_key": "Test launch request keys must be non-empty strings."})
        head_id = workflow.pk if workflow.published_from_id is None else workflow.published_from_id
        dedup_key = f"test:{head_id}:{request_key}"
        if len(dedup_key) > self.model._meta.get_field("dedup_key").max_length:
            raise ValidationError({"request_key": "Test launch request key is too long."})
        alias = self.db
        authorized = read_scoped_queryset(workflow_model, actor, action="write")
        if authorized is None or not authorized.filter(pk=workflow.pk).exists():
            raise PermissionDenied("Test workflow access was denied.")
        repair_context = None
        if repair_source_attempt is not None:
            repair_context = self.test_repair_context(repair_source_attempt, actor=actor)
            if repair_context.draft_workflow_id != workflow_model.public_id_from_pk(head_id):
                raise ValidationError(
                    {"repair_source_attempt": "Repair evidence belongs to another workflow lineage."}
                )
            if (
                scope is not TestScope.NODE
                or selected_step is None
                or repair_context.current_source_step_id != str(selected_step.sqid)
            ):
                raise ValidationError(
                    {"repair_source_attempt": "Repair tests must select the source operation in the current draft."}
                )
        with system_context(reason="workflows.runs.start_test"), transaction.atomic(using=alias):
            head = system_queryset(workflow_model, using=alias, lock=("self",)).get(pk=head_id)
            requested = head
            if workflow.published_from_id is not None:
                requested = system_queryset(workflow_model, using=alias, lock=("self",)).get(pk=workflow.pk)
                if requested.status != WorkflowStatus.TEST:
                    raise ValidationError({"workflow": "Test runs require a draft head or test snapshot."})
                if requested.published_from_id != head.pk or requested.draft_revision != expected_revision:
                    raise StaleDefinitionError(expected=expected_revision, current=requested.draft_revision)
            elif head.status != WorkflowStatus.DRAFT:
                raise ValidationError({"workflow": "Test runs require a draft head or test snapshot."})
            try:
                actor_ref = str(to_subject_ref(actor))
            except NoActorResolvedError as error:
                raise PermissionDenied("Test launches require an effective actor.") from error
            existing = self.select_related("workflow", "test_step").filter(dedup_key=dedup_key).first()
            if existing is not None:
                if existing.test_request_actor_ref != actor_ref:
                    raise PermissionDenied("Test launch request is owned by another actor.")
                selected_identity = selected_step.pk if selected_step is not None else None
                if existing.test_source_step_id != selected_identity:
                    raise ValidationError({"request_key": "Test launch selected-step facts do not match."})
                if existing.test_repair_source_attempt_id != getattr(
                    repair_source_attempt, "pk", None
                ):
                    raise ValidationError({"request_key": "Test launch repair evidence does not match."})
                from angee.workflows.graph import WorkflowGraph

                retry_graph = WorkflowGraph.from_workflow(existing.workflow)
                retry_rows = self._resolve_test_fixture_rows(
                    existing.workflow,
                    scope=scope,
                    selected_step=existing.test_step,
                    specs=fixtures,
                    actor=actor,
                    graph=retry_graph,
                )
                self._validate_test_retry(
                    existing,
                    expected_revision=expected_revision,
                    subject=subject,
                    input=input,
                    requested_snapshot_id=(requested.pk if requested.status == WorkflowStatus.TEST else None),
                    scope=scope,
                    selected_step_key=existing.test_step.key if existing.test_step_id else None,
                    fixture_rows=retry_rows,
                )
                return existing
            selected_key = None
            if selected_step is not None:
                step_model = workflow_model._meta.apps.get_model("workflows", "Step")
                try:
                    locked_selected = system_queryset(step_model, using=alias, lock=("self",)).get(
                        pk=selected_step.pk,
                        workflow=requested,
                    )
                except (ObjectDoesNotExist, TypeError, ValueError) as error:
                    raise ValidationError(
                        {"selected_step": "The selected step must belong to this exact workflow revision."}
                    ) from error
                selected_key = locked_selected.key
                if repair_context is not None and (
                    repair_context.current_source_step_id != str(locked_selected.sqid)
                    or repair_context.source_step_key != locked_selected.key
                ):
                    raise ValidationError(
                        {"repair_source_attempt": "Repair source identity changed; reload and try again."}
                    )
            if workflow.published_from_id is None:
                snapshot = workflow_model.objects.test_snapshot(
                    head,
                    expected_revision=expected_revision,
                    require_readiness=False,
                )
            else:
                snapshot = requested
            copied_test_step = None
            if selected_key is not None:
                copied_test_step = snapshot.steps.get(key=selected_key)
            from angee.workflows.graph import WorkflowGraph

            graph = WorkflowGraph.from_workflow(snapshot)
            fixture_rows = self._resolve_test_fixture_rows(
                snapshot,
                scope=scope,
                selected_step=copied_test_step,
                specs=fixtures,
                actor=actor,
                graph=graph,
            )
            self._validate_test_scope_readiness(
                snapshot,
                scope=scope,
                selected_step=copied_test_step,
                fixture_rows=fixture_rows,
                graph=graph,
            )
            return self._start_pinned_locked(
                snapshot,
                subject,
                actor,
                dedup_key=dedup_key,
                origin=cast(RunOrigin, RunOrigin.TEST),
                input=input,
                test_request_actor_ref=actor_ref,
                test_scope=scope,
                test_step=copied_test_step,
                test_source_step_id=locked_selected.pk if selected_step is not None else None,
                test_repair_source_attempt=repair_source_attempt,
                test_fixture_rows=fixture_rows,
                available_at=timezone.now(),
                using=alias,
            )

    def start_recovery(
        self,
        source_attempt: Any,
        *,
        request_key: str,
        actor: Any,
    ) -> Any:
        """Admit one linked same-revision recovery from exact retained evidence."""

        if not isinstance(request_key, str) or not request_key.strip():
            raise ValidationError({"request_key": "Recovery request keys must be non-empty strings."})
        alias = self.db
        attempt_model = self.model._meta.apps.get_model("workflows", "StepAttempt")
        step_run_model = self.model._meta.apps.get_model("workflows", "StepRun")
        source_run_id = system_queryset(attempt_model, using=alias, lock=None).values_list(
            "step_run__run_id", flat=True
        ).get(pk=source_attempt.pk)
        readable = read_scoped_queryset(self.model, actor, action="read")
        if readable is None or not readable.filter(pk=source_run_id).exists():
            raise PermissionDenied("Recovery source evidence is unavailable.")
        capability_source = (
            system_queryset(attempt_model, using=alias, lock=None)
            .select_related("step_run__step")
            .get(pk=source_attempt.pk)
        )
        if capability_source.step_run.step_id is None:
            raise ValidationError({"attempt": "Recovery source execution is unavailable."})
        capability_step_class = capability_source.step_run.step.step_class
        capability_config = copy.deepcopy(capability_source.step_run.step.config)
        impl = capability_source.step_run.step.resolve_impl("step_class")
        capability = impl.recovery_capability(attempt=capability_source)
        if not capability.available:
            raise ValidationError({"attempt": capability.unavailable_reason})
        with system_context(reason="workflows.runs.start_recovery"), transaction.atomic(using=alias):
            source_run = system_queryset(self.model, using=alias, lock=("self",)).select_related(
                "workflow"
            ).get(pk=source_run_id)
            writable_workflows = read_scoped_queryset(
                type(source_run.workflow), actor, action="write"
            )
            if writable_workflows is None or not writable_workflows.filter(
                pk=source_run.workflow_id
            ).exists():
                raise PermissionDenied("Recovery workflow access was denied.")
            source_subject = source_run.subject
            if source_subject is not None:
                readable_subjects = read_scoped_queryset(
                    type(source_subject), actor, action="read"
                )
                if readable_subjects is None or not readable_subjects.filter(
                    pk=source_subject.pk
                ).exists():
                    raise PermissionDenied("Recovery subject access was denied.")
            locked_step_runs = list(
                system_queryset(step_run_model, using=alias, lock=("self",))
                .select_related("step")
                .filter(run=source_run)
                .order_by("pk")
            )
            source_step_run = next(
                (row for row in locked_step_runs if row.pk == source_attempt.step_run_id),
                None,
            )
            if source_step_run is None:
                raise ValidationError({"attempt": "Recovery source execution is unavailable."})
            locked_attempt = system_queryset(attempt_model, using=alias, lock=("self",)).get(
                pk=source_attempt.pk
            )
            if (
                source_step_run.run_id != source_run.pk
                or source_step_run.step_id is None
                or locked_attempt.step_run_id != source_step_run.pk
                or source_step_run.step.step_class != capability_step_class
                or not json_values_equal(source_step_run.step.config, capability_config)
                or source_step_run.current_attempt_id != locked_attempt.pk
                or source_step_run.status not in {StepRunStatus.FAILED, StepRunStatus.CANCELED}
                or not (
                    (
                        locked_attempt.result_kind in {
                            str(AttemptResultKind.ERROR),
                            str(AttemptResultKind.NO_RESULT),
                            str(AttemptResultKind.PREPARATION_ERROR),
                            str(AttemptResultKind.TRANSIENT_ERROR),
                        }
                        and locked_attempt.applied_at is not None
                    )
                    or (
                        locked_attempt.result_recorded_at is None
                        and locked_attempt.lease_revoked_at is not None
                    )
                )
            ):
                raise ValidationError({"attempt": "Recovery requires an applied retained failure."})
            try:
                actor_ref = str(to_subject_ref(actor))
            except NoActorResolvedError as error:
                raise PermissionDenied("Recovery requires an effective actor.") from error
            dedup_key = f"recovery:{source_run.pk}:{locked_attempt.pk}:{request_key}"
            existing = self.filter(dedup_key=dedup_key).first()
            if existing is not None:
                if (
                    existing.recovery_source_attempt_id != locked_attempt.pk
                    or existing.recovery_request_actor_ref != actor_ref
                    or existing.recovery_mode != str(capability.mode)
                ):
                    raise ValidationError({"request_key": "Recovery request facts do not match."})
                return existing
            from angee.workflows.graph import GraphIdentity, WorkflowGraph

            recovery_graph = WorkflowGraph.from_workflow(source_run.workflow)
            accepted_step_ids = {
                source.node_identity.existing_id
                for source in recovery_graph.input_sources(
                    GraphIdentity(existing_id=source_step_run.step_id)
                )
                if source.node_identity is not None
                and source.node_identity.existing_id is not None
            }
            accepted_step_runs = [
                row for row in locked_step_runs if row.step_id in accepted_step_ids
            ]
            accepted = list(
                system_queryset(attempt_model, using=alias, lock=("self",))
                .select_related("step_run__step")
                .filter(
                    step_run__in=accepted_step_runs,
                    step_run__status=StepRunStatus.SUCCEEDED,
                    step_run__current_attempt=models.F("pk"),
                    effect_key=models.F("step_run__effect_key"),
                    effect_generation=models.F("step_run__effect_generation"),
                    result_kind=AttemptResultKind.DONE,
                    applied_at__isnull=False,
                    lease_revoked_at__isnull=True,
                )
                .exclude(step_run=source_step_run)
                .order_by("pk")
            )
            return self._start_pinned_locked(
                source_run.workflow,
                source_subject,
                actor,
                dedup_key=dedup_key,
                origin=cast(RunOrigin, RunOrigin.RECOVERY),
                input=JsonPresence(source_run.input_present, source_run.input),
                recovery_source_attempt=locked_attempt,
                recovery_request_actor_ref=actor_ref,
                recovery_mode=str(capability.mode),
                recovery_evidence=tuple(accepted),
                recovery_step=source_step_run.step,
                recovery_map_index=source_step_run.map_index,
                available_at=timezone.now(),
                using=alias,
            )

    def test_setup_plan(
        self,
        workflow: Any,
        *,
        expected_revision: int,
        actor: Any,
        subject: Any = None,
        input: JsonPresence = JsonPresence(),
        scope: TestScope = TestScope.WHOLE,
        selected_step: Any = None,
        fixtures: tuple[TestFixtureSpec, ...] = (),
        previous_run: Any = None,
    ) -> WorkflowTestSetupPlan:
        """Project the exact graph, fixture and effect facts used by test admission."""

        from angee.workflows.graph import GraphFreshnessReason, GraphIdentity, WorkflowGraph

        if not isinstance(scope, TestScope):
            raise ValidationError({"scope": "Test plans require a declared scope."})
        if (scope is TestScope.NODE) != (selected_step is not None):
            raise ValidationError({"selected_step": "Node test plans require exactly one selected step."})
        input = validate_json_presence(input, label="workflow run input")
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        authorized = read_scoped_queryset(workflow_model, actor, action="write")
        if authorized is None or not authorized.filter(pk=workflow.pk).exists():
            raise PermissionDenied("Test workflow access was denied.")
        alias = self.db
        with system_context(reason="workflows.runs.test_setup_plan"), transaction.atomic(using=alias):
            head_id = workflow.pk if workflow.published_from_id is None else workflow.published_from_id
            head = system_queryset(workflow_model, using=alias, lock=("self",)).get(pk=head_id)
            requested = head
            if workflow.published_from_id is not None:
                requested = system_queryset(workflow_model, using=alias, lock=("self",)).get(pk=workflow.pk)
                if requested.status != WorkflowStatus.TEST or requested.published_from_id != head.pk:
                    raise ValidationError({"workflow": "Test plans require a draft head or test snapshot."})
            elif head.status != WorkflowStatus.DRAFT:
                raise ValidationError({"workflow": "Test plans require a draft head or test snapshot."})
            if requested.draft_revision != expected_revision:
                raise StaleDefinitionError(expected=expected_revision, current=requested.draft_revision)
            requested.validate_subject_declaration(subject)
            locked_selected = None
            if selected_step is not None:
                step_model = workflow_model._meta.apps.get_model("workflows", "Step")
                try:
                    locked_selected = system_queryset(step_model, using=alias, lock=("self",)).get(
                        pk=selected_step.pk,
                        workflow=requested,
                    )
                except (ObjectDoesNotExist, TypeError, ValueError) as error:
                    raise ValidationError(
                        {"selected_step": "The selected step must belong to this exact workflow revision."}
                    ) from error
            graph = WorkflowGraph.from_workflow(requested)
            fixture_rows = self._resolve_test_fixture_rows(
                requested,
                scope=scope,
                selected_step=locked_selected,
                specs=fixtures,
                actor=actor,
                graph=graph,
                require_complete=False,
            )
            output_slots = frozenset(
                (GraphIdentity(existing_id=row.step_id), row.item_index)
                for row in fixture_rows
                if row.role == TestFixtureRole.OUTPUT
            )
            plan = graph.test_execution_plan(
                selected_identity=(
                    GraphIdentity(existing_id=locked_selected.pk)
                    if locked_selected is not None
                    else None
                ),
                output_slots=output_slots,
                map_item_slots=frozenset(
                    (GraphIdentity(existing_id=row.step_id), row.item_index)
                    for row in fixture_rows
                    if row.role == TestFixtureRole.MAP_ITEM and row.item_index is not None
                ),
            )
            freshness = (
                graph.test_freshness(
                    WorkflowGraph.from_workflow(head),
                    selected_identity=(
                        GraphIdentity(existing_id=locked_selected.pk)
                        if locked_selected is not None
                        else None
                    ),
                    plan=plan,
                )
                if requested.pk != head.pk
                else ()
            )
            if previous_run is not None:
                run_access = read_scoped_queryset(self.model, actor, action="read")
                locked_previous = (
                    None
                    if run_access is None
                    else run_access.select_related("workflow", "test_step").filter(
                        pk=previous_run.pk,
                    ).first()
                )
                if locked_previous is None:
                    raise PermissionDenied("Previous test evidence is unavailable.")
                previous_head_id = locked_previous.workflow.published_from_id or locked_previous.workflow_id
                if previous_head_id != head.pk:
                    raise ValidationError({"previous_run": "Previous test evidence belongs to another lineage."})
                previous_fixture_rows = (
                    list(locked_previous.test_fixtures.select_related("step", "captured_attempt"))
                    if locked_previous.origin == RunOrigin.TEST
                    else []
                )
                previous_graph = WorkflowGraph.from_workflow(locked_previous.workflow)
                previous_selected = (
                    locked_previous.workflow.steps.filter(key=locked_selected.key).first()
                    if locked_selected is not None
                    else None
                )
                previous_selected_identity = (
                    GraphIdentity(existing_id=locked_previous.test_step_id)
                    if locked_previous.test_step_id is not None
                    else GraphIdentity(existing_id=previous_selected.pk)
                    if previous_selected is not None
                    else None
                )
                previous_plan = previous_graph.test_execution_plan(
                    selected_identity=previous_selected_identity,
                    output_slots=frozenset(
                        (GraphIdentity(existing_id=row.step_id), row.item_index)
                        for row in previous_fixture_rows
                        if row.role == TestFixtureRole.OUTPUT
                    ),
                    map_item_slots=frozenset(
                        (GraphIdentity(existing_id=row.step_id), row.item_index)
                        for row in previous_fixture_rows
                        if row.role == TestFixtureRole.MAP_ITEM and row.item_index is not None
                    ),
                )
                previous_freshness = previous_graph.test_freshness(
                    graph,
                    selected_identity=previous_selected_identity,
                    plan=previous_plan,
                )
                freshness = tuple(dict.fromkeys((*freshness, *previous_freshness)))
                proposed_content_type = (
                    None
                    if subject is None
                    else ContentType.objects.get_for_model(subject, for_concrete_model=False)
                )
                if (
                    locked_previous.subject_content_type_id
                    != (None if proposed_content_type is None else proposed_content_type.pk)
                    or locked_previous.subject_object_id != (None if subject is None else subject.pk)
                ):
                    freshness = (*freshness, GraphFreshnessReason("subject_changed", None, "subject"))
                if (
                    locked_previous.input_present is not input.present
                    or not json_values_equal(
                        locked_previous.input,
                        input.value if input.present else None,
                    )
                ):
                    freshness = (*freshness, GraphFreshnessReason("input_changed", None, "input"))
                selected_key = locked_selected.key if locked_selected is not None else None
                if (
                    locked_previous.origin == RunOrigin.TEST
                    and (
                        (locked_previous.test_scope or TestScope.WHOLE) != scope
                        or (locked_previous.test_step.key if locked_previous.test_step_id else None)
                        != selected_key
                    )
                ):
                    freshness = (*freshness, GraphFreshnessReason("scope_changed", selected_key, "scope"))
                actual_fixture_facts = [
                    (
                        row.step.key,
                        str(row.role),
                        row.item_index,
                        row.value_present,
                        row.value,
                        row.outcome,
                        row.captured_attempt_id,
                    )
                    for row in previous_fixture_rows
                ]
                proposed_fixture_facts = [
                    (
                        row.step.key,
                        str(row.role),
                        row.item_index,
                        row.value_present,
                        row.value,
                        row.outcome,
                        row.captured_attempt_id,
                    )
                    for row in fixture_rows
                ]
                actual_fixture_facts.sort(
                    key=lambda value: (value[0], value[1], -1 if value[2] is None else value[2])
                )
                proposed_fixture_facts.sort(
                    key=lambda value: (value[0], value[1], -1 if value[2] is None else value[2])
                )
                if len(actual_fixture_facts) != len(proposed_fixture_facts) or any(
                    left[:4] != right[:4]
                    or not json_values_equal(left[4], right[4])
                    or left[5:] != right[5:]
                    for left, right in zip(
                        actual_fixture_facts,
                        proposed_fixture_facts,
                        strict=True,
                    )
                ):
                    freshness = (
                        *freshness,
                        GraphFreshnessReason("fixtures_changed", selected_key, "fixtures"),
                    )
                attempt_model = self.model._meta.apps.get_model("workflows", "StepAttempt")
                for retained_fixture in previous_fixture_rows:
                    if retained_fixture.captured_attempt_id is None:
                        continue
                    available = attempt_model.objects._test_fixture_source_record(
                        head,
                        actor=actor,
                        attempt_id=retained_fixture.captured_attempt.sqid,
                        role=retained_fixture.role,
                        step_key=retained_fixture.step.key,
                        item_index=retained_fixture.item_index,
                    )
                    if available is None:
                        freshness = (
                            *freshness,
                            GraphFreshnessReason(
                                "captured_source_unavailable",
                                retained_fixture.step.key,
                                "fixtures",
                            ),
                        )
            source_step_id = locked_selected.sqid if locked_selected is not None else None
            if locked_selected is not None and requested.pk != head.pk:
                step_model = workflow_model._meta.apps.get_model("workflows", "Step")
                if (
                    previous_run is not None
                    and locked_previous.origin == RunOrigin.TEST
                    and locked_previous.workflow_id == requested.pk
                    and locked_previous.test_step_id == locked_selected.pk
                    and locked_previous.test_source_step_id is not None
                ):
                    source_step_id = step_model.public_id_from_pk(locked_previous.test_source_step_id)
                else:
                    source_step = system_queryset(step_model, using=alias, lock=None).filter(
                        workflow=head,
                        key=locked_selected.key,
                    ).first()
                    source_step_id = source_step.sqid if source_step is not None else None
            freshness = tuple(dict.fromkeys(freshness))
            return WorkflowTestSetupPlan(
                source_step_id=source_step_id,
                snapshot_step_id=(
                    locked_selected.sqid
                    if locked_selected is not None and requested.status == WorkflowStatus.TEST
                    else None
                ),
                operations=plan.operations,
                required_fixtures=plan.required_fixtures,
                diagnostics=plan.diagnostics,
                requires_map_item=plan.map_item,
                freshness=freshness,
            )

    def test_repair_context(self, source_attempt: Any, *, actor: Any) -> WorkflowTestRepairContext:
        """Resolve an authorized retained attempt into current draft test identities."""

        attempt_model = self.model._meta.apps.get_model("workflows", "StepAttempt")
        readable_attempts = read_scoped_queryset(attempt_model, actor, action="read")
        row = None if readable_attempts is None else readable_attempts.select_related(
            "step_run__run__workflow", "step_run__step"
        ).filter(pk=source_attempt.pk).first()
        if row is None or row.step_run.step_id is None:
            raise PermissionDenied("Repair source evidence is unavailable.")
        source_run = row.step_run.run
        readable_runs = read_scoped_queryset(self.model, actor, action="read")
        if readable_runs is None or not readable_runs.filter(pk=source_run.pk).exists():
            raise PermissionDenied("Repair source evidence is unavailable.")
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        head_id = source_run.workflow.published_from_id or source_run.workflow_id
        writable = read_scoped_queryset(workflow_model, actor, action="write")
        if writable is None:
            raise PermissionDenied("Repair workflow access was denied.")
        head = writable.filter(pk=head_id, status=WorkflowStatus.DRAFT).first()
        if head is None:
            raise PermissionDenied("Repair workflow access was denied.")
        subject = source_run.subject
        if subject is not None:
            readable_subjects = read_scoped_queryset(type(subject), actor, action="read")
            if readable_subjects is None or not readable_subjects.filter(pk=subject.pk).exists():
                raise PermissionDenied("The source run subject is no longer available.")
        source_step = row.step_run.step
        current_step = system_queryset(
            type(source_step), using=self.db, lock=None
        ).filter(workflow=head, key=source_step.key).first()
        fixture_summaries: tuple[TestFixtureSourceSummary, ...] = ()
        if current_step is not None:
            from angee.workflows.graph import GraphIdentity, WorkflowGraph

            with system_context(reason="workflows.runs.test_repair_context"):
                graph = WorkflowGraph.from_workflow(head)
            plan = graph.test_plan(GraphIdentity(existing_id=current_step.pk))
            admitted_keys = {
                graph.nodes[identity].key for identity in plan.executable
                if identity in graph.nodes and identity.existing_id != current_step.pk
            }
            candidates = (
                system_queryset(attempt_model, using=self.db, lock=None).select_related(
                    "step_run__step", "step_run__run", "step_run__step__workflow"
                )
                .filter(
                    step_run__run=source_run,
                    step_run__step__key__in=admitted_keys,
                    result_kind=AttemptResultKind.DONE,
                    applied_at__isnull=False,
                    lease_revoked_at__isnull=True,
                    output_present=True,
                )
                .order_by("step_run__step_id", "step_run__map_index", "pk")
            )
            fixture_summaries = tuple(
                attempt_model.objects._fixture_source_summary(candidate, TestFixtureRole.OUTPUT)
                for candidate in candidates
            )
        return WorkflowTestRepairContext(
            source_attempt_id=row.sqid,
            source_run_id=source_run.sqid,
            source_workflow_id=source_run.workflow.sqid,
            source_revision=(
                source_run.workflow.draft_revision
                if source_run.workflow.status == WorkflowStatus.TEST
                else source_run.workflow.version
            ),
            draft_workflow_id=head.sqid,
            draft_revision=head.draft_revision,
            source_step_key=source_step.key,
            source_step_id=source_step.sqid,
            current_source_step_id=current_step.sqid if current_step is not None else None,
            subject=subject,
            input=JsonPresence(source_run.input_present, copy.deepcopy(source_run.input)),
            fixtures=fixture_summaries,
        )

    @staticmethod
    def _validate_test_scope_readiness(
        snapshot: Any,
        *,
        scope: TestScope,
        selected_step: Any,
        fixture_rows: tuple[Any, ...],
        graph: Any = None,
    ) -> None:
        from angee.workflows.graph import GraphIdentity, WorkflowGraph

        graph = graph or WorkflowGraph.from_workflow(snapshot)
        output_slots = frozenset(
            (
                GraphIdentity(existing_id=row.step_id),
                row.item_index,
            )
            for row in fixture_rows
            if row.role == TestFixtureRole.OUTPUT
        )
        selected_identity = (
            GraphIdentity(existing_id=selected_step.pk) if scope == TestScope.NODE else None
        )
        plan = graph.test_execution_plan(
            selected_identity=selected_identity,
            output_slots=output_slots,
            map_item_slots=frozenset(
                (GraphIdentity(existing_id=row.step_id), row.item_index)
                for row in fixture_rows
                if row.role == TestFixtureRole.MAP_ITEM and row.item_index is not None
            ),
        )
        if plan.diagnostics:
            raise ValidationError(
                {item.location.path: item.message for item in plan.diagnostics}
            )

    def _validate_test_retry(
        self,
        run: Any,
        *,
        expected_revision: int,
        subject: Any,
        input: JsonPresence,
        requested_snapshot_id: int | None,
        scope: TestScope,
        selected_step_key: str | None,
        fixture_rows: tuple[Any, ...],
    ) -> None:
        """Reject reuse of one request identity with different admission facts."""

        content_type = None if subject is None else ContentType.objects.get_for_model(
            subject, for_concrete_model=False
        )
        object_id = None if subject is None else subject.pk
        matches = (
            run.origin == RunOrigin.TEST
            and run.workflow.status == WorkflowStatus.TEST
            and run.workflow.draft_revision == expected_revision
            and (requested_snapshot_id is None or run.workflow_id == requested_snapshot_id)
            and run.subject_content_type_id == (None if content_type is None else content_type.pk)
            and run.subject_object_id == object_id
            and run.input_present is input.present
            and json_values_equal(run.input, input.value if input.present else None)
            and (run.test_scope or TestScope.WHOLE) == scope
            and (run.test_step.key if run.test_step_id is not None else None) == selected_step_key
        )
        if not matches:
            raise ValidationError({"request_key": "Test launch request facts do not match."})
        if not self._test_fixture_facts_match(run, fixture_rows):
            raise ValidationError({"request_key": "Test launch fixture facts do not match."})

    @staticmethod
    def _test_fixture_facts_match(run: Any, fixture_rows: tuple[Any, ...]) -> bool:
        """Compare immutable fixture facts with exact JSON type semantics."""

        actual = list(
            run.test_fixtures.select_related("step", "captured_attempt").order_by(
                "step_id", "role", "item_index"
            )
        )
        expected = sorted(fixture_rows, key=lambda row: (row.step_id, str(row.role), row.item_index or -1))
        return len(actual) == len(expected) and not any(
            left.step_id != right.step_id
            or left.role != right.role
            or left.item_index != right.item_index
            or left.value_present is not right.value_present
            or not json_values_equal(left.value, right.value)
            or left.outcome != right.outcome
            or left.captured_attempt_id != right.captured_attempt_id
            for left, right in zip(actual, expected, strict=True)
        )

    def _start_pinned_locked(
        self,
        version: Any,
        subject: Any,
        actor: Any,
        *,
        trigger: Any = None,
        parent_step_run: Any = None,
        dedup_key: str | None = None,
        occurrence_id: str | None = None,
        origin: RunOrigin | None = None,
        input: JsonPresence = JsonPresence(),
        test_request_actor_ref: str = "",
        test_scope: TestScope | str = "",
        test_step: Any = None,
        test_source_step_id: int | None = None,
        test_repair_source_attempt: Any = None,
        test_fixture_rows: tuple[Any, ...] = (),
        recovery_source_attempt: Any = None,
        recovery_request_actor_ref: str = "",
        recovery_mode: str = "",
        recovery_evidence: tuple[Any, ...] = (),
        recovery_step: Any = None,
        recovery_map_index: int = -1,
        available_at: datetime,
        using: str,
    ) -> Any:
        """Create a Run and its first durable work for one explicit immutable definition."""

        version.validate_subject_declaration(subject)
        content_type = None if subject is None else ContentType.objects.get_for_model(subject, for_concrete_model=False)
        object_id = None if subject is None else subject.pk
        run_dedup_key = dedup_key or self._trigger_dedup_key(trigger, content_type, object_id)
        owner_id = self._owner_id(actor, trigger, version)
        attrs = {
            "workflow": version,
            "origin": origin or (
                RunOrigin.TRIGGER
                if trigger is not None
                else RunOrigin.ERROR_WORKFLOW if parent_step_run is not None else RunOrigin.MANUAL
            ),
            "trigger": trigger,
            "occurrence_id": occurrence_id,
            "parent_step_run": parent_step_run,
            "subject_content_type": content_type,
            "subject_object_id": object_id,
            "input_present": input.present,
            "input": copy.deepcopy(input.value) if input.present else None,
            "test_request_actor_ref": test_request_actor_ref,
            "test_scope": test_scope,
            "test_step": test_step,
            "test_source_step_id": test_source_step_id,
            "test_repair_source_attempt": test_repair_source_attempt,
            "recovery_source_attempt": recovery_source_attempt,
            "recovery_request_actor_ref": recovery_request_actor_ref,
            "recovery_mode": recovery_mode,
            "created_by_id": owner_id,
            "updated_by_id": owner_id,
        }
        if parent_step_run is not None:
            run, created = self.get_or_create(parent_step_run=parent_step_run, defaults=attrs)
        elif run_dedup_key:
            run, created = self.get_or_create(dedup_key=run_dedup_key, defaults=attrs)
        else:
            run, created = self.create(**attrs), True
        if not created:
            return run
        fixtures: tuple[Any, ...] = ()
        if run.origin == RunOrigin.TEST:
            fixture_model = self.model._meta.apps.get_model("workflows", "WorkflowTestFixture")
            connection = connections[using]
            fixture_token = _test_fixture_write_run.set(
                (using, id(connection), id(connection.atomic_blocks[0]), run.pk)
            )
            batch_token = _test_fixture_batch_rows.set(
                frozenset(id(row) for row in test_fixture_rows)
            )
            try:
                fixtures = fixture_model.objects._create_batch(run, test_fixture_rows)
                apply_token = _test_fixture_apply_ids.set(
                    frozenset(
                        fixture.pk
                        for fixture in fixtures
                        if fixture.role == TestFixtureRole.OUTPUT
                    )
                )
                attempt_model = self.model._meta.apps.get_model("workflows", "StepAttempt")
                try:
                    for fixture in fixtures:
                        if (
                            fixture.role == TestFixtureRole.OUTPUT
                            and run.test_scope == TestScope.NODE
                        ):
                            attempt_model.objects.record_test_fixture(fixture, at=available_at)
                finally:
                    _test_fixture_apply_ids.reset(apply_token)
            finally:
                _test_fixture_batch_rows.reset(batch_token)
                _test_fixture_write_run.reset(fixture_token)
        if run.origin == RunOrigin.RECOVERY:
            evidence_model = self.model._meta.apps.get_model("workflows", "WorkflowRecoveryEvidence")
            connection = connections[using]
            recovery_token = _recovery_write_run.set(
                (using, id(connection), id(connection.atomic_blocks[0]), run.pk)
            )
            recovery_batch_token = _recovery_evidence_batch.set(
                frozenset(attempt.pk for attempt in recovery_evidence)
            )
            try:
                evidence_model.objects._create_for_run(run, recovery_evidence, using=using)
            finally:
                _recovery_evidence_batch.reset(recovery_batch_token)
                _recovery_write_run.reset(recovery_token)
        if test_scope == TestScope.NODE:
            entries = [test_step] if test_step is not None else []
        elif run.origin == RunOrigin.RECOVERY:
            entries = [recovery_step] if recovery_step is not None else []
        else:
            entries = list(version.steps.filter(is_entry=True).order_by("pk"))
        if len(entries) != 1:
            raise ValidationError({"workflow": "Workflow version must have exactly one initial step."})
        step_run_model = self.model._meta.apps.get_model("workflows", "StepRun")
        entry_index = recovery_map_index if run.origin == RunOrigin.RECOVERY else -1
        if test_scope == TestScope.NODE:
            map_fixture = next(
                (fixture for fixture in fixtures if fixture.role == TestFixtureRole.MAP_ITEM),
                None,
            )
            if map_fixture is not None:
                entry_index = map_fixture.item_index
        step_run_model.objects.get_or_create(
            run=run,
            step=entries[0],
            map_index=entry_index,
            defaults={"status": StepRunStatus.SCHEDULED, "input": {}},
        )
        dispatch_model = self.model._meta.apps.get_model("workflows", "WorkflowDispatch")
        dispatch_model.objects.schedule_advance(run, available_at=available_at)
        from angee.workflows.engine import enqueue_dispatch_publisher

        transaction.on_commit(enqueue_dispatch_publisher, using=using)
        return run

    def _resolve_test_fixture_rows(
        self,
        snapshot: Any,
        *,
        scope: TestScope,
        selected_step: Any,
        specs: tuple[TestFixtureSpec, ...],
        actor: Any,
        graph: Any = None,
        require_complete: bool = True,
    ) -> tuple[Any, ...]:
        """Resolve a complete fixture request before the test run writes begin."""

        from angee.workflows.graph import GraphIdentity, WorkflowGraph

        fixture_model = self.model._meta.apps.get_model("workflows", "WorkflowTestFixture")
        attempt_model = self.model._meta.apps.get_model("workflows", "StepAttempt")
        graph = graph or WorkflowGraph.from_workflow(snapshot)
        step_by_key = {step.key: step for step in snapshot.steps.order_by("pk")}
        allowed_outputs = set(step_by_key)
        allows_map_item = False
        if scope == TestScope.NODE:
            plan = graph.test_execution_plan(
                selected_identity=GraphIdentity(existing_id=selected_step.pk)
            )
            allowed_outputs = {
                node.key for node in graph.nodes if node.identity in plan.output_sources
            }
            allows_map_item = plan.map_item
        rows: list[Any] = []
        seen: set[tuple[str, str, int | None]] = set()
        if not isinstance(specs, tuple):
            raise ValidationError({"fixtures": "Test fixtures must be an immutable tuple."})
        map_fixture_count = 0
        for raw in specs:
            try:
                spec = validate_test_fixture_spec(raw)
            except ValueError as error:
                raise ValidationError({"fixtures": str(error)}) from error
            step = step_by_key.get(spec.step_key)
            slot = (spec.step_key, str(spec.role), spec.item_index)
            if step is None or slot in seen:
                raise ValidationError({"fixtures": "Fixture slots must be unique copied workflow steps."})
            seen.add(slot)
            if spec.role == TestFixtureRole.OUTPUT and spec.step_key not in allowed_outputs:
                raise ValidationError({"fixtures": "Output fixture is outside this test scope's sources."})
            if spec.role == TestFixtureRole.MAP_ITEM and (
                not allows_map_item or selected_step is None or step.pk != selected_step.pk
            ):
                raise ValidationError({"fixtures": "Map item fixture is outside the selected body scope."})
            if spec.role == TestFixtureRole.MAP_ITEM:
                map_fixture_count += 1
                if map_fixture_count > 1:
                    raise ValidationError({"fixtures": "A node test selects exactly one Map item slot."})
            if spec.role == TestFixtureRole.OUTPUT:
                is_map_body = graph.test_plan(GraphIdentity(existing_id=step.pk)).map_item
                if is_map_body != (spec.item_index is not None):
                    raise ValidationError(
                        {"fixtures": "Map body outputs require an exact item index; ordinary outputs do not."}
                    )
            value = spec.value
            outcome = spec.outcome
            captured = None
            if spec.captured_attempt_id is not None:
                captured = attempt_model.objects._test_fixture_source_record(
                    snapshot,
                    actor=actor,
                    attempt_id=spec.captured_attempt_id,
                    role=spec.role,
                    step_key=spec.step_key,
                    item_index=spec.item_index,
                )
                if captured is None:
                    raise PermissionDenied("Captured fixture evidence is unavailable.")
                if spec.role == TestFixtureRole.OUTPUT:
                    value = JsonPresence(captured.output_present, captured.output)
                    outcome = captured.outcome
                else:
                    value = JsonPresence(True, captured.map_item)
            if spec.role == TestFixtureRole.OUTPUT and outcome:
                declared_outcomes = {
                    declared.key
                    for declared in step.resolve_impl("step_class").outcomes
                }
                if declared_outcomes and outcome not in declared_outcomes:
                    raise ValidationError(
                        {"fixtures": "Output fixture outcome is not declared by this operation."}
                    )
            rows.append(
                fixture_model(
                    step=step,
                    role=spec.role,
                    item_index=spec.item_index,
                    value_present=value.present,
                    value=copy.deepcopy(value.value),
                    outcome=outcome,
                    captured_attempt=captured,
                )
            )
        if require_complete and scope == TestScope.NODE and allows_map_item and map_fixture_count != 1:
            raise ValidationError({"fixtures": "A selected Map body requires exactly one Map item fixture."})
        return tuple(rows)

    @staticmethod
    def _trigger_dedup_key(trigger: Any, content_type: Any, object_id: Any) -> str:
        if trigger is None:
            return ""
        subject = "none" if content_type is None or object_id is None else f"{content_type.pk}:{object_id}"
        return f"trigger:{trigger.pk}:subject:{subject}"

    @staticmethod
    def _owner_id(actor: Any, trigger: Any, workflow: Any) -> Any | None:
        if actor is not None:
            try:
                user_id = actor_user_id(to_subject_ref(actor))
            except NoActorResolvedError:
                user_id = None
            if user_id is not None:
                return user_id
        if trigger is not None and trigger.created_by_id is not None:
            return trigger.created_by_id
        lineage = workflow.published_from
        return (
            lineage.created_by_id
            if lineage is not None and lineage.created_by_id is not None
            else workflow.created_by_id
        )


class Workflow(ResourceLoadMixin, AuditMixin, AngeeDataModel):
    """Editable workflow lineage head or immutable published workflow version.

    A resource-assigned stable key identifies the lineage independently of its
    mutable display name and is shared by every published version.
    """

    runtime = True

    sqid_prefix = "wfl_"
    key = models.SlugField(max_length=100, blank=True, default="")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    purpose = StateField(choices_enum=WorkflowPurpose, default=WorkflowPurpose.AUTOMATION)
    subject_declaration = models.CharField(max_length=200, blank=True, default="")
    status = StateField(choices_enum=WorkflowStatus, default=WorkflowStatus.DRAFT)
    version = models.PositiveIntegerField(default=0)
    draft_revision = models.PositiveIntegerField(default=0, editable=False)
    published_from = models.ForeignKey(
        "workflows.Workflow",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="published_versions",
    )
    error_workflow = models.ForeignKey(
        "workflows.Workflow",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="error_for_workflows",
    )
    max_steps = models.PositiveIntegerField(default=1000)
    budget = models.JSONField(default=dict, blank=True)

    status_transitions = StateTransitions(
        status,
        {
            WorkflowStatus.DRAFT: [WorkflowStatus.TEST, WorkflowStatus.PUBLISHED],
            WorkflowStatus.PUBLISHED: [WorkflowStatus.ARCHIVED],
        },
    )

    objects = WorkflowManager()

    class Meta:
        """Django model options for workflow definitions."""

        abstract = True
        ordering = ("name", "version")
        rebac_resource_type = "workflows/workflow"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("key",),
                condition=models.Q(published_from__isnull=True) & ~models.Q(key=""),
                name="uniq_workflows_workflow_head_key",
                violation_error_code="unique",
                violation_error_message="A workflow with this key already exists.",
            ),
            models.UniqueConstraint(
                fields=("published_from", "draft_revision"),
                condition=models.Q(status=WorkflowStatus.TEST),
                name="uniq_workflows_test_snapshot_revision",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=WorkflowStatus.TEST)
                    | (models.Q(published_from__isnull=False) & models.Q(version=0))
                ),
                name="chk_workflows_test_snapshot_shape",
            ),
        )

    def __str__(self) -> str:
        """Return the workflow's display label."""

        return self.name

    @classmethod
    def lineage_projection_annotation(cls) -> dict[str, Any]:
        """Return the ORM projection for lineage and current publication context."""

        lineage = models.Q(pk=models.OuterRef("_workflow_lineage_id")) | models.Q(
            published_from_id=models.OuterRef("_workflow_lineage_id")
        )
        current = cls.objects.current_published().filter(lineage)
        latest = cls.objects.filter(lineage, status__in=_CURRENCY_STATUSES).order_by("-version", "-pk")
        return {
            "_workflow_lineage_id": models.functions.Coalesce("published_from_id", "pk"),
            "_workflow_publication_status": models.functions.Coalesce(
                models.Subquery(latest.values("status")[:1]),
                models.Value(WorkflowStatus.DRAFT),
            ),
            "_workflow_current_published_pk": models.Subquery(current.values("pk")[:1]),
            "_workflow_current_published_version": models.Subquery(current.values("version")[:1]),
            "_workflow_current_published_subject_declaration": models.Subquery(
                current.values("subject_declaration")[:1]
            ),
        }

    @classmethod
    def resource_write_preparation(cls, resource: Any, dataset: Any) -> ResourceWritePreparation | None:
        """Declare existing lineage heads before a resource batch starts writing."""

        targets = frozenset(
            instance.pk
            for xref in dataset["_xref"]
            if (instance := resource.instance_for_xref(xref)) is not None
        )
        return ResourceWritePreparation(cls, targets) if targets else None

    @classmethod
    @contextmanager
    def prepare_resource_writes(cls, workflow_ids: Iterable[int]) -> Iterator[None]:
        """Prelock every declared resource lineage in deterministic order."""

        ids = sorted(set(workflow_ids))
        alias = router.db_for_write(cls)
        with transaction.atomic(using=alias):
            rows = list(
                system_queryset(cls, using=alias, lock=("self",))
                .filter(pk__in=ids)
                .order_by("pk")
            )
            if len(rows) != len(ids):
                raise ValidationError("A workflow definition parent no longer exists.")
            if any(row.is_immutable for row in rows):
                raise ValidationError("Published workflow versions are immutable.")
            yield

    @classmethod
    def after_resource_load(
        cls,
        instances: Iterable[Any],
        *,
        tier: str,
        source: str,
        publish: bool = False,
    ) -> None:
        """Reconcile stable keys and publish loaded drafts when requested."""

        for workflow in sorted(instances, key=lambda instance: instance.pk or 0):
            if workflow.published_from_id is not None:
                continue
            workflow._propagate_resource_key_backfill()
            if publish and workflow.status == WorkflowStatus.DRAFT:
                workflow.publish_if_changed()

        super().after_resource_load(instances, tier=tier, source=source, publish=publish)

    @transition(status, source=WorkflowStatus.DRAFT, target=WorkflowStatus.PUBLISHED, on_success=_save_workflow_status)
    def mark_published(self) -> None:
        """Mark this copied version as published."""

        session = _definition_write_session.get()
        if (
            self.published_from_id is None
            or session is None
            or self.pk not in session.copy_target_ids
        ):
            raise ValidationError("Only a new snapshot created by publish() can be marked published.")

    @transition(status, source=WorkflowStatus.DRAFT, target=WorkflowStatus.TEST, on_success=_save_workflow_status)
    def mark_test(self) -> None:
        """Mark a copied saved revision as an immutable test snapshot."""

        session = _definition_write_session.get()
        if self.published_from_id is None or session is None or self.pk not in session.copy_target_ids:
            raise ValidationError("Only a copied saved revision can be marked as a test snapshot.")

    @transition(
        status,
        source=WorkflowStatus.PUBLISHED,
        target=WorkflowStatus.ARCHIVED,
        on_success=_save_workflow_status,
    )
    def archive(self) -> None:
        """Archive a published workflow version."""

    def clean(self) -> None:
        """Validate lineage-owned links and normalize stable and subject keys."""

        super().clean()
        self.key = (self.key or "").lower()
        if self.published_from_id is not None and self.key != self.published_from.key:
            raise ValidationError({"key": "Published workflow versions must share their lineage stable key."})
        if self.error_workflow_id is not None and self.error_workflow.published_from_id is not None:
            raise ValidationError({"error_workflow": "Error workflow must point to a workflow lineage head."})
        # Mirror Trigger.event_model_label: store the canonical label_lower form
        # and reject labels that resolve to no installed model.
        self.subject_declaration = self.subject_declaration.strip().lower()
        if self.subject_declaration:
            try:
                apps.get_model(self.subject_declaration)
            except (LookupError, ValueError) as error:
                raise ValidationError(
                    {
                        "subject_declaration": (
                            f"Subject declaration {self.subject_declaration!r} is not an installed model label."
                        )
                    }
                ) from error

    def validate_subject_declaration(self, subject: Any) -> None:
        """Raise when ``subject`` does not satisfy this workflow's subject declaration."""

        if not self.subject_declaration:
            return
        if subject is None:
            raise ValidationError(
                {"subject": f"Subject declaration {self.subject_declaration!r} requires a subject."}
            )
        if subject._meta.label_lower != self.subject_declaration:
            raise ValidationError(
                {
                    "subject": (
                        f"Subject model {subject._meta.label!r} does not satisfy "
                        f"subject declaration {self.subject_declaration!r}."
                    )
                }
            )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the workflow after enforcing immutability and model validation."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        if self._state.adding:
            if self.published_from_id is not None:
                session = _definition_write_session.get()
                if session is None or self.published_from_id not in session.workflow_ids:
                    raise ValidationError("Published workflow versions can only be created by publish().")
                if self.status != WorkflowStatus.DRAFT:
                    raise ValidationError({"status": "New publications must begin as draft snapshots."})
            elif self.status != WorkflowStatus.DRAFT or self.version != 0 or self.draft_revision != 0:
                raise ValidationError("New workflow lineage heads must begin as revision-zero drafts.")
            with _definition_caller_context(self):
                self.full_clean()
            super().save(*args, **kwargs)
            return

        manager = type(self).objects.db_manager(alias)
        with manager._definition_write(
            (self.pk,),
            using=alias,
            _allow_status_transition=getattr(self, "_allow_immutable_status_save", False),
        ):
            persisted = cast(Self, _definition_rows(type(self), alias).get(pk=self.pk))
            self._raise_if_immutable_save(persisted)
            if self.published_from_id != persisted.published_from_id:
                raise ValidationError({"published_from": "Workflow lineage ownership is immutable."})
            if self.version != persisted.version:
                raise ValidationError({"version": "Workflow publication versions are immutable."})
            if self.status != persisted.status and not getattr(self, "_allow_immutable_status_save", False):
                raise ValidationError({"status": "Workflow status changes require a declared transition."})
            # The database counter is the sole owner; a stale model can never write it backwards.
            self.draft_revision = persisted.draft_revision
            with _definition_caller_context(self):
                self.full_clean()
            self._raise_if_key_changed(persisted)
            update_fields = kwargs.get("update_fields")
            definition_fields = {
                "name",
                "description",
                "purpose",
                "subject_declaration",
                "error_workflow",
                "error_workflow_id",
                "max_steps",
                "budget",
            }
            updated = None if update_fields is None else set(update_fields)
            considered = definition_fields if updated is None else {
                field for field in definition_fields if field in updated or field.removesuffix("_id") in updated
            }
            changed = any(getattr(persisted, field) != getattr(self, field) for field in considered)
            assigns_stable_key = (
                not persisted.key
                and bool(self.key)
                and (update_fields is None or "key" in update_fields)
            )
            super().save(*args, **kwargs)
            if assigns_stable_key:
                self._propagate_resource_key_backfill()
            if changed and self.published_from_id is None:
                manager.mark_definition_changed(self.pk)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only mutable workflow rows."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        manager = type(self).objects.db_manager(alias)
        with manager._definition_write((self.pk,), using=alias):
            persisted = cast(Self, _definition_rows(type(self), alias).get(pk=self.pk))
            self._raise_if_immutable_save(persisted)
            if persisted.published_versions.exists():
                raise ValidationError("A workflow with publication history cannot be deleted.")
            # Collector skips child instance delete methods. Own the full cascade here.
            edges = _definition_related_rows(persisted.edges, self, alias).all().delete()
            steps = _definition_related_rows(persisted.steps, self, alias).all().delete()
            with _definition_caller_context(self):
                workflow = super().delete(*args, **kwargs)
            return _combined_delete_results(edges, steps, workflow)

    def publish(self) -> Self:
        """Copy this draft lineage head into an immutable published version."""

        if self.published_from_id is not None:
            raise ValidationError({"published_from": "Only a workflow lineage head can be published."})
        alias = router.db_for_write(type(self), instance=self)
        manager = type(self).objects.db_manager(alias)
        with manager._definition_write((self.pk,), using=alias):
            draft = cast(Self, _bind_definition_caller(_definition_rows(type(self), alias).get(pk=self.pk), self))
            if draft.status != WorkflowStatus.DRAFT:
                raise ValidationError({"status": "Only draft workflows can be published."})
            with _definition_caller_context(draft):
                draft._validate_publishable()
            with _definition_caller_context(draft):
                version = draft._next_published_version()
            published = draft._new_definition_copy(
                version=version,
                draft_revision=manager._definition_revision(draft.pk, draft.draft_revision),
            )
            _bind_definition_caller(published, draft)
            published.save(using=alias)
            with manager._copy_to(published.pk):
                with _definition_caller_context(draft):
                    draft._copy_definition_to(published)
                published.mark_published()
            return cast(Self, published)

    def _new_definition_copy(self, *, version: int, draft_revision: int) -> Self:
        """Build an unsaved immutable-definition copy with lineage-owned fields."""

        return type(self)(
            key=self.key,
            name=self.name,
            description=self.description,
            purpose=self.purpose,
            subject_declaration=self.subject_declaration,
            status=WorkflowStatus.DRAFT,
            version=version,
            draft_revision=draft_revision,
            published_from=self,
            error_workflow=self.error_workflow,
            max_steps=self.max_steps,
            budget=copy.deepcopy(self.budget),
            created_by_id=self.created_by_id,
            updated_by_id=self.updated_by_id,
        )

    def publish_if_changed(self) -> Self | None:
        """Publish this draft only when no current version has the same definition."""

        alias = router.db_for_write(type(self), instance=self)
        manager = type(self).objects.db_manager(alias)
        with manager._definition_write((self.pk,), using=alias):
            draft = cast(Self, _bind_definition_caller(_definition_rows(type(self), alias).get(pk=self.pk), self))
            with _definition_caller_context(draft):
                current = manager.current_published_for(draft)
                if current is not None and draft._definition_signature() == current._definition_signature():
                    return None
            return draft.publish()

    def _next_published_version(self) -> int:
        """Return the next immutable version number for this lineage head."""

        current = (
            type(self).objects.filter(published_from=self).aggregate(max_version=models.Max("version"))["max_version"]
            or 0
        )
        return int(current) + 1

    def _copy_definition_to(self, published: Workflow) -> None:
        """Copy this draft's steps and edges to ``published``."""

        step_model = self.steps.model
        edge_model = self.edges.model
        step_map: dict[int, Any] = {}
        for step in self.steps.order_by("pk"):
            copied = step_model(
                workflow=published,
                key=step.key,
                name=step.name,
                step_class=step.step_class,
                config=copy.deepcopy(step.config),
                input_binding=copy.deepcopy(step.input_binding),
                join_rule=step.join_rule,
                is_entry=step.is_entry,
                position=copy.deepcopy(step.position),
            )
            _bind_definition_caller(copied, published)
            copied.save()
            step_map[step.pk] = copied
        for edge in self.edges.select_related("source", "target").order_by("pk"):
            copied_edge = edge_model(
                workflow=published,
                source=step_map[edge.source_id],
                target=step_map[edge.target_id],
                condition=edge.condition,
            )
            _bind_definition_caller(copied_edge, published)
            copied_edge.save()

    def _definition_signature(self) -> dict[str, Any]:
        """Return the versioned definition content for publish idempotency."""

        return {
            "workflow": {
                "name": self.name,
                "description": self.description,
                "purpose": str(self.purpose),
                "subject_declaration": self.subject_declaration,
                "error_workflow_id": self.error_workflow_id,
                "max_steps": self.max_steps,
                "budget": copy.deepcopy(self.budget),
            },
            "steps": [
                {
                    "key": step.key,
                    "name": step.name,
                    "step_class": step.step_class,
                    "config": copy.deepcopy(step.config),
                    "input_binding": copy.deepcopy(step.input_binding),
                    "join_rule": str(step.join_rule),
                    "is_entry": step.is_entry,
                    "position": copy.deepcopy(step.position),
                }
                for step in self.steps.order_by("key", "pk")
            ],
            "edges": [
                {
                    "source": edge.source.key,
                    "target": edge.target.key,
                    "condition": edge.condition,
                }
                for edge in self.edges.select_related("source", "target").order_by(
                    "source__key",
                    "target__key",
                    "condition",
                    "pk",
                )
            ],
        }

    def _validate_publishable(self) -> None:
        """Validate the exact locked graph before creating an executable snapshot."""

        self.validate_readiness()

    def graph_diagnostics(self) -> tuple[Any, ...]:
        """Return readiness diagnostics for the currently persisted definition."""

        from angee.workflows.graph import WorkflowGraph

        return WorkflowGraph.from_workflow(self).diagnostics()

    def validate_readiness(self) -> None:
        """Raise all readiness diagnostics for the currently persisted definition."""

        from angee.workflows.graph import WorkflowGraph

        WorkflowGraph.from_workflow(self).validate()

    def _persisted_save_snapshot(self) -> Self | None:
        """Return the persisted status and stable key for save guards."""

        if self._state.adding:
            return None
        try:
            return cast(Self, type(self)._base_manager.only("status", "key").get(pk=self.pk))
        except ObjectDoesNotExist:
            return None

    def _raise_if_immutable_save(self, persisted: Self | None) -> None:
        """Reject writes to persisted published or archived workflow definitions."""

        if persisted is None or getattr(self, "_allow_immutable_status_save", False):
            return
        if persisted.is_immutable:
            raise ValidationError("Published workflow versions are immutable.")

    def _raise_if_key_changed(self, persisted: Self | None) -> None:
        """Keep an assigned stable key immutable while allowing legacy backfill."""

        if persisted is None:
            return
        if persisted.key and self.key != persisted.key:
            raise ValidationError({"key": "Workflow stable keys are immutable once assigned."})

    def _propagate_resource_key_backfill(self) -> None:
        """Copy a resource-assigned stable key to legacy published versions."""

        if not self.key:
            return
        versions = type(self)._base_manager.filter(published_from=self)
        if versions.exclude(key__in=("", self.key)).exists():
            raise ValidationError({"key": "Published workflow versions disagree with their lineage stable key."})
        models.QuerySet.update(
            versions.filter(key=""),
            key=self.key,
            updated_at=timezone.now(),
        )

    @property
    def is_immutable(self) -> bool:
        """Return whether this workflow version rejects definition edits."""

        return self.status in {WorkflowStatus.TEST, WorkflowStatus.PUBLISHED, WorkflowStatus.ARCHIVED}


class StepQuerySet(DefinitionQuerySet):
    """Guard collection writes to workflow steps."""


class StepManager(AngeeManager.from_queryset(StepQuerySet)):  # type: ignore[misc]
    """Manager for guarded workflow-step writes."""


class Step(ImplDefaultsMixin, AuditMixin, AngeeDataModel):
    """One node in a workflow definition graph."""

    runtime = True

    sqid_prefix = "wfs_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="steps")
    key = models.SlugField(max_length=100)
    name = models.CharField(max_length=200)
    step_class = ImplClassField(
        base_class=StepImpl,
        registry_setting="ANGEE_WORKFLOW_STEP_CLASSES",
        default="handler",
    )
    config = models.JSONField(default=dict, blank=True)
    input_binding = models.JSONField(null=True, blank=True, default=None)
    join_rule = StateField(choices_enum=JoinRule, default=JoinRule.ALL_SUCCESS)
    is_entry = models.BooleanField(default=False)
    position = models.JSONField(default=dict, blank=True)

    objects = StepManager()

    class Meta:
        """Django model options for workflow steps."""

        abstract = True
        ordering = ("workflow", "key")
        rebac_resource_type = "workflows/step"
        rebac_id_attr = "sqid"
        constraints = (models.UniqueConstraint(fields=("workflow", "key"), name="uniq_workflows_step_key"),)

    def __str__(self) -> str:
        """Return the step's display label."""

        return self.name or self.key

    @classmethod
    def resource_write_preparation(cls, resource: Any, dataset: Any) -> ResourceWritePreparation | None:
        """Declare old and proposed workflow parents for a resource step batch."""

        workflows = set(resource.related_instances(dataset, "workflow"))
        workflows.update(
            instance.workflow
            for xref in dataset["_xref"]
            if (instance := resource.instance_for_xref(xref)) is not None
        )
        if not workflows:
            return None
        workflow_model = cls._meta.get_field("workflow").remote_field.model
        return ResourceWritePreparation(workflow_model, frozenset(row.pk for row in workflows))

    def config_projection(self) -> StepConfigProjection:
        """Project legacy config for repair without rewriting its stored value."""

        impl = cast(type[StepImpl], self.resolve_impl("step_class"))
        raw = copy.deepcopy(self.config)
        try:
            impl.validate_config(raw)
            value = impl.normalize_config(raw) if impl.config_model is not None else raw
            return StepConfigProjection(value=value, errors={})
        except ValidationError as error:
            return StepConfigProjection(value=raw, errors=error.message_dict)

    def clean(self) -> None:
        """Validate structural draft fields without requiring readiness."""

        super().clean()
        if not isinstance(self.config, Mapping):
            raise ValidationError({"config": "Step config must be an object."})
        if self.input_binding is not None and not isinstance(self.input_binding, Mapping):
            raise ValidationError({"input_binding": "Step input binding must be an object or null."})
        try:
            self.resolve_impl("step_class")
        except ValidationError:
            raise
        except Exception as error:
            raise ValidationError({"step_class": "Unknown workflow step class."}) from error

    def validate_impl_configs(self, *, update_fields: Any = None) -> None:
        """Canonicalize complete drafts while preserving incomplete object configs."""

        if not self._state.adding and update_fields is not None and "config" not in update_fields:
            return
        if not isinstance(self.config, Mapping):
            raise ValidationError({"config": "Step config must be an object."})
        impl = cast(type[StepImpl], self.resolve_impl("step_class"))
        if impl.config_model is None:
            return
        try:
            self.config = impl.normalize_config(self.config)
        except ValidationError:
            # Incomplete typed config is a readiness diagnostic, not a storage error.
            self.config = copy.deepcopy(dict(self.config))

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the step after enforcing parent immutability and validation."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        old_parent_id = None
        if not self._state.adding:
            old_parent_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
                "workflow_id", flat=True
            ).first()
        parent_ids = {value for value in (old_parent_id, self.workflow_id) if value is not None}
        manager = type(self.workflow).objects
        with manager._definition_write(parent_ids, using=alias):
            old = None if self._state.adding else _definition_rows(type(self), alias).filter(pk=self.pk).first()
            if not self._state.adding and (old is None or old.workflow_id != old_parent_id):
                raise ValidationError("The stored workflow step changed while it was being edited.")
            if old is not None and old.workflow_id != self.workflow_id:
                if _definition_rows(type(self), alias).filter(
                    models.Q(outgoing_edges__isnull=False) | models.Q(incoming_edges__isnull=False), pk=self.pk
                ).exists():
                    raise ValidationError({"workflow": "A connected step cannot move to another workflow."})
            with _definition_caller_context(self):
                self.full_clean()
            update_fields = kwargs.get("update_fields")
            self.validate_impl_configs(update_fields=update_fields)
            fields = {
                "workflow_id",
                "key",
                "name",
                "step_class",
                "config",
                "input_binding",
                "join_rule",
                "is_entry",
                "position",
            }
            updated = None if update_fields is None else set(update_fields)
            considered = fields if updated is None else {
                field for field in fields if field in updated or field.removesuffix("_id") in updated
            }
            changed = old is None or any(
                getattr(old, field) != getattr(self, field)
                for field in considered
            )
            super().save(*args, **kwargs)
            session = _definition_write_session.get()
            if changed and session is not None and self.workflow_id not in session.copy_target_ids:
                for workflow_id in parent_ids:
                    manager.mark_definition_changed(workflow_id)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only steps belonging to mutable workflow rows."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        workflow_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
            "workflow_id", flat=True
        ).first()
        if workflow_id is None:
            return (0, {})
        manager = type(self.workflow).objects
        with manager._definition_write((workflow_id,), using=alias):
            persisted = _definition_rows(type(self), alias).get(pk=self.pk)
            if persisted.workflow_id != workflow_id:
                raise ValidationError("The stored workflow step changed while it was being deleted.")
            edge_model = persisted.outgoing_edges.model
            edges = _definition_related_rows(edge_model.objects, self, alias).filter(
                models.Q(source_id=self.pk) | models.Q(target_id=self.pk)
            ).delete()
            with _definition_caller_context(self):
                step = super().delete(*args, **kwargs)
            session = _definition_write_session.get()
            if session is not None and workflow_id not in session.copy_target_ids:
                manager.mark_definition_changed(workflow_id)
            return _combined_delete_results(edges, step)

    def _raise_if_workflow_immutable(self) -> None:
        """Reject writes when this step belongs to an immutable workflow version."""

        if self.workflow_id is None:
            return
        workflow = type(self.workflow)._base_manager.only("status").get(pk=self.workflow_id)
        if workflow.is_immutable:
            raise ValidationError("Published workflow versions are immutable.")


class EdgeQuerySet(DefinitionQuerySet):
    """Guard collection writes to workflow edges."""


class EdgeManager(AngeeManager.from_queryset(EdgeQuerySet)):  # type: ignore[misc]
    """Manager for guarded workflow-edge writes."""


class Edge(AuditMixin, AngeeDataModel):
    """Directed edge between two workflow steps."""

    runtime = True

    sqid_prefix = "wfe_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="edges")
    source = models.ForeignKey("workflows.Step", on_delete=models.CASCADE, related_name="outgoing_edges")
    target = models.ForeignKey("workflows.Step", on_delete=models.CASCADE, related_name="incoming_edges")
    condition = models.SlugField(max_length=100, blank=True, default="")

    objects = EdgeManager()

    class Meta:
        """Django model options for workflow edges."""

        abstract = True
        ordering = ("workflow", "source", "target", "condition")
        rebac_resource_type = "workflows/edge"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(fields=("source", "target", "condition"), name="uniq_workflows_edge_condition"),
        )

    def __str__(self) -> str:
        """Return a compact edge label."""

        return f"{self.source_id}->{self.target_id}:{self.condition}"

    @classmethod
    def resource_write_preparation(cls, resource: Any, dataset: Any) -> ResourceWritePreparation | None:
        """Declare old and proposed workflow parents for a resource edge batch."""

        workflows = set(resource.related_instances(dataset, "workflow"))
        workflows.update(
            instance.workflow
            for xref in dataset["_xref"]
            if (instance := resource.instance_for_xref(xref)) is not None
        )
        if not workflows:
            return None
        workflow_model = cls._meta.get_field("workflow").remote_field.model
        return ResourceWritePreparation(workflow_model, frozenset(row.pk for row in workflows))

    def clean(self) -> None:
        """Validate that an edge is fully contained in one workflow."""

        super().clean()
        errors: dict[str, str] = {}
        if self.workflow_id is not None and self.source_id is not None and self.source.workflow_id != self.workflow_id:
            errors["source"] = "Edge source must belong to the same workflow."
        if self.workflow_id is not None and self.target_id is not None and self.target.workflow_id != self.workflow_id:
            errors["target"] = "Edge target must belong to the same workflow."
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the edge after enforcing parent immutability and validation."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        old_parent_id = None
        if not self._state.adding:
            old_parent_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
                "workflow_id", flat=True
            ).first()
        step_model = self._meta.get_field("source").remote_field.model
        parent_ids = {value for value in (old_parent_id, self.workflow_id) if value is not None}
        manager = type(self.workflow).objects
        with manager._definition_write(parent_ids, using=alias):
            old = None if self._state.adding else _definition_rows(type(self), alias).filter(pk=self.pk).first()
            if not self._state.adding and (old is None or old.workflow_id != old_parent_id):
                raise ValidationError("The stored workflow edge changed while it was being edited.")
            endpoints = dict(
                _definition_rows(step_model, alias)
                .filter(pk__in=(self.source_id, self.target_id))
                .values_list("pk", "workflow_id")
            )
            if endpoints.get(self.source_id) != self.workflow_id:
                raise ValidationError({"source": "Edge source must belong to the same workflow."})
            if endpoints.get(self.target_id) != self.workflow_id:
                raise ValidationError({"target": "Edge target must belong to the same workflow."})
            with _definition_caller_context(self):
                self.full_clean()
            update_fields = kwargs.get("update_fields")
            fields = {"workflow_id", "source_id", "target_id", "condition"}
            updated = None if update_fields is None else set(update_fields)
            considered = fields if updated is None else {
                field for field in fields if field in updated or field.removesuffix("_id") in updated
            }
            changed = old is None or any(
                getattr(old, field) != getattr(self, field)
                for field in considered
            )
            super().save(*args, **kwargs)
            session = _definition_write_session.get()
            if changed and session is not None and self.workflow_id not in session.copy_target_ids:
                for workflow_id in parent_ids:
                    manager.mark_definition_changed(workflow_id)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete only edges belonging to mutable workflow rows."""

        alias = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        workflow_id = _definition_rows(type(self), alias).filter(pk=self.pk).values_list(
            "workflow_id", flat=True
        ).first()
        if workflow_id is None:
            return (0, {})
        manager = type(self.workflow).objects
        with manager._definition_write((workflow_id,), using=alias):
            persisted = _definition_rows(type(self), alias).get(pk=self.pk)
            if persisted.workflow_id != workflow_id:
                raise ValidationError("The stored workflow edge changed while it was being deleted.")
            with _definition_caller_context(self):
                result = super().delete(*args, **kwargs)
            session = _definition_write_session.get()
            if session is not None and workflow_id not in session.copy_target_ids:
                manager.mark_definition_changed(workflow_id)
            return result

    def _raise_if_workflow_immutable(self) -> None:
        """Reject writes when this edge belongs to an immutable workflow version."""

        if self.workflow_id is None:
            return
        workflow = type(self.workflow)._base_manager.only("status").get(pk=self.workflow_id)
        if workflow.is_immutable:
            raise ValidationError("Published workflow versions are immutable.")


class TriggerQuerySet(AngeeQuerySet[Any]):
    """Collection writes that preserve trigger activation and rule invariants."""

    def update(self, **kwargs: Any) -> int:
        protected = {
            "workflow",
            "workflow_id",
            "kind",
            "enabled",
            "config",
            "event_model_label",
            "next_fire_at",
            "last_fire_at",
            "hourly_window_started_at",
            "hourly_fire_count",
        }
        if protected & kwargs.keys():
            raise TypeError("Trigger rules do not support QuerySet.update(); save instances instead.")
        return super().update(**kwargs)

    def bulk_create(self, objs: Iterable[Any], *args: Any, **kwargs: Any) -> list[Any]:
        rows = list(objs)
        for row in rows:
            row.enabled = False
            row.full_clean()
        return super().bulk_create(rows, *args, **kwargs)

    def bulk_update(self, objs: Iterable[Any], fields: Iterable[str], *args: Any, **kwargs: Any) -> int:
        field_names = set(fields)
        protected = {
            "workflow",
            "workflow_id",
            "kind",
            "enabled",
            "config",
            "event_model_label",
            "next_fire_at",
            "last_fire_at",
            "hourly_window_started_at",
            "hourly_fire_count",
        }
        if protected & field_names:
            raise TypeError("Trigger rules do not support bulk_update(); save instances instead.")
        return super().bulk_update(objs, field_names, *args, **kwargs)


class TriggerManager(AngeeManager.from_queryset(TriggerQuerySet)):  # type: ignore[misc]
    """Manager owning trigger row claims and due schedule priming."""

    def record_fire(
        self,
        caller: Any,
        *,
        timestamp: datetime,
    ) -> Any:
        """Authorize, lock, and derive one trigger fire from canonical state."""

        caller._require_record_access("write")
        alias = self.db
        with system_context(reason="workflows.triggers.record_fire"), transaction.atomic(using=alias):
            workflow_model = self.model._meta.get_field("workflow").remote_field.model
            system_queryset(workflow_model, using=alias, lock=("self",)).get(pk=caller.workflow_id)
            trigger = system_queryset(self.model, using=alias, lock=("self",)).get(pk=caller.pk)
            if trigger.workflow_id != caller.workflow_id:
                raise ValidationError({"workflow": "The trigger lineage changed before recording its fire."})
            self._record_fire_locked(trigger, timestamp=timestamp)
            return trigger

    def _record_fire_locked(
        self, trigger: Any, *, timestamp: datetime, extra_update_fields: Iterable[str] = ()
    ) -> None:
        window_start = trigger.hourly_window_started_at
        if window_start is None or timestamp - window_start >= timedelta(hours=1):
            trigger.hourly_window_started_at = timestamp
            trigger.hourly_fire_count = 0
        trigger.hourly_fire_count += 1
        trigger.last_fire_at = timestamp
        trigger._save_validated(
            using=self.db,
            update_fields={
                "last_fire_at",
                "hourly_window_started_at",
                "hourly_fire_count",
                "updated_at",
                *extra_update_fields,
            },
        )

    def _save_next_fire_locked(self, trigger: Any) -> None:
        trigger._save_validated(using=self.db, update_fields={"next_fire_at", "updated_at"})

    def set_enabled(self, caller: Any, *, enabled: bool) -> Any:
        """Change activation under lineage-before-trigger locks."""

        caller._require_record_access("write")
        trigger_id = caller.pk
        expected_workflow_id = caller.workflow_id
        alias = self.db
        discovered = system_queryset(self.model, using=alias, lock=None).filter(pk=trigger_id).values(
            "workflow_id"
        ).first()
        if discovered is None:
            raise self.model.DoesNotExist
        if discovered["workflow_id"] != expected_workflow_id:
            raise ValidationError({"workflow": "The trigger lineage changed before activation."})
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        with system_context(reason="workflows.triggers.activation"), transaction.atomic(using=alias):
            head = system_queryset(workflow_model, using=alias, lock=("self",)).get(
                pk=discovered["workflow_id"]
            )
            trigger = system_queryset(self.model, using=alias, lock=("self",)).get(pk=trigger_id)
            if trigger.workflow_id != head.pk:
                raise ValidationError({"workflow": "The trigger lineage changed during activation."})
            if not enabled:
                models.QuerySet.update(
                    system_queryset(self.model, using=alias, lock=None).filter(pk=trigger.pk),
                    enabled=False,
                    updated_at=timezone.now(),
                )
                trigger.enabled = False
                return trigger
            if workflow_model.objects.db_manager(alias).current_published_for(head) is None:
                raise ValidationError({"enabled": "Publish this workflow before enabling its trigger."})
            trigger.validated_config(require_publisher=True)
            trigger.enabled = True
            if trigger.kind == TriggerKind.SCHEDULE:
                trigger.next_fire_at = trigger.initial_fire_at(now=timezone.now())
            else:
                trigger.next_fire_at = None
            trigger.full_clean()
            trigger._save_validated(
                update_fields={"enabled", "event_model_label", "next_fire_at", "updated_at"}
            )
            return trigger

    def start_event(
        self,
        trigger_id: int,
        *,
        subject: models.Model,
        occurrence_id: str | None,
        timestamp: datetime,
    ) -> Any | None:
        """Atomically admit one matching event occurrence and its pinned run."""

        alias = self.db
        discovered = system_queryset(self.model, using=alias, lock=None).filter(pk=trigger_id).values(
            "workflow_id"
        ).first()
        if discovered is None:
            return None
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        run_model = self.model._meta.apps.get_model("workflows", "WorkflowRun")
        with system_context(reason="workflows.event_triggers.start"), transaction.atomic(using=alias):
            head = system_queryset(workflow_model, using=alias, lock=("self",)).get(
                pk=discovered["workflow_id"]
            )
            trigger = system_queryset(self.model, using=alias, lock=("self",)).filter(
                pk=trigger_id
            ).first()
            if (
                trigger is None
                or trigger.workflow_id != head.pk
                or trigger.kind != TriggerKind.EVENT
                or not trigger.enabled
            ):
                return None
            declaration = trigger.validated_config(require_publisher=True)
            if not isinstance(declaration, EventTriggerConfig):
                return None
            if subject._meta.label_lower != declaration.model:
                return None
            if not trigger.condition_matches(type(subject), subject):
                return None
            content_type = ContentType.objects.get_for_model(subject, for_concrete_model=False)
            occurrence_max_length = cast(int, run_model._meta.get_field("occurrence_id").max_length)
            retained_occurrence = (
                occurrence_id
                if isinstance(occurrence_id, str)
                and 0 < len(occurrence_id) <= occurrence_max_length
                else None
            )
            if declaration.admission_policy == EventAdmissionPolicy.EACH_CHANGE:
                if retained_occurrence is None:
                    return None
                dedup_key = f"event:{trigger.pk}:occurrence:{retained_occurrence}"
                if len(dedup_key) > cast(int, run_model._meta.get_field("dedup_key").max_length):
                    return None
            else:
                dedup_key = f"trigger:{trigger.pk}:subject:{content_type.pk}:{subject.pk}"
            existing = system_queryset(run_model, using=alias, lock=None).filter(
                dedup_key=dedup_key
            ).first()
            if existing is not None:
                return existing
            if not trigger.rate_limit_allows(timestamp=timestamp):
                return None
            run = run_model.objects._start_locked(
                head,
                subject,
                None,
                trigger=trigger,
                dedup_key=dedup_key,
                occurrence_id=retained_occurrence,
                input=JsonPresence(),
                available_at=timestamp,
                using=alias,
            )
            self._record_fire_locked(trigger, timestamp=timestamp)
            return run

    def claim_due_schedule(self, trigger_id: int, *, timestamp: datetime) -> tuple[Any, datetime] | None:
        """Lock and advance one due schedule trigger if rate limits allow it."""

        with system_context(reason="workflows.schedule_triggers.claim"), transaction.atomic():
            trigger = (
                self.lock_if_supported()
                .select_related("workflow")
                .filter(pk=trigger_id, kind=TriggerKind.SCHEDULE, enabled=True)
                .first()
            )
            if trigger is None or trigger.next_fire_at is None or trigger.next_fire_at > timestamp:
                return None
            due_at = trigger.next_fire_at
            trigger.next_fire_at = trigger.compute_next_fire_at(after=due_at, now=timestamp)
            if not trigger.rate_limit_allows(timestamp=timestamp):
                self._save_next_fire_locked(trigger)
                return None
            self._record_fire_locked(trigger, timestamp=timestamp, extra_update_fields=("next_fire_at",))
            return trigger, due_at

    def start_due_schedule(self, trigger_id: int, *, timestamp: datetime) -> tuple[Any, datetime] | None:
        """Claim one due occurrence and create its pinned durable start atomically."""

        alias = self.db
        discovered = system_queryset(self.model, using=alias, lock=None).filter(pk=trigger_id).values(
            "workflow_id"
        ).first()
        if discovered is None:
            return None
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        run_model = self.model._meta.apps.get_model("workflows", "WorkflowRun")
        with system_context(reason="workflows.schedule_triggers.start"), transaction.atomic(using=alias):
            head = system_queryset(workflow_model, using=alias, lock=("self",)).get(
                pk=discovered["workflow_id"]
            )
            trigger = system_queryset(self.model, using=alias, lock=("self",)).filter(pk=trigger_id).first()
            if (
                trigger is None
                or trigger.workflow_id != head.pk
                or trigger.kind != TriggerKind.SCHEDULE
                or not trigger.enabled
                or trigger.next_fire_at is None
                or trigger.next_fire_at > timestamp
            ):
                return None
            due_at = trigger.next_fire_at
            trigger.next_fire_at = trigger.compute_next_fire_at(after=due_at, now=timestamp)
            if not trigger.rate_limit_allows(timestamp=timestamp):
                self._save_next_fire_locked(trigger)
                return None
            self._record_fire_locked(trigger, timestamp=timestamp, extra_update_fields=("next_fire_at",))
            run = run_model.objects._start_locked(
                head,
                None,
                None,
                trigger=trigger,
                dedup_key=f"schedule:{trigger.pk}:{due_at.isoformat()}",
                input=JsonPresence(),
                available_at=timestamp,
                using=alias,
            )
            return run, due_at

    def prime_due_schedules(self, *, timestamp: datetime) -> int:
        """Persist initial fire times for enabled schedules missing ``next_fire_at``."""

        with system_context(reason="workflows.schedule_triggers.prime"):
            trigger_ids = list(
                self.filter(
                    kind=TriggerKind.SCHEDULE,
                    enabled=True,
                    next_fire_at__isnull=True,
                )
                .order_by("pk")
                .values_list("pk", flat=True)
            )

        primed = 0
        for trigger_id in trigger_ids:
            with system_context(reason="workflows.schedule_triggers.prime"), transaction.atomic():
                trigger = (
                    self.lock_if_supported()
                    .filter(pk=trigger_id, kind=TriggerKind.SCHEDULE, enabled=True, next_fire_at__isnull=True)
                    .first()
                )
                if trigger is None:
                    continue
                try:
                    trigger.next_fire_at = trigger.initial_fire_at(now=timestamp)
                except (ValueError, TypeError):
                    logger.exception(
                        "Skipping workflow schedule trigger %s after initial fire calculation failed.",
                        trigger.pk,
                    )
                    continue
                if trigger.next_fire_at is None:
                    continue
                self._save_next_fire_locked(trigger)
                primed += 1
        return primed


def check_event_trigger_change_publishers(
    app_configs: list[object] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Report persisted event triggers targeting models outside the change feed."""

    del app_configs, kwargs
    try:
        trigger_model = apps.get_model("workflows", "Trigger")
    except LookupError:
        return []
    try:
        published_labels = _change_publisher_model_labels()
        invalid = list(
            trigger_model._base_manager.filter(kind=TriggerKind.EVENT)
            .exclude(event_model_label="")
            .exclude(event_model_label__in=published_labels)
            .order_by("pk")
            .values_list("pk", "event_model_label")[:20]
        )
    except (OperationalError, ProgrammingError):
        return []
    return [
        checks.Error(
            f"Workflow trigger {pk} targets {label!r}, which is not in the change feed; {_CHANGE_FEED_FIX}.",
            obj=trigger_model,
            id="angee.workflows.E001",
        )
        for pk, label in invalid
    ]


def _change_publisher_model_labels() -> frozenset[str]:
    """Return model labels declared into GraphQL's change feed."""

    from angee.graphql.schema import GraphQLSchemas

    return GraphQLSchemas.from_discovery().change_publisher_model_labels()


class Trigger(AuditMixin, AngeeDataModel):
    """Start rule attached to a workflow lineage head.

    Event triggers consume the GraphQL change feed: their target model must
    declare ``changes()`` so publisher wiring and workflow delivery agree.
    """

    runtime = True

    sqid_prefix = "wft_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.CASCADE, related_name="triggers")
    kind = StateField(choices_enum=TriggerKind, default=TriggerKind.MANUAL)
    enabled = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)
    event_model_label = models.CharField(max_length=200, blank=True, default="")
    next_fire_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_fire_at = models.DateTimeField(null=True, blank=True)
    hourly_window_started_at = models.DateTimeField(null=True, blank=True)
    hourly_fire_count = models.PositiveIntegerField(default=0)

    objects = TriggerManager()

    class Meta:
        """Django model options for workflow triggers."""

        abstract = True
        ordering = ("workflow", "kind", "created_at")
        rebac_resource_type = "workflows/trigger"
        rebac_id_attr = "sqid"
        indexes = (
            models.Index(
                fields=("event_model_label",),
                condition=models.Q(kind=TriggerKind.EVENT, enabled=True),
                name="idx_wft_event_enabled",
            ),
        )

    def __str__(self) -> str:
        """Return the trigger's display label."""

        return f"{self.workflow_id}:{self.kind}"

    def clean(self) -> None:
        """Validate lineage ownership and trigger declaration shape."""

        super().clean()
        if self.workflow_id is not None and self.workflow.published_from_id is not None:
            raise ValidationError({"workflow": "Triggers attach only to workflow lineage heads."})
        declaration = self.validated_config(require_publisher=self.enabled)
        self._sync_index_fields(declaration)
        if self.enabled and type(self.workflow).objects.current_published_for(self.workflow) is None:
            raise ValidationError({"enabled": "Publish this workflow before enabling its trigger."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the trigger after model validation."""

        adding = self._state.adding
        if adding:
            self.enabled = False
        update_fields = kwargs.get("update_fields")
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        operational_fields = {
            "last_fire_at",
            "hourly_window_started_at",
            "hourly_fire_count",
            "next_fire_at",
            "updated_at",
        }
        if update_fields is not None and set(update_fields) <= operational_fields:
            raise RuntimeError("Trigger operational fields are written only by the owning manager.")
        old_workflow_id = None
        discovered_state: tuple[Any, Any, Any, Any] | None = None
        if not adding:
            discovered = system_queryset(type(self), using=alias, lock=None).filter(pk=self.pk).values(
                "workflow_id", "kind", "config", "enabled"
            ).first()
            if discovered is None:
                raise type(self).DoesNotExist
            old_workflow_id = discovered["workflow_id"]
            discovered_state = (
                discovered["workflow_id"],
                discovered["kind"],
                discovered["config"],
                discovered["enabled"],
            )
        workflow_ids = sorted({value for value in (old_workflow_id, self.workflow_id) if value is not None})
        with _definition_caller_context(self), transaction.atomic(using=alias):
            workflow_model = type(self)._meta.get_field("workflow").remote_field.model
            list(
                system_queryset(workflow_model, using=alias, lock=("self",))
                .filter(pk__in=workflow_ids)
                .order_by("pk")
            )
            persisted_rule: tuple[Any, Any] | None = None
            if not adding:
                locked = system_queryset(type(self), using=alias, lock=("self",)).get(pk=self.pk)
                if locked.workflow_id != old_workflow_id:
                    raise ValidationError({"workflow": "The trigger lineage changed during this edit."})
                locked_state = (locked.workflow_id, locked.kind, locked.config, locked.enabled)
                if locked_state != discovered_state:
                    raise ValidationError("The trigger changed during this edit; reload and try again.")
                fields = None if update_fields is None else set(update_fields)
                if fields is not None:
                    for field_name in ("workflow_id", "kind", "config", "enabled"):
                        public_name = "workflow" if field_name == "workflow_id" else field_name
                        if public_name not in fields and field_name not in fields:
                            setattr(self, field_name, getattr(locked, field_name))
                if self.enabled != locked.enabled:
                    raise ValidationError({"enabled": "Use the trigger enable or disable action."})
                persisted_rule = (locked.kind, locked.config)
                self.last_fire_at = locked.last_fire_at
                self.hourly_window_started_at = locked.hourly_window_started_at
                self.hourly_fire_count = locked.hourly_fire_count
                self.next_fire_at = locked.next_fire_at
            self.full_clean()
            if adding and self.kind == TriggerKind.EVENT and isinstance(self.config, dict):
                declaration = self.validated_config()
                if isinstance(declaration, EventTriggerConfig) and "admission_policy" not in self.config:
                    self.config = {**self.config, "admission_policy": str(declaration.admission_policy)}
            rule_changed = persisted_rule is not None and persisted_rule != (self.kind, self.config)
            cadence_changed = False
            if rule_changed and self.enabled and persisted_rule is not None:
                persisted_declaration = validate_trigger_config(cast(Any, persisted_rule[0]), persisted_rule[1])
                declaration = self.validated_config()
                persisted_cadence = (
                    persisted_declaration.cadence
                    if isinstance(persisted_declaration, ScheduleTriggerConfig)
                    else None
                )
                cadence = declaration.cadence if isinstance(declaration, ScheduleTriggerConfig) else None
                cadence_changed = persisted_cadence != cadence
            if rule_changed and self.enabled and cadence_changed:
                self.next_fire_at = (
                    self.initial_fire_at(now=timezone.now()) if self.kind == TriggerKind.SCHEDULE else None
                )
            if update_fields is not None:
                fields = set(update_fields)
                if {"kind", "config", "event_model_label"} & fields:
                    fields.update({"event_model_label", "next_fire_at"})
                kwargs["update_fields"] = fields
            self._save_validated(*args, **kwargs)

    def _save_validated(self, *args: Any, **kwargs: Any) -> None:
        """Persist after the owning locked path has completed validation."""

        super().save(*args, **kwargs)

    def enable(self) -> None:
        """Enable this trigger through the model owner."""

        enabled = type(self).objects.db_manager(self._state.db).set_enabled(self, enabled=True)
        self.enabled = enabled.enabled
        self.next_fire_at = enabled.next_fire_at

    def disable(self) -> None:
        """Disable this trigger through the model owner."""

        disabled = type(self).objects.db_manager(self._state.db).set_enabled(self, enabled=False)
        self.enabled = disabled.enabled
        self.next_fire_at = disabled.next_fire_at

    def rate_limit_allows(self, *, timestamp: datetime) -> bool:
        """Return whether this trigger can fire at ``timestamp``."""

        declaration = self.validated_config()
        cooldown_seconds = declaration.cooldown_seconds
        if cooldown_seconds and self.last_fire_at is not None:
            if self.last_fire_at + timedelta(seconds=cooldown_seconds) > timestamp:
                return False

        hourly_cap = declaration.hourly_cap
        if hourly_cap is None:
            return True
        window_start = self.hourly_window_started_at
        if window_start is None or timestamp - window_start >= timedelta(hours=1):
            return True
        return int(self.hourly_fire_count) < hourly_cap

    def record_fire(self, *, timestamp: datetime) -> None:
        """Record one trigger fire and persist rate-limit counters."""

        trigger = type(self).objects.db_manager(self._state.db).record_fire(self, timestamp=timestamp)
        self.last_fire_at = trigger.last_fire_at
        self.hourly_window_started_at = trigger.hourly_window_started_at
        self.hourly_fire_count = trigger.hourly_fire_count
        self.next_fire_at = trigger.next_fire_at

    def condition_matches(self, sender: type[models.Model], instance: models.Model) -> bool:
        """Return whether this event trigger matches a saved model instance."""

        condition = self.config_mapping.get("condition", {})
        if not isinstance(condition, Mapping):
            return False
        with system_context(reason="workflows.event_triggers.condition"):
            return sender._default_manager.filter(pk=instance.pk, **dict(condition)).exists()

    def initial_fire_at(self, *, now: datetime) -> datetime | None:
        """Return the first persisted due timestamp for this schedule trigger."""

        declaration = self.validated_config()
        if not isinstance(declaration, ScheduleTriggerConfig):
            return None
        return declaration.next_fire_at(after=now, now=now)

    def compute_next_fire_at(self, *, after: datetime, now: datetime) -> datetime | None:
        """Return the next scheduled occurrence after ``after`` and later than ``now``."""

        declaration = self.validated_config()
        if not isinstance(declaration, ScheduleTriggerConfig):
            return None
        return declaration.next_fire_at(after=after, now=now)

    def validated_config(self, *, require_publisher: bool = False) -> TriggerConfig:
        """Return the declaration-owned rule and optionally verify event delivery."""

        try:
            declaration = validate_trigger_config(cast(Any, self.kind), self.config)
        except (ValueError, TypeError) as error:
            raise ValidationError({"config": str(error)}) from error
        if require_publisher and isinstance(declaration, EventTriggerConfig):
            if declaration.model not in _change_publisher_model_labels():
                raise ValidationError(
                    {
                        "event_model_label": (
                            f"Event trigger target {declaration.model!r} is not in the change feed; "
                            f"{_CHANGE_FEED_FIX}."
                        )
                    }
                )
        return declaration

    def activation_blocker(self, *, has_current_publication: bool | None = None) -> str | None:
        """Return the rule or publication reason that prevents activation."""

        try:
            self.validated_config(require_publisher=True)
        except ValidationError as error:
            return "; ".join(error.messages)
        if has_current_publication is None:
            has_current_publication = type(self.workflow).objects.current_published_for(self.workflow) is not None
        if not has_current_publication:
            return "Publish this workflow before enabling its trigger."
        return None

    @property
    def config_mapping(self) -> Mapping[str, Any]:
        """Return trigger config when it is a JSON object."""

        return self.config if isinstance(self.config, Mapping) else {}

    def _sync_index_fields(self, declaration: TriggerConfig) -> None:
        """Mirror config-owned event declarations into indexed query fields."""

        self.event_model_label = declaration.model if isinstance(declaration, EventTriggerConfig) else ""


class WorkflowRun(AuditMixin, RecordRefMixin, AngeeDataModel):
    """One execution of a pinned published workflow version."""

    runtime = True

    record_ref_field_prefix = "subject"

    sqid_prefix = "wfr_"
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.PROTECT, related_name="runs")
    origin = StateField(choices_enum=RunOrigin, default=RunOrigin.UNKNOWN)
    trigger = models.ForeignKey(
        "workflows.Trigger",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    parent_step_run = models.ForeignKey(
        "workflows.StepRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_runs",
    )
    status = StateField(choices_enum=RunStatus, default=RunStatus.PENDING)
    subject_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    subject_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("subject_content_type", "subject_object_id")
    dedup_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    occurrence_id = models.CharField(max_length=255, null=True, blank=True, editable=False)
    test_request_actor_ref = models.CharField(max_length=255, blank=True, editable=False)
    test_scope = StateField(choices_enum=TestScope, blank=True, default="", editable=False)
    test_step = models.ForeignKey(
        "workflows.Step",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="selected_test_runs",
        editable=False,
    )
    test_source_step_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    test_repair_source_attempt = models.ForeignKey(
        "workflows.StepAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="repair_test_runs",
        editable=False,
    )
    recovery_source_attempt = models.ForeignKey(
        "workflows.StepAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recovery_runs",
        editable=False,
    )
    recovery_request_actor_ref = models.CharField(max_length=255, blank=True, editable=False)
    recovery_mode = models.CharField(max_length=32, blank=True, editable=False)
    input_present = models.BooleanField(default=False, editable=False)
    input = models.JSONField(null=True, blank=True, editable=False)
    wake_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deliveries = models.PositiveBigIntegerField(default=0)
    steps_taken = models.PositiveIntegerField(default=0)
    budget_spent = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    status_transitions = StateTransitions(
        status,
        {
            RunStatus.PENDING: [RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELED],
            RunStatus.RUNNING: [RunStatus.WAITING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED],
            RunStatus.WAITING: [RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED],
        },
    )

    objects = WorkflowRunManager()

    class Meta:
        """Django model options for workflow runs."""

        abstract = True
        ordering = ("-created_at", "sqid")
        rebac_resource_type = "workflows/run"
        rebac_id_attr = "sqid"
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(origin=RunOrigin.TEST)
                    | models.Q(test_repair_source_attempt__isnull=True)
                ),
                name="chk_wfr_test_repair_source",
            ),
            models.CheckConstraint(
                condition=models.Q(input_present=True) | models.Q(input__isnull=True),
                name="chk_wfr_absent_input_null",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(origin=RunOrigin.TEST, test_request_actor_ref__gt="")
                    | (~models.Q(origin=RunOrigin.TEST) & models.Q(test_request_actor_ref=""))
                ),
                name="chk_wfr_test_request_actor",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        origin=RunOrigin.TEST,
                        test_scope__in=("", TestScope.WHOLE),
                        test_step__isnull=True,
                        test_source_step_id__isnull=True,
                    )
                    | models.Q(
                        origin=RunOrigin.TEST,
                        test_scope=TestScope.NODE,
                        test_step__isnull=False,
                        test_source_step_id__isnull=False,
                    )
                    | (
                        ~models.Q(origin=RunOrigin.TEST)
                        & models.Q(
                            test_scope="",
                            test_step__isnull=True,
                            test_source_step_id__isnull=True,
                        )
                    )
                ),
                name="chk_wfr_test_scope",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        origin=RunOrigin.RECOVERY,
                        recovery_source_attempt__isnull=False,
                        recovery_request_actor_ref__gt="",
                        recovery_mode__gt="",
                    )
                    | (
                        ~models.Q(origin=RunOrigin.RECOVERY)
                        & models.Q(
                            recovery_source_attempt__isnull=True,
                            recovery_request_actor_ref="",
                            recovery_mode="",
                        )
                    )
                ),
                name="chk_wfr_recovery_identity",
            ),
            models.UniqueConstraint(
                fields=("parent_step_run",),
                condition=models.Q(parent_step_run__isnull=False),
                name="uniq_workflows_run_parent_step_run",
            ),
        )
        indexes = (models.Index(fields=("subject_content_type", "subject_object_id"), name="idx_wfr_subject_ref"),)

    @property
    def is_terminal(self) -> bool:
        """Return whether this run has reached a terminal status."""

        return self.status in RunStatus.TERMINAL

    def allows_test_step(self, step: Any) -> bool:
        """Return whether the pinned test scope admits one copied step."""

        if step is None or step.workflow_id != self.workflow_id:
            return False
        if self.origin != RunOrigin.TEST or self.test_scope in {"", TestScope.WHOLE}:
            return True
        if self.test_scope != TestScope.NODE or self.test_step_id is None:
            return False
        from angee.workflows.graph import GraphIdentity, WorkflowGraph

        with system_context(reason="workflows.test_scope.plan"):
            plan = WorkflowGraph.from_workflow(self.workflow).test_plan(
                GraphIdentity(existing_id=self.test_step_id)
            )
        return GraphIdentity(existing_id=step.pk) in plan.executable

    @classmethod
    def waiting_projection_annotation(cls) -> dict[str, Any]:
        """Return declared active wait kind and the next genuine scheduled wake."""

        step_run = cls._meta.apps.get_model("workflows", "StepRun")
        waiting = step_run.objects.filter(run_id=models.OuterRef("pk"), status=StepRunStatus.WAITING)
        scheduled = waiting.filter(waiting_kind=WaitingKind.SCHEDULED)
        return {
            "_workflow_waiting_kind": models.Case(
                models.When(~models.Q(status=RunStatus.WAITING), then=models.Value("")),
                models.When(
                    models.Exists(waiting.filter(waiting_kind=WaitingKind.APPROVAL)),
                    then=models.Value(WaitingKind.APPROVAL),
                ),
                models.When(
                    models.Exists(waiting.filter(waiting_kind=WaitingKind.EXTERNAL)),
                    then=models.Value(WaitingKind.EXTERNAL),
                ),
                models.When(
                    models.Exists(waiting.filter(waiting_kind=WaitingKind.CHILDREN)),
                    then=models.Value(WaitingKind.CHILDREN),
                ),
                models.When(models.Exists(scheduled), then=models.Value(WaitingKind.SCHEDULED)),
                default=models.Value(""),
                output_field=models.CharField(),
            ),
            "_workflow_next_wake_at": models.Subquery(
                scheduled.filter(run__status=RunStatus.WAITING)
                .order_by("wait_until", "pk")
                .values("wait_until")[:1],
                output_field=models.DateTimeField(),
            ),
        }

    def awaiting_decision(self) -> bool:
        """Return whether this run has an unresolved workflow decision."""

        return self.step_runs.filter(decisions__verdict=Verdict.PENDING).exists()

    @transition(status, source=RunStatus.PENDING, target=RunStatus.RUNNING, on_success=save_state)
    def mark_running(self) -> None:
        """Mark a pending run as actively orchestrating."""

    @transition(status, source=RunStatus.WAITING, target=RunStatus.RUNNING, on_success=save_state)
    def resume(self) -> None:
        """Mark a waiting run as actively orchestrating again."""

    @transition(
        status,
        source=RunStatus.RUNNING,
        target=RunStatus.WAITING,
        on_success=save_state,
    )
    def mark_waiting(self, *, wake_at: Any = None) -> None:
        """Mark a run as waiting on durable external or timer state."""

        self.wake_at = wake_at
        self._transition_fields = {"wake_at"}

    @transition(
        status,
        source=[RunStatus.RUNNING, RunStatus.WAITING],
        target=RunStatus.SUCCEEDED,
        on_success=save_state,
    )
    def mark_succeeded(self) -> None:
        """Mark a run as successful."""

        self.wake_at = None
        self._transition_fields = {"wake_at"}

    @transition(
        status,
        source=[RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING],
        target=RunStatus.FAILED,
        on_success=save_state,
    )
    def mark_failed(self, error: str = "") -> None:
        """Mark a run as failed with an optional durable error message."""

        self.error = error
        self.wake_at = None
        self._transition_fields = {"error", "wake_at"}

    @transition(
        status,
        source=[RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING],
        target=RunStatus.CANCELED,
        on_success=save_state,
    )
    def mark_canceled(self) -> None:
        """Mark a run as canceled."""

        self.wake_at = None
        self._transition_fields = {"wake_at"}

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the run while keeping start identity and input immutable."""

        self._raise_if_dedup_key_changed()
        self._raise_if_test_identity_changed()
        if not self._state.adding and (
            not hasattr(self, "_loaded_input") or not hasattr(self, "_loaded_input_present")
        ):
            loaded = (
                system_queryset(type(self), using=self._state.db, lock=None)
                .values("input", "input_present")
                .get(pk=self.pk)
            )
            self._loaded_input = copy.deepcopy(loaded["input"])
            self._loaded_input_present = loaded["input_present"]
        if (
            not self._state.adding
            and (
                getattr(self, "_loaded_input_present", self.input_present) != self.input_present
                or getattr(self, "_loaded_input", self.input) != self.input
            )
        ):
            raise ValidationError("Workflow run input is immutable.")
        super().save(*args, **kwargs)
        self._loaded_input_present = self.input_present
        self._loaded_input = copy.deepcopy(self.input)
        self._loaded_test_identity = self._test_identity()

    def _test_identity(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "origin": str(self.origin),
            "subject_content_type_id": self.subject_content_type_id,
            "subject_object_id": self.subject_object_id,
            "test_request_actor_ref": self.test_request_actor_ref,
            "test_scope": self.test_scope,
            "test_step_id": self.test_step_id,
            "test_source_step_id": self.test_source_step_id,
            "test_repair_source_attempt_id": self.test_repair_source_attempt_id,
        }

    def _raise_if_test_identity_changed(self) -> None:
        """Keep a test request's admission identity immutable after creation."""

        if self._state.adding:
            if self.origin == RunOrigin.TEST and not self.test_request_actor_ref:
                raise ValidationError({"test_request_actor_ref": "Test runs require their requesting actor."})
            if self.origin != RunOrigin.TEST and self.test_request_actor_ref:
                raise ValidationError({"test_request_actor_ref": "Only test runs have a requesting actor."})
            if self.origin != RunOrigin.TEST and self.test_repair_source_attempt_id is not None:
                raise ValidationError(
                    {"test_repair_source_attempt": "Only test runs retain repair source evidence."}
                )
            if self.origin == RunOrigin.TEST:
                if self.test_scope not in TestScope:
                    raise ValidationError({"test_scope": "Test runs require a declared scope."})
                if self.test_scope == TestScope.NODE and (
                    self.test_step_id is None or self.test_source_step_id is None
                ):
                    raise ValidationError({"test_step": "Node test runs require an exact selected step."})
                if self.test_scope == TestScope.WHOLE and (
                    self.test_step_id is not None or self.test_source_step_id is not None
                ):
                    raise ValidationError({"test_step": "Whole workflow tests cannot select one step."})
                if self.test_step_id is not None and self.test_step.workflow_id != self.workflow_id:
                    raise ValidationError({"test_step": "The selected test step must belong to the pinned snapshot."})
            elif self.test_scope or self.test_step_id is not None or self.test_source_step_id is not None:
                raise ValidationError({"test_scope": "Only test runs carry test scope facts."})
            return
        current = self._test_identity()
        loaded = getattr(self, "_loaded_test_identity", None)
        if loaded is None:
            loaded = system_queryset(type(self), using=self._state.db, lock=None).values(
                *current.keys()
            ).get(pk=self.pk)
        if (
            loaded["origin"] == str(RunOrigin.TEST)
            or self.origin == RunOrigin.TEST
            or loaded["test_repair_source_attempt_id"] is not None
            or self.test_repair_source_attempt_id is not None
        ) and loaded != current:
            raise ValidationError("Workflow test request identity is immutable.")

    @classmethod
    def from_db(cls, db: str | None, field_names: list[str], values: list[Any]) -> Self:
        """Capture immutable loaded facts without a save-time SELECT."""

        instance = cast(Self, super().from_db(db, field_names, values))
        if "dedup_key" in field_names:
            instance._loaded_dedup_key = values[field_names.index("dedup_key")]
        if "occurrence_id" in field_names:
            instance._loaded_occurrence_id = values[field_names.index("occurrence_id")]
        if "input_present" in field_names:
            instance._loaded_input_present = values[field_names.index("input_present")]
        if "input" in field_names:
            instance._loaded_input = copy.deepcopy(values[field_names.index("input")])
        identity_fields = (
            "workflow_id",
            "origin",
            "subject_content_type_id",
            "subject_object_id",
            "test_request_actor_ref",
            "test_scope",
            "test_step_id",
            "test_source_step_id",
            "test_repair_source_attempt_id",
        )
        if all(field in field_names for field in identity_fields):
            instance._loaded_test_identity = {
                field: str(values[field_names.index(field)]) if field == "origin" else values[field_names.index(field)]
                for field in identity_fields
            }
        return instance

    def _raise_if_dedup_key_changed(self) -> None:
        """Reject updates that alter the immutable trigger-start dedup key."""

        if self._state.adding:
            return
        loaded_dedup_key = getattr(self, "_loaded_dedup_key", self.dedup_key)
        if loaded_dedup_key != self.dedup_key:
            raise ValidationError({"dedup_key": "Workflow run dedup keys are immutable."})
        loaded_occurrence_id = getattr(self, "_loaded_occurrence_id", self.occurrence_id)
        if loaded_occurrence_id != self.occurrence_id:
            raise ValidationError({"occurrence_id": "Workflow run occurrence identities are immutable."})

    def debit_budget(self, delta: Mapping[str, int]) -> None:
        """Atomically add usage deltas to this run's budget ledger."""

        if not delta:
            return
        locked = type(self).objects.lock_if_supported().get(pk=self.pk)
        spent = dict(locked.budget_spent or {})
        for key, value in delta.items():
            spent[str(key)] = int(spent.get(str(key), 0)) + int(value)
        locked.budget_spent = spent
        locked.save(update_fields=["budget_spent", "updated_at"])


_test_fixture_write_run: ContextVar[tuple[str, int, int, int] | None] = ContextVar(
    "workflow_test_fixture_write_run", default=None
)
_test_fixture_batch_rows: ContextVar[frozenset[int]] = ContextVar(
    "workflow_test_fixture_batch_rows", default=frozenset()
)
_test_fixture_apply_ids: ContextVar[frozenset[int]] = ContextVar(
    "workflow_test_fixture_apply_ids", default=frozenset()
)
_recovery_write_run: ContextVar[tuple[str, int, int, int] | None] = ContextVar(
    "workflow_recovery_write_run", default=None
)
_recovery_evidence_batch: ContextVar[frozenset[int]] = ContextVar(
    "workflow_recovery_evidence_batch", default=frozenset()
)
_recovery_evidence_write_row: ContextVar[tuple[int, int, int, int, int] | None] = ContextVar(
    "workflow_recovery_evidence_write_row", default=None
)


class WorkflowTestFixtureQuerySet(AngeeQuerySet[Any]):
    """Keep admitted test fixture facts append-only."""

    def update(self, **kwargs: Any) -> int:
        audit_fields = frozenset(field.name for field in AuditMixin._meta.fields)
        if kwargs and set(kwargs).issubset(audit_fields) and all(value is None for value in kwargs.values()):
            return super().update(**kwargs)
        raise TypeError("Workflow test fixtures are immutable admission facts.")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Workflow test fixtures can only be created by WorkflowRunManager.")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        raise TypeError("Workflow test fixtures are immutable admission facts.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise TypeError("Workflow test fixtures are retained admission facts.")


class WorkflowTestFixtureManager(AngeeManager.from_queryset(WorkflowTestFixtureQuerySet)):  # type: ignore[misc]
    """Persist a fully validated fixture batch for one newly created test run."""

    def _create_batch(self, run: Any, rows: Iterable[Any]) -> tuple[Any, ...]:
        if run.pk is None or run.origin != RunOrigin.TEST:
            raise ValidationError({"run": "Fixtures require a persisted workflow test run."})
        connection = connections[self.db]
        if not connection.in_atomic_block:
            raise RuntimeError("Fixture admission requires the owning run transaction.")
        authority = _test_fixture_write_run.get()
        expected = (self.db, id(connection), id(connection.atomic_blocks[0]), run.pk)
        if authority != expected:
            raise RuntimeError("Fixture admission requires the exact owning run transaction.")
        prepared = tuple(rows)
        expected_rows = frozenset(id(row) for row in prepared)
        if _test_fixture_batch_rows.get() != expected_rows:
            raise RuntimeError("Fixture admission requires the exact prepared batch.")
        _test_fixture_batch_rows.set(frozenset())
        for row in prepared:
            row.run = run
            row.full_clean()
        created: list[Any] = []
        for row in prepared:
            row._fixture_owner_write = expected
            row.save(force_insert=True, using=self.db)
            created.append(row)
        return tuple(created)


class WorkflowTestFixture(AuditMixin, AngeeDataModel):
    """Immutable manual or captured evidence admitted for one workflow test."""

    runtime = True
    sqid_prefix = "wtf_"

    run = models.ForeignKey("workflows.WorkflowRun", on_delete=models.PROTECT, related_name="test_fixtures")
    step = models.ForeignKey("workflows.Step", on_delete=models.PROTECT, related_name="test_fixtures")
    role = StateField(choices_enum=TestFixtureRole)
    item_index = models.PositiveIntegerField(null=True, blank=True, editable=False)
    value_present = models.BooleanField(default=False, editable=False)
    value = models.JSONField(null=True, blank=True, editable=False)
    outcome = models.SlugField(max_length=100, blank=True, default="", editable=False)
    captured_attempt = models.ForeignKey(
        "workflows.StepAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="test_fixture_captures",
        editable=False,
    )

    objects = WorkflowTestFixtureManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        ordering = ("run_id", "step_id", "role", "item_index", "pk")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(value_present=True) | models.Q(value__isnull=True),
                name="chk_wtf_absent_value_null",
            ),
            models.UniqueConstraint(
                fields=("run", "step", "role", "item_index"),
                name="uniq_workflows_test_fixture_slot",
                nulls_distinct=False,
            ),
        )

    def clean(self) -> None:
        super().clean()
        if self.run_id is not None and self.step_id is not None:
            if self.run.origin != RunOrigin.TEST or self.step.workflow_id != self.run.workflow_id:
                raise ValidationError({"step": "Fixture steps must belong to the pinned test snapshot."})
        try:
            validate_json_presence(
                JsonPresence(self.value_present, self.value), label="test fixture value"
            )
        except ValueError as error:
            raise ValidationError({"value": str(error)}) from error

    def save(self, *args: Any, **kwargs: Any) -> None:
        alias = kwargs.get("using") or self._state.db or "default"
        connection = connections[alias]
        expected = (
            (alias, id(connection), id(connection.atomic_blocks[0]), self.run_id)
            if connection.in_atomic_block
            else None
        )
        if (
            not self._state.adding
            or expected is None
            or _test_fixture_write_run.get() != expected
            or getattr(self, "_fixture_owner_write", None) != expected
        ):
            raise TypeError("Workflow test fixtures can only be written by WorkflowRunManager.")
        del self._fixture_owner_write
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Workflow test fixtures are retained admission facts.")


class WorkflowRecoveryEvidenceQuerySet(AngeeQuerySet[Any]):
    """Immutable accepted predecessor evidence for one recovery run."""

    def update(self, **kwargs: Any) -> int:
        raise TypeError("Recovery evidence is immutable admission data.")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Recovery evidence can only be created by WorkflowRunManager.")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        raise TypeError("Recovery evidence is immutable admission data.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise TypeError("Recovery evidence is immutable admission data.")


class WorkflowRecoveryEvidenceManager(AngeeManager.from_queryset(WorkflowRecoveryEvidenceQuerySet)):  # type: ignore[misc]
    def _create_for_run(self, run: Any, attempts: tuple[Any, ...], *, using: str) -> tuple[Any, ...]:
        connection = connections[using]
        owner = _recovery_write_run.get()
        expected = (using, id(connection), id(connection.atomic_blocks[0]), run.pk)
        attempt_ids = frozenset(attempt.pk for attempt in attempts)
        if (
            owner != expected
            or _recovery_evidence_batch.get() != attempt_ids
            or run.origin != RunOrigin.RECOVERY
        ):
            raise RuntimeError("Recovery evidence requires its exact run admission transaction.")
        _recovery_evidence_batch.set(frozenset())
        rows: list[Any] = []
        for attempt in attempts:
            row = self.model(
                run=run,
                source_attempt=attempt,
                step=attempt.step_run.step,
                map_index=attempt.step_run.map_index,
            )
            token = _recovery_evidence_write_row.set(
                (id(row), run.pk, attempt.pk, attempt.step_run.step_id, attempt.step_run.map_index)
            )
            try:
                row.save(using=using, force_insert=True)
            finally:
                _recovery_evidence_write_row.reset(token)
            rows.append(row)
        return tuple(rows)


class WorkflowRecoveryEvidence(AuditMixin, AngeeDataModel):
    """Protected link to an accepted predecessor result reused by recovery."""

    runtime = True
    sqid_prefix = "wre_"
    run = models.ForeignKey("workflows.WorkflowRun", on_delete=models.PROTECT, related_name="recovery_evidence")
    source_attempt = models.ForeignKey(
        "workflows.StepAttempt", on_delete=models.PROTECT, related_name="reused_by_recoveries"
    )
    step = models.ForeignKey("workflows.Step", on_delete=models.PROTECT, related_name="recovery_evidence")
    map_index = models.IntegerField(default=-1, editable=False)
    objects = WorkflowRecoveryEvidenceManager()

    class Meta:
        abstract = True
        ordering = ("run_id", "step_id", "map_index")
        rebac_resource_type = "workflows/recovery_evidence"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(fields=("run", "step", "map_index"), name="uniq_wre_run_step_slot"),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        expected = (
            id(self), self.run_id, self.source_attempt_id, self.step_id, self.map_index
        )
        if not self._state.adding or _recovery_evidence_write_row.get() != expected:
            raise TypeError("Recovery evidence can only be saved by WorkflowRunManager.")
        _recovery_evidence_write_row.set(None)
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Recovery evidence is immutable admission data.")


class StepRunQuerySet(AngeeQuerySet[Any]):
    """Step-run collection writes protecting attempt-owned execution facts."""

    _attempt_owned = frozenset(
        {
            "status",
            "run",
            "run_id",
            "step",
            "step_id",
            "map_index",
            "input",
            "output",
            "resume_state",
            "claimed_deliveries",
            "outcome",
            "attempt",
            "current_attempt",
            "current_attempt_id",
            "current_map_expansion",
            "current_map_expansion_id",
            "effect_key",
            "effect_generation",
            "wait_until",
            "waiting_kind",
            "heartbeat_at",
            "error",
            "stacktrace",
        }
    )

    def update(self, **kwargs: Any) -> int:
        if self._attempt_owned.intersection(kwargs):
            raise TypeError("Attempt-owned StepRun fields can only be changed by StepAttemptManager.")
        return super().update(**kwargs)

    def bulk_update(self, objs: Iterable[Any], fields: Iterable[str], **kwargs: Any) -> int:
        field_names = tuple(fields)
        if self._attempt_owned.intersection(field_names):
            raise TypeError("Attempt-owned StepRun fields can only be changed by StepAttemptManager.")
        return super().bulk_update(objs, field_names, **kwargs)

    def bulk_create(
        self,
        objs: Iterable[Any],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Collection[str] | None = None,
        unique_fields: Collection[str] | None = None,
    ) -> list[Any]:
        """Reject retained identity initialization through collection inserts."""

        rows = list(objs)
        if any(
            row.attempt != 0
            or row.current_attempt_id is not None
            or row.current_map_expansion_id is not None
            or row.effect_key is not None
            or row.effect_generation != 0
            for row in rows
        ):
            raise TypeError("Attempt identity can only be initialized by StepAttemptManager.")
        return super().bulk_create(
            rows,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


def decision_policy_outcome(step_run: Any, decisions: list[Any]) -> str | None:
    """Derive the authoritative outcome from a suspension's complete decision set."""

    gate = step_run.resume_state.get("gate")
    policy = str(gate.get("policy", "one_done") or "one_done") if isinstance(gate, dict) else "one_done"
    terminal = [decision for decision in decisions if decision.verdict in Verdict.TERMINAL]
    if not decisions or not terminal:
        return None
    if policy == "one_done":
        return str(terminal[0].verdict.value)
    if policy == "all_success":
        for verdict in (Verdict.REJECTED, Verdict.ESCALATED, Verdict.EXPIRED):
            if any(decision.verdict == verdict for decision in terminal):
                return str(verdict)
        return "completed" if len(terminal) == len(decisions) else None
    if policy == "all_done":
        return "completed" if len(terminal) == len(decisions) else None
    if policy == "majority":
        for verdict in (Verdict.ESCALATED, Verdict.EXPIRED):
            if any(decision.verdict == verdict for decision in terminal):
                return str(verdict)
        threshold = len(decisions) // 2 + 1
        completed = sum(decision.verdict == Verdict.COMPLETED for decision in terminal)
        rejected = sum(decision.verdict == Verdict.REJECTED for decision in terminal)
        if completed >= threshold:
            return "completed"
        if rejected >= threshold:
            return "rejected"
        return "rejected" if len(terminal) == len(decisions) else None
    if policy == "sequential":
        for decision in terminal:
            if decision.verdict != Verdict.COMPLETED:
                return str(decision.verdict.value)
        return "completed" if len(terminal) == len(decisions) else None
    return None


class StepRunManager(AngeeManager.from_queryset(StepRunQuerySet)):  # type: ignore[misc]
    """Manager preserving existing StepRun creation with guarded attempt facts."""

    def bind_map_membership(
        self,
        *,
        run_id: int,
        target_id: int,
        expansion_attempt_id: int,
        item_count: int,
        at: datetime,
    ) -> tuple[Any, ...]:
        """Bind the current Map generation to its complete body-slot membership."""

        alias = self.db
        attempt_model = self.model._meta.get_field("current_attempt").remote_field.model
        run_model = self.model._meta.get_field("run").remote_field.model
        with transaction.atomic(using=alias), system_context(reason="workflows.map.membership"):
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
            locked_rows = list(
                system_queryset(self.model, using=alias, lock=("self",)).filter(
                    run_id=run.pk
                ).order_by("pk")
            )
            rows = [
                row for row in locked_rows
                if row.step_id == target_id and row.map_index >= 0
            ]
            expansion = system_queryset(attempt_model, using=alias, lock=("self",)).get(
                pk=expansion_attempt_id
            )
            controller = next((row for row in locked_rows if row.pk == expansion.step_run_id), None)
            checkpoint = expansion.checkpoint if expansion.checkpoint_present else None
            map_state = checkpoint.get("map") if isinstance(checkpoint, dict) else None
            if not isinstance(map_state, dict):
                raise ValidationError({"map": "Map aggregate requires retained expansion facts."})
            items = map_state.get("items")
            declared_target_id = (
                map_state.get("target_step_id") if isinstance(map_state, dict) else None
            )
            if (
                controller is None
                or controller.current_attempt_id != expansion.pk
                or controller.status != StepRunStatus.WAITING
                or controller.effect_generation != expansion.effect_generation
                or expansion.cause != str(AttemptCause.MAP_ENGINE)
                or expansion.result_kind != str(AttemptResultKind.WAIT)
                or expansion.applied_at is None
                or expansion.lease_revoked_at is not None
                or not isinstance(items, list)
                or len(items) != item_count
                or declared_target_id != target_id
            ):
                raise ValidationError({"attempt": "Map membership requires this run's expansion wait."})
            current_attempt_ids = sorted(
                row.current_attempt_id for row in rows if row.current_attempt_id is not None
            )
            current_attempts = {}
            if current_attempt_ids:
                current_attempts = {
                    attempt.pk: attempt
                    for attempt in (
                    system_queryset(attempt_model, using=alias, lock=("self",))
                    .filter(pk__in=current_attempt_ids)
                    .order_by("pk")
                    )
                }
            current: list[Any] = []
            for row in rows:
                wanted = row.map_index < item_count
                if (
                    wanted
                    and row.current_map_expansion_id != expansion.pk
                    and (row.status != StepRunStatus.SCHEDULED or row.is_retained)
                    and not (
                        row.current_attempt_id in current_attempts
                        and current_attempts[row.current_attempt_id].cause
                        == str(AttemptCause.TEST_FIXTURE)
                        and current_attempts[row.current_attempt_id].test_fixture_id is not None
                    )
                ):
                    item = items[row.map_index]
                    child_input = map_child_input(item)
                    row = self.reschedule_for_override(row.pk, input=child_input, at=at)
                elif not wanted and row.status in StepRunStatus.ACTIVE:
                    attempt_model.objects.cancel_current(row.pk, at=at)
                    row.refresh_from_db()
                with attempt_model.objects._write(alias, row.pk):
                    row.current_map_expansion_id = expansion.pk if wanted else None
                    attempt_model.objects._write_step_run(
                        row,
                        alias=alias,
                        operation=lambda row=row: row.save(
                            using=alias,
                            update_fields=["current_map_expansion", "updated_at"],
                        ),
                    )
                if wanted:
                    current.append(row)
            return tuple(sorted(current, key=lambda row: row.map_index))

    def reschedule_for_override(self, step_run_id: int, *, input: Any, at: datetime) -> Any:
        """Fence one retained generation and reopen its logical slot."""

        attempt_model = self.model._meta.get_field("current_attempt").remote_field.model
        alias = self.db
        with (
            transaction.atomic(using=alias),
            attempt_model.objects._write(alias, step_run_id),
            system_context(reason="workflows.step_run.override"),
        ):
            run_id = system_queryset(self.model, using=alias, lock=None).values_list("run_id", flat=True).get(
                pk=step_run_id
            )
            run_model = self.model._meta.get_field("run").remote_field.model
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
            if run.is_terminal:
                raise ValidationError({"run": "A terminal workflow run cannot be overridden."})
            step_run = system_queryset(self.model, using=alias, lock=("self",)).get(pk=step_run_id)
            if step_run.current_attempt_id is not None:
                attempt = system_queryset(attempt_model, using=alias, lock=("self",)).get(
                    pk=step_run.current_attempt_id
                )
                if attempt.result_recorded_at is None and attempt.lease_revoked_at is None:
                    attempt.lease_revoked_at = at
                    attempt.lease_revocation_reason = str(LeaseRevocationReason.SUPERSEDED)
                    attempt_model.objects._save_attempt(
                        attempt,
                        alias=alias,
                        update_fields=["lease_revoked_at", "lease_revocation_reason", "updated_at"],
                    )
            if step_run.status not in StepRunStatus.TERMINAL:
                attempt_model.objects._write_step_run(
                    step_run, alias=alias, operation=step_run.mark_canceled
                )
            attempt_model.objects._write_step_run(
                step_run,
                alias=alias,
                operation=lambda: step_run.reschedule_for_override(input=input),
            )
            step_run.current_map_expansion = None
            step_run.effect_generation += 1
            step_run.effect_key = uuid.uuid4()
            attempt_model.objects._write_step_run(
                step_run,
                alias=alias,
                operation=lambda: step_run.save(
                    using=alias,
                    update_fields=[
                        "current_map_expansion",
                        "effect_generation",
                        "effect_key",
                        "updated_at",
                    ],
                ),
            )
            return step_run

    def settle_retained_decisions(
        self, step_run_id: int, *, outcome: str, decision_ids: tuple[int, ...], at: datetime
    ) -> Any:
        """Project one applied suspension's completed decision policy."""

        decision_model = self.model._meta.apps.get_model("workflows", "Decision")
        active_resolution = _decision_resolution_session.get()
        if active_resolution is None:
            raise RuntimeError("Decision settlement requires the retained resolution owner.")
        resolution_session = decision_model.objects._require_resolution_owner(
            active_resolution.decision_id
        )
        attempt_model = self.model._meta.get_field("current_attempt").remote_field.model
        alias = self.db
        with (
            transaction.atomic(using=alias),
            attempt_model.objects._write(alias, step_run_id),
            system_context(reason="workflows.step_run.decisions"),
        ):
            run, step_run = attempt_model.objects._locked_ancestry(step_run_id, alias)
            if step_run.current_attempt_id is None or step_run.status != StepRunStatus.WAITING or run.is_terminal:
                raise ValidationError({"step_run": "Decision settlement requires the current retained wait."})
            attempt = system_queryset(attempt_model, using=alias, lock=("self",)).get(
                pk=step_run.current_attempt_id
            )
            if (
                attempt.result_kind != str(AttemptResultKind.SUSPEND)
                or attempt.applied_at is None
                or attempt.lease_revoked_at is not None
                or attempt.effect_generation != step_run.effect_generation
            ):
                raise ValidationError({"step_run": "Decision settlement requires an applied suspension."})
            decision_model = step_run.decisions.model
            decisions = list(
                system_queryset(decision_model, using=alias, lock=("self",))
                .filter(suspension_attempt=attempt)
                .order_by("priority", "pk")
            )
            if tuple(decision.pk for decision in decisions) != decision_ids:
                raise ValidationError({"decisions": "Decision settlement does not match this suspension."})
            if resolution_session.decision_id not in decision_ids:
                raise ValidationError({"decisions": "The resolving decision is outside this suspension."})
            authoritative = decision_policy_outcome(step_run, decisions)
            if authoritative is None or authoritative != outcome:
                raise ValidationError({"outcome": "Decision outcome must be derived from the full policy."})
            if step_run.resume_state.get("_resume_after_decisions"):
                state = dict(step_run.resume_state)
                state["_decision_outcome"] = outcome
                step_run.resume_state = state
                attempt_model.objects._write_step_run(
                    step_run, alias=alias, operation=lambda: step_run.wake(at=at)
                )
            else:
                attempt_model.objects._write_step_run(
                    step_run,
                    alias=alias,
                    operation=lambda: step_run.mark_succeeded(
                        output={"decisions": [decision.sqid for decision in decisions]},
                        outcome=outcome,
                    ),
                )
            return step_run

    def fail_retained_decisions(self, step_run_id: int, *, error: str) -> Any:
        """Fail one applied suspension after exhausting invalid resolutions."""

        decision_model = self.model._meta.apps.get_model("workflows", "Decision")
        active_resolution = _decision_resolution_session.get()
        if active_resolution is None:
            raise RuntimeError("Decision failure requires the retained resolution owner.")
        resolution_session = decision_model.objects._require_resolution_owner(
            active_resolution.decision_id
        )
        attempt_model = self.model._meta.get_field("current_attempt").remote_field.model
        alias = self.db
        with (
            transaction.atomic(using=alias),
            attempt_model.objects._write(alias, step_run_id),
            system_context(reason="workflows.step_run.decision_failure"),
        ):
            run, step_run = attempt_model.objects._locked_ancestry(step_run_id, alias)
            if step_run.current_attempt_id is None or step_run.status != StepRunStatus.WAITING or run.is_terminal:
                raise ValidationError({"step_run": "Decision failure requires the current retained wait."})
            attempt = system_queryset(attempt_model, using=alias, lock=("self",)).get(
                pk=step_run.current_attempt_id
            )
            if (
                attempt.result_kind != str(AttemptResultKind.SUSPEND)
                or attempt.applied_at is None
                or attempt.lease_revoked_at is not None
                or attempt.effect_generation != step_run.effect_generation
            ):
                raise ValidationError({"step_run": "Decision failure requires an applied suspension."})
            decision_model = step_run.decisions.model
            decision = system_queryset(decision_model, using=alias, lock=("self",)).get(
                pk=resolution_session.decision_id,
                suspension_attempt=attempt,
            )
            if decision.max_attempts is None or decision.attempts < decision.max_attempts:
                raise ValidationError({"decision": "Decision failure requires exhausted attempts."})
            attempt_model.objects._write_step_run(
                step_run,
                alias=alias,
                operation=lambda: step_run.mark_failed(error=error, stacktrace=""),
            )
            return step_run


class StepRun(AuditMixin, AngeeDataModel):
    """Journal row for one workflow step execution or system-injected event."""

    runtime = True

    sqid_prefix = "wsr_"
    run = models.ForeignKey("workflows.WorkflowRun", on_delete=models.CASCADE, related_name="step_runs")
    step = models.ForeignKey(
        "workflows.Step",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="step_runs",
    )
    system_kind = models.SlugField(max_length=100, blank=True, default="")
    map_index = models.IntegerField(default=-1)
    status = StateField(choices_enum=StepRunStatus, default=StepRunStatus.SCHEDULED)
    previous = models.ManyToManyField("self", symmetrical=False, blank=True, related_name="next_step_runs")
    input = models.JSONField(default=dict, blank=True)
    output = models.JSONField(default=dict, blank=True)
    resume_state = models.JSONField(default=dict, blank=True)
    claimed_deliveries = models.PositiveBigIntegerField(default=0)
    outcome = models.SlugField(max_length=100, blank=True, default="")
    attempt = models.PositiveIntegerField(default=0)
    effect_key = models.UUIDField(null=True, blank=True, editable=False)
    effect_generation = models.PositiveIntegerField(default=0, editable=False)
    current_attempt = models.OneToOneField(
        "workflows.StepAttempt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_for_step_run",
        editable=False,
    )
    current_map_expansion = models.ForeignKey(
        "workflows.StepAttempt",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="current_map_members",
        editable=False,
    )
    wait_until = models.DateTimeField(null=True, blank=True, db_index=True)
    waiting_kind = StateField(choices_enum=WaitingKind, blank=True, default="")
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    stacktrace = models.TextField(blank=True)

    status_transitions = StateTransitions(
        status,
        {
            StepRunStatus.SCHEDULED: [
                StepRunStatus.STARTED,
                StepRunStatus.CANCELED,
                StepRunStatus.SKIPPED,
            ],
            StepRunStatus.STARTED: [
                StepRunStatus.WAITING,
                StepRunStatus.SUCCEEDED,
                StepRunStatus.FAILED,
                StepRunStatus.CANCELED,
            ],
            StepRunStatus.WAITING: [
                StepRunStatus.STARTED,
                StepRunStatus.SUCCEEDED,
                StepRunStatus.FAILED,
                StepRunStatus.CANCELED,
                StepRunStatus.SKIPPED,
            ],
            StepRunStatus.SUCCEEDED: [StepRunStatus.SCHEDULED],
            StepRunStatus.FAILED: [StepRunStatus.SCHEDULED],
            StepRunStatus.CANCELED: [StepRunStatus.SCHEDULED],
            StepRunStatus.SKIPPED: [StepRunStatus.SCHEDULED],
        },
    )

    objects = StepRunManager()

    class Meta:
        """Django model options for workflow step-run journal rows."""

        abstract = True
        ordering = ("created_at", "sqid")
        rebac_resource_type = "workflows/step_run"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(fields=("run", "step", "map_index"), name="uniq_workflows_step_run_map"),
            models.CheckConstraint(
                condition=(
                    models.Q(current_map_expansion__isnull=True)
                    | (models.Q(map_index__gte=0) & models.Q(step__isnull=False))
                ),
                name="chk_wsr_map_membership_body",
            ),
        )

    @property
    def is_terminal(self) -> bool:
        """Return whether this journal row has reached a terminal status."""

        return self.status in StepRunStatus.TERMINAL

    @property
    def is_retained(self) -> bool:
        """Return whether this slot has crossed the permanent retained boundary."""

        return (
            self.effect_key is not None
            or self.current_attempt_id is not None
            or self.current_map_expansion_id is not None
            or self.attempts.exists()
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep attempt-owned facts immutable outside the attempt manager."""

        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        protected = {
            "run", "run_id", "step", "step_id", "map_index",
            "status", "input", "output", "resume_state", "claimed_deliveries", "outcome",
            "attempt", "current_attempt", "current_attempt_id", "current_map_expansion",
            "current_map_expansion_id", "effect_key", "effect_generation",
            "wait_until", "waiting_kind", "heartbeat_at", "error", "stacktrace",
        }
        if self._state.adding:
            invalid = (
                self.attempt != 0
                or self.effect_key is not None
                or self.effect_generation != 0
                or self.current_attempt_id is not None
                or self.current_map_expansion_id is not None
            )
            if invalid:
                raise ValidationError({"effect_key": "Attempt identity is initialized by StepAttemptManager."})
        elif not self._state.adding:
            loaded = system_queryset(type(self), using=alias, lock=None).filter(pk=self.pk).values(
                "run_id",
                "step_id",
                "map_index",
                "attempt",
                "current_attempt_id",
                "current_map_expansion_id",
                "effect_key",
                "effect_generation",
            ).get()
            update_fields = kwargs.get("update_fields")
            touches_projection = update_fields is None or bool(protected.intersection(update_fields))
            attempt_model = self._meta.get_field("current_attempt").remote_field.model
            retained = (
                loaded["current_attempt_id"] is not None
                or loaded["current_map_expansion_id"] is not None
                or loaded["effect_key"] is not None
                or system_queryset(attempt_model, using=alias, lock=None).filter(step_run_id=self.pk).exists()
            )
            changed_ancestry = {
                name
                for name in ("run_id", "step_id", "map_index")
                if loaded[name] != getattr(self, name)
            }
            if retained and changed_ancestry:
                raise ValidationError(
                    {name: "Retained StepRun ancestry is immutable." for name in changed_ancestry}
                )
            if (retained or _attempt_write_active(alias, self.pk)) and touches_projection:
                capability = _step_run_save_capability.get()
                connection = connections[alias]
                if (
                    capability is None
                    or capability.consumed
                    or capability.alias != alias
                    or capability.connection_id != id(connection)
                    or not connection.atomic_blocks
                    or capability.outer_atomic_id != id(connection.atomic_blocks[0])
                    or capability.run_id != loaded["run_id"]
                    or capability.step_run_id != self.pk
                    or capability.instance_id != id(self)
                    or not connection.in_atomic_block
                ):
                    raise TypeError("Retained StepRun projections can only be saved by their manager owner.")
                capability.consumed = True
            if _attempt_write_active(alias, self.pk):
                super().save(*args, **kwargs)
                return
            changed = {
                name
                for name in ("current_attempt_id", "effect_key", "effect_generation")
                if loaded[name] != getattr(self, name)
            }
            if loaded["attempt"] != self.attempt and (
                loaded["current_attempt_id"] is not None or loaded["effect_key"] is not None
            ):
                changed.add("attempt")
            if changed:
                raise ValidationError({name: "This field is owned by StepAttemptManager." for name in changed})
        super().save(*args, **kwargs)

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.WAITING],
        target=StepRunStatus.STARTED,
        on_success=save_state,
    )
    def mark_started(self, *, heartbeat_at: Any = None, claimed_deliveries: int = 0) -> None:
        """Claim this row for execution."""

        self.heartbeat_at = heartbeat_at
        self.claimed_deliveries = claimed_deliveries
        self.waiting_kind = ""
        self._transition_fields = {"heartbeat_at", "claimed_deliveries", "waiting_kind"}

    def record_attempt(self, *, heartbeat_at: Any = None) -> None:
        """Record one implementation invocation for this started row."""

        self.attempt += 1
        if heartbeat_at is not None:
            self.heartbeat_at = heartbeat_at
        self.save(update_fields=["attempt", "heartbeat_at", "updated_at"])

    @transition(status, source=StepRunStatus.STARTED, target=StepRunStatus.WAITING, on_success=save_state)
    def mark_waiting(
        self,
        *,
        until: Any = None,
        resume_state: dict[str, Any] | None = None,
        waiting_kind: WaitingKind = WaitingKind.SCHEDULED,
    ) -> None:
        """Persist durable wait conditions for this row."""

        self.wait_until = until
        self.waiting_kind = waiting_kind
        if resume_state is not None:
            self.resume_state = resume_state
        self._transition_fields = {"wait_until", "resume_state", "waiting_kind"}

    def wake(self, *, at: datetime) -> None:
        """Make this waiting journal row due without changing its state.

        Event delivery changes only the durable due time. The engine owns the
        later ``WAITING`` → ``STARTED`` claim and therefore remains the sole
        scheduler of implementation work.
        """

        if self.status != StepRunStatus.WAITING:
            raise TransitionNotAllowed(
                f"StepRun.wake requires status={StepRunStatus.WAITING}; found {self.status}."
            )
        self.wait_until = at
        self.save(update_fields=["wait_until", "resume_state", "updated_at"])

    @transition(
        status,
        source=[StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.SUCCEEDED,
        on_success=save_state,
    )
    def mark_succeeded(self, *, output: Any = None, outcome: str = "") -> None:
        """Persist a successful step result."""

        self.output = output if output is not None else {}
        self.outcome = outcome
        self.error = ""
        self.stacktrace = ""
        self.wait_until = None
        self.waiting_kind = ""
        self._transition_fields = {"output", "outcome", "error", "stacktrace", "wait_until", "waiting_kind"}

    @transition(
        status,
        source=[StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.FAILED,
        on_success=save_state,
    )
    def mark_failed(self, *, error: str = "", stacktrace: str = "", outcome: str = "failed") -> None:
        """Persist a failed step result."""

        self.error = error
        self.stacktrace = stacktrace
        self.outcome = outcome
        self.wait_until = None
        self.waiting_kind = ""
        self._transition_fields = {"error", "stacktrace", "outcome", "wait_until", "waiting_kind"}

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.WAITING],
        target=StepRunStatus.SKIPPED,
        on_success=save_state,
    )
    def mark_skipped(self) -> None:
        """Mark this row as skipped by routing or join semantics."""

        self.wait_until = None
        self.waiting_kind = ""
        self._transition_fields = {"wait_until", "waiting_kind", "resume_state"}

    @transition(
        status,
        source=[StepRunStatus.SCHEDULED, StepRunStatus.STARTED, StepRunStatus.WAITING],
        target=StepRunStatus.CANCELED,
        on_success=save_state,
    )
    def mark_canceled(self) -> None:
        """Mark this row as canceled."""

        self.wait_until = None
        self.waiting_kind = ""
        self._transition_fields = {"wait_until", "waiting_kind", "resume_state"}

    @transition(
        status,
        source=[StepRunStatus.SUCCEEDED, StepRunStatus.FAILED, StepRunStatus.CANCELED, StepRunStatus.SKIPPED],
        target=StepRunStatus.SCHEDULED,
        on_success=save_state,
    )
    def reschedule_for_override(self, *, input: Any = None) -> None:
        """Reset a terminal journal row so a manual override can run it again."""

        self.input = input if input is not None else {}
        self.output = {}
        self.resume_state = {}
        self.claimed_deliveries = 0
        self.outcome = ""
        self.wait_until = None
        self.waiting_kind = ""
        self.heartbeat_at = None
        self.error = ""
        self.stacktrace = ""
        self._transition_fields = {
            "input",
            "output",
            "resume_state",
            "claimed_deliveries",
            "outcome",
            "wait_until",
            "waiting_kind",
            "heartbeat_at",
            "error",
            "stacktrace",
        }


class StepAttemptQuerySet(AngeeQuerySet[Any]):
    """Reject collection mutations that would bypass retained-evidence rules."""

    @staticmethod
    def _is_audit_nullification(values: Mapping[str, Any]) -> bool:
        audit_fields = frozenset(field.name for field in AuditMixin._meta.fields)
        return bool(values) and set(values).issubset(audit_fields) and all(value is None for value in values.values())

    def update(self, **kwargs: Any) -> int:
        if self._is_audit_nullification(kwargs):
            return super().update(**kwargs)
        raise TypeError("Step attempts do not support collection updates.")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Step attempts do not support bulk_create().")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        raise TypeError("Step attempts do not support bulk_update().")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise TypeError("Step attempts are retained execution evidence and cannot be deleted.")


class StepAttemptManager(AngeeManager.from_queryset(StepAttemptQuerySet)):  # type: ignore[misc]
    """Allocate, lease, and finalize retained attempts under ancestor locks."""

    def recovery_plan(self, attempt: Any, *, actor: Any) -> RecoveryPlan:
        """Return the operation-owned recovery capability for authorized evidence."""

        row = self.select_related("step_run__run__workflow", "step_run__step").filter(
            pk=attempt.pk
        ).first()
        if row is None or row.step_run.step_id is None:
            raise PermissionDenied("Recovery source evidence is unavailable.")
        run_model = row.step_run._meta.get_field("run").remote_field.model
        readable = read_scoped_queryset(run_model, actor, action="read")
        if readable is None or not readable.filter(pk=row.step_run.run_id).exists():
            raise PermissionDenied("Recovery source evidence is unavailable.")
        step_run = row.step_run
        workflow = step_run.run.workflow
        writable = read_scoped_queryset(type(workflow), actor, action="write")
        subject = step_run.run.subject
        subject_readable = (
            subject is None
            or (
                (scoped := read_scoped_queryset(type(subject), actor, action="read")) is not None
                and scoped.filter(pk=subject.pk).exists()
            )
        )
        admissible = (
            writable is not None
            and writable.filter(pk=workflow.pk).exists()
            and subject_readable
            and step_run.current_attempt_id == row.pk
            and step_run.status in {StepRunStatus.FAILED, StepRunStatus.CANCELED}
            and (
                (row.applied_at is not None and row.result_kind in {
                    str(AttemptResultKind.ERROR), str(AttemptResultKind.NO_RESULT),
                    str(AttemptResultKind.PREPARATION_ERROR), str(AttemptResultKind.TRANSIENT_ERROR),
                })
                or (row.result_recorded_at is None and row.lease_revoked_at is not None)
            )
        )
        capability = (
            step_run.step.resolve_impl("step_class").recovery_capability(attempt=row)
            if admissible
            else RecoveryCapability(None, "This retained attempt is not an admissible failure.")
        )
        return RecoveryPlan(
            attempt_id=row.sqid,
            run_id=step_run.run.sqid,
            workflow_id=workflow.sqid,
            workflow_revision=workflow.draft_revision if workflow.status == WorkflowStatus.TEST else workflow.version,
            step_id=step_run.step.sqid,
            step_key=step_run.step.key,
            map_index=step_run.map_index if step_run.map_index >= 0 else None,
            capability=capability,
        )

    @staticmethod
    def _fixture_source_summary(attempt: Any, role: TestFixtureRole) -> TestFixtureSourceSummary:
        step = attempt.step_run.step
        workflow = step.workflow
        return TestFixtureSourceSummary(
            attempt_id=attempt.sqid,
            run_id=attempt.step_run.run.sqid,
            workflow_id=workflow.sqid,
            workflow_revision=workflow.draft_revision,
            step_id=step.sqid,
            step_key=step.key,
            role=role,
            item_index=(
                attempt.map_item_index
                if role == TestFixtureRole.MAP_ITEM
                else (attempt.step_run.map_index if attempt.step_run.map_index >= 0 else None)
            ),
            outcome=attempt.outcome,
            recorded_at=attempt.result_recorded_at,
        )

    def _eligible_fixture_source_queryset(
        self,
        workflow: Any,
        *,
        actor: Any,
        role: TestFixtureRole,
        step_key: str,
        item_index: int | None,
    ) -> Any:
        """Return authorized accepted evidence for one exact fixture slot."""

        if not isinstance(role, TestFixtureRole):
            raise ValidationError({"role": "Captured sources require a declared fixture role."})
        if type(step_key) is not str or not step_key:
            raise ValidationError({"step_key": "Captured sources require a step key."})
        target_access = read_scoped_queryset(type(workflow), actor, action="write")
        if target_access is None or not target_access.filter(pk=workflow.pk).exists():
            raise PermissionDenied("Test workflow access was denied.")
        authorized = read_scoped_queryset(self.model, actor, action="read")
        if authorized is None:
            return self.none()
        head_id = workflow.published_from_id or workflow.pk
        queryset = authorized.select_related(
            "step_run__run", "step_run__step__workflow"
        ).filter(
            result_kind=AttemptResultKind.DONE,
            result_recorded_at__isnull=False,
            applied_at__isnull=False,
            lease_revoked_at__isnull=True,
            step_run__step__isnull=False,
            step_run__step__key=step_key,
        ).filter(
            models.Q(step_run__step__workflow_id=head_id)
            | models.Q(step_run__step__workflow__published_from_id=head_id)
        )
        if role == TestFixtureRole.OUTPUT:
            queryset = queryset.filter(output_present=True)
            return queryset.filter(step_run__map_index=-1 if item_index is None else item_index)
        if item_index is None:
            return queryset.none()
        return queryset.filter(map_item_present=True, map_item_index=item_index)

    def eligible_test_fixture_sources(
        self,
        workflow: Any,
        *,
        actor: Any,
        role: TestFixtureRole,
        step_key: str,
        item_index: int | None = None,
        after: str | None = None,
        first: int = 20,
    ) -> TestFixtureSourcePage:
        """Return a bounded page of summaries without retained payload values."""

        if type(first) is not int or not 1 <= first <= 50:
            raise ValidationError({"first": "Captured source pages contain between one and fifty rows."})
        queryset = self._eligible_fixture_source_queryset(
            workflow,
            actor=actor,
            role=role,
            step_key=step_key,
            item_index=item_index,
        ).only(
            "pk",
            "outcome",
            "result_recorded_at",
            "map_item_index",
            "step_run_id",
            "step_run__map_index",
            "step_run__run_id",
            "step_run__run__id",
            "step_run__step_id",
            "step_run__step__id",
            "step_run__step__key",
            "step_run__step__workflow_id",
            "step_run__step__workflow__id",
            "step_run__step__workflow__draft_revision",
        ).order_by("-pk")
        if after is not None:
            cursor = queryset.filter(sqid=after).values_list("pk", flat=True).first()
            if cursor is None:
                raise ValidationError({"after": "Captured source cursor is unavailable."})
            queryset = queryset.filter(pk__lt=cursor)
        rows = list(queryset[: first + 1])
        visible = rows[:first]
        return TestFixtureSourcePage(
            items=tuple(self._fixture_source_summary(row, role) for row in visible),
            next_after=visible[-1].sqid if len(rows) > first else None,
        )

    def test_fixture_source(
        self,
        workflow: Any,
        *,
        actor: Any,
        attempt_id: str,
        role: TestFixtureRole,
        step_key: str,
        item_index: int | None = None,
    ) -> TestFixtureSource | None:
        """Return one revalidated selected payload, or none when unavailable."""

        attempt = self._test_fixture_source_record(
            workflow,
            actor=actor,
            attempt_id=attempt_id,
            role=role,
            step_key=step_key,
            item_index=item_index,
        )
        if attempt is None:
            return None
        value = (
            JsonPresence(True, copy.deepcopy(attempt.output))
            if role == TestFixtureRole.OUTPUT
            else JsonPresence(True, copy.deepcopy(attempt.map_item))
        )
        return TestFixtureSource(self._fixture_source_summary(attempt, role), value)

    def _test_fixture_source_record(
        self,
        workflow: Any,
        *,
        actor: Any,
        attempt_id: str,
        role: TestFixtureRole,
        step_key: str,
        item_index: int | None,
    ) -> Any | None:
        """Resolve the exact persisted row used by both preview and admission."""

        return self._eligible_fixture_source_queryset(
            workflow,
            actor=actor,
            role=role,
            step_key=step_key,
            item_index=item_index,
        ).filter(sqid=attempt_id).first()

    def record_test_fixture(
        self, fixture: Any, *, at: datetime, due_step_run_id: int | None = None
    ) -> Any:
        """Apply one admitted output fixture as nonphysical retained DONE evidence."""

        alias = self.db
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        with transaction.atomic(using=alias), system_context(reason="workflows.test_fixture.apply"):
            run_model = step_run_model._meta.get_field("run").remote_field.model
            run_id = type(fixture)._base_manager.filter(pk=fixture.pk).values_list("run_id", flat=True).get()
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
            connection = connections[alias]
            authority = (alias, id(connection), id(connection.atomic_blocks[0]), run.pk)
            if due_step_run_id is None and _test_fixture_write_run.get() != authority:
                raise RuntimeError("Fixture application requires the exact owning run transaction.")
            locked_fixture = system_queryset(type(fixture), using=alias, lock=("self",)).get(pk=fixture.pk)
            if locked_fixture.run_id != run.pk:
                raise ValidationError({"fixture": "Fixture ancestry changed during admission."})
            if due_step_run_id is None:
                allowed = _test_fixture_apply_ids.get()
                if fixture.pk not in allowed:
                    raise RuntimeError("Fixture application requires its exact admission authority.")
                _test_fixture_apply_ids.set(allowed - {fixture.pk})
            locked_fixture.full_clean()
            fixture = locked_fixture
            if fixture.role != TestFixtureRole.OUTPUT:
                raise ValidationError({"fixture": "Only output fixtures create retained result evidence."})
            if run.is_terminal:
                raise ValidationError({"fixture": "A terminal run cannot apply fixture evidence."})
            if run.origin != RunOrigin.TEST or fixture.step.workflow_id != run.workflow_id:
                raise ValidationError({"fixture": "Fixture evidence must belong to its pinned test run."})
            step_run, created = system_queryset(step_run_model, using=alias, lock=("self",)).get_or_create(
                run=run,
                step=fixture.step,
                map_index=fixture.item_index if fixture.item_index is not None else -1,
                defaults={"status": StepRunStatus.SCHEDULED, "input": {}},
            )
            if due_step_run_id is not None and (
                created
                or step_run.pk != due_step_run_id
                or step_run.status != StepRunStatus.SCHEDULED
                or step_run.current_attempt_id is not None
            ):
                raise ValidationError({"fixture": "Output fixture substitution is not currently due."})
            if not created and step_run.current_attempt_id is not None:
                raise ValidationError({"fixture": "This fixture slot already has retained evidence."})
            with self._write(alias, step_run.pk):
                attempt = self._allocate_locked(
                    step_run,
                    cause=AttemptCause.TEST_FIXTURE,
                    input=AttemptInput(),
                    claimed_at=at,
                    alias=alias,
                    test_fixture=fixture,
                )
                attempt.result_kind = str(AttemptResultKind.DONE)
                attempt.result_recorded_at = at
                attempt.output_present = fixture.value_present
                attempt.output = copy.deepcopy(fixture.value)
                attempt.outcome = fixture.outcome
                attempt.applied_at = at
                self._save_attempt(attempt, alias=alias)
                self._write_step_run(
                    step_run,
                    alias=alias,
                    operation=lambda: step_run.mark_started(
                        heartbeat_at=None, claimed_deliveries=run.deliveries
                    ),
                )
                self._write_step_run(
                    step_run,
                    alias=alias,
                    operation=lambda: step_run.mark_succeeded(
                        output=copy.deepcopy(fixture.value) if fixture.value_present else None,
                        outcome=fixture.outcome,
                    ),
                )
            return attempt

    @contextmanager
    def _write(self, alias: str, step_run_id: int) -> Iterator[None]:
        connection = connections[alias]
        if not connection.in_atomic_block:
            raise RuntimeError("Attempt writes require an active database transaction.")
        active = _attempt_write_session.get()
        if active is not None:
            if (
                active.alias != alias
                or active.connection_id != id(connection)
                or active.step_run_id != step_run_id
            ):
                raise RuntimeError("An attempt write cannot span database connections or logical step runs.")
            yield
            return
        token = _attempt_write_session.set(_AttemptWriteSession(alias, id(connection), step_run_id))
        try:
            yield
        finally:
            _attempt_write_session.reset(token)

    def _locked_ancestry(self, step_run_id: int, alias: str) -> tuple[Any, Any]:
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        run_id = system_queryset(step_run_model, using=alias, lock=None).values_list("run_id", flat=True).get(
            pk=step_run_id
        )
        run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
        step_run = system_queryset(step_run_model, using=alias, lock=("self",)).get(
            pk=step_run_id
        )
        if step_run.run_id != run.pk:
            raise OperationalError("Step run ownership changed while its attempt was being locked.")
        return run, step_run

    def _save_attempt(self, attempt: Any, *, alias: str, **kwargs: Any) -> None:
        """Authorize one exact instance save and consume authority before signals run."""

        connection = connections[alias]
        capability = _AttemptSaveCapability(
            alias=alias,
            connection_id=id(connection),
            step_run_id=attempt.step_run_id,
            instance_id=id(attempt),
            pk=attempt.pk,
            adding=attempt._state.adding,
        )
        token = _attempt_save_capability.set(capability)
        try:
            attempt.save(using=alias, **kwargs)
        finally:
            _attempt_save_capability.reset(token)

    def _write_step_run(self, step_run: Any, *, alias: str, operation: Any) -> Any:
        """Authorize exactly one retained projection save, spent before signals."""

        connection = connections[alias]
        if not connection.atomic_blocks:
            raise RuntimeError("Retained StepRun projection writes require an owner transaction.")
        capability = _StepRunSaveCapability(
            alias=alias,
            connection_id=id(connection),
            outer_atomic_id=id(connection.atomic_blocks[0]),
            run_id=step_run.run_id,
            step_run_id=step_run.pk,
            instance_id=id(step_run),
        )
        token = _step_run_save_capability.set(capability)
        try:
            result = operation()
            if not capability.consumed:
                raise RuntimeError("Retained StepRun projection operation did not save its exact row.")
            return result
        finally:
            _step_run_save_capability.reset(token)

    def validate_result(self, result: AttemptResult) -> AttemptResult:
        """Validate one physical result before any retained write begins."""

        self._validate_result(result)
        self._validated_artifacts(result)
        return result

    def record_map_expansion(
        self,
        step_run: Any,
        *,
        at: datetime,
    ) -> tuple[Any, MapExpansionPlan] | None:
        """Retain one internal Map expansion wait without physical admission."""

        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run.pk),
            system_context(reason="workflows.map.expand"),
        ):
            run, locked = self._locked_ancestry(step_run.pk, alias)
            if run.is_terminal or locked.status != StepRunStatus.SCHEDULED:
                raise ValidationError({"step_run": "Map expansion requires a scheduled current generation."})
            plan = self._map_expansion_plan(locked)
            step_run_model = self.model._meta.get_field("step_run").remote_field.model
            run_rows = system_queryset(step_run_model, using=alias, lock=None).filter(
                run_id=run.pk
            )
            admitted = run_rows.filter(status=StepRunStatus.SCHEDULED).count()
            existing_rows = (
                {
                    row.map_index: row.status
                    for row in run_rows.filter(
                        step_id=plan.target_id,
                        map_index__gte=0,
                        map_index__lt=len(plan.items),
                    ).only("map_index", "status")
                }
                if plan.target_id is not None
                else {}
            )
            fixture_indexes: set[int] = set()
            if run.origin == RunOrigin.TEST and plan.target_id is not None:
                fixture_model = self.model._meta.apps.get_model(
                    "workflows", "WorkflowTestFixture"
                )
                fixture_indexes = set(
                    system_queryset(fixture_model, using=alias, lock=None)
                    .filter(
                        run_id=run.pk,
                        step_id=plan.target_id,
                        role=TestFixtureRole.OUTPUT,
                        item_index__gte=0,
                        item_index__lt=len(plan.items),
                    )
                    .values_list("item_index", flat=True)
                )
            additional = sum(
                index not in fixture_indexes
                and existing_rows.get(index) != StepRunStatus.SCHEDULED
                for index in range(len(plan.items))
            )
            if run.steps_taken + admitted + additional > run.workflow.max_steps:
                run.mark_failed(f"Workflow exceeded max_steps={run.workflow.max_steps}.")
                return None
            checkpoint = {
                "map": {
                    "target_step_key": plan.target_key,
                    "target_step_id": plan.target_id,
                    "items": plan.items,
                    "error": plan.error,
                }
            }
            attempt = self._allocate_locked(
                locked,
                cause=AttemptCause.MAP_ENGINE,
                input=AttemptInput(),
                claimed_at=at,
                alias=alias,
            )
            self._write_step_run(
                locked,
                alias=alias,
                operation=lambda: locked.mark_started(
                    heartbeat_at=None, claimed_deliveries=run.deliveries
                ),
            )
            attempt.result_kind = str(AttemptResultKind.WAIT)
            attempt.result_recorded_at = at
            attempt.checkpoint_present = True
            attempt.checkpoint = copy.deepcopy(checkpoint)
            attempt.waiting_kind = "children"
            attempt.applied_at = at
            self._save_attempt(attempt, alias=alias)
            self._write_step_run(
                locked,
                alias=alias,
                operation=lambda: locked.mark_waiting(
                    resume_state=copy.deepcopy(checkpoint),
                    waiting_kind=WaitingKind.CHILDREN,
                ),
            )
            self._charge_logical_execution(run, alias=alias)
            return attempt, plan

    @staticmethod
    def _map_expansion_plan(step_run: Any) -> MapExpansionPlan:
        impl_class = step_run.step.resolve_impl("step_class")
        if getattr(impl_class, "key", None) != "map":
            raise ValidationError({"step_run": "Map expansion requires a Map step."})
        try:
            target = impl_class.target_step(step_run)
            items = impl_class.items(step_run)
        except ValidationError as error:
            return MapExpansionPlan(None, "", [], str(error))
        return MapExpansionPlan(target.pk, target.key, items, "")

    def record_map_aggregate(
        self,
        step_run_id: int,
        *,
        expansion_attempt_id: int,
        at: datetime,
    ) -> Any:
        """Retain a separate internal Map aggregate result without another charge."""

        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run_id),
            system_context(reason="workflows.map.aggregate"),
        ):
            step_run_model = self.model._meta.get_field("step_run").remote_field.model
            run_model = step_run_model._meta.get_field("run").remote_field.model
            run_id = system_queryset(step_run_model, using=alias, lock=None).values_list(
                "run_id", flat=True
            ).get(pk=step_run_id)
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
            locked_rows = list(
                system_queryset(step_run_model, using=alias, lock=("self",))
                .filter(run_id=run.pk)
                .order_by("pk")
            )
            locked = next((row for row in locked_rows if row.pk == step_run_id), None)
            if locked is None:
                raise OperationalError("Map controller disappeared while locking membership.")
            expansion = system_queryset(self.model, using=alias, lock=("self",)).get(
                pk=expansion_attempt_id
            )
            if (
                run.is_terminal
                or locked.status != StepRunStatus.WAITING
                or locked.current_attempt_id != expansion.pk
                or expansion.step_run_id != locked.pk
                or expansion.cause != str(AttemptCause.MAP_ENGINE)
                or expansion.result_kind != str(AttemptResultKind.WAIT)
                or expansion.applied_at is None
                or expansion.lease_revoked_at is not None
                or expansion.effect_generation != locked.effect_generation
            ):
                raise ValidationError({"attempt": "Map aggregate requires the current applied expansion."})
            checkpoint = expansion.checkpoint if expansion.checkpoint_present else None
            map_state = checkpoint.get("map") if isinstance(checkpoint, dict) else None
            if not isinstance(map_state, dict):
                raise ValidationError({"map": "Map aggregate requires retained expansion facts."})
            items = map_state.get("items")
            target_id = map_state.get("target_step_id")
            children = sorted(
                (
                    row
                    for row in locked_rows
                    if row.current_map_expansion_id == expansion.pk
                ),
                key=lambda row: (row.map_index, row.pk),
            )
            if (
                not isinstance(items, list)
                or (target_id is None and not map_state.get("error"))
                or len(children) != len(items)
                or [child.map_index for child in children] != list(range(len(items)))
                or (target_id is not None and any(child.step_id != target_id for child in children))
            ):
                raise ValidationError({"map": "Map aggregate requires complete current membership."})
            if any(child.status not in StepRunStatus.TERMINAL for child in children):
                return None
            attempt_ids = sorted(
                child.current_attempt_id
                for child in children
                if child.current_attempt_id is not None
            )
            attempts = {
                item.pk: item
                for item in system_queryset(self.model, using=alias, lock=("self",))
                .filter(pk__in=attempt_ids)
                .order_by("pk")
            }
            results: list[dict[str, Any]] = []
            for child in children:
                item_attempt = attempts.get(child.current_attempt_id)
                output = None
                if child.status == StepRunStatus.SUCCEEDED:
                    if (
                        item_attempt is None
                        or item_attempt.map_expansion_id != expansion.pk
                        or item_attempt.map_item_index != child.map_index
                        or item_attempt.effect_generation != child.effect_generation
                        or item_attempt.result_kind != str(AttemptResultKind.DONE)
                        or item_attempt.applied_at is None
                        or item_attempt.lease_revoked_at is not None
                    ):
                        raise ValidationError(
                            {"map": "Map member success lacks current retained DONE evidence."}
                        )
                    if item_attempt.output_present:
                        output = copy.deepcopy(item_attempt.output)
                results.append(
                    {
                        "map_index": child.map_index,
                        "status": str(child.status),
                        "outcome": child.outcome,
                        "output": output,
                        "error": child.error,
                    }
                )
            successes = sum(child.status == StepRunStatus.SUCCEEDED for child in children)
            failures = sum(
                child.status in {StepRunStatus.FAILED, StepRunStatus.CANCELED}
                for child in children
            )
            expected_output = {
                "total": len(children),
                "successes": successes,
                "failures": failures,
                "results": results,
            }
            if map_state.get("error"):
                expected_output["error"] = map_state["error"]
            impl_class = locked.step.resolve_impl("step_class")
            expected_outcome = (
                "failed"
                if map_state.get("error")
                else "succeeded"
                if impl_class.policy_passes(locked.step.config, expected_output)
                else "failed"
            )
            aggregate = self._allocate_locked(
                locked,
                cause=AttemptCause.MAP_ENGINE,
                input=AttemptInput(),
                claimed_at=at,
                alias=alias,
            )
            aggregate.result_kind = str(AttemptResultKind.DONE)
            aggregate.result_recorded_at = at
            aggregate.output_present = True
            aggregate.output = copy.deepcopy(expected_output)
            aggregate.outcome = expected_outcome
            aggregate.applied_at = at
            self._save_attempt(aggregate, alias=alias)
            self._write_step_run(
                locked,
                alias=alias,
                operation=lambda: locked.mark_succeeded(
                    output=expected_output, outcome=expected_outcome
                ),
            )
            return aggregate

    def claim(
        self,
        step_run: Any,
        *,
        cause: AttemptCause = AttemptCause.INITIAL,
        input: AttemptInput = AttemptInput(),
        map_item: MapItemSource | None = None,
        test_fixture: Any = None,
        claimed_at: datetime,
    ) -> AttemptClaim:
        """Claim one logical delivery, returning the existing claim on duplicate admission."""

        self._validate_claim(cause, input)
        self._validate_map_item(map_item)
        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run.pk),
            system_context(reason="workflows.attempt.claim"),
        ):
            run, locked = self._locked_ancestry(step_run.pk, alias)
            if run.is_terminal:
                raise ValidationError({"step_run": "A terminal workflow run cannot claim an attempt."})
            if locked.step_id is not None and not run.allows_test_step(locked.step):
                raise ValidationError({"step_run": "This step is outside the admitted test scope."})
            if locked.current_attempt_id is not None:
                current = system_queryset(self.model, using=alias, lock=("self",)).get(pk=locked.current_attempt_id)
                active = current.result_recorded_at is None and current.lease_revoked_at is None
                if locked.status == StepRunStatus.STARTED and active:
                    self._validate_duplicate_claim(current, cause, input, map_item)
                    return AttemptClaim(current, False)
                if active:
                    raise ValidationError({"step_run": "This step run already has an active attempt."})
            self._validate_claim_source(locked, cause)
            self._validate_map_item_owner(locked, map_item, alias=alias)
            self._validate_test_fixture_owner(locked, test_fixture, alias=alias)
            attempt = self._allocate_locked(
                locked,
                cause=cause,
                input=input,
                map_item=map_item,
                claimed_at=claimed_at,
                alias=alias,
                test_fixture=test_fixture,
            )
            self._write_step_run(
                locked,
                alias=alias,
                operation=lambda: locked.mark_started(
                    heartbeat_at=None, claimed_deliveries=run.deliveries
                ),
            )
            self._charge_logical_execution(run, alias=alias)
            return AttemptClaim(attempt, True)

    def fail_preparation(
        self,
        step_run: Any,
        *,
        cause: AttemptCause,
        input: AttemptInput,
        map_item: MapItemSource | None = None,
        test_fixture: Any = None,
        result: AttemptResult,
        claimed_at: datetime,
        recorded_at: datetime,
    ) -> Any:
        """Retain a preparation failure without describing a physical invocation."""

        self._validate_claim(cause, input)
        self._validate_map_item(map_item)
        if result.kind != AttemptResultKind.PREPARATION_ERROR:
            raise ValidationError({"result": "Preparation failure requires a preparation-error result."})
        self._validate_result(result)
        if result.output_present or result.checkpoint_present or result.waiting_kind:
            raise ValidationError({"result": "Preparation failure cannot carry output, checkpoint, or wait state."})
        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run.pk),
            system_context(reason="workflows.attempt.prepare"),
        ):
            run, locked = self._locked_ancestry(step_run.pk, alias)
            if run.is_terminal:
                raise ValidationError({"step_run": "A terminal workflow run cannot record preparation failure."})
            if locked.current_attempt_id is not None:
                current = system_queryset(self.model, using=alias, lock=("self",)).get(pk=locked.current_attempt_id)
                if current.result_recorded_at is None and current.lease_revoked_at is None:
                    raise ValidationError({"step_run": "This step run already has an active attempt."})
            self._validate_claim_source(locked, cause)
            self._validate_map_item_owner(locked, map_item, alias=alias)
            self._validate_test_fixture_owner(locked, test_fixture, alias=alias)
            attempt = self._allocate_locked(
                locked,
                cause=cause,
                input=input,
                map_item=map_item,
                claimed_at=claimed_at,
                alias=alias,
                test_fixture=test_fixture,
            )
            attempt.result_kind = str(result.kind)
            attempt.result_recorded_at = recorded_at
            attempt.error = result.error
            attempt.stacktrace = result.stacktrace
            attempt.outcome = result.outcome
            self._apply_result(run, locked, attempt, result)
            attempt.applied_at = recorded_at
            self._save_attempt(attempt, alias=alias)
            self._charge_logical_execution(run, alias=alias)
            return attempt

    @staticmethod
    def _charge_logical_execution(run: Any, *, alias: str) -> None:
        """Charge one newly retained logical execution under the locked run owner."""

        run.steps_taken += 1
        run.save(using=alias, update_fields=["steps_taken", "updated_at"])

    def _allocate_locked(
        self,
        locked: Any,
        *,
        cause: AttemptCause,
        input: AttemptInput,
        claimed_at: datetime,
        alias: str,
        map_item: MapItemSource | None = None,
        test_fixture: Any = None,
    ) -> Any:
        recovery_source = (
            locked.run.recovery_source_attempt
            if cause == AttemptCause.MANUAL_RETRY and locked.run.origin == RunOrigin.RECOVERY
            else None
        )
        if recovery_source is not None and locked.run.recovery_mode == "reconcile":
            locked.effect_key = recovery_source.effect_key
        elif locked.effect_key is None:
            locked.effect_key = uuid.uuid4()
        ordinal = locked.attempt + 1
        attempt = self.model(
            step_run=locked,
            ordinal=ordinal,
            cause=str(cause),
            lease_token=uuid.uuid4(),
            input_present=input.present,
            input=input.value,
            input_provenance=input.provenance or {},
            map_expansion_id=map_item.expansion_attempt_id if map_item else None,
            map_item_index=map_item.index if map_item else None,
            map_item_present=map_item.value.present if map_item else False,
            map_item=map_item.value.value if map_item else None,
            test_fixture=test_fixture,
            claimed_at=claimed_at,
            effect_key=locked.effect_key,
            effect_generation=locked.effect_generation,
            recovery_source_attempt=recovery_source,
            recovery_mode=(locked.run.recovery_mode if recovery_source is not None else ""),
            intended_effect_key=(recovery_source.effect_key if recovery_source is not None else None),
        )
        self._save_attempt(attempt, alias=alias, force_insert=True)
        locked.attempt = ordinal
        locked.current_attempt = attempt
        self._write_step_run(
            locked,
            alias=alias,
            operation=lambda: locked.save(
                using=alias,
                update_fields=["attempt", "current_attempt", "effect_key", "updated_at"],
            ),
        )
        return attempt

    def _validate_test_fixture_owner(self, step_run: Any, fixture: Any, *, alias: str) -> None:
        if fixture is None:
            return
        fixture_model = self.model._meta.get_field("test_fixture").remote_field.model
        locked = system_queryset(fixture_model, using=alias, lock=("self",)).get(pk=fixture.pk)
        if (
            locked.run_id != step_run.run_id
            or locked.step_id != step_run.step_id
            or locked.role != TestFixtureRole.MAP_ITEM
            or locked.item_index != step_run.map_index
            or step_run.current_map_expansion_id is not None
        ):
            raise ValidationError({"test_fixture": "Map item fixture does not match this test slot."})

    def admit_invocation(
        self, attempt_id: int, *, lease_token: uuid.UUID, at: datetime
    ) -> InvocationAdmission:
        """Admit exactly the first physical invocation for a current logical claim."""

        alias = self.db
        unresolved = system_queryset(self.model, using=alias, lock=None).get(pk=attempt_id)
        with (
            transaction.atomic(using=alias),
            self._write(alias, unresolved.step_run_id),
            system_context(reason="workflows.attempt.invoke"),
        ):
            run, step_run = self._locked_ancestry(unresolved.step_run_id, alias)
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=attempt_id)
            if (
                run.is_terminal
                or step_run.status != StepRunStatus.STARTED
                or step_run.current_attempt_id != attempt.pk
                or attempt.lease_token != lease_token
                or attempt.lease_revoked_at is not None
                or attempt.result_recorded_at is not None
            ):
                return InvocationAdmission.FENCED
            if attempt.started_at is not None:
                return InvocationAdmission.ALREADY_STARTED
            if attempt.available_at is not None and at < attempt.available_at:
                return InvocationAdmission.NOT_DUE
            timestamps = (attempt.claimed_at, attempt.heartbeat_at, at)
            freshness = max(value for value in timestamps if value)
            attempt.started_at = freshness
            attempt.heartbeat_at = freshness
            self._save_attempt(
                attempt,
                alias=alias,
                update_fields=["started_at", "heartbeat_at", "updated_at"],
            )
            return InvocationAdmission.FIRST_START

    def heartbeat(self, attempt_id: int, *, lease_token: uuid.UUID, at: datetime) -> bool:
        """Refresh a live lease without changing logical lifecycle state."""

        alias = self.db
        unresolved = system_queryset(self.model, using=alias, lock=None).get(pk=attempt_id)
        with (
            transaction.atomic(using=alias),
            self._write(alias, unresolved.step_run_id),
            system_context(reason="workflows.attempt.lease"),
        ):
            run, step_run = self._locked_ancestry(unresolved.step_run_id, alias)
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=attempt_id)
            if (
                run.is_terminal
                or step_run.current_attempt_id != attempt.pk
                or attempt.lease_token != lease_token
                or attempt.lease_revoked_at is not None
                or attempt.result_recorded_at is not None
            ):
                return False
            timestamps = (attempt.claimed_at, attempt.started_at, attempt.heartbeat_at, at)
            freshness = max(value for value in timestamps if value)
            if attempt.started_at is None:
                return False
            attempt.heartbeat_at = freshness
            self._save_attempt(
                attempt,
                alias=alias,
                update_fields=["started_at", "heartbeat_at", "updated_at"],
            )
            step_run.heartbeat_at = freshness
            self._write_step_run(
                step_run,
                alias=alias,
                operation=lambda: step_run.save(
                    using=alias, update_fields=["heartbeat_at", "updated_at"]
                ),
            )
            return True

    def wake_current(self, step_run_id: int, *, at: datetime) -> bool:
        """Make one retained wait due through its exact locked projection owner."""

        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run_id),
            system_context(reason="workflows.attempt.wake"),
        ):
            _, step_run = self._locked_ancestry(step_run_id, alias)
            if step_run.status != StepRunStatus.WAITING or step_run.current_attempt_id is None:
                return False
            self._write_step_run(
                step_run, alias=alias, operation=lambda: step_run.wake(at=at)
            )
            return True

    def cancel_current(self, step_run_id: int, *, at: datetime) -> bool:
        """Revoke a resultless current lease and project logical cancellation."""

        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run_id),
            system_context(reason="workflows.attempt.cancel"),
        ):
            _, step_run = self._locked_ancestry(step_run_id, alias)
            if step_run.status in StepRunStatus.TERMINAL:
                return False
            if step_run.current_attempt_id is not None:
                attempt = system_queryset(self.model, using=alias, lock=("self",)).get(
                    pk=step_run.current_attempt_id
                )
                if attempt.result_recorded_at is None and attempt.lease_revoked_at is None:
                    attempt.lease_revoked_at = at
                    attempt.lease_revocation_reason = str(LeaseRevocationReason.CANCELED)
                    self._save_attempt(
                        attempt,
                        alias=alias,
                        update_fields=["lease_revoked_at", "lease_revocation_reason", "updated_at"],
                    )
            state = dict(step_run.resume_state or {})
            state["cancel_requested"] = True
            step_run.resume_state = state
            if step_run.status == StepRunStatus.STARTED:
                self._write_step_run(
                    step_run, alias=alias, operation=step_run.mark_canceled
                )
            elif step_run.status in {StepRunStatus.SCHEDULED, StepRunStatus.WAITING}:
                self._write_step_run(
                    step_run, alias=alias, operation=step_run.mark_canceled
                )
            return True

    def timeout_current(self, step_run_id: int, *, heartbeat_before: datetime, at: datetime) -> bool:
        """Revoke one stale current lease without inventing physical result evidence."""

        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run_id),
            system_context(reason="workflows.attempt.timeout"),
        ):
            _, step_run = self._locked_ancestry(step_run_id, alias)
            if step_run.status != StepRunStatus.STARTED or step_run.current_attempt_id is None:
                return False
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(
                pk=step_run.current_attempt_id
            )
            heartbeat = attempt.heartbeat_at or attempt.started_at or attempt.claimed_at
            if (
                attempt.started_at is None
                or heartbeat is None
                or heartbeat >= heartbeat_before
                or attempt.result_recorded_at is not None
                or attempt.lease_revoked_at is not None
            ):
                return False
            attempt.lease_revoked_at = at
            attempt.lease_revocation_reason = str(LeaseRevocationReason.HEARTBEAT_LOST)
            self._save_attempt(
                attempt,
                alias=alias,
                update_fields=["lease_revoked_at", "lease_revocation_reason", "updated_at"],
            )
            self._write_step_run(
                step_run,
                alias=alias,
                operation=lambda: step_run.mark_failed(
                    error="Step heartbeat timed out.", stacktrace=""
                ),
            )
            return True

    @staticmethod
    def _validate_claim(cause: AttemptCause, input: AttemptInput) -> None:
        if not isinstance(input, AttemptInput):
            raise ValidationError({"input": "Attempt claim requires a typed input envelope."})
        try:
            validate_json_presence(input, label="attempt input")
        except ValueError as error:
            raise ValidationError({"input": str(error)}) from error
        if input.provenance is not None and not isinstance(input.provenance, dict):
            raise ValidationError({"input_provenance": "Input provenance must be a JSON object."})
        if not isinstance(cause, AttemptCause):
            raise ValidationError({"cause": "Attempt claim requires a declared cause."})

    @staticmethod
    def _validate_map_item(map_item: MapItemSource | None) -> None:
        if map_item is None:
            return
        if (
            not isinstance(map_item, MapItemSource)
            or type(map_item.expansion_attempt_id) is not int
            or map_item.expansion_attempt_id <= 0
            or type(map_item.index) is not int
            or map_item.index < 0
        ):
            raise ValidationError({"map_item": "Map item source identity is invalid."})
        try:
            validate_json_presence(map_item.value, label="Map item")
        except ValueError as error:
            raise ValidationError({"map_item": str(error)}) from error
        if not map_item.value.present:
            raise ValidationError({"map_item": "A retained Map expansion item is always present."})

    def _validate_map_item_owner(
        self, step_run: Any, map_item: MapItemSource | None, *, alias: str
    ) -> None:
        if map_item is None:
            if step_run.current_map_expansion_id is not None:
                raise ValidationError(
                    {"map_item": "Current Map membership requires captured item provenance."}
                )
            return
        if step_run.run.origin == RunOrigin.RECOVERY:
            source_id = step_run.run.recovery_source_attempt_id
            if source_id is None:
                raise ValidationError({"map_item": "Recovery Map item has no admitted source evidence."})
            source = (
                system_queryset(self.model, using=alias, lock=None)
                .select_related("step_run")
                .get(pk=source_id)
            )
            if (
                source.map_expansion_id == map_item.expansion_attempt_id
                and source.map_item_index == map_item.index
                and source.map_item_present is map_item.value.present
                and json_values_equal(source.map_item, map_item.value.value)
                and source.step_run.step_id == step_run.step_id
                and source.step_run.map_index == step_run.map_index
            ):
                return
            raise ValidationError(
                {"map_item": "Recovery Map item does not match admitted source evidence."}
            )
        if (
            step_run.map_index != map_item.index
            or step_run.current_map_expansion_id != map_item.expansion_attempt_id
        ):
            raise ValidationError({"map_item": "Map item source does not match current membership."})
        expansion = system_queryset(self.model, using=alias, lock=("self",)).get(
            pk=map_item.expansion_attempt_id
        )
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        controller = system_queryset(step_run_model, using=alias, lock=None).get(
            pk=expansion.step_run_id
        )
        checkpoint = expansion.checkpoint if expansion.checkpoint_present else None
        map_state = checkpoint.get("map") if isinstance(checkpoint, dict) else None
        items = map_state.get("items") if isinstance(map_state, dict) else None
        target_id = map_state.get("target_step_id") if isinstance(map_state, dict) else None
        if (
            expansion.cause != str(AttemptCause.MAP_ENGINE)
            or controller.run_id != step_run.run_id
            or controller.current_attempt_id != expansion.pk
            or controller.status != StepRunStatus.WAITING
            or controller.effect_generation != expansion.effect_generation
            or step_run.step_id != target_id
            or expansion.result_kind != str(AttemptResultKind.WAIT)
            or expansion.applied_at is None
            or expansion.lease_revoked_at is not None
            or not isinstance(items, list)
            or map_item.index >= len(items)
            or not json_values_equal(items[map_item.index], map_item.value.value)
        ):
            raise ValidationError(
                {"map_item": "Map item source is not current applied expansion evidence."}
            )

    @staticmethod
    def _validate_result(result: AttemptResult) -> None:
        if not isinstance(result.kind, AttemptResultKind):
            raise ValidationError({"result": "Attempt results require a declared result kind."})
        for field, presence in (
            ("output", JsonPresence(result.output_present, result.output)),
            ("checkpoint", JsonPresence(result.checkpoint_present, result.checkpoint)),
        ):
            try:
                validate_json_presence(presence, label=f"attempt {field}")
            except ValueError as error:
                raise ValidationError({field: str(error)}) from error
        if result.waiting_kind and result.waiting_kind not in WaitingKind.values:
            raise ValidationError({"waiting_kind": "Attempt result waiting kind is not declared."})
        wait_facts = result.requested_until is not None or bool(result.decisions) or result.checkpoint_present
        if result.kind == AttemptResultKind.WAIT:
            if result.requested_until is None or result.decisions:
                raise ValidationError({"result": "Wait requires a deadline and cannot declare decisions."})
        elif result.kind == AttemptResultKind.SUSPEND:
            if result.requested_until is not None:
                raise ValidationError({"result": "Suspension cannot carry a timer deadline."})
        elif wait_facts or result.waiting_kind:
            raise ValidationError({"result": "This result kind cannot carry wait or decision facts."})
        if result.checkpoint_present and result.checkpoint is not None and not isinstance(result.checkpoint, dict):
            raise ValidationError({"checkpoint": "A non-null checkpoint must be a JSON object."})
        if result.kind == AttemptResultKind.TRANSIENT_ERROR and (
            not result.error or result.output_present or result.checkpoint_present
        ):
            raise ValidationError(
                {"result": "Transient errors require an error and cannot carry output or checkpoint."}
            )
        for declaration in result.decisions:
            if not declaration.action:
                raise ValidationError({"decisions": "Decision actions cannot be empty."})
            try:
                validate_slug(declaration.action)
                for subject in (*declaration.assignees, *declaration.escalation):
                    SubjectRef.parse(subject)
                if declaration.requester:
                    SubjectRef.parse(declaration.requester)
            except (TypeError, ValueError, ValidationError) as error:
                raise ValidationError({"decisions": "Decision declarations are invalid."}) from error
        if not isinstance(result.artifacts_present, bool):
            raise ValidationError({"artifacts": "Artifact presence must be a boolean."})
        if not result.artifacts_present and result.artifacts:
            raise ValidationError({"artifacts": "Absent artifacts cannot carry declarations."})

    @staticmethod
    def _validated_artifacts(result: AttemptResult) -> tuple[tuple[int, int, str], ...]:
        rows: list[tuple[int, int, str]] = []
        for declaration in result.artifacts:
            if not isinstance(declaration, ArtifactSpec) or not isinstance(declaration.label, str):
                raise ValidationError({"artifacts": "Artifact declarations are invalid."})
            label = declaration.label.strip()
            if not label or len(label) > 255:
                raise ValidationError({"artifacts": "Artifact labels must contain at most 255 characters."})
            target = declaration.target
            if not isinstance(target, models.Model) or target.pk is None:
                raise ValidationError({"artifacts": "Artifact targets must be saved records."})
            canonical = canonical_record_target(target)
            if type(canonical.object_id) is bool or not isinstance(canonical.object_id, int):
                raise ValidationError({"artifacts": "Artifact targets require an integer record identity."})
            rows.append((canonical.content_type.pk, canonical.object_id, label))
        return tuple(rows)

    @staticmethod
    def _validate_claim_source(step_run: Any, cause: AttemptCause) -> None:
        if step_run.status == StepRunStatus.WAITING and cause != AttemptCause.CONTINUATION:
            raise ValidationError({"cause": "A waiting step run requires a continuation attempt."})
        if step_run.status == StepRunStatus.SCHEDULED and cause not in {
            AttemptCause.INITIAL,
            AttemptCause.MANUAL_RETRY,
        }:
            raise ValidationError({"cause": "A scheduled step run requires an initial attempt."})
        if cause == AttemptCause.MANUAL_RETRY and (
            step_run.run.origin != RunOrigin.RECOVERY
            or step_run.run.recovery_source_attempt_id is None
            or step_run.step_id != step_run.run.recovery_source_attempt.step_run.step_id
        ):
            raise ValidationError({"cause": "Manual retry requires the exact admitted recovery step."})
        if step_run.status not in {StepRunStatus.SCHEDULED, StepRunStatus.WAITING}:
            raise ValidationError({"step_run": "Only a scheduled or waiting step run can claim an attempt."})

    @staticmethod
    def _validate_duplicate_claim(
        attempt: Any,
        cause: AttemptCause,
        input: AttemptInput,
        map_item: MapItemSource | None,
    ) -> None:
        if (
            attempt.cause != str(cause)
            or attempt.input_present != input.present
            or not json_values_equal(attempt.input, input.value)
            or not json_values_equal(attempt.input_provenance, input.provenance or {})
            or attempt.map_expansion_id != (map_item.expansion_attempt_id if map_item else None)
            or attempt.map_item_index != (map_item.index if map_item else None)
            or attempt.map_item_present != (map_item.value.present if map_item else False)
            or not json_values_equal(
                attempt.map_item, map_item.value.value if map_item else None
            )
        ):
            raise ValidationError({"step_run": "The active attempt was claimed with different immutable input."})

    def revoke(
        self, attempt_id: int, *, lease_token: uuid.UUID, reason: LeaseRevocationReason, at: datetime
    ) -> LeaseRevocation:
        """Revoke a current resultless lease while preserving later evidence."""

        if not isinstance(reason, LeaseRevocationReason):
            raise ValidationError({"reason": "Lease revocation requires a declared reason."})
        alias = self.db
        unresolved = system_queryset(self.model, using=alias, lock=None).get(pk=attempt_id)
        with (
            transaction.atomic(using=alias),
            self._write(alias, unresolved.step_run_id),
            system_context(reason="workflows.attempt.revoke"),
        ):
            _, step_run = self._locked_ancestry(unresolved.step_run_id, alias)
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=attempt_id)
            if attempt.lease_token != lease_token or step_run.current_attempt_id != attempt.pk:
                return LeaseRevocation(False, attempt.result_recorded_at is not None)
            if attempt.result_recorded_at is not None:
                return LeaseRevocation(False, True)
            if attempt.lease_revoked_at is not None:
                return LeaseRevocation(attempt.lease_revocation_reason == str(reason), False)
            attempt.lease_revoked_at = at
            attempt.lease_revocation_reason = str(reason)
            self._save_attempt(
                attempt,
                alias=alias,
                update_fields=["lease_revoked_at", "lease_revocation_reason", "updated_at"],
            )
            return LeaseRevocation(True, False)

    def finalize(
        self, attempt_id: int, *, lease_token: uuid.UUID, result: AttemptResult, recorded_at: datetime
    ) -> AttemptFinalization:
        """Retain one result and atomically apply its closed legacy projection."""

        try:
            encoded_decisions = serialize_decision_specs(result.decisions)
            result = replace(result, decisions=deserialize_decision_specs(encoded_decisions))
            self._validate_result(result)
        except (TypeError, ValueError, PydanticSerializationError) as error:
            raise ValidationError({"decisions": "Decision declarations are invalid."}) from error
        encoded_artifacts = self._validated_artifacts(result)
        alias = self.db
        unresolved = system_queryset(self.model, using=alias, lock=None).get(pk=attempt_id)
        with (
            transaction.atomic(using=alias),
            self._write(alias, unresolved.step_run_id),
            system_context(reason="workflows.attempt.finalize"),
        ):
            run, step_run = self._locked_ancestry(unresolved.step_run_id, alias)
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=attempt_id)
            if attempt.lease_token != lease_token:
                return AttemptFinalization(False, False)
            if attempt.result_recorded_at is not None:
                if not self._result_matches(attempt, result):
                    raise ValidationError({"result": "A different result is already retained for this attempt."})
                applied = attempt.applied_at is not None
                intents: tuple[DecisionTimerIntent, ...] = ()
                if applied and result.kind == AttemptResultKind.SUSPEND:
                    intents = step_run.decisions.model.objects.timer_intents_for(attempt=attempt, using=alias)
                return AttemptFinalization(
                    False,
                    applied,
                    intents,
                    self._retry_intent_for(attempt, alias=alias),
                )
            attempt.result_kind = str(result.kind)
            attempt.result_recorded_at = recorded_at
            attempt.output_present = result.output_present
            attempt.output = result.output
            attempt.checkpoint_present = result.checkpoint_present
            attempt.checkpoint = result.checkpoint
            attempt.error = result.error
            attempt.stacktrace = result.stacktrace
            attempt.outcome = result.outcome
            attempt.waiting_kind = result.waiting_kind
            attempt.result_requested_until = result.requested_until
            attempt.result_decisions = encoded_decisions
            attempt.artifacts_present = result.artifacts_present
            applicable = (
                not run.is_terminal
                and step_run.current_attempt_id == attempt.pk
                and attempt.lease_revoked_at is None
            )
            timer_intents: tuple[DecisionTimerIntent, ...] = ()
            retry_intent: RetryIntent | None = None
            if applicable:
                if result.kind == AttemptResultKind.TRANSIENT_ERROR:
                    retry_intent = self._apply_transient_result(
                        step_run,
                        attempt,
                        result,
                        recorded_at=recorded_at,
                        alias=alias,
                    )
                else:
                    timer_intents = self._apply_result(run, step_run, attempt, result)
                attempt.applied_at = recorded_at
            self._save_attempt(attempt, alias=alias)
            if result.artifacts_present:
                artifact_model = self.model._meta.apps.get_model("workflows", "StepArtifact")
                connection = connections[alias]
                capability = _ArtifactBatchCapability(
                    alias,
                    id(connection),
                    id(connection.atomic_blocks[0]),
                    attempt.pk,
                    encoded_artifacts,
                )
                token = _artifact_batch_capability.set(capability)
                try:
                    artifact_model.objects._record_for_attempt(attempt, encoded_artifacts, using=alias)
                finally:
                    _artifact_batch_capability.reset(token)
            return AttemptFinalization(True, applicable, timer_intents, retry_intent)

    def _retry_intent_for(self, attempt: Any, *, alias: str) -> RetryIntent | None:
        successor = (
            system_queryset(self.model, using=alias, lock=None)
            .filter(retry_of=attempt)
            .values("pk", "lease_token", "available_at")
            .first()
        )
        if successor is None:
            return None
        available_at = successor["available_at"]
        if available_at is None:
            raise OperationalError("An automatic retry successor has no availability time.")
        return RetryIntent(successor["pk"], successor["lease_token"], available_at)

    def _apply_transient_result(
        self,
        step_run: Any,
        attempt: Any,
        result: AttemptResult,
        *,
        recorded_at: datetime,
        alias: str,
    ) -> RetryIntent | None:
        """Project exhaustion or allocate one automatic successor under the existing locks."""

        if attempt.started_at is None or step_run.status != StepRunStatus.STARTED:
            raise ValidationError({"result": "Transient errors require a started current attempt."})
        try:
            policy = retry_policy_from_config(step_run.step.config)
            retry_index = attempt.retry_index + 1
            available_at = (
                recorded_at + timedelta(seconds=policy.delay_for(retry_index))
                if retry_index < policy.max_attempts
                else None
            )
        except (ValidationError, OverflowError) as error:
            attempt.orchestration_error = f"Retry policy could not schedule a successor: {error}"
            self._write_step_run(
                step_run,
                alias=alias,
                operation=lambda: self._project_transient_failure(step_run, result),
            )
            return None
        if retry_index >= policy.max_attempts:
            self._write_step_run(
                step_run,
                alias=alias,
                operation=lambda: self._project_transient_failure(step_run, result),
            )
            return None
        if available_at is None:
            raise OperationalError("An allowed automatic retry has no availability time.")

        successor = self._allocate_retry_locked(
            step_run,
            retry_of=attempt,
            retry_index=retry_index,
            available_at=available_at,
            claimed_at=recorded_at,
            alias=alias,
        )
        return RetryIntent(successor.pk, successor.lease_token, available_at)

    @staticmethod
    def _project_transient_failure(step_run: Any, result: AttemptResult) -> None:
        step_run.mark_failed(
            error=result.error or "",
            stacktrace=result.stacktrace or "",
            outcome=result.outcome or "failed",
        )

    def _allocate_retry_locked(
        self,
        step_run: Any,
        *,
        retry_of: Any,
        retry_index: int,
        available_at: datetime,
        claimed_at: datetime,
        alias: str,
    ) -> Any:
        """Allocate one automatic successor without charging the logical run again."""

        successor = self.model(
            step_run=step_run,
            ordinal=step_run.attempt + 1,
            cause=str(AttemptCause.AUTOMATIC_RETRY),
            retry_of=retry_of,
            retry_index=retry_index,
            available_at=available_at,
            lease_token=uuid.uuid4(),
            input_present=retry_of.input_present,
            input=copy.deepcopy(retry_of.input),
            input_provenance=copy.deepcopy(retry_of.input_provenance),
            claimed_at=claimed_at,
            effect_key=retry_of.effect_key,
            effect_generation=retry_of.effect_generation,
            map_expansion_id=retry_of.map_expansion_id,
            map_item_index=retry_of.map_item_index,
            map_item_present=retry_of.map_item_present,
            map_item=copy.deepcopy(retry_of.map_item),
            test_fixture_id=retry_of.test_fixture_id,
        )
        self._save_attempt(successor, alias=alias, force_insert=True)
        step_run.attempt = successor.ordinal
        step_run.current_attempt = successor
        step_run.heartbeat_at = None
        self._write_step_run(
            step_run,
            alias=alias,
            operation=lambda: step_run.save(
                using=alias,
                update_fields=["attempt", "current_attempt", "heartbeat_at", "updated_at"],
            ),
        )
        return successor

    @staticmethod
    def _result_matches(attempt: Any, result: AttemptResult) -> bool:
        """Compare a retry with the retained semantic envelope, excluding delivery time."""

        return (
            attempt.result_kind == str(result.kind)
            and attempt.output_present == result.output_present
            and json_values_equal(attempt.output, result.output)
            and attempt.checkpoint_present == result.checkpoint_present
            and json_values_equal(attempt.checkpoint, result.checkpoint)
            and attempt.error == result.error
            and attempt.stacktrace == result.stacktrace
            and attempt.outcome == result.outcome
            and attempt.waiting_kind == result.waiting_kind
            and attempt.result_requested_until == result.requested_until
            and json_values_equal(
                attempt.result_decisions, serialize_decision_specs(result.decisions)
            )
            and attempt.artifacts_present == result.artifacts_present
            and (
                not result.artifacts_present
                or tuple(
                    attempt.artifacts.order_by("declaration_index").values_list(
                        "target_content_type_id", "target_object_id", "label"
                    )
                ) == StepAttemptManager._validated_artifacts(result)
            )
        )

    def _apply_result(
        self, run: Any, step_run: Any, attempt: Any, result: AttemptResult
    ) -> tuple[DecisionTimerIntent, ...]:
        if result.kind == AttemptResultKind.PREPARATION_ERROR:
            if attempt.started_at is not None or step_run.status not in {
                StepRunStatus.SCHEDULED,
                StepRunStatus.WAITING,
            }:
                raise ValidationError({"result": "Preparation errors require an unstarted active step run."})
            step_run.error = result.error or ""
            step_run.stacktrace = result.stacktrace or ""
            step_run.outcome = result.outcome or "failed"
            step_run.wait_until = None
            step_run.waiting_kind = ""
            step_run._transition_fields = {"error", "stacktrace", "outcome", "wait_until", "waiting_kind"}
            self._write_step_run(
                step_run,
                alias=self.db,
                operation=lambda: step_run.status_transitions.force_state(
                    step_run, StepRunStatus.FAILED, reason="attempt preparation failed before invocation"
                ),
            )
            return ()
        if attempt.started_at is None or step_run.status != StepRunStatus.STARTED:
            raise ValidationError({"result": "This result requires a started current attempt."})
        if result.kind == AttemptResultKind.DONE:
            self._write_step_run(
                step_run,
                alias=self.db,
                operation=lambda: step_run.mark_succeeded(
                    output=result.output if result.output_present else None,
                    outcome=result.outcome,
                ),
            )
        elif result.kind in {AttemptResultKind.WAIT, AttemptResultKind.SUSPEND}:
            effective_until = result.requested_until
            if result.kind == AttemptResultKind.WAIT and run.deliveries > step_run.claimed_deliveries:
                effective_until = attempt.result_recorded_at
            resume_state = result.checkpoint if result.checkpoint_present else None
            timer_intents: tuple[DecisionTimerIntent, ...] = ()
            if result.kind == AttemptResultKind.SUSPEND:
                decision_model = step_run.decisions.model
                decisions, timer_intents = decision_model.objects.create_for_suspension(
                    step_run=step_run,
                    attempt=attempt,
                    declarations=result.decisions,
                    using=self.db,
                )
                resume_state = dict(resume_state or {})
                if decisions:
                    resume_state["_decision_ids"] = [decision.pk for decision in decisions]
                schemas = {
                    str(decision.pk): dict(spec.decision_schema)
                    for decision, spec in zip(decisions, result.decisions, strict=True)
                    if spec.decision_schema
                }
                if schemas:
                    resume_state["_decision_schemas"] = schemas
            self._write_step_run(
                step_run,
                alias=self.db,
                operation=lambda: step_run.mark_waiting(
                    until=effective_until,
                    resume_state=resume_state,
                    waiting_kind=result.waiting_kind or WaitingKind.EXTERNAL,
                ),
            )
            return timer_intents
        elif result.kind in {AttemptResultKind.ERROR, AttemptResultKind.NO_RESULT}:
            error = result.error or (
                "Step implementation returned no result." if result.kind == AttemptResultKind.NO_RESULT else ""
            )
            self._write_step_run(
                step_run,
                alias=self.db,
                operation=lambda: step_run.mark_failed(
                    error=error,
                    stacktrace=result.stacktrace or "",
                    outcome=result.outcome or "failed",
                ),
            )
        else:
            raise ValidationError({"result": f"Unsupported attempt result kind {result.kind!s}."})
        return ()


class StepAttemptSystemManager(StepAttemptManager):
    """Expose guarded unscoped rows to Django and field-backed REBAC traversal."""

    def get_queryset(self) -> StepAttemptQuerySet:
        return super().get_queryset().system_context(
            reason="workflows.step_attempt.base_manager"
        )


class StepAttempt(AuditMixin, AngeeDataModel):
    """Append-only evidence for one physical execution attempt."""

    runtime = True

    sqid_prefix = "wsa_"
    step_run = models.ForeignKey("workflows.StepRun", on_delete=models.PROTECT, related_name="attempts")
    retry_of = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="retry_successor",
        editable=False,
    )
    retry_index = models.PositiveIntegerField(default=0, editable=False)
    available_at = models.DateTimeField(null=True, blank=True, db_index=True, editable=False)
    ordinal = models.PositiveIntegerField()
    cause = models.CharField(max_length=32, choices=[(value.value, value.name.title()) for value in AttemptCause])
    lease_token = models.UUIDField(editable=False)
    effect_key = models.UUIDField(editable=False)
    effect_generation = models.PositiveIntegerField(default=0, editable=False)
    input_present = models.BooleanField(default=False)
    input = models.JSONField(null=True, blank=True)
    input_provenance = models.JSONField(default=dict, blank=True)
    map_expansion = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="map_item_attempts",
        editable=False,
    )
    map_item_index = models.PositiveIntegerField(null=True, blank=True, editable=False)
    map_item_present = models.BooleanField(default=False, editable=False)
    map_item = models.JSONField(null=True, blank=True, editable=False)
    test_fixture = models.ForeignKey(
        "workflows.WorkflowTestFixture",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="applied_attempts",
        editable=False,
    )
    recovery_source_attempt = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="recovery_attempts", editable=False,
    )
    recovery_mode = models.CharField(max_length=32, blank=True, editable=False)
    intended_effect_key = models.UUIDField(null=True, blank=True, editable=False)
    claimed_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    lease_revoked_at = models.DateTimeField(null=True, blank=True)
    lease_revocation_reason = models.CharField(
        max_length=32,
        blank=True,
        choices=[(value.value, value.name.title()) for value in LeaseRevocationReason],
    )
    result_kind = models.CharField(
        max_length=32,
        blank=True,
        choices=[(value.value, value.name.title()) for value in AttemptResultKind],
    )
    result_recorded_at = models.DateTimeField(null=True, blank=True)
    output_present = models.BooleanField(default=False)
    output = models.JSONField(null=True, blank=True)
    checkpoint_present = models.BooleanField(default=False)
    checkpoint = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    stacktrace = models.TextField(null=True, blank=True)
    outcome = models.SlugField(max_length=100, blank=True, default="")
    waiting_kind = models.CharField(max_length=32, blank=True, default="")
    result_requested_until = models.DateTimeField(null=True, blank=True)
    result_decisions = models.JSONField(default=list, blank=True)
    artifacts_present = models.BooleanField(default=False, editable=False)
    orchestration_error = models.TextField(blank=True, default="", editable=False)
    applied_at = models.DateTimeField(null=True, blank=True)

    objects = StepAttemptManager()
    system_objects = StepAttemptSystemManager()

    class Meta:
        abstract = True
        base_manager_name = "system_objects"
        ordering = ("step_run_id", "ordinal")
        rebac_resource_type = "workflows/step_attempt"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(fields=("step_run", "ordinal"), name="uniq_workflows_step_attempt_ordinal"),
            models.UniqueConstraint(fields=("lease_token",), name="uniq_workflows_step_attempt_lease"),
            models.CheckConstraint(
                condition=models.Q(input_present=True) | models.Q(input__isnull=True),
                name="chk_wsa_absent_input_null",
            ),
            models.CheckConstraint(
                condition=models.Q(output_present=True) | models.Q(output__isnull=True),
                name="chk_wsa_absent_output_null",
            ),
            models.CheckConstraint(
                condition=models.Q(checkpoint_present=True) | models.Q(checkpoint__isnull=True),
                name="chk_wsa_absent_checkpoint_null",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(lease_revoked_at__isnull=True, lease_revocation_reason="")
                    | (models.Q(lease_revoked_at__isnull=False) & ~models.Q(lease_revocation_reason=""))
                ),
                name="chk_wsa_revocation_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(result_recorded_at__isnull=True, result_kind="")
                    | (models.Q(result_recorded_at__isnull=False) & ~models.Q(result_kind=""))
                ),
                name="chk_wsa_result_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(applied_at__isnull=True)
                    | (models.Q(result_recorded_at__isnull=False) & models.Q(lease_revoked_at__isnull=True))
                ),
                name="chk_wsa_applied_result",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        cause=AttemptCause.AUTOMATIC_RETRY,
                        retry_of__isnull=False,
                        retry_index__gt=0,
                        available_at__isnull=False,
                    )
                    | (
                        ~models.Q(cause=AttemptCause.AUTOMATIC_RETRY)
                        & models.Q(retry_of__isnull=True, retry_index=0, available_at__isnull=True)
                    )
                ),
                name="chk_wsa_retry_series",
            ),
            models.CheckConstraint(
                condition=models.Q(orchestration_error="") | models.Q(result_kind=AttemptResultKind.TRANSIENT_ERROR),
                name="chk_wsa_orchestration_error",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        map_expansion__isnull=True,
                        map_item_index__isnull=True,
                        map_item_present=False,
                        map_item__isnull=True,
                    )
                    | models.Q(
                        map_expansion__isnull=False,
                        map_item_index__isnull=False,
                        map_item_present=True,
                    )
                ),
                name="chk_wsa_map_item_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(cause=AttemptCause.TEST_FIXTURE, test_fixture__isnull=False)
                    | ~models.Q(cause=AttemptCause.TEST_FIXTURE)
                ),
                name="chk_wsa_test_fixture_cause",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        cause=AttemptCause.MANUAL_RETRY,
                        recovery_source_attempt__isnull=False,
                        recovery_mode__gt="",
                        intended_effect_key__isnull=False,
                    )
                    | (
                        ~models.Q(cause=AttemptCause.MANUAL_RETRY)
                        & models.Q(
                            recovery_source_attempt__isnull=True,
                            recovery_mode="",
                            intended_effect_key__isnull=True,
                        )
                    )
                ),
                name="chk_wsa_recovery_attempt",
            ),
        )

    @property
    def status(self) -> AttemptStatus:
        """Derive lifecycle display from retained lease and result evidence."""

        if self.result_recorded_at is not None:
            return AttemptStatus.COMPLETED if self.applied_at is not None else AttemptStatus.LATE_RESULT
        if self.lease_revoked_at is not None:
            return AttemptStatus.REVOKED
        if self.started_at is not None:
            return AttemptStatus.RUNNING
        if self.claimed_at is not None:
            return AttemptStatus.CLAIMED
        return AttemptStatus.ALLOCATED

    def save(self, *args: Any, **kwargs: Any) -> None:
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        capability = _attempt_save_capability.get()
        connection = connections[alias]
        if (
            capability is None
            or capability.consumed
            or capability.alias != alias
            or capability.connection_id != id(connection)
            or not connection.in_atomic_block
            or not _attempt_write_active(alias, self.step_run_id)
            or capability.step_run_id != self.step_run_id
            or capability.instance_id != id(self)
            or capability.pk != self.pk
            or capability.adding != self._state.adding
        ):
            raise TypeError("Step attempts can only be saved by StepAttemptManager.")
        capability.consumed = True
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Step attempts are retained execution evidence and cannot be deleted.")


class StepArtifactQuerySet(AngeeQuerySet[Any]):
    """Read-only collection of explicit retained result artifacts."""

    def update(self, **kwargs: Any) -> int:
        raise TypeError("Workflow artifacts are immutable retained result evidence.")

    def bulk_create(self, objs: Iterable[Any], *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Workflow artifacts can only be recorded during attempt finalization.")

    def bulk_update(self, objs: Iterable[Any], fields: Iterable[str], batch_size: int | None = None) -> int:
        raise TypeError("Workflow artifacts are immutable retained result evidence.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise TypeError("Workflow artifacts are immutable retained result evidence.")


class StepArtifactManager(AngeeManager.from_queryset(StepArtifactQuerySet)):  # type: ignore[misc]
    """Record a validated artifact batch for one result inside its attempt transaction."""

    def _record_for_attempt(
        self,
        attempt: Any,
        rows: tuple[tuple[int, int, str], ...],
        *,
        using: str,
    ) -> tuple[Any, ...]:
        connection = connections[using]
        capability = _artifact_batch_capability.get()
        if (
            capability is None
            or capability.consumed
            or capability.alias != using
            or capability.connection_id != id(connection)
            or not connection.in_atomic_block
            or capability.outer_atomic_id != id(connection.atomic_blocks[0])
            or capability.attempt_id != attempt.pk
            or capability.rows != rows
            or not _attempt_write_active(using, attempt.step_run_id)
            or attempt.result_recorded_at is None
            or not attempt.artifacts_present
        ):
            raise RuntimeError("Artifact recording requires the exact attempt finalization transaction.")
        capability.consumed = True
        created: list[Any] = []
        for index, (content_type_id, object_id, label) in enumerate(rows):
            artifact = self.model(
                attempt=attempt,
                declaration_index=index,
                target_content_type_id=content_type_id,
                target_object_id=object_id,
                label=label,
            )
            capability = _ArtifactWriteCapability(
                using,
                id(connection),
                id(connection.atomic_blocks[0]),
                attempt.pk,
                index,
                id(artifact),
            )
            token = _artifact_write_capability.set(capability)
            try:
                artifact.save(using=using, force_insert=True)
            finally:
                _artifact_write_capability.reset(token)
            created.append(artifact)
        return tuple(created)


class StepArtifact(AuditMixin, AngeeDataModel):
    """Immutable ordered pointer explicitly emitted by one retained attempt result."""

    runtime = True
    sqid_prefix = "war_"
    attempt = models.ForeignKey("workflows.StepAttempt", on_delete=models.PROTECT, related_name="artifacts")
    declaration_index = models.PositiveIntegerField(editable=False)
    label = models.CharField(max_length=255, editable=False)
    target_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    target_object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("target_content_type", "target_object_id")

    objects = StepArtifactManager()

    class Meta:
        abstract = True
        ordering = ("attempt_id", "declaration_index")
        rebac_resource_type = "workflows/step_artifact"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(fields=("attempt", "declaration_index"), name="uniq_war_attempt_index"),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        capability = _artifact_write_capability.get()
        if (
            capability is None
            or capability.consumed
            or capability.alias != alias
            or capability.connection_id != id(connections[alias])
            or not connections[alias].in_atomic_block
            or capability.outer_atomic_id != id(connections[alias].atomic_blocks[0])
            or capability.attempt_id != self.attempt_id
            or capability.declaration_index != self.declaration_index
            or capability.instance_id != id(self)
            or not self._state.adding
        ):
            raise TypeError("Workflow artifacts can only be saved by StepArtifactManager.")
        capability.consumed = True
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Workflow artifacts are immutable retained result evidence.")


class DecisionQuerySet(AngeeQuerySet[Any]):
    """Decision reads with protected retained-suspension provenance."""

    _PROTECTED_FIELDS = frozenset(
        {
            "suspension_attempt", "suspension_attempt_id", "declaration_index",
            "priority", "action", "payload", "max_attempts", "expires_at", "escalate_at",
            "verdict", "resolution", "resolved_by", "attempts",
        }
    )

    def update(self, **kwargs: Any) -> int:
        if self._PROTECTED_FIELDS.intersection(kwargs):
            raise TypeError("Decision suspension provenance is owned by DecisionManager.")
        return super().update(**kwargs)

    def bulk_create(self, objs: Iterable[Any], *args: Any, **kwargs: Any) -> list[Any]:
        rows = list(objs)
        retained = any(
            row.suspension_attempt_id is not None or row.declaration_index is not None for row in rows
        )
        if retained:
            raise TypeError("Decision suspension provenance is owned by DecisionManager.")
        return super().bulk_create(rows, *args, **kwargs)

    def bulk_update(
        self,
        objs: Iterable[Any],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        field_names = tuple(fields)
        if self._PROTECTED_FIELDS.intersection(field_names):
            raise TypeError("Decision suspension provenance is owned by DecisionManager.")
        return super().bulk_update(objs, field_names, batch_size=batch_size)


class DecisionManager(AngeeManager.from_queryset(DecisionQuerySet)):  # type: ignore[misc]
    """Create actionable decisions and their authorization tuples atomically."""

    @contextmanager
    def _resolution_owner(self, decision_id: int) -> Iterator[None]:
        """Require one retained resolution to finish its logical projection."""

        alias = self.db
        connection = connections[alias]
        if not connection.atomic_blocks:
            raise RuntimeError("Retained decision resolution requires an outer owner transaction.")
        session = _DecisionResolutionSession(
            alias, id(connection), id(connection.atomic_blocks[0]), decision_id
        )
        token = _decision_resolution_session.set(session)
        try:
            yield
            if not session.completed:
                raise RuntimeError("Retained decision resolution did not complete its projection.")
        finally:
            _decision_resolution_session.reset(token)

    def _require_resolution_owner(self, decision_id: int) -> _DecisionResolutionSession:
        session = _decision_resolution_session.get()
        connection = connections[self.db]
        if (
            session is None
            or session.completed
            or session.alias != self.db
            or session.connection_id != id(connection)
            or not connection.atomic_blocks
            or session.outer_atomic_id != id(connection.atomic_blocks[0])
            or session.decision_id != decision_id
        ):
            raise RuntimeError("Retained decision writes require their exact transition owner.")
        return session

    def complete_retained_resolution(self, decision_id: int) -> None:
        """Mark the exact retained resolution's projection and dispatch complete."""

        self._require_resolution_owner(decision_id).completed = True

    def _write_retained(self, decision: Any, operation: Any) -> Any:
        """Authorize one exact retained Decision save and spend it before signals."""

        connection = connections[self.db]
        if not connection.atomic_blocks:
            raise RuntimeError("Retained Decision writes require an owner transaction.")
        capability = _DecisionSaveCapability(
            self.db,
            id(connection),
            id(connection.atomic_blocks[0]),
            decision.pk,
            id(decision),
        )
        token = _decision_save_capability.set(capability)
        try:
            result = operation()
            if not capability.consumed:
                raise RuntimeError("Retained Decision transition did not save its exact row.")
            return result
        finally:
            _decision_save_capability.reset(token)

    def _lock_retained_resolution(self, decision_id: int, *, using: str) -> Any | None:
        """Lock and validate one retained decision in canonical ancestry order."""

        discovered = system_queryset(self.model, using=using, lock=None).filter(pk=decision_id).values(
            "step_run_id", "suspension_attempt_id"
        ).first()
        if discovered is None or discovered["suspension_attempt_id"] is None:
            return None
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        attempt_model = self.model._meta.get_field("suspension_attempt").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        run_id = system_queryset(step_run_model, using=using, lock=None).values_list(
            "run_id", flat=True
        ).get(pk=discovered["step_run_id"])
        run = system_queryset(run_model, using=using, lock=("self",)).get(pk=run_id)
        step_run = system_queryset(step_run_model, using=using, lock=("self",)).get(
            pk=discovered["step_run_id"]
        )
        attempt = system_queryset(attempt_model, using=using, lock=("self",)).get(
            pk=discovered["suspension_attempt_id"]
        )
        decision = system_queryset(self.model, using=using, lock=("self",)).select_related(
            "step_run__run", "step_run__step"
        ).get(pk=decision_id)
        valid = (
            step_run.run_id == run.pk
            and attempt.step_run_id == step_run.pk
            and decision.step_run_id == step_run.pk
            and decision.suspension_attempt_id == attempt.pk
            and step_run.current_attempt_id == attempt.pk
            and step_run.status == StepRunStatus.WAITING
            and not run.is_terminal
            and attempt.result_kind == str(AttemptResultKind.SUSPEND)
            and attempt.applied_at is not None
            and attempt.lease_revoked_at is None
        )
        return decision if valid else None

    def resolve_retained(
        self,
        decision_id: int,
        *,
        verdict: Any,
        resolution: Any,
        resolved_by: str,
        at: datetime,
        expected_attempts: int | None = None,
        deadline: str | None = None,
    ) -> Any | None:
        """Resolve one current retained suspension decision under canonical locks."""

        self._require_resolution_owner(decision_id)
        alias = self.db
        with transaction.atomic(using=alias), system_context(reason="workflows.decision.resolve"):
            decision = self._lock_retained_resolution(decision_id, using=alias)
            if decision is None or decision.verdict != Verdict.PENDING:
                return None
            if expected_attempts is not None and decision.attempts != expected_attempts:
                return None
            gate = decision.step_run.resume_state.get("gate")
            if isinstance(gate, dict) and gate.get("policy") == "sequential":
                current_id = system_queryset(self.model, using=alias, lock=None).filter(
                    step_run_id=decision.step_run_id,
                    suspension_attempt_id=decision.suspension_attempt_id,
                    verdict=Verdict.PENDING,
                ).order_by("priority", "pk").values_list("pk", flat=True).first()
                if current_id != decision.pk:
                    raise ValidationError(
                        {"decision": "Sequential decisions must resolve in priority order."}
                    )
            deadline_at = getattr(decision, deadline, None) if deadline else None
            if deadline and (deadline_at is None or deadline_at > at):
                return None
            self._write_retained(
                decision,
                lambda: decision.resolve(verdict, resolution=resolution, resolved_by=resolved_by),
            )
            return decision

    def record_invalid_retained(self, decision_id: int) -> tuple[Any, bool]:
        """Record one invalid submission against the exact current suspension."""

        self._require_resolution_owner(decision_id)
        alias = self.db
        with transaction.atomic(using=alias), system_context(reason="workflows.decision.invalid"):
            decision = self._lock_retained_resolution(decision_id, using=alias)
            if decision is None or decision.verdict != Verdict.PENDING:
                raise ValidationError({"decision": "This retained decision is no longer current."})
            gate = decision.step_run.resume_state.get("gate")
            if isinstance(gate, dict) and gate.get("policy") == "sequential":
                current_id = system_queryset(self.model, using=alias, lock=None).filter(
                    step_run_id=decision.step_run_id,
                    suspension_attempt_id=decision.suspension_attempt_id,
                    verdict=Verdict.PENDING,
                ).order_by("priority", "pk").values_list("pk", flat=True).first()
                if current_id != decision.pk:
                    raise ValidationError(
                        {"decision": "Sequential decisions must resolve in priority order."}
                    )
            self._write_retained(decision, decision.record_invalid_resolution)
            exhausted = decision.max_attempts is not None and decision.attempts >= decision.max_attempts
            return decision, exhausted

    def expire_retained_suspension(self, decision_id: int, *, resolved_by: str) -> Any | None:
        """Expire every pending slot of one exact current retained suspension."""

        self._require_resolution_owner(decision_id)
        alias = self.db
        with transaction.atomic(using=alias), system_context(reason="workflows.decision.expire_all"):
            decision = self._lock_retained_resolution(decision_id, using=alias)
            if decision is None:
                return None
            pending = list(
                system_queryset(self.model, using=alias, lock=("self",)).filter(
                    suspension_attempt_id=decision.suspension_attempt_id,
                    verdict=Verdict.PENDING,
                ).order_by("priority", "pk")
            )
            for pending_decision in pending:
                self._write_retained(
                    pending_decision,
                    lambda pending_decision=pending_decision: pending_decision.resolve(
                        Verdict.EXPIRED, resolution={}, resolved_by=resolved_by
                    ),
                )
            return decision

    def expire_canceled_suspension(self, step_run_id: int, *, resolved_by: str) -> int:
        """Expire pending decisions only after their retained suspension was canceled."""

        alias = self.db
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        attempt_model = self.model._meta.get_field("suspension_attempt").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        with transaction.atomic(using=alias), system_context(reason="workflows.decision.cancel"):
            run_id = system_queryset(step_run_model, using=alias, lock=None).values_list(
                "run_id", flat=True
            ).get(pk=step_run_id)
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
            step_run = system_queryset(step_run_model, using=alias, lock=("self",)).get(pk=step_run_id)
            if step_run.run_id != run.pk or step_run.status != StepRunStatus.CANCELED:
                raise ValidationError({"step_run": "Decision cancellation requires a canceled retained slot."})
            if step_run.current_attempt_id is None:
                return 0
            attempt = system_queryset(attempt_model, using=alias, lock=("self",)).get(
                pk=step_run.current_attempt_id
            )
            if attempt.step_run_id != step_run.pk or attempt.lease_revoked_at is None:
                raise ValidationError({"attempt": "Decision cancellation requires a revoked current attempt."})
            pending = list(
                system_queryset(self.model, using=alias, lock=("self",)).filter(
                    suspension_attempt=attempt,
                    verdict=Verdict.PENDING,
                ).order_by("pk")
            )
            for decision in pending:
                self._write_retained(
                    decision,
                    lambda decision=decision: decision.resolve(
                        Verdict.EXPIRED, resolution={}, resolved_by=resolved_by
                    ),
                )
            return len(pending)

    def timer_intents_for(self, *, attempt: Any, using: str) -> tuple[DecisionTimerIntent, ...]:
        """Reconstruct deterministic post-commit work for an applied suspension."""

        decisions = system_queryset(self.model, using=using, lock=None).filter(
            suspension_attempt=attempt
        ).order_by("declaration_index")
        intents: list[DecisionTimerIntent] = []
        for decision in decisions:
            if decision.escalate_at is not None:
                intents.append(
                    DecisionTimerIntent(
                        DecisionTimerKind.ESCALATE,
                        decision.pk,
                        decision.attempts,
                        decision.escalate_at,
                    )
                )
            if decision.expires_at is not None:
                intents.append(
                    DecisionTimerIntent(
                        DecisionTimerKind.EXPIRE,
                        decision.pk,
                        decision.attempts,
                        decision.expires_at,
                    )
                )
        return tuple(intents)

    def create_for_suspension(
        self,
        *,
        step_run: Any,
        attempt: Any,
        declarations: tuple[DecisionSpec, ...],
        using: str,
    ) -> tuple[tuple[Any, ...], tuple[DecisionTimerIntent, ...]]:
        """Create one validated, ordered batch for an applicable suspension.

        ORM and relationship rows share rollback on the current host's default
        database with REBAC's transactional local backend. Other aliases and
        remote backends require a durable relationship-intent contract before
        this unused API can enter the production execution cutover.
        """
        if using != DEFAULT_DB_ALIAS:
            raise ValidationError(
                {"using": "Atomic decision relationship creation currently requires the default database."}
            )
        if not _attempt_write_active(using, step_run.pk):
            raise RuntimeError("Retained decisions require an active StepAttemptManager transaction.")

        declarations = deserialize_decision_specs(serialize_decision_specs(declarations))

        prepared = tuple(
            (
                spec,
                tuple(SubjectRef.parse(subject) for subject in spec.assignees),
                SubjectRef.parse(spec.requester) if spec.requester else None,
                tuple(SubjectRef.parse(subject) for subject in spec.escalation),
            )
            for spec in declarations
        )
        if attempt.step_run_id != step_run.pk:
            raise ValidationError({"attempt": "The suspension attempt must belong to this step run."})
        if (
            step_run.current_attempt_id != attempt.pk
            or step_run.status != StepRunStatus.STARTED
            or step_run.run.is_terminal
            or attempt.started_at is None
            or attempt.lease_revoked_at is not None
            or attempt.result_kind != str(AttemptResultKind.SUSPEND)
            or attempt.result_recorded_at is None
        ):
            raise ValidationError({"attempt": "Decisions require the current applicable suspension attempt."})

        decisions: list[Any] = []
        timer_intents: list[DecisionTimerIntent] = []
        with transaction.atomic(using=using):
            connection = connections[using]
            manager = self.db_manager(using)
            for index, (spec, assignees, requester, escalation) in enumerate(prepared):
                token = _decision_write_session.set(
                    _DecisionWriteSession(using, id(connection), step_run.pk, attempt.pk, index)
                )
                try:
                    decision = manager.create(
                        step_run=step_run,
                        suspension_attempt=attempt,
                        declaration_index=index,
                        priority=spec.priority,
                        action=spec.action,
                        payload=spec.payload,
                        max_attempts=spec.max_attempts,
                        expires_at=spec.expires_at,
                        escalate_at=spec.escalate_at,
                    )
                finally:
                    _decision_write_session.reset(token)
                resource = to_object_ref(decision)
                relationships = [
                    RelationshipTuple(resource=resource, relation="assignee", subject=subject)
                    for subject in assignees
                ]
                if requester is not None:
                    relationships.append(
                        RelationshipTuple(resource=resource, relation="requester", subject=requester)
                    )
                relationships.extend(
                    RelationshipTuple(resource=resource, relation="escalation", subject=subject)
                    for subject in escalation
                )
                if relationships:
                    write_relationships(relationships)
                decisions.append(decision)
                if spec.escalate_at is not None:
                    timer_intents.append(
                        DecisionTimerIntent(
                            DecisionTimerKind.ESCALATE, decision.pk, decision.attempts, spec.escalate_at
                        )
                    )
                if spec.expires_at is not None:
                    timer_intents.append(
                        DecisionTimerIntent(
                            DecisionTimerKind.EXPIRE, decision.pk, decision.attempts, spec.expires_at
                        )
                    )
        return tuple(decisions), tuple(timer_intents)


class Decision(AuditMixin, AngeeDataModel):
    """One awaited resolution slot for a suspended step-run."""

    runtime = True
    _form_schema_state_attribute = "_workflows_form_schema_state"

    sqid_prefix = "wdc_"
    step_run = models.ForeignKey("workflows.StepRun", on_delete=models.CASCADE, related_name="decisions")
    suspension_attempt = models.ForeignKey(
        "workflows.StepAttempt", on_delete=models.PROTECT, null=True, blank=True, related_name="decisions"
    )
    declaration_index = models.PositiveIntegerField(null=True, blank=True, editable=False)
    priority = models.IntegerField(default=0)
    action = models.SlugField(max_length=100)
    payload = models.JSONField(default=dict, blank=True)
    verdict = StateField(choices_enum=Verdict, default=Verdict.PENDING)
    resolution = models.JSONField(default=dict, blank=True)
    resolved_by = models.CharField(max_length=255, blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    escalate_at = models.DateTimeField(null=True, blank=True, db_index=True)

    verdict_transitions = StateTransitions(
        verdict,
        {
            Verdict.PENDING: [
                Verdict.COMPLETED,
                Verdict.REJECTED,
                Verdict.ESCALATED,
                Verdict.EXPIRED,
            ],
        },
    )

    objects = DecisionManager()

    class Meta:
        """Django model options for workflow decisions."""

        abstract = True
        ordering = ("step_run", "priority", "declaration_index", "created_at", "sqid")
        rebac_resource_type = "workflows/decision"
        rebac_id_attr = "sqid"
        indexes = (models.Index(fields=("step_run", "verdict", "priority"), name="idx_wdc_step_verdict"),)
        constraints = (
            models.UniqueConstraint(
                fields=("suspension_attempt", "declaration_index"), name="uniq_wdc_attempt_declaration"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(suspension_attempt__isnull=True, declaration_index__isnull=True)
                    | models.Q(suspension_attempt__isnull=False, declaration_index__isnull=False)
                ),
                name="chk_wdc_declaration_source_pair",
            ),
        )

    @property
    def is_terminal(self) -> bool:
        """Return whether this decision has a terminal verdict."""

        return self.verdict in Verdict.TERMINAL

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep retained suspension provenance immutable outside its manager."""

        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        if self._state.adding:
            if (
                (self.suspension_attempt_id is not None or self.declaration_index is not None)
                and not _decision_write_active(alias, self)
            ):
                raise TypeError("Decision suspension provenance is owned by DecisionManager.")
        else:
            retained = system_queryset(type(self), using=alias, lock=None).filter(pk=self.pk).values(
                "suspension_attempt_id", "declaration_index", "priority", "action", "payload",
                "max_attempts", "expires_at", "escalate_at",
            ).get()
            if retained["suspension_attempt_id"] != self.suspension_attempt_id or retained[
                "declaration_index"
            ] != self.declaration_index:
                raise TypeError("Decision suspension provenance is immutable.")
            immutable = {
                name
                for name in (
                    "priority", "action", "payload", "max_attempts", "expires_at", "escalate_at"
                )
                if retained[name] != getattr(self, name)
            }
            if retained["suspension_attempt_id"] is not None and immutable:
                raise TypeError("Retained Decision declarations are immutable.")
            update_fields = kwargs.get("update_fields")
            execution_fields = {"verdict", "resolution", "resolved_by", "attempts"}
            touches_execution = update_fields is None or bool(execution_fields.intersection(update_fields))
            if retained["suspension_attempt_id"] is not None and touches_execution:
                capability = _decision_save_capability.get()
                connection = connections[alias]
                if (
                    capability is None
                    or capability.consumed
                    or capability.alias != alias
                    or capability.connection_id != id(connection)
                    or not connection.atomic_blocks
                    or capability.outer_atomic_id != id(connection.atomic_blocks[0])
                    or capability.decision_id != self.pk
                    or capability.instance_id != id(self)
                ):
                    raise TypeError("Retained Decisions can only be changed by their transition owner.")
                capability.consumed = True
        super().save(*args, **kwargs)

    @classmethod
    def form_schema_annotation(cls) -> dict[str, Any]:
        """Return the narrow ORM projection consumed by :attr:`form_schema`."""

        return {cls._form_schema_state_attribute: models.F("step_run__resume_state")}

    @property
    def form_schema(self) -> dict[str, Any] | None:
        """Return the enforced JSON-authored form schema, excluding Python model schemas."""

        state = getattr(self, self._form_schema_state_attribute, None)
        if not isinstance(state, dict):
            state = self.step_run.resume_state
        if not isinstance(state, dict):
            return None
        schemas = state.get("_decision_schemas", {})
        if isinstance(schemas, dict) and str(self.pk) in schemas:
            schema = schemas[str(self.pk)]
            return dict(schema) if isinstance(schema, dict) and schema else None
        gate = state.get("gate")
        if isinstance(gate, dict):
            schema = gate.get("decision_schema")
            return dict(schema) if isinstance(schema, dict) and schema else None
        return None

    @transition(verdict, source=Verdict.PENDING, target=Verdict.COMPLETED, on_success=save_state)
    def mark_completed(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as completed."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.REJECTED, on_success=save_state)
    def mark_rejected(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as rejected."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.ESCALATED, on_success=save_state)
    def mark_escalated(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as escalated."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    @transition(verdict, source=Verdict.PENDING, target=Verdict.EXPIRED, on_success=save_state)
    def mark_expired(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot as expired."""

        self._set_resolution(resolution=resolution, resolved_by=resolved_by)

    def record_invalid_resolution(self) -> None:
        """Record one failed validation attempt while leaving the slot pending."""

        self.attempts += 1
        self.save(update_fields=["attempts", "updated_at"])

    def resolve(self, verdict: Verdict, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Resolve this slot through the transition matching ``verdict``."""

        if verdict == Verdict.COMPLETED:
            self.mark_completed(resolution=resolution, resolved_by=resolved_by)
        elif verdict == Verdict.REJECTED:
            self.mark_rejected(resolution=resolution, resolved_by=resolved_by)
        elif verdict == Verdict.ESCALATED:
            self.mark_escalated(resolution=resolution, resolved_by=resolved_by)
        elif verdict == Verdict.EXPIRED:
            self.mark_expired(resolution=resolution, resolved_by=resolved_by)
        else:
            raise ValidationError({"verdict": "Decision verdict must be terminal."})

    def _set_resolution(self, *, resolution: Any = None, resolved_by: str = "") -> None:
        """Persist normalized resolution audit fields for a terminal verdict."""

        self.resolution = resolution if resolution is not None else {}
        self.resolved_by = resolved_by
        self._transition_fields = {"resolution", "resolved_by"}


@dataclass(slots=True)
class _DispatchSaveCapability:
    alias: str
    connection_id: int
    instance_id: int
    pk: Any
    adding: bool
    kind: str
    target: tuple[int | None, int | None, int | None, int | None]
    consumed: bool = False


_dispatch_save_capability: ContextVar[_DispatchSaveCapability | None] = ContextVar(
    "workflow_dispatch_save_capability", default=None
)


@dataclass(slots=True)
class _DispatchConsumeSession:
    alias: str
    connection_id: int
    outer_atomic_id: int
    kind: str
    target_id: int
    generation: int | None
    dispatch_id: int
    lease_token: uuid.UUID | None
    consumed: bool = False


_dispatch_consume_session: ContextVar[_DispatchConsumeSession | None] = ContextVar(
    "workflow_dispatch_consume_session", default=None
)


class WorkflowDispatchQuerySet(AngeeQuerySet[Any]):
    """Read durable delivery intents without exposing collection mutation bypasses."""

    @staticmethod
    def _is_audit_nullification(values: Mapping[str, Any]) -> bool:
        audit_fields = frozenset(field.name for field in AuditMixin._meta.fields)
        return bool(values) and set(values).issubset(audit_fields) and all(value is None for value in values.values())

    def update(self, **kwargs: Any) -> int:
        if self._is_audit_nullification(kwargs):
            return super().update(**kwargs)
        raise TypeError("Workflow dispatches do not support collection updates.")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Workflow dispatches do not support bulk_create().")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        raise TypeError("Workflow dispatches do not support bulk_update().")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise TypeError("Workflow dispatches are durable delivery evidence and cannot be deleted.")


class WorkflowDispatchManager(AngeeManager.from_queryset(WorkflowDispatchQuerySet)):  # type: ignore[misc]
    """Schedule intents and own bounded publication telemetry."""

    def _save(self, dispatch: Any, *, alias: str, **kwargs: Any) -> None:
        connection = connections[alias]
        capability = _DispatchSaveCapability(
            alias,
            id(connection),
            id(dispatch),
            dispatch.pk,
            dispatch._state.adding,
            str(dispatch.kind),
            dispatch.target_identity,
        )
        token = _dispatch_save_capability.set(capability)
        try:
            dispatch.save(using=alias, **kwargs)
        finally:
            _dispatch_save_capability.reset(token)

    @contextmanager
    def _owner_transition(
        self,
        *,
        dispatch_id: int,
        lease_token: uuid.UUID | None,
        at: datetime,
        using: str,
    ) -> Iterator[DispatchPreflight]:
        """Lock canonical ancestry and preflight one exact delivery intent."""

        connection = connections[using]
        if not connection.in_atomic_block or not connection.atomic_blocks:
            raise RuntimeError("Dispatch consumption requires the owning database transaction.")
        if _dispatch_consume_session.get() is not None:
            raise RuntimeError("Dispatch consumption authority is single-use and cannot nest.")
        unresolved = system_queryset(self.model, using=using, lock=None).get(pk=dispatch_id)
        if unresolved.kind == WorkflowDispatchKind.ADVANCE:
            run_model = self.model._meta.get_field("run").remote_field.model
            locked_run = system_queryset(run_model, using=using, lock=("self",)).get(pk=unresolved.run_id)
            if locked_run.pk != unresolved.run_id:
                raise OperationalError("Advance dispatch ancestry changed while locking.")
        elif unresolved.kind == WorkflowDispatchKind.EXECUTE:
            attempt_model = self.model._meta.get_field("step_attempt").remote_field.model
            ancestry = system_queryset(attempt_model, using=using, lock=None).values(
                "step_run_id", "step_run__run_id"
            ).get(pk=unresolved.step_attempt_id)
            step_run_model = attempt_model._meta.get_field("step_run").remote_field.model
            run_model = step_run_model._meta.get_field("run").remote_field.model
            locked_run = system_queryset(run_model, using=using, lock=("self",)).get(
                pk=ancestry["step_run__run_id"]
            )
            locked_step_run = system_queryset(step_run_model, using=using, lock=("self",)).get(
                pk=ancestry["step_run_id"]
            )
            locked_attempt = system_queryset(attempt_model, using=using, lock=("self",)).get(
                pk=unresolved.step_attempt_id
            )
            if (
                locked_step_run.run_id != locked_run.pk
                or locked_attempt.step_run_id != locked_step_run.pk
            ):
                raise OperationalError("Execution dispatch ancestry changed while locking.")
        else:
            decision_model = self.model._meta.get_field("decision").remote_field.model
            ancestry = system_queryset(decision_model, using=using, lock=None).values(
                "step_run_id", "step_run__run_id", "suspension_attempt_id"
            ).get(pk=unresolved.decision_id)
            step_run_model = decision_model._meta.get_field("step_run").remote_field.model
            run_model = step_run_model._meta.get_field("run").remote_field.model
            locked_run = system_queryset(run_model, using=using, lock=("self",)).get(
                pk=ancestry["step_run__run_id"]
            )
            locked_step_run = system_queryset(step_run_model, using=using, lock=("self",)).get(
                pk=ancestry["step_run_id"]
            )
            locked_attempt = None
            if ancestry["suspension_attempt_id"] is not None:
                attempt_model = self.model._meta.get_field("step_attempt").remote_field.model
                locked_attempt = system_queryset(attempt_model, using=using, lock=("self",)).get(
                    pk=ancestry["suspension_attempt_id"]
                )
            locked_decision = system_queryset(decision_model, using=using, lock=("self",)).get(
                pk=unresolved.decision_id
            )
            if (
                locked_step_run.run_id != locked_run.pk
                or locked_decision.step_run_id != locked_step_run.pk
                or locked_decision.suspension_attempt_id != ancestry["suspension_attempt_id"]
                or (
                    locked_attempt is not None
                    and locked_attempt.step_run_id != locked_step_run.pk
                )
            ):
                raise OperationalError("Decision dispatch ancestry changed while locking.")
        dispatch = system_queryset(self.model, using=using, lock=None).select_related(
            "step_attempt"
        ).get(pk=dispatch_id)
        envelope = dispatch.envelope
        if envelope.lease_token != lease_token:
            yield DispatchPreflight(envelope, DispatchPreflightDisposition.FENCED)
            return
        if dispatch.consumed_at is not None:
            yield DispatchPreflight(envelope, DispatchPreflightDisposition.DUPLICATE)
            return
        if at < dispatch.available_at:
            yield DispatchPreflight(envelope, DispatchPreflightDisposition.EARLY)
            return
        token = _dispatch_consume_session.set(
            _DispatchConsumeSession(
                using,
                id(connection),
                id(connection.atomic_blocks[0]),
                str(dispatch.kind),
                envelope.target_id,
                dispatch.generation,
                dispatch.pk,
                lease_token,
            )
        )
        try:
            yield DispatchPreflight(envelope, DispatchPreflightDisposition.READY)
            session = _dispatch_consume_session.get()
            if session is not None and not session.consumed:
                raise RuntimeError("Ready dispatch transition exited without consuming its intent.")
        finally:
            _dispatch_consume_session.reset(token)

    def _consume_locked(
        self,
        dispatch_id: int,
        *,
        at: datetime,
        fenced: bool = False,
    ) -> DispatchConsumption:
        """Consume one exact intent last in an already locked domain transition."""

        alias = self.db
        session = _dispatch_consume_session.get()
        connection = connections[alias]
        if (
            session is None
            or session.alias != alias
            or session.connection_id != id(connection)
            or not connection.in_atomic_block
            or not connection.atomic_blocks
            or session.outer_atomic_id != id(connection.atomic_blocks[0])
            or session.consumed
            or session.dispatch_id != dispatch_id
        ):
            raise RuntimeError("Dispatch consumption requires exact domain-owner authority.")
        dispatch = system_queryset(self.model, using=alias, lock=("self",)).get(pk=dispatch_id)
        if (
            dispatch.kind != session.kind
            or dispatch.envelope.target_id != session.target_id
            or dispatch.generation != session.generation
            or dispatch.envelope.lease_token != session.lease_token
        ):
            raise RuntimeError("Dispatch consumption authority does not match this intent.")
        if dispatch.consumed_at is not None or at < dispatch.available_at:
            raise RuntimeError("Ready dispatch admission changed before consumption.")
        session.consumed = True
        dispatch.consumed_at = at
        self._save(dispatch, alias=alias, update_fields=["consumed_at", "updated_at"])
        return DispatchConsumption.FENCED if fenced else DispatchConsumption.CONSUMED

    def schedule_advance(self, run: Any, *, available_at: datetime) -> Any:
        """Create one independent run advance after locking its owner."""

        alias = self.db
        run_model = self.model._meta.get_field("run").remote_field.model
        with transaction.atomic(using=alias), system_context(reason="workflows.dispatch.schedule_advance"):
            locked = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run.pk)
            dispatch = self.model(kind=WorkflowDispatchKind.ADVANCE, run=locked, available_at=available_at)
            self._save(dispatch, alias=alias, force_insert=True)
            return dispatch

    def schedule_execute(self, attempt: Any) -> tuple[Any, bool]:
        """Ensure one execution intent using the attempt's immutable availability."""

        alias = self.db
        attempt_model = self.model._meta.get_field("step_attempt").remote_field.model
        row = system_queryset(attempt_model, using=alias, lock=None).values("step_run_id").get(pk=attempt.pk)
        step_run_model = attempt_model._meta.get_field("step_run").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        run_id = system_queryset(step_run_model, using=alias, lock=None).values_list("run_id", flat=True).get(
            pk=row["step_run_id"]
        )
        with transaction.atomic(using=alias), system_context(reason="workflows.dispatch.schedule_execute"):
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
            step_run = system_queryset(step_run_model, using=alias, lock=("self",)).get(pk=row["step_run_id"])
            locked = system_queryset(attempt_model, using=alias, lock=("self",)).get(pk=attempt.pk)
            if locked.step_run_id != step_run.pk or step_run.run_id != run.pk:
                raise OperationalError("Execution dispatch ancestry changed while locking.")
            existing = system_queryset(self.model, using=alias, lock=("self",)).filter(
                kind=WorkflowDispatchKind.EXECUTE, step_attempt=locked
            ).first()
            if existing is not None:
                return existing, False
            if (
                run.is_terminal
                or step_run.status != StepRunStatus.STARTED
                or step_run.current_attempt_id != locked.pk
                or locked.lease_revoked_at is not None
                or locked.started_at is not None
                or locked.result_recorded_at is not None
            ):
                raise ValidationError({"attempt": "Execution dispatch requires the current unfinished attempt."})
            available_at = locked.available_at or locked.claimed_at
            if available_at is None:
                raise ValidationError({"attempt": "Execution dispatch requires a claimed availability time."})
            dispatch = self.model(
                kind=WorkflowDispatchKind.EXECUTE,
                step_attempt=locked,
                available_at=available_at,
            )
            self._save(dispatch, alias=alias, force_insert=True)
            return dispatch, True

    def schedule_decision(self, kind: WorkflowDispatchKind, decision: Any) -> tuple[Any, bool]:
        """Ensure one timer intent from the locked Decision deadline and generation."""

        if kind not in {WorkflowDispatchKind.DECISION_EXPIRE, WorkflowDispatchKind.DECISION_ESCALATE}:
            raise ValidationError({"kind": "Decision dispatch kind must be expire or escalate."})
        alias = self.db
        decision_model = self.model._meta.get_field("decision").remote_field.model
        ancestry = system_queryset(decision_model, using=alias, lock=None).values(
            "step_run_id", "step_run__run_id", "suspension_attempt_id"
        ).get(pk=decision.pk)
        step_run_model = decision_model._meta.get_field("step_run").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        attempt_model = self.model._meta.get_field("step_attempt").remote_field.model
        with transaction.atomic(using=alias), system_context(reason="workflows.dispatch.schedule_decision"):
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=ancestry["step_run__run_id"])
            step_run = system_queryset(step_run_model, using=alias, lock=("self",)).get(pk=ancestry["step_run_id"])
            attempt = None
            if ancestry["suspension_attempt_id"] is not None:
                attempt = system_queryset(attempt_model, using=alias, lock=("self",)).get(
                    pk=ancestry["suspension_attempt_id"]
                )
            locked = system_queryset(decision_model, using=alias, lock=("self",)).get(pk=decision.pk)
            if locked.step_run_id != step_run.pk or step_run.run_id != run.pk or (
                attempt is not None and attempt.step_run_id != step_run.pk
            ):
                raise OperationalError("Decision dispatch ancestry changed while locking.")
            existing = system_queryset(self.model, using=alias, lock=("self",)).filter(
                kind=kind, decision=locked, generation=locked.attempts
            ).first()
            if existing is not None:
                return existing, False
            available_at = (
                locked.expires_at
                if kind == WorkflowDispatchKind.DECISION_EXPIRE
                else locked.escalate_at
            )
            if available_at is None:
                raise ValidationError({"decision": "Decision dispatch requires its declared deadline."})
            if locked.verdict != Verdict.PENDING or step_run.status != StepRunStatus.WAITING or run.is_terminal:
                raise ValidationError({"decision": "Decision timer dispatch requires a current pending decision."})
            if attempt is not None and (
                step_run.current_attempt_id != attempt.pk
                or attempt.result_kind != str(AttemptResultKind.SUSPEND)
                or attempt.applied_at is None
            ):
                raise ValidationError({"decision": "Decision timer dispatch requires its applied suspension."})
            dispatch = self.model(
                kind=kind,
                decision=locked,
                generation=locked.attempts,
                available_at=available_at,
            )
            self._save(dispatch, alias=alias, force_insert=True)
            return dispatch, True

    def due_envelopes(self, *, now: datetime, limit: int) -> tuple[WorkflowDispatchEnvelope, ...]:
        """Return a fair bounded publication snapshot without holding row locks."""

        with system_context(reason="workflows.dispatch.due"):
            rows = list(
                self.filter(
                    consumed_at__isnull=True,
                    available_at__lte=now,
                    next_send_at__lte=now,
                ).select_related("step_attempt").order_by("next_send_at", "available_at", "pk")[:limit]
            )
        return tuple(row.envelope for row in rows)

    def record_publication(self, dispatch_id: int, *, attempted_at: datetime, error: str) -> None:
        """Record bounded send telemetry after one unlocked transport call."""

        alias = self.db
        with transaction.atomic(using=alias), system_context(reason="workflows.dispatch.telemetry"):
            dispatch = system_queryset(self.model, using=alias, lock=("self",)).get(pk=dispatch_id)
            dispatch.send_count += 1
            fields = ["send_count", "updated_at"]
            if dispatch.last_sent_at is None or attempted_at >= dispatch.last_sent_at:
                dispatch.last_sent_at = attempted_at
                dispatch.last_send_error = "Transport send failed." if error else ""
                seconds = min(300, 2 ** min(dispatch.send_count, 8)) if error else 30
                dispatch.next_send_at = attempted_at + timedelta(seconds=seconds)
                fields.extend(["last_sent_at", "last_send_error", "next_send_at"])
            self._save(
                dispatch,
                alias=alias,
                update_fields=fields,
            )


class WorkflowDispatch(AuditMixin, AngeeDataModel):
    """One durable, resendable workflow delivery intent."""

    runtime = True

    sqid_prefix = "wfd_"
    kind = models.CharField(
        max_length=32,
        choices=[(value.value, value.name.title()) for value in WorkflowDispatchKind],
    )
    run = models.ForeignKey(
        "workflows.WorkflowRun", on_delete=models.PROTECT, null=True, blank=True, related_name="dispatches"
    )
    step_attempt = models.ForeignKey(
        "workflows.StepAttempt", on_delete=models.PROTECT, null=True, blank=True, related_name="dispatches"
    )
    decision = models.ForeignKey(
        "workflows.Decision", on_delete=models.PROTECT, null=True, blank=True, related_name="dispatches"
    )
    generation = models.PositiveIntegerField(null=True, blank=True, editable=False)
    available_at = models.DateTimeField(db_index=True, editable=False)
    next_send_at = models.DateTimeField(db_index=True, editable=False)
    consumed_at = models.DateTimeField(null=True, blank=True, editable=False)
    send_count = models.PositiveIntegerField(default=0, editable=False)
    last_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_send_error = models.CharField(max_length=64, blank=True, default="", editable=False)

    objects = WorkflowDispatchManager()

    class Meta:
        abstract = True
        base_manager_name = "objects"
        ordering = ("available_at", "pk")
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(kind=WorkflowDispatchKind.ADVANCE, run__isnull=False, step_attempt__isnull=True,
                             decision__isnull=True, generation__isnull=True)
                    | models.Q(kind=WorkflowDispatchKind.EXECUTE, run__isnull=True, step_attempt__isnull=False,
                               decision__isnull=True, generation__isnull=True)
                    | models.Q(kind__in=[WorkflowDispatchKind.DECISION_EXPIRE,
                                         WorkflowDispatchKind.DECISION_ESCALATE], run__isnull=True,
                               step_attempt__isnull=True, decision__isnull=False, generation__isnull=False)
                ),
                name="chk_wfd_target_shape",
            ),
            models.UniqueConstraint(
                fields=("step_attempt",),
                condition=models.Q(kind=WorkflowDispatchKind.EXECUTE),
                name="uniq_wfd_execute_attempt",
            ),
            models.UniqueConstraint(
                fields=("kind", "decision", "generation"),
                condition=models.Q(kind__in=[WorkflowDispatchKind.DECISION_EXPIRE,
                                             WorkflowDispatchKind.DECISION_ESCALATE]),
                name="uniq_wfd_decision_timer",
            ),
        )

    @property
    def target_identity(self) -> tuple[int | None, int | None, int | None, int | None]:
        """Return immutable target fields used by the exact-row write guard."""

        return self.run_id, self.step_attempt_id, self.decision_id, self.generation

    @property
    def envelope(self) -> WorkflowDispatchEnvelope:
        """Return the identifier-only transport message for this validated row."""

        kind = WorkflowDispatchKind(self.kind)
        target_id = self.run_id if kind == WorkflowDispatchKind.ADVANCE else (
            self.step_attempt_id if kind == WorkflowDispatchKind.EXECUTE else self.decision_id
        )
        if target_id is None:
            raise ValueError("Workflow dispatch target does not match its kind.")
        lease_token = self.step_attempt.lease_token if kind == WorkflowDispatchKind.EXECUTE else None
        return WorkflowDispatchEnvelope(self.pk, kind, target_id, self.generation, lease_token)

    def save(self, *args: Any, **kwargs: Any) -> None:
        alias = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        capability = _dispatch_save_capability.get()
        connection = connections[alias]
        if (
            capability is None
            or capability.consumed
            or capability.alias != alias
            or capability.connection_id != id(connection)
            or not connection.in_atomic_block
            or capability.instance_id != id(self)
            or capability.pk != self.pk
            or capability.adding != self._state.adding
            or capability.kind != str(self.kind)
            or capability.target != self.target_identity
        ):
            raise TypeError("Workflow dispatches can only be saved by WorkflowDispatchManager.")
        if not self._state.adding:
            persisted = system_queryset(type(self), using=alias, lock=None).values(
                "kind", "run_id", "step_attempt_id", "decision_id", "generation", "available_at"
            ).get(pk=self.pk)
            if (
                persisted["kind"] != self.kind
                or persisted["run_id"] != self.run_id
                or persisted["step_attempt_id"] != self.step_attempt_id
                or persisted["decision_id"] != self.decision_id
                or persisted["generation"] != self.generation
                or persisted["available_at"] != self.available_at
            ):
                raise TypeError("Workflow dispatch identity and availability are immutable.")
        capability.consumed = True
        if self._state.adding and self.next_send_at is None:
            self.next_send_at = self.available_at
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise TypeError("Workflow dispatches are durable delivery evidence and cannot be deleted.")
