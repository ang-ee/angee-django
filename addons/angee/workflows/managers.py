"""Shared manager primitives for workflow domain transitions."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Collection, Iterable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Literal, Self, cast

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.validators import validate_slug
from django.db import (
    DEFAULT_DB_ALIAS,
    IntegrityError,
    OperationalError,
    connections,
    models,
    transaction,
)
from django.utils import timezone
from jsonschema import Draft202012Validator
from pydantic import ValidationError as PydanticValidationError
from pydantic_core import PydanticSerializationError
from rebac import (
    LocalBackend,
    ObjectRef,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    resource_id_attr,
    system_context,
    write_relationships,
)
from rebac.actors import NoActorResolvedError, to_subject_ref
from rebac.backends import backend as rebac_backend
from rebac.relationships import delete_relationship
from rebac.resources import model_for_resource_type, to_object_ref

from angee.base.actors import actor_user_id
from angee.base.identity import (
    canonical_subject_ref,
    instance_from_public_id,
    public_data_id_field,
    public_id_for,
)
from angee.base.mixins import AppendOnlyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.refs import canonical_record_model, canonical_record_target
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.workflows.attempts import (
    ArtifactSpec,
    AttemptCause,
    AttemptClaim,
    AttemptFinalization,
    AttemptInput,
    AttemptResult,
    AttemptResultKind,
    DecisionGateOutput,
    DecisionInputSource,
    DecisionResolution,
    DecisionSpec,
    DecisionTimerIntent,
    DecisionTimerKind,
    ExternalOperationPolicy,
    FixtureRole,
    FixtureSource,
    FixtureSourcePage,
    FixtureSourceSummary,
    FixtureSpec,
    InvocationAdmission,
    JsonPresence,
    LeaseRevocation,
    LeaseRevocationReason,
    MapExpansionPlan,
    MapItemSource,
    RecoveryCapability,
    RecoveryMode,
    RecoveryPlan,
    RetryIntent,
    WorkflowRepairContext,
    WorkflowScope,
    WorkflowSetupPlan,
    deserialize_decision_specs,
    json_values_equal,
    map_child_input,
    serialize_decision_specs,
    validate_fixture_spec,
    validate_json_presence,
    workflow_result_terminal_match_error,
)
from angee.workflows.data_contracts import JsonPath, json_value_at_path
from angee.workflows.decision_actions import (
    compile_decision_action_schema,
    retained_decision_form_schema,
)
from angee.workflows.definitions import StaleDefinitionError, WorkflowDefinitionManagerMixin
from angee.workflows.dispatch import (
    DispatchConsumption,
    DispatchPreflight,
    DispatchPreflightDisposition,
    WorkflowDispatchEnvelope,
    WorkflowDispatchKind,
    enqueue_dispatch_publisher,
)
from angee.workflows.graph import GraphFreshnessReason, GraphIdentity, WorkflowGraph
from angee.workflows.manager_authority import (
    _artifact_batch_capability,
    _artifact_write_capability,
    _ArtifactBatchCapability,
    _ArtifactWriteCapability,
    _AtomicWriteCapability,
    _attempt_save_capability,
    _attempt_write_active,
    _attempt_write_session,
    _AttemptSaveCapability,
    _AttemptWriteSession,
    _decision_resolution_session,
    _decision_save_capability,
    _decision_write_session,
    _DecisionResolutionSession,
    _DecisionSaveCapability,
    _DecisionWriteSession,
    _definition_write_session,
    _DefinitionWriteSession,
    _dispatch_consume_session,
    _dispatch_save_capability,
    _DispatchConsumeSession,
    _DispatchSaveCapability,
    _recovery_evidence_batch,
    _recovery_evidence_write_row,
    _recovery_write_run,
    _step_run_save_capability,
    _StepRunSaveCapability,
    _test_fixture_apply_ids,
    _test_fixture_batch_rows,
    _test_fixture_write_run,
)
from angee.workflows.states import (
    CURRENT_PUBLICATION_STATUSES,
    ParentRelation,
    RunOrigin,
    RunStatus,
    StepRunStatus,
    TriggerKind,
    Verdict,
    WaitingKind,
    WorkflowPurpose,
    WorkflowStatus,
)
from angee.workflows.steps import StepExecutionMode, retry_policy_from_config
from angee.workflows.trigger_declarations import (
    EventAdmissionPolicy,
    EventSource,
    EventTriggerConfig,
)

_CURRENCY_STATUSES = CURRENT_PUBLICATION_STATUSES
logger = logging.getLogger(__name__)


def _retained_record_access_refs(decision: Any) -> tuple[ObjectRef, ...]:
    """Parse one Decision's immutable delegation identities without widening them."""

    refs: list[ObjectRef] = []
    seen: set[tuple[str, str]] = set()
    for raw in decision.record_access:
        if not isinstance(raw, dict) or set(raw) not in (
            {"resource_type", "resource_id"},
            {"resource_type", "resource_id", "authority_decision_id"},
        ):
            raise ValidationError({"record_access": "Retained Decision delegation identity is invalid."})
        resource_type = raw["resource_type"]
        resource_id = raw["resource_id"]
        authority_id = raw.get("authority_decision_id")
        if (
            not isinstance(resource_type, str)
            or not resource_type
            or not isinstance(resource_id, str)
            or not resource_id
            or ("authority_decision_id" in raw and (not isinstance(authority_id, str) or not authority_id))
        ):
            raise ValidationError({"record_access": "Retained Decision delegation identity is invalid."})
        key = (resource_type, resource_id)
        if key in seen:
            raise ValidationError({"record_access": "Retained Decision delegation identities must be unique."})
        seen.add(key)
        refs.append(ObjectRef(resource_type, resource_id))
    return tuple(refs)


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


class DefinitionQuerySet(AngeeQuerySet[Any]):
    """Definition collection writes that cannot preserve row invariants."""

    def bound_to(self, owner: Any) -> Self:
        """Carry one explicit actor or sudo binding into related definition work."""

        if owner.is_sudo():
            return cast(Self, self.sudo(reason="workflows.definition_write.cascade"))
        if actor := owner.actor():
            return cast(Self, self.with_actor(actor))
        return self

    @staticmethod
    def caller_context(owner: Any) -> Any:
        """Project an explicit definition binding into validation queries."""

        if owner.is_sudo():
            return system_context(reason="workflows.definition_write.validation")
        if actor := owner.actor():
            return actor_context(actor)
        return nullcontext()

    @staticmethod
    def bind_instance(instance: Any, owner: Any) -> Any:
        """Carry a caller binding onto one manager-fetched or copied row."""

        if owner.is_sudo():
            return instance.sudo(reason="workflows.definition_write.owner")
        if actor := owner.actor():
            return instance.with_actor(actor)
        return instance

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
            workflow_model.objects.sudo(reason="workflows.subject_declaration.current")
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
            .filter(subject_declaration=declaration)
            .order_by("name", "version", "pk"),
        )

    def with_lineage_projection(self) -> Self:
        """Annotate lineage and current-publication context for every row."""

        workflow_model = cast(Any, self.model)
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
                system_queryset(self.model, using=alias, lock=("self",)).filter(pk__in=ordered_ids).order_by("pk")
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
                        self.model._base_manager.using(alias).filter(pk=workflow_id, published_from__isnull=True),
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

        with DefinitionQuerySet.caller_context(workflow):
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

    def test_snapshot(self, workflow: Any, *, expected_revision: int, require_readiness: bool = True) -> Any:
        """Return the immutable test copy of one exact saved draft revision."""

        self._validate_expected_revision(workflow, expected_revision)
        if workflow.published_from_id is not None:
            raise ValidationError({"workflow": "Test snapshots can only be taken from a lineage head."})
        alias = self.db
        with system_context(reason="workflows.test_snapshot"), self._definition_write((workflow.pk,), using=alias):
            draft = cast(
                Any,
                DefinitionQuerySet.bind_instance(_definition_rows(self.model, alias).get(pk=workflow.pk), workflow),
            )
            if draft.draft_revision != expected_revision:
                raise StaleDefinitionError(expected=expected_revision, current=draft.draft_revision)
            existing = (
                system_queryset(self.model, using=alias, lock=None)
                .filter(
                    published_from=draft,
                    status=WorkflowStatus.TEST,
                    draft_revision=expected_revision,
                )
                .first()
            )
            if existing is not None:
                return existing
            if require_readiness:
                with DefinitionQuerySet.caller_context(draft):
                    draft._validate_publishable()
            snapshot = draft._new_definition_copy(
                version=0,
                draft_revision=expected_revision,
            )
            DefinitionQuerySet.bind_instance(snapshot, draft)
            snapshot.save(using=alias)
            with self._copy_to(snapshot.pk):
                with DefinitionQuerySet.caller_context(draft):
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
        """No admitted run can have its creation tuple changed by a collection."""

        if self.model.invocation_identity_write_names() & kwargs.keys() or {"result", "status"} & kwargs.keys():
            raise TypeError("Workflow run invocation identity and terminal facts are immutable.")
        return super().update(**kwargs)

    def bulk_update(self, objs: Iterable[Any], fields: Iterable[str], batch_size: int | None = None) -> int:
        """Apply the same admitted-run identity rule to bulk writes."""

        field_names = tuple(fields)
        if self.model.invocation_identity_write_names() & set(field_names) or {"result", "status"} & set(field_names):
            raise TypeError("Workflow run invocation identity and terminal facts are immutable.")
        return super().bulk_update(objs, field_names, batch_size=batch_size)

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        """Start admission pins publication, actor, and first durable work together."""

        raise TypeError("Workflow runs must be admitted by the run owner, not bulk-created.")


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
        parent_relation: ParentRelation | str = "",
        dedup_key: str | None = None,
        origin: RunOrigin | None = None,
        input: JsonPresence = JsonPresence(),
        available_at: datetime | None = None,
        validate_new: Callable[[], None] | None = None,
    ) -> Any:
        """Create or exactly retain a pinned run and its first ADVANCE.

        ``validate_new`` is a side-effect-free domain consistency check. It runs
        inside the start transaction only when no retained identity exists;
        callers must perform authorization before entering this system-owned
        persistence boundary.
        """

        input = validate_json_presence(input, label="workflow run input")
        if (parent_step_run is None) != (parent_relation == ""):
            raise ValidationError(
                {"parent_relation": "Choose a parent relationship exactly when starting from a parent step."}
            )
        if parent_relation and parent_relation not in ParentRelation.values:
            raise ValidationError({"parent_relation": "Unknown parent relationship."})
        if dedup_key is not None and len(dedup_key) > self.model._meta.get_field("dedup_key").max_length:
            raise ValidationError({"dedup_key": "Workflow run dedup key is too long."})
        alias = self.db
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        head_id = workflow.pk if workflow.published_from_id is None else workflow.published_from_id
        with system_context(reason="workflows.runs.start"), transaction.atomic(using=alias):
            locked_parent = self._lock_child_parent_for_start(
                parent_step_run,
                origin=origin,
                using=alias,
            )
            head = system_queryset(workflow_model, using=alias, lock=("self",)).get(pk=head_id)
            locked_trigger = None
            if trigger is not None:
                trigger_model = self.model._meta.get_field("trigger").remote_field.model
                locked_trigger = system_queryset(trigger_model, using=alias, lock=("self",)).get(pk=trigger.pk)
                if locked_trigger.workflow_id != head.pk:
                    raise ValidationError({"trigger": "Workflow trigger does not belong to this lineage."})
            return self._start_locked(
                head,
                subject,
                actor,
                trigger=locked_trigger,
                parent_step_run=locked_parent,
                parent_relation=parent_relation,
                dedup_key=dedup_key,
                origin=origin,
                input=input,
                available_at=available_at or timezone.now(),
                using=alias,
                validate_new=validate_new,
                requested_publication_id=workflow.pk if workflow.published_from_id is not None else None,
            )

    def retained_child_for_start(
        self,
        parent_step_run: Any,
        *,
        actor: Any,
        origin: RunOrigin,
    ) -> Any | None:
        """Return the readable child retained for one authoritative start slot.

        Recovery normalization is execution provenance only. This lookup grants
        no authority over the child or its business subject; callers must still
        validate their immutable domain input before reusing the returned run.
        """

        alias = self.db
        step_run_model = self.model._meta.apps.get_model("workflows", "StepRun")
        parent_run_id = (
            system_queryset(step_run_model, using=alias, lock=None)
            .values_list(
                "run_id",
                flat=True,
            )
            .get(pk=parent_step_run.pk)
        )
        readable_runs = read_scoped_queryset(self.model, actor, action="read")
        if readable_runs is not None:
            readable_runs = readable_runs.using(alias)
        if readable_runs is None or not readable_runs.filter(pk=parent_run_id).exists():
            raise PermissionDenied("Parent workflow execution is unavailable.")
        with system_context(reason="workflows.runs.retained_child_for_start"), transaction.atomic(using=alias):
            locked_parent = self._lock_child_parent_for_start(
                parent_step_run,
                origin=origin,
                using=alias,
            )
            retained = (
                system_queryset(self.model, using=alias, lock=("self",))
                .filter(
                    parent_step_run=locked_parent,
                )
                .first()
            )
        if retained is None:
            return None
        readable_runs = read_scoped_queryset(self.model, actor, action="read")
        if readable_runs is not None:
            readable_runs = readable_runs.using(alias)
        readable = None if readable_runs is None else readable_runs.filter(pk=retained.pk).first()
        if readable is None:
            raise PermissionDenied("Retained child workflow execution is unavailable.")
        return readable

    def _lock_child_parent_for_start(
        self,
        parent_step_run: Any | None,
        *,
        origin: RunOrigin | None,
        using: str,
    ) -> Any | None:
        """Lock and normalize the one parent identity accepted by child start."""

        if parent_step_run is None:
            return None
        step_run_model = self.model._meta.apps.get_model("workflows", "StepRun")
        parent_run_id = (
            system_queryset(step_run_model, using=using, lock=None)
            .values_list(
                "run_id",
                flat=True,
            )
            .get(pk=parent_step_run.pk)
        )
        locked_parent_run = (
            system_queryset(self.model, using=using, lock=("self",))
            .select_related("recovery_source_attempt__step_run")
            .get(pk=parent_run_id)
        )
        locked_parent = system_queryset(step_run_model, using=using, lock=("self",)).get(
            pk=parent_step_run.pk,
        )
        if locked_parent.run_id != parent_run_id:
            raise OperationalError("Parent workflow step changed while locking.")
        recovery_source = locked_parent_run.recovery_source_attempt
        if (
            origin == RunOrigin.WORKFLOW
            and locked_parent_run.origin == RunOrigin.RECOVERY
            and locked_parent_run.recovery_mode == RecoveryMode.FRESH
            and recovery_source is not None
            and recovery_source.step_run.step_id == locked_parent.step_id
            and recovery_source.step_run.map_index == locked_parent.map_index
        ):
            return self._fresh_recovery_child_parent(
                locked_parent_run,
                locked_parent,
                using=using,
            )
        return locked_parent

    def _fresh_recovery_child_parent(
        self,
        recovery_run: Any,
        recovery_step_run: Any,
        *,
        using: str,
        require_active: bool = True,
    ) -> Any:
        """Resolve a replayed child handoff to its oldest exact FRESH source slot."""

        attempt_model = self.model._meta.apps.get_model("workflows", "StepAttempt")
        step_run_model = self.model._meta.apps.get_model("workflows", "StepRun")
        lineage, _retained_child_target = attempt_model.objects._fresh_recovery_anchor(
            recovery_step_run.current_attempt_id,
            recovery_run_id=recovery_run.pk,
            alias=using,
            lock=("self",),
            require_active=require_active,
        )
        parent = recovery_step_run
        seen: set[int] = set()
        while (
            lineage.cause == AttemptCause.MANUAL_RETRY
            and lineage.recovery_mode == RecoveryMode.FRESH
            and lineage.recovery_source_attempt_id is not None
        ):
            if lineage.pk in seen:
                raise ValidationError({"parent_step_run": "Recovery attempt lineage contains a cycle."})
            seen.add(lineage.pk)
            source = (
                system_queryset(attempt_model, using=using, lock=("self",))
                .select_related("step_run")
                .get(pk=lineage.recovery_source_attempt_id)
            )
            source_parent = source.step_run
            if (
                source_parent.step_id != parent.step_id
                or source_parent.map_index != parent.map_index
                or source_parent.current_attempt_id != source.pk
                or source_parent.status not in {StepRunStatus.FAILED, StepRunStatus.CANCELED}
                or source.input_present != lineage.input_present
                or not json_values_equal(source.input, lineage.input)
            ):
                raise ValidationError({"parent_step_run": "Recovery child handoff source identity changed."})
            parent = system_queryset(step_run_model, using=using, lock=("self",)).get(pk=source_parent.pk)
            lineage = source
        return parent

    def reprocess(self, source_run: Any, *, actor: Any, request_key: str) -> Any:
        """Start an idempotent new current-publication run for the same subject.

        This is whole-run business reprocessing. Failed-attempt evidence recovery
        remains the separate ``start_recovery`` contract.
        """

        source_run.with_actor(actor)._require_record_access("write")
        request_key = request_key.strip() if isinstance(request_key, str) else ""
        if not request_key:
            raise ValidationError({"request_key": "Reprocessing requires a non-empty request key."})
        alias = self.db
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        source = system_queryset(self.model, using=alias, lock=None).select_related("workflow").get(pk=source_run.pk)
        head_id = source.workflow.published_from_id or source.workflow_id
        dedup_key = f"reprocess:{source.pk}:{request_key}"
        if len(dedup_key) > self.model._meta.get_field("dedup_key").max_length:
            raise ValidationError({"request_key": "Reprocessing request key is too long."})
        with system_context(reason="workflows.runs.reprocess"), transaction.atomic(using=alias):
            head = system_queryset(workflow_model, using=alias, lock=("self",)).get(pk=head_id)
            existing = system_queryset(self.model, using=alias, lock=None).filter(dedup_key=dedup_key).first()
            if existing is not None:
                if existing.reprocessed_from_id != source.pk:
                    raise ValidationError("Reprocessing request identity conflicts with an existing run.")
                return existing
            lineage_versions = (
                system_queryset(workflow_model, using=alias, lock=None)
                .filter(models.Q(pk=head.pk) | models.Q(published_from_id=head.pk))
                .values("pk")
            )
            active = (
                system_queryset(self.model, using=alias, lock=("self",))
                .filter(
                    workflow_id__in=models.Subquery(lineage_versions),
                    subject_content_type_id=source.subject_content_type_id,
                    subject_object_id=source.subject_object_id,
                    status__in=(RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING),
                )
                .exists()
            )
            if active:
                raise ValidationError({"source_run": "This workflow subject already has an active run."})
            return self._start_locked(
                head,
                source.subject,
                actor,
                dedup_key=dedup_key,
                origin=cast(RunOrigin, RunOrigin.MANUAL),
                input=JsonPresence(source.input_present, copy.deepcopy(source.input)),
                reprocessed_from=source,
                available_at=timezone.now(),
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
        parent_relation: ParentRelation | str = "",
        dedup_key: str | None = None,
        occurrence_id: str | None = None,
        origin: RunOrigin | None = None,
        input: JsonPresence = JsonPresence(),
        available_at: datetime,
        using: str,
        reprocessed_from: Any = None,
        validate_new: Callable[[], None] | None = None,
        requested_publication_id: int | None = None,
    ) -> Any:
        """Create initial rows after callers lock the exact lineage and trigger."""

        connection = connections[using]
        if not connection.in_atomic_block:
            raise RuntimeError("Pinned workflow start requires its owning transaction.")
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        content_type = None if subject is None else ContentType.objects.get_for_model(subject, for_concrete_model=False)
        run_dedup_key = dedup_key or self._trigger_dedup_key(
            trigger, content_type, None if subject is None else subject.pk
        )
        retained = None
        if parent_step_run is not None:
            retained = (
                system_queryset(self.model, using=using, lock=("self",))
                .select_related("workflow")
                .filter(
                    parent_step_run=parent_step_run,
                )
                .first()
            )
        if retained is None and run_dedup_key:
            retained = (
                system_queryset(self.model, using=using, lock=("self",))
                .select_related("workflow")
                .filter(
                    dedup_key=run_dedup_key,
                )
                .first()
            )
        if retained is not None and (retained.workflow.published_from_id or retained.workflow_id) != head.pk:
            raise ValidationError({"workflow": "Retained invocation belongs to a different workflow lineage."})
        if requested_publication_id is not None:
            version = system_queryset(workflow_model, using=using, lock=("self",)).get(pk=requested_publication_id)
            if version.published_from_id != head.pk:
                raise ValidationError({"workflow": "Requested publication does not belong to this lineage."})
        elif retained is not None:
            version = retained.workflow
        else:
            version = workflow_model.objects.current_published_for(head)
        if version is None:
            raise ValidationError({"workflow": "Workflow has no published version to start."})
        if version.status != WorkflowStatus.PUBLISHED and retained is None:
            raise ValidationError({"workflow": "Workflow runs must pin a published version."})
        if retained is None and trigger is not None and not trigger.enabled:
            raise ValidationError({"trigger": "Workflow trigger is disabled."})
        return self._start_pinned_locked(
            version,
            subject,
            actor,
            trigger=trigger,
            parent_step_run=parent_step_run,
            parent_relation=parent_relation,
            dedup_key=dedup_key,
            occurrence_id=occurrence_id,
            origin=origin,
            input=input,
            available_at=available_at,
            using=using,
            reprocessed_from=reprocessed_from,
            validate_new=validate_new,
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
        scope: WorkflowScope = cast(WorkflowScope, WorkflowScope.WHOLE),
        selected_step: Any = None,
        fixtures: tuple[FixtureSpec, ...] = (),
        repair_source_attempt: Any = None,
    ) -> Any:
        """Start or recover one idempotent whole-workflow test request."""

        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        workflow_model.objects._validate_expected_revision(workflow, expected_revision)
        input = validate_json_presence(input, label="workflow run input")
        if not isinstance(scope, WorkflowScope):
            raise ValidationError({"scope": "Test launches require a declared scope."})
        if (scope is WorkflowScope.NODE) != (selected_step is not None):
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
                raise ValidationError({"repair_source_attempt": "Repair evidence belongs to another workflow lineage."})
            if (
                scope is not WorkflowScope.NODE
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
                if existing.test_repair_source_attempt_id != getattr(repair_source_attempt, "pk", None):
                    raise ValidationError({"request_key": "Test launch repair evidence does not match."})

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
        acknowledge_uncertain_external: bool = False,
        prior_recovery: Any = None,
    ) -> Any:
        """Admit one linked same-revision recovery from exact retained evidence."""

        if not isinstance(request_key, str) or not request_key.strip():
            raise ValidationError({"request_key": "Recovery request keys must be non-empty strings."})
        alias = self.db
        attempt_model = self.model._meta.apps.get_model("workflows", "StepAttempt")
        step_run_model = self.model._meta.apps.get_model("workflows", "StepRun")
        source_run_id = (
            system_queryset(attempt_model, using=alias, lock=None)
            .values_list("step_run__run_id", flat=True)
            .get(pk=source_attempt.pk)
        )
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
        if capability.requires_uncertainty_ack and not acknowledge_uncertain_external:
            raise ValidationError(
                {
                    "acknowledge_uncertain_external": capability.uncertainty_reason
                    or "This external request may already have run; explicit acknowledgement is required."
                }
            )
        if acknowledge_uncertain_external and not capability.requires_uncertainty_ack:
            raise ValidationError(
                {"acknowledge_uncertain_external": ("This recovery does not require uncertainty acknowledgement.")}
            )
        with system_context(reason="workflows.runs.start_recovery"), transaction.atomic(using=alias):
            source_run = (
                system_queryset(self.model, using=alias, lock=("self",))
                .select_related("workflow")
                .get(pk=source_run_id)
            )
            writable_workflows = read_scoped_queryset(type(source_run.workflow), actor, action="write")
            if writable_workflows is None or not writable_workflows.filter(pk=source_run.workflow_id).exists():
                raise PermissionDenied("Recovery workflow access was denied.")
            source_subject = source_run.subject
            if source_subject is not None:
                readable_subjects = read_scoped_queryset(type(source_subject), actor, action="read")
                if readable_subjects is None or not readable_subjects.filter(pk=source_subject.pk).exists():
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
            locked_attempt = system_queryset(attempt_model, using=alias, lock=("self",)).get(pk=source_attempt.pk)
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
                        locked_attempt.result_kind
                        in {
                            str(AttemptResultKind.ERROR),
                            str(AttemptResultKind.NO_RESULT),
                            str(AttemptResultKind.PREPARATION_ERROR),
                            str(AttemptResultKind.TRANSIENT_ERROR),
                        }
                        and locked_attempt.applied_at is not None
                    )
                    or (locked_attempt.result_recorded_at is None and locked_attempt.lease_revoked_at is not None)
                )
            ):
                raise ValidationError({"attempt": "Recovery requires an applied retained failure."})
            map_base_attempt = None
            map_controller = None
            continuation_attempt = None
            recovery_entry_step = source_step_run.step
            recovery_skipped_steps: tuple[Any, ...] = ()

            def recovery_lineage_runs() -> tuple[Any, ...]:
                """Return this source run through its exact recovery ancestry."""

                lineage_rows: list[Any] = []
                lineage = source_run
                seen_lineage: set[int] = set()
                while True:
                    if lineage.pk in seen_lineage:
                        raise ValidationError({"run": "Workflow recovery lineage is cyclic."})
                    seen_lineage.add(lineage.pk)
                    lineage_rows.append(lineage)
                    if lineage.origin != RunOrigin.RECOVERY:
                        return tuple(lineage_rows)
                    source = lineage.recovery_source_attempt
                    if source is None or source.step_run.run.workflow_id != source_run.workflow_id:
                        raise ValidationError({"run": "Workflow recovery lineage is incomplete or stale."})
                    lineage = source.step_run.run

            def retained_skipped_controllers(step_ids: set[int]) -> tuple[Any, ...]:
                """Select the nearest exact non-Map skipped rows in this recovery lineage."""

                if not step_ids:
                    return ()
                lineage_ids = [row.pk for row in recovery_lineage_runs()]
                rank = {run_id: index for index, run_id in enumerate(lineage_ids)}
                rows = list(
                    system_queryset(step_run_model, using=alias, lock=None)
                    .select_related("step", "run")
                    .filter(
                        run_id__in=lineage_ids,
                        step_id__in=step_ids,
                        map_index=-1,
                    )
                    .order_by("pk")
                )
                rows.sort(key=lambda row: (rank[row.run_id], row.pk))
                nearest: dict[int, Any] = {}
                for row in rows:
                    nearest.setdefault(row.step_id, row)
                return tuple(
                    nearest[step_id] for step_id in sorted(nearest) if nearest[step_id].status == StepRunStatus.SKIPPED
                )

            def downstream_merge_sibling_ids(entry_step: Any) -> set[int]:
                """Find external predecessors of merges reachable from this recovery entry."""

                edges = list(source_run.workflow.edges.select_related("source", "target").order_by("pk"))
                outgoing: dict[int, set[int]] = {}
                incoming: dict[int, set[int]] = {}
                steps: dict[int, Any] = {entry_step.pk: entry_step}
                for edge in edges:
                    outgoing.setdefault(edge.source_id, set()).add(edge.target_id)
                    incoming.setdefault(edge.target_id, set()).add(edge.source_id)
                    steps[edge.source_id] = edge.source
                    steps[edge.target_id] = edge.target
                reachable = {entry_step.pk}
                frontier = [entry_step.pk]
                while frontier:
                    source_id = frontier.pop()
                    for target_id in outgoing.get(source_id, ()):
                        if target_id not in reachable:
                            reachable.add(target_id)
                            frontier.append(target_id)
                return {
                    source_id
                    for target_id in reachable
                    if str(steps[target_id].join_rule) == "none_failed_min_one_success"
                    for source_id in incoming.get(target_id, ())
                    if source_id not in reachable
                }

            def accepted_recovery_evidence(candidate: Any) -> bool:
                """Validate one exact applied success for immutable recovery evidence."""

                row = None if candidate is None else candidate.step_run
                if (
                    row is None
                    or row.status != StepRunStatus.SUCCEEDED
                    or row.current_attempt_id != candidate.pk
                    or candidate.effect_key != row.effect_key
                    or candidate.effect_generation != row.effect_generation
                    or candidate.result_kind not in {str(AttemptResultKind.DONE), str(AttemptResultKind.SUSPEND)}
                    or candidate.applied_at is None
                    or candidate.lease_revoked_at is not None
                ):
                    return False
                if candidate.result_kind == str(AttemptResultKind.DONE):
                    return True
                if not row.output_present:
                    return False
                decisions = list(
                    system_queryset(
                        self.model._meta.apps.get_model("workflows", "Decision"),
                        using=alias,
                        lock=None,
                    )
                    .filter(suspension_attempt=candidate)
                    .order_by("priority", "pk")
                )
                projected = retained_gate_output(candidate, decisions)
                return projected is not None and projected["outcome"] == row.outcome and projected == row.output

            def retained_success(row: Any) -> bool:
                """Validate one exact applied success used only to follow its frozen route."""

                attempt = None if row is None else row.current_attempt
                return (
                    row is not None
                    and row.map_index == -1
                    and attempt is not None
                    and attempt.step_run_id == row.pk
                    and accepted_recovery_evidence(attempt)
                )

            if source_step_run.map_index >= 0:
                (
                    _map_source,
                    _map_source_step,
                    _map_expansion,
                    map_controller,
                    map_base_attempt,
                    _map_state,
                ) = attempt_model.objects._map_recovery_source(
                    locked_attempt.pk,
                    alias=alias,
                    lock=("self",),
                )
                if prior_recovery is not None:
                    readable_prior = read_scoped_queryset(self.model, actor, action="read")
                    prior = (
                        None
                        if readable_prior is None
                        else readable_prior.using(alias).filter(pk=prior_recovery.pk).first()
                    )
                    if (
                        prior is None
                        or prior.origin != RunOrigin.RECOVERY
                        or prior.status not in RunStatus.TERMINAL
                        or prior.workflow_id != source_run.workflow_id
                        or not prior.same_execution_lineage(source_run)
                        or prior.recovery_source_attempt_id is None
                        or prior.recovery_source_attempt.map_expansion_id != locked_attempt.map_expansion_id
                    ):
                        raise ValidationError(
                            {
                                "prior_recovery": (
                                    "Prior Map recovery must be a readable terminal descendant of this expansion."
                                )
                            }
                        )
                    prior_controller = (
                        system_queryset(
                            step_run_model,
                            using=alias,
                            lock=None,
                        )
                        .filter(
                            run_id=prior.pk,
                            step_id=map_controller.step_id,
                            map_index=-1,
                            status=StepRunStatus.SUCCEEDED,
                        )
                        .first()
                    )
                    if prior_controller is None or prior_controller.current_attempt_id is None:
                        raise ValidationError(
                            {"prior_recovery": "Prior Map recovery has no retained controller aggregate."}
                        )
                    (
                        _map_source,
                        _map_source_step,
                        _map_expansion,
                        map_controller,
                        map_base_attempt,
                        _map_state,
                    ) = attempt_model.objects._map_recovery_source(
                        locked_attempt.pk,
                        alias=alias,
                        lock=("self",),
                        aggregate_attempt_id=prior_controller.current_attempt_id,
                    )
                elif source_run.origin == RunOrigin.RECOVERY:
                    evidence = (
                        source_run.recovery_evidence.filter(
                            step_id=map_controller.step_id,
                            map_index=-1,
                        )
                        .select_related("source_attempt")
                        .first()
                    )
                    if evidence is None:
                        raise ValidationError({"attempt": "Failed Map recovery has no admitted controller basis."})
                    (
                        _map_source,
                        _map_source_step,
                        _map_expansion,
                        map_controller,
                        map_base_attempt,
                        _map_state,
                    ) = attempt_model.objects._map_recovery_source(
                        locked_attempt.pk,
                        alias=alias,
                        lock=("self",),
                        aggregate_attempt_id=evidence.source_attempt_id,
                    )
            elif prior_recovery is not None:
                readable_prior = read_scoped_queryset(self.model, actor, action="read")
                prior = (
                    None if readable_prior is None else readable_prior.using(alias).filter(pk=prior_recovery.pk).first()
                )
                prior_rows = (
                    []
                    if prior is None
                    else list(
                        system_queryset(step_run_model, using=alias, lock=("self",))
                        .select_related("step", "current_attempt")
                        .filter(run_id=prior.pk)
                        .order_by("pk")
                    )
                )
                prior_by_step = {row.step_id: row for row in prior_rows if row.map_index == -1}
                recovered = prior_by_step.get(source_step_run.step_id)
                continuation_row = recovered
                target = None
                route_valid = True
                seen_route: set[int] = set()
                while continuation_row is not None:
                    if continuation_row.step_id in seen_route or not retained_success(continuation_row):
                        route_valid = False
                        break
                    seen_route.add(continuation_row.step_id)
                    routed_targets = list(
                        continuation_row.step.outgoing_edges.select_related("target")
                        .filter(models.Q(condition="") | models.Q(condition=continuation_row.outcome))
                        .order_by("target_id")
                    )
                    if len(routed_targets) != 1:
                        route_valid = False
                        break
                    target = routed_targets[0].target
                    next_row = prior_by_step.get(target.pk)
                    if next_row is None:
                        break
                    continuation_row = next_row
                continuation_attempt = (
                    continuation_row.current_attempt if route_valid and continuation_row is not None else None
                )
                sibling_ids = (
                    set()
                    if target is None
                    else set(
                        target.incoming_edges.exclude(source_id=continuation_row.step_id).values_list(
                            "source_id", flat=True
                        )
                    )
                )
                skipped = retained_skipped_controllers(sibling_ids)
                unresolved_siblings = sibling_ids.difference(row.step_id for row in skipped)
                if (
                    prior is None
                    or prior.origin != RunOrigin.RECOVERY
                    or prior.status != RunStatus.FAILED
                    or prior.error != workflow_result_terminal_match_error(0)
                    or prior.workflow_id != source_run.workflow_id
                    or prior.recovery_source_attempt_id != locked_attempt.pk
                    or prior.recovery_mode != str(RecoveryMode.FRESH)
                    or prior.recovery_mode != str(capability.mode)
                    or not prior.same_execution_lineage(source_run)
                    or not route_valid
                    or any(row.status not in StepRunStatus.TERMINAL for row in prior_rows)
                    or any(row.status in {StepRunStatus.FAILED, StepRunStatus.CANCELED} for row in prior_rows)
                    or recovered is None
                    or continuation_attempt is None
                    or continuation_attempt.step_run_id != continuation_row.pk
                    or continuation_attempt.result_kind != str(AttemptResultKind.DONE)
                    or continuation_attempt.applied_at is None
                    or continuation_attempt.lease_revoked_at is not None
                    or target is None
                    or target.join_rule != "none_failed_min_one_success"
                    or not skipped
                    or unresolved_siblings
                    or any(row.step_id == target.pk for row in prior_rows)
                ):
                    raise ValidationError(
                        {
                            "prior_recovery": (
                                "Prior recovery does not retain one completed slot blocked only by "
                                "its exact skipped merge siblings."
                            )
                        }
                    )
                recovery_entry_step = target
                recovery_skipped_steps = skipped
            elif source_step_run.map_index < 0:
                recovery_skipped_steps = retained_skipped_controllers(
                    downstream_merge_sibling_ids(source_step_run.step)
                )
            try:
                actor_ref = str(to_subject_ref(actor))
            except NoActorResolvedError as error:
                raise PermissionDenied("Recovery requires an effective actor.") from error
            dedup_key = f"recovery:{source_run.pk}:{locked_attempt.pk}:{request_key}"
            existing = self.filter(dedup_key=dedup_key).first()
            if existing is not None:
                existing_map_basis_id = None
                if map_controller is not None:
                    existing_map_basis_id = (
                        existing.recovery_evidence.filter(
                            step_id=map_controller.step_id,
                            map_index=-1,
                        )
                        .values_list("source_attempt_id", flat=True)
                        .first()
                    )
                existing_continuation_id = None
                if continuation_attempt is not None:
                    existing_continuation_id = (
                        existing.recovery_evidence.filter(
                            step_id=continuation_attempt.step_run.step_id,
                            map_index=-1,
                        )
                        .values_list("source_attempt_id", flat=True)
                        .first()
                    )
                existing_skipped_ids = set(
                    existing.step_runs.filter(status=StepRunStatus.SKIPPED).values_list("step_id", flat=True)
                )
                if (
                    existing.recovery_source_attempt_id != locked_attempt.pk
                    or existing.recovery_request_actor_ref != actor_ref
                    or existing.recovery_mode != str(capability.mode)
                    or existing.recovery_uncertainty_ack != acknowledge_uncertain_external
                    or existing_map_basis_id != (None if map_base_attempt is None else map_base_attempt.pk)
                    or existing_continuation_id != (None if continuation_attempt is None else continuation_attempt.pk)
                    or not {row.step_id for row in recovery_skipped_steps}.issubset(existing_skipped_ids)
                ):
                    raise ValidationError({"request_key": "Recovery request facts do not match."})
                return existing

            recovery_graph = WorkflowGraph.from_workflow(source_run.workflow)
            accepted_step_ids = {
                source.node_identity.existing_id
                for source in recovery_graph.input_sources(GraphIdentity(existing_id=source_step_run.step_id))
                if source.node_identity is not None and source.node_identity.existing_id is not None
            }
            accepted_step_runs = [row for row in locked_step_runs if row.step_id in accepted_step_ids]
            direct_candidate_ids = list(
                system_queryset(attempt_model, using=alias, lock=None)
                .filter(
                    step_run__in=accepted_step_runs,
                    step_run__status=StepRunStatus.SUCCEEDED,
                    step_run__current_attempt=models.F("pk"),
                    effect_key=models.F("step_run__effect_key"),
                    effect_generation=models.F("step_run__effect_generation"),
                    result_kind__in=(AttemptResultKind.DONE, AttemptResultKind.SUSPEND),
                    applied_at__isnull=False,
                    lease_revoked_at__isnull=True,
                )
                .exclude(step_run=source_step_run)
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            if continuation_attempt is not None:
                direct_candidate_ids.append(continuation_attempt.pk)

            # Recovery evidence is an immutable, admission-time snapshot.  Carry
            # the nearest retained basis forward so repeated recoveries do not
            # have to rediscover predecessor output dynamically during execution.
            # Older recovery rows also let a new admission repair historical runs
            # created before evidence propagation, without rewriting those runs.
            evidence_model = self.model._meta.apps.get_model("workflows", "WorkflowRecoveryEvidence")
            lineage_run_ids = [row.pk for row in recovery_lineage_runs() if row.origin == RunOrigin.RECOVERY]
            lineage_root_id = source_run.execution_lineage_root_id()

            inherited_rows = list(
                system_queryset(evidence_model, using=alias, lock=None)
                .select_related("source_attempt__step_run__step", "source_attempt__step_run__run")
                .filter(run_id__in=lineage_run_ids, step_id__in=accepted_step_ids)
                .order_by("pk")
            )
            lineage_rank = {run_id: rank for rank, run_id in enumerate(lineage_run_ids)}
            inherited_rows.sort(key=lambda row: (lineage_rank[row.run_id], row.pk))
            candidate_ids = {
                *direct_candidate_ids,
                *(row.source_attempt_id for row in inherited_rows),
            }
            candidates = {
                candidate.pk: candidate
                for candidate in (
                    system_queryset(attempt_model, using=alias, lock=("self",))
                    .select_related("step_run__step", "step_run__run")
                    .filter(
                        pk__in=candidate_ids,
                        step_run__status=StepRunStatus.SUCCEEDED,
                        step_run__current_attempt=models.F("pk"),
                        effect_key=models.F("step_run__effect_key"),
                        effect_generation=models.F("step_run__effect_generation"),
                        result_kind__in=(AttemptResultKind.DONE, AttemptResultKind.SUSPEND),
                        applied_at__isnull=False,
                        lease_revoked_at__isnull=True,
                    )
                    .order_by("pk")
                )
            }

            accepted_by_slot: dict[tuple[int, int], Any] = {}
            for evidence in inherited_rows:
                candidate = candidates.get(evidence.source_attempt_id)
                if (
                    candidate is None
                    or candidate.step_run.step_id != evidence.step_id
                    or candidate.step_run.map_index != evidence.map_index
                    or candidate.step_run.run.execution_lineage_root_id() != lineage_root_id
                    or not accepted_recovery_evidence(candidate)
                ):
                    raise ValidationError({"attempt": "Recovery lineage contains stale predecessor evidence."})
                accepted_by_slot.setdefault(
                    (evidence.step_id, evidence.map_index),
                    candidate,
                )
            for candidate_id in direct_candidate_ids:
                candidate = candidates.get(candidate_id)
                if candidate is None or not accepted_recovery_evidence(candidate):
                    continue
                accepted_by_slot[(candidate.step_run.step_id, candidate.step_run.map_index)] = candidate
            if map_base_attempt is not None and map_controller is not None:
                accepted_by_slot[(map_controller.step_id, -1)] = map_base_attempt
            accepted = tuple(accepted_by_slot[slot] for slot in sorted(accepted_by_slot))
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
                recovery_uncertainty_ack=acknowledge_uncertain_external,
                recovery_evidence=accepted,
                recovery_step=recovery_entry_step,
                recovery_map_index=source_step_run.map_index,
                recovery_skipped_steps=recovery_skipped_steps,
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
        scope: WorkflowScope = cast(WorkflowScope, WorkflowScope.WHOLE),
        selected_step: Any = None,
        fixtures: tuple[FixtureSpec, ...] = (),
        previous_run: Any = None,
    ) -> WorkflowSetupPlan:
        """Project the exact graph, fixture and effect facts used by test admission."""

        if not isinstance(scope, WorkflowScope):
            raise ValidationError({"scope": "Test plans require a declared scope."})
        if (scope is WorkflowScope.NODE) != (selected_step is not None):
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
                if row.role == FixtureRole.OUTPUT
            )
            plan = graph.test_execution_plan(
                selected_identity=(
                    GraphIdentity(existing_id=locked_selected.pk) if locked_selected is not None else None
                ),
                output_slots=output_slots,
                map_item_slots=frozenset(
                    (GraphIdentity(existing_id=row.step_id), row.item_index)
                    for row in fixture_rows
                    if row.role == FixtureRole.MAP_ITEM and row.item_index is not None
                ),
            )
            freshness = (
                graph.test_freshness(
                    WorkflowGraph.from_workflow(head),
                    selected_identity=(
                        GraphIdentity(existing_id=locked_selected.pk) if locked_selected is not None else None
                    ),
                    plan=plan,
                )
                if requested.pk != head.pk
                else ()
            )
            locked_previous = None
            if previous_run is not None:
                run_access = read_scoped_queryset(self.model, actor, action="read")
                locked_previous = (
                    None
                    if run_access is None
                    else run_access.select_related("workflow", "test_step")
                    .filter(
                        pk=previous_run.pk,
                    )
                    .first()
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
                        if row.role == FixtureRole.OUTPUT
                    ),
                    map_item_slots=frozenset(
                        (GraphIdentity(existing_id=row.step_id), row.item_index)
                        for row in previous_fixture_rows
                        if row.role == FixtureRole.MAP_ITEM and row.item_index is not None
                    ),
                )
                previous_freshness = previous_graph.test_freshness(
                    graph,
                    selected_identity=previous_selected_identity,
                    plan=previous_plan,
                )
                freshness = tuple(dict.fromkeys((*freshness, *previous_freshness)))
                proposed_content_type = (
                    None if subject is None else ContentType.objects.get_for_model(subject, for_concrete_model=False)
                )
                if locked_previous.subject_content_type_id != (
                    None if proposed_content_type is None else proposed_content_type.pk
                ) or locked_previous.subject_object_id != (None if subject is None else subject.pk):
                    freshness = (*freshness, GraphFreshnessReason("subject_changed", None, "subject"))
                if locked_previous.input_present is not input.present or not json_values_equal(
                    locked_previous.input,
                    input.value if input.present else None,
                ):
                    freshness = (*freshness, GraphFreshnessReason("input_changed", None, "input"))
                selected_key = locked_selected.key if locked_selected is not None else None
                if locked_previous.origin == RunOrigin.TEST and (
                    (locked_previous.test_scope or WorkflowScope.WHOLE) != scope
                    or (locked_previous.test_step.key if locked_previous.test_step_id else None) != selected_key
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
                actual_fixture_facts.sort(key=lambda value: (value[0], value[1], -1 if value[2] is None else value[2]))
                proposed_fixture_facts.sort(
                    key=lambda value: (value[0], value[1], -1 if value[2] is None else value[2])
                )
                if len(actual_fixture_facts) != len(proposed_fixture_facts) or any(
                    left[:4] != right[:4] or not json_values_equal(left[4], right[4]) or left[5:] != right[5:]
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
                    locked_previous is not None
                    and locked_previous.origin == RunOrigin.TEST
                    and locked_previous.workflow_id == requested.pk
                    and locked_previous.test_step_id == locked_selected.pk
                    and locked_previous.test_source_step_id is not None
                ):
                    source_step_id = step_model.public_id_from_pk(locked_previous.test_source_step_id)
                else:
                    source_step = (
                        system_queryset(step_model, using=alias, lock=None)
                        .filter(
                            workflow=head,
                            key=locked_selected.key,
                        )
                        .first()
                    )
                    source_step_id = source_step.sqid if source_step is not None else None
            freshness = tuple(dict.fromkeys(freshness))
            return WorkflowSetupPlan(
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

    def test_repair_context(self, source_attempt: Any, *, actor: Any) -> WorkflowRepairContext:
        """Resolve an authorized retained attempt into current draft test identities."""

        attempt_model = self.model._meta.apps.get_model("workflows", "StepAttempt")
        readable_attempts = read_scoped_queryset(attempt_model, actor, action="read")
        row = (
            None
            if readable_attempts is None
            else readable_attempts.select_related("step_run__run__workflow", "step_run__step")
            .filter(pk=source_attempt.pk)
            .first()
        )
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
        current_step = (
            system_queryset(type(source_step), using=self.db, lock=None)
            .filter(workflow=head, key=source_step.key)
            .first()
        )
        fixture_summaries: tuple[FixtureSourceSummary, ...] = ()
        if current_step is not None:
            with system_context(reason="workflows.runs.test_repair_context"):
                graph = WorkflowGraph.from_workflow(head)
            plan = graph.test_plan(GraphIdentity(existing_id=current_step.pk))
            nodes_by_identity = graph.nodes_by_identity
            admitted_keys = {
                nodes_by_identity[identity].key
                for identity in plan.output_sources
                if identity in nodes_by_identity and identity.existing_id != current_step.pk
            }
            candidates = (
                system_queryset(attempt_model, using=self.db, lock=None)
                .select_related("step_run__step", "step_run__run", "step_run__step__workflow")
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
                attempt_model.objects._fixture_source_summary(candidate, FixtureRole.OUTPUT) for candidate in candidates
            )
        return WorkflowRepairContext(
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
        scope: WorkflowScope,
        selected_step: Any,
        fixture_rows: tuple[Any, ...],
        graph: Any = None,
    ) -> None:

        graph = graph or WorkflowGraph.from_workflow(snapshot)
        output_slots = frozenset(
            (
                GraphIdentity(existing_id=row.step_id),
                row.item_index,
            )
            for row in fixture_rows
            if row.role == FixtureRole.OUTPUT
        )
        selected_identity = GraphIdentity(existing_id=selected_step.pk) if scope == WorkflowScope.NODE else None
        plan = graph.test_execution_plan(
            selected_identity=selected_identity,
            output_slots=output_slots,
            map_item_slots=frozenset(
                (GraphIdentity(existing_id=row.step_id), row.item_index)
                for row in fixture_rows
                if row.role == FixtureRole.MAP_ITEM and row.item_index is not None
            ),
        )
        if plan.diagnostics:
            raise ValidationError({item.location.path: item.message for item in plan.diagnostics})

    def _validate_test_retry(
        self,
        run: Any,
        *,
        expected_revision: int,
        subject: Any,
        input: JsonPresence,
        requested_snapshot_id: int | None,
        scope: WorkflowScope,
        selected_step_key: str | None,
        fixture_rows: tuple[Any, ...],
    ) -> None:
        """Reject reuse of one request identity with different admission facts."""

        content_type = None if subject is None else ContentType.objects.get_for_model(subject, for_concrete_model=False)
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
            and (run.test_scope or WorkflowScope.WHOLE) == scope
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
            run.test_fixtures.select_related("step", "captured_attempt").order_by("step_id", "role", "item_index")
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
        parent_relation: ParentRelation | str = "",
        dedup_key: str | None = None,
        occurrence_id: str | None = None,
        origin: RunOrigin | None = None,
        input: JsonPresence = JsonPresence(),
        test_request_actor_ref: str = "",
        test_scope: WorkflowScope | str = "",
        test_step: Any = None,
        test_source_step_id: int | None = None,
        test_repair_source_attempt: Any = None,
        test_fixture_rows: tuple[Any, ...] = (),
        recovery_source_attempt: Any = None,
        recovery_request_actor_ref: str = "",
        recovery_mode: str = "",
        recovery_uncertainty_ack: bool = False,
        recovery_evidence: tuple[Any, ...] = (),
        recovery_step: Any = None,
        recovery_map_index: int = -1,
        recovery_skipped_steps: tuple[Any, ...] = (),
        available_at: datetime,
        using: str,
        reprocessed_from: Any = None,
        validate_new: Callable[[], None] | None = None,
    ) -> Any:
        """Create a Run and its first durable work for one explicit immutable definition."""

        if list(Draft202012Validator(version.input_schema).iter_errors(input.value if input.present else None)):
            raise ValidationError({"input": "Invocation input does not satisfy the published workflow schema."})
        version.validate_subject_declaration(subject)
        content_type = None if subject is None else ContentType.objects.get_for_model(subject, for_concrete_model=False)
        object_id = None if subject is None else subject.pk
        run_dedup_key = dedup_key or self._trigger_dedup_key(trigger, content_type, object_id)
        owner_id = self._owner_id(actor, trigger, version)
        admitted_actor_ref = ""
        if owner_id is not None:
            owner_id = self.model._meta.get_field("created_by").target_field.get_prep_value(owner_id)
            admitted_actor = get_user_model()._base_manager.using(using).get(pk=owner_id)
            admitted_actor_ref = str(to_subject_ref(admitted_actor))
        resolved_origin = origin or (
            RunOrigin.TRIGGER
            if trigger is not None
            else RunOrigin.ERROR_WORKFLOW
            if parent_step_run is not None
            else RunOrigin.MANUAL
        )
        attrs = {
            "workflow": version,
            "origin": resolved_origin,
            "trigger": trigger,
            "dedup_key": run_dedup_key,
            "occurrence_id": occurrence_id,
            "parent_step_run": parent_step_run,
            "parent_relation": parent_relation,
            "reprocessed_from": reprocessed_from,
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
            "recovery_uncertainty_ack": recovery_uncertainty_ack,
            "admitted_actor_ref": admitted_actor_ref,
            "created_by_id": owner_id,
            "updated_by_id": owner_id,
        }

        def retain_exact(existing: Any) -> Any:
            expected = {
                "workflow": version.pk,
                "subject_content_type": None if content_type is None else content_type.pk,
                "subject_object_id": object_id,
                "actor": admitted_actor_ref,
                "origin": resolved_origin,
                "trigger": None if trigger is None else trigger.pk,
                "parent_step_run": None if parent_step_run is None else parent_step_run.pk,
                "parent_relation": parent_relation,
                "dedup_key": run_dedup_key,
                "occurrence_id": occurrence_id,
                "input_present": input.present,
                "recovery_uncertainty_ack": recovery_uncertainty_ack,
            }
            actual = {
                "workflow": existing.workflow_id,
                "subject_content_type": existing.subject_content_type_id,
                "subject_object_id": existing.subject_object_id,
                "actor": (
                    str(existing.admission_actor_subject()) if existing.admission_actor_subject() is not None else ""
                ),
                "origin": existing.origin,
                "trigger": existing.trigger_id,
                "parent_step_run": existing.parent_step_run_id,
                "parent_relation": existing.parent_relation,
                "dedup_key": existing.dedup_key,
                "occurrence_id": existing.occurrence_id,
                "input_present": existing.input_present,
                "recovery_uncertainty_ack": existing.recovery_uncertainty_ack,
            }
            changed = {
                field: "Retained workflow invocation has different immutable facts."
                for field, value in expected.items()
                if actual[field] != value
            }
            if not json_values_equal(existing.input, input.value if input.present else None):
                changed["input"] = "Retained workflow invocation has different frozen input."
            if changed:
                raise ValidationError(changed)
            return existing

        retained = None
        if parent_step_run is not None:
            retained = (
                system_queryset(self.model, using=using, lock=("self",))
                .filter(
                    parent_step_run=parent_step_run,
                )
                .first()
            )
        if retained is None and run_dedup_key:
            retained = (
                system_queryset(self.model, using=using, lock=("self",))
                .filter(
                    dedup_key=run_dedup_key,
                )
                .first()
            )
        if retained is not None:
            return retain_exact(retained)
        if parent_step_run is not None:
            parent_run = system_queryset(self.model, using=using, lock=("self",)).get(pk=parent_step_run.run_id)
            if parent_run.status == RunStatus.CANCELED or (
                parent_relation == ParentRelation.OWNED_CALL and parent_run.status in RunStatus.TERMINAL
            ):
                raise ValidationError({"parent_step_run": "A terminal parent cannot admit another owned child."})
        if validate_new is not None:
            validate_new()
        if parent_step_run is not None:
            run, created = self.get_or_create(parent_step_run=parent_step_run, defaults=attrs)
        elif run_dedup_key:
            run, created = self.get_or_create(dedup_key=run_dedup_key, defaults=attrs)
        else:
            run, created = self.create(**attrs), True
        if not created:
            return retain_exact(run)
        fixtures: tuple[Any, ...] = ()
        if run.origin == RunOrigin.TEST:
            fixture_model = self.model._meta.apps.get_model("workflows", "WorkflowTestFixture")
            connection = connections[using]
            fixture_token = _test_fixture_write_run.set(
                (using, id(connection), id(connection.atomic_blocks[0]), run.pk)
            )
            batch_token = _test_fixture_batch_rows.set(frozenset(id(row) for row in test_fixture_rows))
            try:
                fixtures = fixture_model.objects._create_batch(run, test_fixture_rows)
                apply_token = _test_fixture_apply_ids.set(
                    frozenset(fixture.pk for fixture in fixtures if fixture.role == FixtureRole.OUTPUT)
                )
                attempt_model = self.model._meta.apps.get_model("workflows", "StepAttempt")
                try:
                    for fixture in fixtures:
                        if fixture.role == FixtureRole.OUTPUT and run.test_scope == WorkflowScope.NODE:
                            attempt_model.objects.record_test_fixture(fixture, at=available_at)
                finally:
                    _test_fixture_apply_ids.reset(apply_token)
            finally:
                _test_fixture_batch_rows.reset(batch_token)
                _test_fixture_write_run.reset(fixture_token)
        if run.origin == RunOrigin.RECOVERY:
            evidence_model = self.model._meta.apps.get_model("workflows", "WorkflowRecoveryEvidence")
            connection = connections[using]
            recovery_token = _recovery_write_run.set((using, id(connection), id(connection.atomic_blocks[0]), run.pk))
            recovery_batch_token = _recovery_evidence_batch.set(frozenset(attempt.pk for attempt in recovery_evidence))
            try:
                evidence_model.objects._create_for_run(run, recovery_evidence, using=using)
            finally:
                _recovery_evidence_batch.reset(recovery_batch_token)
                _recovery_write_run.reset(recovery_token)
        if test_scope == WorkflowScope.NODE:
            entries = [test_step] if test_step is not None else []
        elif run.origin == RunOrigin.RECOVERY:
            entries = [recovery_step] if recovery_step is not None else []
        else:
            entries = list(version.steps.filter(is_entry=True).order_by("pk"))
        if len(entries) != 1:
            raise ValidationError({"workflow": "Workflow version must have exactly one initial step."})
        step_run_model = self.model._meta.apps.get_model("workflows", "StepRun")
        for skipped in recovery_skipped_steps:
            if skipped.run.workflow_id != run.workflow_id or skipped.status != StepRunStatus.SKIPPED:
                raise ValidationError({"recovery": "Skipped merge evidence changed during admission."})
            step_run_model.objects.create(
                run=run,
                step=skipped.step,
                map_index=-1,
                status=StepRunStatus.SKIPPED,
                input={},
            )
        entry_index = recovery_map_index if run.origin == RunOrigin.RECOVERY else -1
        if test_scope == WorkflowScope.NODE:
            map_fixture = next(
                (fixture for fixture in fixtures if fixture.role == FixtureRole.MAP_ITEM),
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

        transaction.on_commit(enqueue_dispatch_publisher, using=using)
        return run

    def _resolve_test_fixture_rows(
        self,
        snapshot: Any,
        *,
        scope: WorkflowScope,
        selected_step: Any,
        specs: tuple[FixtureSpec, ...],
        actor: Any,
        graph: Any = None,
        require_complete: bool = True,
    ) -> tuple[Any, ...]:
        """Resolve a complete fixture request before the test run writes begin."""

        fixture_model = self.model._meta.apps.get_model("workflows", "WorkflowTestFixture")
        attempt_model = self.model._meta.apps.get_model("workflows", "StepAttempt")
        graph = graph or WorkflowGraph.from_workflow(snapshot)
        step_by_key = {step.key: step for step in snapshot.steps.order_by("pk")}
        allowed_outputs = set(step_by_key)
        allows_map_item = False
        if scope == WorkflowScope.NODE:
            plan = graph.test_execution_plan(selected_identity=GraphIdentity(existing_id=selected_step.pk))
            allowed_outputs = {node.key for node in graph.nodes if node.identity in plan.output_sources}
            allows_map_item = plan.map_item
        rows: list[Any] = []
        seen: set[tuple[str, str, int | None]] = set()
        if not isinstance(specs, tuple):
            raise ValidationError({"fixtures": "Test fixtures must be an immutable tuple."})
        map_fixture_count = 0
        for raw in specs:
            try:
                spec = validate_fixture_spec(raw)
            except ValueError as error:
                raise ValidationError({"fixtures": str(error)}) from error
            step = step_by_key.get(spec.step_key)
            slot = (spec.step_key, str(spec.role), spec.item_index)
            if step is None or slot in seen:
                raise ValidationError({"fixtures": "Fixture slots must be unique copied workflow steps."})
            seen.add(slot)
            if spec.role == FixtureRole.OUTPUT and spec.step_key not in allowed_outputs:
                raise ValidationError({"fixtures": "Output fixture is outside this test scope's sources."})
            if spec.role == FixtureRole.MAP_ITEM and (
                not allows_map_item or selected_step is None or step.pk != selected_step.pk
            ):
                raise ValidationError({"fixtures": "Map item fixture is outside the selected body scope."})
            if spec.role == FixtureRole.MAP_ITEM:
                map_fixture_count += 1
                if map_fixture_count > 1:
                    raise ValidationError({"fixtures": "A node test selects exactly one Map item slot."})
            if spec.role == FixtureRole.OUTPUT:
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
                if spec.role == FixtureRole.OUTPUT:
                    value = JsonPresence(captured.output_present, captured.output)
                    outcome = captured.outcome
                else:
                    value = JsonPresence(True, captured.map_item)
            if spec.role == FixtureRole.OUTPUT and outcome:
                declared_outcomes = {declared.key for declared in step.resolve_impl("step_class").outcomes}
                if declared_outcomes and outcome not in declared_outcomes:
                    raise ValidationError({"fixtures": "Output fixture outcome is not declared by this operation."})
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
        if require_complete and scope == WorkflowScope.NODE and allows_map_item and map_fixture_count != 1:
            raise ValidationError({"fixtures": "A selected Map body requires exactly one Map item fixture."})
        return tuple(rows)

    @staticmethod
    def _trigger_dedup_key(trigger: Any, content_type: Any, object_id: Any) -> str | None:
        if trigger is None:
            return None
        subject = "none" if content_type is None or object_id is None else f"{content_type.pk}:{object_id}"
        return f"trigger:{trigger.pk}:subject:{subject}"

    @staticmethod
    def _owner_id(actor: Any, trigger: Any, workflow: Any) -> Any | None:
        if trigger is not None and trigger.execution_actor_id is not None:
            return trigger.execution_actor_id
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


class WorkflowRunSystemManager(WorkflowRunManager):
    """Expose guarded unscoped rows to Django and field-backed REBAC traversal."""

    def get_queryset(self) -> WorkflowRunQuerySet:
        return super().get_queryset().system_context(reason="workflows.run.base_manager")


class _WorkflowChildCreateQuerySetMixin:
    """Let workflow-child models preflight their proposed parent relation."""

    model: type[Any]
    db: str

    def create(self, **kwargs: Any) -> Any:
        """Construct first so the model owner can authorize its related create."""

        self._for_write = True
        instance = DefinitionQuerySet.bind_instance(self.model(**kwargs), self)
        instance.save(force_insert=True, using=self.db)
        return instance


class StepQuerySet(_WorkflowChildCreateQuerySetMixin, DefinitionQuerySet):
    """Guard collection writes to workflow steps."""


class StepManager(AngeeManager.from_queryset(StepQuerySet)):  # type: ignore[misc]
    """Manager for guarded workflow-step writes."""


class EdgeQuerySet(_WorkflowChildCreateQuerySetMixin, DefinitionQuerySet):
    """Guard collection writes to workflow edges."""


class EdgeManager(AngeeManager.from_queryset(EdgeQuerySet)):  # type: ignore[misc]
    """Manager for guarded workflow-edge writes."""


class TriggerQuerySet(_WorkflowChildCreateQuerySetMixin, AngeeQuerySet[Any]):
    """Collection writes that preserve trigger activation and rule invariants."""

    def update(self, **kwargs: Any) -> int:
        protected = {
            "workflow",
            "workflow_id",
            "kind",
            "enabled",
            "config",
            "execution_actor",
            "execution_actor_id",
            "event_model_label",
            "next_fire_at",
            "last_fire_at",
            "hourly_window_started_at",
            "hourly_fire_count",
        }
        protected.update(_trigger_extension_protected_fields(self.model))
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
            "execution_actor",
            "execution_actor_id",
            "event_model_label",
            "next_fire_at",
            "last_fire_at",
            "hourly_window_started_at",
            "hourly_fire_count",
        }
        protected.update(_trigger_extension_protected_fields(self.model))
        if protected & field_names:
            raise TypeError("Trigger rules do not support bulk_update(); save instances instead.")
        return super().bulk_update(objs, field_names, *args, **kwargs)


def _trigger_extension_protected_fields(model: type[Any]) -> set[str]:
    """Collect same-row Trigger rule fields declared by composed donor classes."""

    return {field for owner in model.__mro__ for field in owner.__dict__.get("trigger_protected_fields", ())}


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
        discovered = (
            system_queryset(self.model, using=alias, lock=None).filter(pk=trigger_id).values("workflow_id").first()
        )
        if discovered is None:
            raise self.model.DoesNotExist
        if discovered["workflow_id"] != expected_workflow_id:
            raise ValidationError({"workflow": "The trigger lineage changed before activation."})
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        with system_context(reason="workflows.triggers.activation"), transaction.atomic(using=alias):
            head = system_queryset(workflow_model, using=alias, lock=("self",)).get(pk=discovered["workflow_id"])
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
            trigger._save_validated(update_fields={"enabled", "event_model_label", "next_fire_at", "updated_at"})
            return trigger

    def start_event(
        self,
        trigger_id: int,
        *,
        subject: models.Model,
        occurrence_id: str | None,
        timestamp: datetime,
        actor: Any = None,
        source: str = EventSource.CHANGE_PUBLISHED,
    ) -> Any | None:
        """Atomically admit one matching event occurrence and its pinned run."""

        alias = self.db
        discovered = (
            system_queryset(self.model, using=alias, lock=None).filter(pk=trigger_id).values("workflow_id").first()
        )
        if discovered is None:
            return None
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        run_model = self.model._meta.apps.get_model("workflows", "WorkflowRun")
        with system_context(reason="workflows.event_triggers.start"), transaction.atomic(using=alias):
            head = system_queryset(workflow_model, using=alias, lock=("self",)).get(pk=discovered["workflow_id"])
            trigger = system_queryset(self.model, using=alias, lock=("self",)).filter(pk=trigger_id).first()
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
            if declaration.source != source:
                return None
            if subject._meta.label_lower != declaration.model:
                return None
            if not trigger.event_subject_matches(subject, source=source):
                return None
            if not trigger.condition_matches(type(subject), subject):
                return None
            content_type = ContentType.objects.db_manager(alias).get_for_model(subject, for_concrete_model=False)
            occurrence_max_length = cast(int, run_model._meta.get_field("occurrence_id").max_length)
            retained_occurrence = (
                occurrence_id
                if isinstance(occurrence_id, str) and 0 < len(occurrence_id) <= occurrence_max_length
                else None
            )
            if declaration.admission_policy == EventAdmissionPolicy.EACH_CHANGE:
                if retained_occurrence is None:
                    return None
                dedup_key = f"event:{trigger.pk}:occurrence:{retained_occurrence}"
                if len(dedup_key) > cast(int, run_model._meta.get_field("dedup_key").max_length):
                    return None
            else:
                retained_occurrence = None
                dedup_key = f"trigger:{trigger.pk}:subject:{content_type.pk}:{subject.pk}"
            existing = system_queryset(run_model, using=alias, lock=None).filter(dedup_key=dedup_key).first()
            if existing is not None:
                existing_head_id = existing.workflow.published_from_id or existing.workflow_id
                if (
                    existing_head_id != head.pk
                    or existing.trigger_id != trigger.pk
                    or existing.origin != RunOrigin.TRIGGER
                    or existing.subject_content_type_id != content_type.pk
                    or existing.subject_object_id != subject.pk
                    or existing.occurrence_id != retained_occurrence
                    or existing.dedup_key != dedup_key
                ):
                    raise ValidationError({"occurrence_id": "Event occurrence identity conflicts with retained work."})
                return existing
            trigger.validate_event_admission(subject, source=source, dedup_key=dedup_key)
            if not trigger.rate_limit_allows(timestamp=timestamp):
                return None
            input_snapshot = validate_json_presence(
                trigger.event_input_snapshot(subject, actor, source),
                label="trigger event input snapshot",
            )
            run = run_model.objects._start_locked(
                head,
                subject,
                actor,
                trigger=trigger,
                dedup_key=dedup_key,
                occurrence_id=retained_occurrence,
                input=input_snapshot,
                available_at=timestamp,
                using=alias,
            )
            self._record_fire_locked(trigger, timestamp=timestamp)
            return run

    def fire_event(self, trigger: Any, *, subject: models.Model, actor: Any, request_key: str) -> Any:
        """Manually admit one existing subject through its native event trigger.

        The public caller authorizes the exact trigger and subject here; the
        existing ``start_event`` owner still locks and validates the trigger,
        captures donor input, pins publication, and owns occurrence dedup.
        """

        trigger.with_actor(actor)._require_record_access("write")
        subject.with_actor(actor)._require_record_access("read")
        request_key = request_key.strip() if isinstance(request_key, str) else ""
        if not request_key:
            raise ValidationError({"request_key": "Firing an event trigger requires a non-empty request key."})
        declaration = trigger.validated_config(require_publisher=True)
        if not isinstance(declaration, EventTriggerConfig):
            raise ValidationError({"trigger": "Only an enabled event trigger can process an existing record."})
        if trigger.kind != TriggerKind.EVENT or not trigger.enabled:
            raise ValidationError({"trigger": "Only an enabled event trigger can process an existing record."})
        if subject._meta.label_lower != declaration.model:
            raise ValidationError({"subject": "The record does not match this event trigger."})
        identity = ":".join(
            (
                str(trigger.pk),
                str(getattr(actor, "pk", "")),
                subject._meta.label_lower,
                str(subject.pk),
                request_key,
            )
        )
        occurrence_id = f"manual:{hashlib.sha256(identity.encode()).hexdigest()}"
        run = self.start_event(
            trigger.pk,
            subject=subject,
            occurrence_id=occurrence_id,
            timestamp=timezone.now(),
            actor=actor,
            source=declaration.source,
        )
        if run is None:
            raise ValidationError({"trigger": "The event trigger did not admit this record."})
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
        discovered = (
            system_queryset(self.model, using=alias, lock=None).filter(pk=trigger_id).values("workflow_id").first()
        )
        if discovered is None:
            return None
        workflow_model = self.model._meta.get_field("workflow").remote_field.model
        run_model = self.model._meta.apps.get_model("workflows", "WorkflowRun")
        with system_context(reason="workflows.schedule_triggers.start"), transaction.atomic(using=alias):
            head = system_queryset(workflow_model, using=alias, lock=("self",)).get(pk=discovered["workflow_id"])
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
                except ValueError, TypeError:
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


class WorkflowTestFixtureQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet[Any]):
    """Keep admitted test fixture facts append-only."""

    def immutable_error(self, operation: str) -> Exception:
        """Keep the fixture owner's immutable and retained-row errors."""

        if operation in {"delete", "_raw_delete"}:
            return TypeError("Workflow test fixtures are retained admission facts.")
        return TypeError("Workflow test fixtures are immutable admission facts.")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Workflow test fixtures can only be created by WorkflowRunManager.")


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
        if owner != expected or _recovery_evidence_batch.get() != attempt_ids or run.origin != RunOrigin.RECOVERY:
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


def _decision_resolution(decision: Any, *, index: int | None) -> DecisionResolution:
    """Project exact terminal Decision evidence into one typed binding value."""

    if (
        index is None
        or decision.verdict not in Verdict.TERMINAL
        or decision.resolved_at is None
        or not isinstance(decision.resolution, dict)
    ):
        raise ValidationError({"decision": "Terminal Decision projection requires complete retained evidence."})
    return DecisionResolution(
        decision_id=str(decision.sqid),
        action=decision.action,
        verdict=str(decision.verdict),
        resolution=decision.resolution,
        resolved_by=decision.resolved_by,
        resolved_at=decision.resolved_at,
        declaration_index=index,
    )


def decision_gate_output(decisions: Collection[Any], *, outcome: str) -> dict[str, Any]:
    """Project terminal slots in declaration order through one typed owner."""

    ordered = sorted(
        decisions,
        key=lambda row: (row.declaration_index if row.declaration_index is not None else row.priority, row.pk),
    )
    return DecisionGateOutput(
        resolutions=tuple(
            _decision_resolution(
                decision, index=(decision.declaration_index if decision.declaration_index is not None else index)
            )
            for index, decision in enumerate(ordered)
            if decision.verdict in Verdict.TERMINAL
        ),
        outcome=outcome,
    ).model_dump(mode="json")


def retained_gate_output(attempt: Any, decisions: Collection[Any]) -> dict[str, Any] | None:
    """Derive one historical gate value from its immutable suspension declaration."""

    if (
        attempt.result_kind != str(AttemptResultKind.SUSPEND)
        or not attempt.checkpoint_present
        or not isinstance(attempt.checkpoint, dict)
    ):
        return None
    settlement = attempt.decision_settlement
    if not isinstance(settlement, dict) or set(settlement) != {"decision_ids", "outcome"}:
        return None
    ids = settlement["decision_ids"]
    outcome = settlement["outcome"]
    if not isinstance(ids, list) or not ids or any(type(value) is not int for value in ids):
        return None
    selected = [decision for decision in decisions if decision.pk in ids]
    if len(selected) != len(ids) or any(decision.verdict not in Verdict.TERMINAL for decision in selected):
        return None
    return decision_gate_output(selected, outcome=outcome)


def _decision_source_leaf(
    provenance: Any, path: tuple[str | int, ...], *, recovery_source_attempt_id: int | None
) -> tuple[dict[str, Any], tuple[str | int, ...]]:
    """Pair one admitted input path with its exact engine-authored source leaf."""

    current = provenance
    remaining = path
    while isinstance(current, dict):
        kind = current.get("kind")
        if kind == "recovery_input":
            if recovery_source_attempt_id is None or current.get("attempt_id") != recovery_source_attempt_id:
                break
            current = current.get("source_provenance")
            continue
        if kind == "object" and remaining and isinstance(remaining[0], str):
            fields = current.get("fields")
            if not isinstance(fields, dict):
                break
            current = fields.get(remaining[0])
            remaining = remaining[1:]
            continue
        if kind == "array" and remaining and type(remaining[0]) is int:
            items = current.get("items")
            if not isinstance(items, list) or remaining[0] < 0 or remaining[0] >= len(items):
                break
            current = items[remaining[0]]
            remaining = remaining[1:]
            continue
        if kind in {"step_output", "recovery_evidence"}:
            return current, remaining
        break
    raise ValidationError({"resolution_path": "This value was not admitted from a retained Decision gate."})


def _admitted_source_attempt(
    leaf: dict[str, Any], *, run: Any, alias: str, attempt_model: type[Any]
) -> tuple[Any, Any]:
    """Resolve one exact current output or immutable recovery-evidence source."""

    if leaf.get("kind") == "recovery_evidence":
        evidence_model = attempt_model._meta.apps.get_model("workflows", "WorkflowRecoveryEvidence")
        evidence = (
            system_queryset(evidence_model, using=alias, lock=None)
            .filter(
                sqid=leaf.get("evidence_id"),
                run_id=run.pk,
                source_attempt_id=leaf.get("attempt_id"),
            )
            .select_related("source_attempt__step_run__run", "step")
            .first()
        )
        if evidence is None or evidence.step.key != leaf.get("step_key") or evidence.map_index != leaf.get("map_index"):
            raise ValidationError({"decision": "Exact recovery source was not admitted."})
        source_attempt = evidence.source_attempt
        source_step_run = source_attempt.step_run
    elif leaf.get("kind") == "step_output":
        step_run_model = attempt_model._meta.get_field("step_run").remote_field.model
        source_step_run = (
            system_queryset(step_run_model, using=alias, lock=None)
            .filter(
                pk=leaf.get("step_run_id"),
                step__key=leaf.get("step_key"),
            )
            .select_related("run")
            .first()
        )
        if (
            source_step_run is None
            or source_step_run.current_attempt_id != leaf.get("attempt_id")
            or source_step_run.status != StepRunStatus.SUCCEEDED
            or source_step_run.effect_generation != leaf.get("effect_generation")
        ):
            raise ValidationError({"decision": "Exact upstream result is not current."})
        source_attempt = (
            system_queryset(attempt_model, using=alias, lock=None)
            .filter(
                pk=leaf.get("attempt_id"),
                step_run_id=source_step_run.pk,
            )
            .first()
        )
    else:
        raise ValidationError({"decision": "Decision authority requires an exact retained source."})
    if (
        source_attempt is None
        or source_attempt.applied_at is None
        or source_attempt.lease_revoked_at is not None
        or (
            leaf.get("kind") == "step_output"
            and (
                source_attempt.effect_key != source_step_run.effect_key
                or source_attempt.effect_generation != source_step_run.effect_generation
            )
        )
        or not run.same_execution_lineage(source_step_run.run)
    ):
        raise ValidationError({"decision": "Decision authority is outside exact execution lineage."})
    return source_attempt, source_step_run


def _canonical_decision_target(
    model_label: str,
    public_id: str,
    *,
    actor: Any,
) -> tuple[str, str]:
    """Use the same native record target identity at creation and consumption."""

    if not model_label and not public_id:
        return "", ""
    if not model_label or not public_id:
        raise ValidationError({"target": "Decision target model and id must be supplied together."})
    try:
        model = apps.get_model(model_label)
    except (LookupError, ValueError) as error:
        raise ValidationError({"target": "Decision target model is not installed."}) from error
    queryset = read_scoped_queryset(model, actor, action="read")
    if queryset is None:
        raise PermissionDenied("Decision target is not readable by the execution actor.")
    target = instance_from_public_id(model, public_id, queryset=queryset)
    if target is None:
        raise ValidationError({"target": "Decision target was not found."})
    canonical = canonical_record_target(target)
    canonical_model = canonical.content_type.model_class()
    if canonical_model is None:
        raise ValidationError({"target": "Canonical Decision target model is not installed."})
    return canonical_model._meta.label, public_id_for(canonical_model, canonical.object_id)


def _canonical_declared_target(model_label: str, public_id: str) -> tuple[str, str]:
    """Canonicalize a declared target identity without reading the target record."""

    if not model_label and not public_id:
        return "", ""
    if not model_label or not public_id:
        raise ValidationError({"target": "Decision target model and id must be supplied together."})
    try:
        model = apps.get_model(model_label)
    except (LookupError, ValueError) as error:
        raise ValidationError({"target": "Decision target model is not installed."}) from error
    field = public_data_id_field(model)
    try:
        object_id = field.public_id_to_value(public_id) if field is not None else model._meta.pk.to_python(public_id)
    except (TypeError, ValueError, ValidationError) as error:
        raise ValidationError({"target": "Decision target id is invalid."}) from error
    if object_id in (None, ""):
        raise ValidationError({"target": "Decision target id is invalid."})
    canonical = canonical_record_model(model)
    return canonical._meta.label, public_id_for(canonical, object_id)


def _admitted_decision_input(
    *,
    input_source: DecisionInputSource,
    path: JsonPath,
    step_run: Any,
    attempt: Any,
    using: str,
) -> tuple[Any, dict[str, Any] | None, Any, tuple[str | int, ...]]:
    """Return one input path and the immutable provenance that admitted it.

    Owned calls prove the original child slot and parent call attempt without
    taking parent locks.  The caller already holds the child invocation locks,
    so immutable evidence reads preserve the engine's parent-before-child lock
    order.
    """

    if input_source == "attempt_input":
        return attempt.input, attempt.input_provenance, step_run.run, path
    if input_source != "owned_call_input":
        raise ValidationError({"input_source": "Unknown admitted input source."})

    run_model = step_run._meta.get_field("run").remote_field.model
    original_child_id = step_run.run.execution_lineage_root_id()
    original_child = (
        system_queryset(run_model, using=using, lock=None)
        .select_related(
            "workflow",
            "parent_step_run__step",
        )
        .filter(pk=original_child_id)
        .first()
    )
    parent_step = None if original_child is None else original_child.parent_step_run
    if (
        original_child is None
        or original_child.parent_relation != ParentRelation.OWNED_CALL
        or parent_step is None
        or parent_step.step_id is None
        or parent_step.step.step_class != "call_workflow"
        or not step_run.run.input_present
        or not original_child.input_present
        or not json_values_equal(step_run.run.input, original_child.input)
    ):
        raise ValidationError({"decision": "The active run is not this exact admitted owned call."})

    parent_attempt = (
        system_queryset(type(attempt), using=using, lock=None)
        .select_related("step_run__run")
        .filter(pk=parent_step.current_attempt_id, step_run_id=parent_step.pk)
        .first()
    )
    retained_wait = (
        parent_attempt is not None
        and parent_step.status == StepRunStatus.WAITING
        and parent_attempt.result_kind == str(AttemptResultKind.SUSPEND)
    )
    failed_source = (
        parent_attempt is not None
        and parent_step.status in {StepRunStatus.FAILED, StepRunStatus.CANCELED}
        and parent_attempt.result_kind
        in {
            str(AttemptResultKind.ERROR),
            str(AttemptResultKind.NO_RESULT),
            str(AttemptResultKind.PREPARATION_ERROR),
            str(AttemptResultKind.TRANSIENT_ERROR),
        }
    )
    if (
        parent_attempt is None
        or not (retained_wait or failed_source)
        or parent_attempt.result_recorded_at is None
        or parent_attempt.applied_at is None
        or parent_attempt.lease_revoked_at is not None
        or original_child.admission_actor_subject() != parent_step.run.admission_actor_subject()
    ):
        raise ValidationError({"decision": "The owned call is not the exact retained parent invocation."})
    child_target = canonical_record_target(original_child)
    retained_target = (
        parent_attempt.external_content_type_id,
        parent_attempt.external_object_id,
    )
    if retained_target != (child_target.content_type.pk, original_child.pk) and not (
        failed_source and retained_target == (None, None)
    ):
        raise ValidationError({"decision": "The retained parent invocation names another child."})

    payload = parent_attempt.input
    child_input = payload.get("input") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or "input" not in payload
        or not json_values_equal(
            child_input,
            original_child.input,
        )
    ):
        raise ValidationError({"decision": "The owned child input differs from its retained call input."})
    publication_id = parent_step.step.config.get("publication")
    if not publication_id:
        publication_id = payload.get("publication")
    if publication_id != public_id_for(type(original_child.workflow), original_child.workflow_id):
        raise ValidationError({"decision": "The owned child publication differs from its retained call."})

    child_value = json_value_at_path(attempt.input, path, field="resolution_path")
    original_value = json_value_at_path(original_child.input, path, field="resolution_path")
    parent_path = ("input", *path)
    parent_value = json_value_at_path(parent_attempt.input, parent_path, field="resolution_path")
    if not json_values_equal(child_value, original_value) or not json_values_equal(
        original_value,
        parent_value,
    ):
        raise ValidationError({"decision": "The child input path differs from its exact owned-call source."})
    return parent_attempt.input, parent_attempt.input_provenance, parent_step.run, parent_path


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
                system_queryset(self.model, using=alias, lock=("self",)).filter(run_id=run.pk).order_by("pk")
            )
            rows = [row for row in locked_rows if row.step_id == target_id and row.map_index >= 0]
            expansion = system_queryset(attempt_model, using=alias, lock=("self",)).get(pk=expansion_attempt_id)
            controller = next((row for row in locked_rows if row.pk == expansion.step_run_id), None)
            checkpoint = expansion.checkpoint if expansion.checkpoint_present else None
            map_state = checkpoint.get("map") if isinstance(checkpoint, dict) else None
            if not isinstance(map_state, dict):
                raise ValidationError({"map": "Map aggregate requires retained expansion facts."})
            items = map_state.get("items")
            declared_target_id = map_state.get("target_step_id") if isinstance(map_state, dict) else None
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
            current_attempt_ids = sorted(row.current_attempt_id for row in rows if row.current_attempt_id is not None)
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
                        and current_attempts[row.current_attempt_id].cause == str(AttemptCause.TEST_FIXTURE)
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
            run_id = (
                system_queryset(self.model, using=alias, lock=None).values_list("run_id", flat=True).get(pk=step_run_id)
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
                attempt_model.objects._write_step_run(step_run, alias=alias, operation=step_run.mark_canceled)
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
        resolution_session = decision_model.objects._require_resolution_owner(active_resolution.decision_id)
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
            attempt = system_queryset(attempt_model, using=alias, lock=("self",)).get(pk=step_run.current_attempt_id)
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
            authoritative = step_run.decision_gate.outcome(decisions)
            if authoritative is None or authoritative != outcome:
                raise ValidationError({"outcome": "Decision outcome must be derived from the full policy."})
            if attempt.decision_settlement:
                raise ValidationError({"decisions": "This suspension was already settled."})
            settled = [decision for decision in decisions if decision.verdict in Verdict.TERMINAL]
            attempt.decision_settlement = {
                "decision_ids": [decision.pk for decision in settled],
                "outcome": outcome,
            }
            attempt_model.objects._save_attempt(
                attempt, alias=alias, update_fields=["decision_settlement", "updated_at"]
            )
            for sibling in decisions:
                if sibling.verdict == Verdict.PENDING:
                    decision_model.objects._write_retained(
                        sibling,
                        lambda sibling=sibling: sibling.resolve(
                            Verdict.EXPIRED, resolution={}, resolved_by="workflows/gate_settlement"
                        ),
                    )
            projected = retained_gate_output(attempt, decisions)
            if projected is None:
                raise ValidationError({"decisions": "Retained gate settlement cannot be projected."})
            if step_run.resume_state.get("_resume_after_decisions"):
                state = dict(step_run.resume_state)
                state["_decision_outcome"] = outcome
                state["_decision_resolutions"] = projected
                step_run.resume_state = state
                attempt_model.objects._write_step_run(step_run, alias=alias, operation=lambda: step_run.wake(at=at))
            else:
                attempt_model.objects._write_step_run(
                    step_run,
                    alias=alias,
                    operation=lambda: step_run.mark_succeeded(
                        output=projected,
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
        resolution_session = decision_model.objects._require_resolution_owner(active_resolution.decision_id)
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
            attempt = system_queryset(attempt_model, using=alias, lock=("self",)).get(pk=step_run.current_attempt_id)
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


class StepAttemptQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet[Any]):
    """Reject collection mutations that would bypass retained-evidence rules."""

    def immutable_error(self, operation: str) -> Exception:
        """Keep the attempt owner's collection-update and retention errors."""

        if operation in {"delete", "_raw_delete"}:
            return TypeError("Step attempts are retained execution evidence and cannot be deleted.")
        return TypeError("Step attempts do not support collection updates.")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Step attempts do not support bulk_create().")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        raise TypeError("Step attempts do not support bulk_update().")


class StepAttemptManager(AngeeManager.from_queryset(StepAttemptQuerySet)):  # type: ignore[misc]
    """Allocate, lease, and finalize retained attempts under ancestor locks."""

    def admitted_continuation_child(
        self,
        step_run_id: int,
        *,
        lease_token: uuid.UUID,
        child_id_path: JsonPath,
        expected_starter_class: str,
    ) -> Any:
        """Resolve the exact child named by a current admitted starter output."""

        if (
            not child_id_path
            or any(type(part) not in {str, int} or part == "" for part in child_id_path)
            or not expected_starter_class
        ):
            raise ValidationError({"child": "An exact typed starter output path and class are required."})
        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run_id),
            system_context(reason="workflows.continuation.admitted_child"),
        ):
            run, step_run = self._locked_ancestry(step_run_id, alias)
            if run.is_terminal or step_run.status != StepRunStatus.STARTED or step_run.current_attempt_id is None:
                raise ValidationError({"child": "Child result requires the current active invocation."})
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=step_run.current_attempt_id)
            if (
                attempt.step_run_id != step_run.pk
                or attempt.lease_token != lease_token
                or attempt.started_at is None
                or attempt.result_recorded_at is not None
                or attempt.lease_revoked_at is not None
            ):
                raise ValidationError({"child": "Child result requires the current admitted lease."})
            leaf, remaining = _decision_source_leaf(
                attempt.input_provenance,
                child_id_path,
                recovery_source_attempt_id=run.recovery_source_attempt_id,
            )
            source_attempt, starter = _admitted_source_attempt(
                leaf,
                run=run,
                alias=alias,
                attempt_model=self.model,
            )
            if (
                source_attempt.result_kind != str(AttemptResultKind.DONE)
                or not source_attempt.output_present
                or starter.step.step_class != expected_starter_class
                or (leaf.get("kind") == "step_output" and starter.status != StepRunStatus.SUCCEEDED)
            ):
                raise ValidationError({"child": "The admitted source is not a completed child starter."})
            selected_path = leaf.get("path", [])
            if not isinstance(selected_path, list) or any(type(part) not in {str, int} for part in selected_path):
                raise ValidationError({"child": "The retained starter path is invalid."})
            retained_id = json_value_at_path(
                source_attempt.output,
                (*selected_path, *remaining),
                field="child_id_path",
            )
            admitted_id = json_value_at_path(attempt.input, child_id_path, field="child_id_path")
            if not isinstance(retained_id, str) or not retained_id or retained_id != admitted_id:
                raise ValidationError({"child": "The child id differs from its exact admitted starter."})
            run_model = self.model._meta.apps.get_model("workflows", "WorkflowRun")
            child = instance_from_public_id(
                run_model,
                retained_id,
                queryset=system_queryset(run_model, using=alias, lock=None),
            )
            if (
                child is None
                or child.parent_relation != ParentRelation.CONTINUATION
                or child.admission_actor_subject() != run.admission_actor_subject()
            ):
                raise ValidationError({"child": "The retained run is not this admitted continuation child."})
            if child.parent_step_run_id != starter.pk:
                normalized_parent = run_model.objects._fresh_recovery_child_parent(
                    starter.run,
                    starter,
                    using=alias,
                    require_active=False,
                )
                if child.parent_step_run_id != normalized_parent.pk:
                    raise ValidationError({"child": "The retained run is not this admitted continuation child."})
            return child

    def admitted_continuation_completion(
        self,
        step_run_id: int,
        *,
        lease_token: uuid.UUID,
        child_id_path: JsonPath,
        expected_starter_class: str,
        actor: Any,
    ) -> tuple[Any, Any | None]:
        """Resolve an admitted child and its one exact successful FRESH recovery."""

        try:
            actor_ref = canonical_subject_ref(str(to_subject_ref(actor)))
        except (NoActorResolvedError, TypeError, ValueError) as error:
            raise PermissionDenied("Continuation completion requires an acting subject.") from error
        alias = self.db
        run_model = self.model._meta.apps.get_model("workflows", "WorkflowRun")
        with transaction.atomic(using=alias), system_context(reason="workflows.continuation.admitted_completion"):
            child = self.admitted_continuation_child(
                step_run_id,
                lease_token=lease_token,
                child_id_path=child_id_path,
                expected_starter_class=expected_starter_class,
            )
            original = (
                system_queryset(run_model, using=alias, lock=("self",)).select_related("workflow").get(pk=child.pk)
            )
            readable = read_scoped_queryset(run_model, actor, action="read")
            if readable is None or not readable.using(alias).filter(pk=original.pk).exists():
                raise PermissionDenied("Admitted continuation child is unavailable.")
            if original.admission_actor_subject() != actor_ref:
                raise PermissionDenied("Admitted continuation child belongs to another actor.")
            if original.status == RunStatus.SUCCEEDED:
                return original, original
            if original.status not in {RunStatus.FAILED, RunStatus.CANCELED}:
                return original, None
            original_root_id = original.execution_lineage_root_id()
            if original_root_id != original.pk:
                raise ValidationError({"child": "The admitted continuation child is not an execution-lineage root."})

            recoveries = list(
                system_queryset(run_model, using=alias, lock=("self",))
                .select_related("workflow", "recovery_source_attempt__step_run__run")
                .filter(
                    origin=RunOrigin.RECOVERY,
                    recovery_mode=str(RecoveryMode.FRESH),
                    recovery_source_attempt__step_run__run_id=original.pk,
                )
                .order_by("pk")
            )
            exact: list[Any] = []
            active: list[Any] = []
            for recovery in recoveries:
                source = recovery.recovery_source_attempt
                source_step = None if source is None else source.step_run
                result = recovery.result
                if (
                    source is None
                    or source_step is None
                    or source_step.run_id != original.pk
                    or source_step.current_attempt_id != source.pk
                    or source_step.status not in {StepRunStatus.FAILED, StepRunStatus.CANCELED}
                    or recovery.workflow_id != original.workflow_id
                    or recovery.input_present != original.input_present
                    or not json_values_equal(recovery.input, original.input)
                    or recovery.subject_content_type_id != original.subject_content_type_id
                    or recovery.subject_object_id != original.subject_object_id
                    or recovery.admission_actor_subject() != actor_ref
                    or recovery.recovery_request_actor_ref != str(actor_ref)
                    or recovery.execution_lineage_root_id() != original_root_id
                ):
                    raise ValidationError({"child": "A child recovery has conflicting retained facts."})
                if recovery.status in {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING}:
                    active.append(recovery)
                elif recovery.status == RunStatus.SUCCEEDED:
                    if (
                        not isinstance(result, dict)
                        or result.get("status") != "succeeded"
                        or not isinstance(result.get("outcome"), str)
                        or not result.get("outcome")
                        or result.get("error") is not None
                        or "output" not in result
                    ):
                        raise ValidationError({"child": "A successful child recovery has conflicting results."})
                    exact.append(recovery)
            if len(exact) > 1:
                raise ValidationError({"child": "The admitted child has multiple successful FRESH recoveries."})
            if not exact:
                return (original, None) if active else (original, original)
            completion = exact[0]
            if readable is None or not readable.using(alias).filter(pk=completion.pk).exists():
                raise PermissionDenied("Successful child recovery is unavailable.")
            return original, completion

    def target_read_authority(
        self,
        step_run_id: int,
        *,
        lease_token: uuid.UUID,
        authority_path: JsonPath,
        proposal_gate_path: tuple[str | int, ...] = (),
    ) -> tuple[Any, Any]:
        """Resolve a prior human gate through this exact active invocation input."""

        if not authority_path or any(type(part) not in {str, int} for part in authority_path):
            raise ValidationError({"target": "Target authority needs an admitted typed input path."})
        if any(type(part) not in {str, int} for part in proposal_gate_path):
            raise ValidationError({"target": "Proposal gate path must be typed."})
        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run_id),
            system_context(reason="workflows.decision.target_read_authority"),
        ):
            run, step_run = self._locked_ancestry(step_run_id, alias)
            if run.is_terminal or step_run.status != StepRunStatus.STARTED or step_run.current_attempt_id is None:
                raise ValidationError({"step_run": "Target authority requires the active admitted step."})
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=step_run.current_attempt_id)
            if (
                attempt.step_run_id != step_run.pk
                or attempt.lease_token != lease_token
                or attempt.started_at is None
                or attempt.result_recorded_at is not None
                or attempt.lease_revoked_at is not None
            ):
                raise ValidationError({"attempt": "Target authority requires the current invocation lease."})
            actor, prior = step_run.decisions.model.objects._resolve_admitted_input_authority(
                input_source="attempt_input",
                authority_path=authority_path,
                proposal_gate_path=proposal_gate_path,
                step_run=step_run,
                attempt=attempt,
                using=alias,
            )
            if actor is None or prior is None:
                raise ValidationError({"target": "No prior gate authority was admitted."})
            return actor, prior

    def consume_decision_resolution(
        self,
        step_run_id: int,
        resolution_path: JsonPath,
        *,
        lease_token: uuid.UUID,
        input_source: DecisionInputSource = "attempt_input",
        expected_action: str,
        expected_target: tuple[str, str],
        expected_verdict: str,
        actor: Any,
        required_record_access: Collection[models.Model] | Callable[[], Collection[models.Model]] = (),
    ) -> tuple[Any, DecisionResolution]:
        """Authorize one current admitted gate value and its exact record basis."""

        if (
            not isinstance(resolution_path, tuple)
            or not resolution_path
            or any(type(part) not in {str, int} or part == "" for part in resolution_path)
        ):
            raise ValidationError({"resolution_path": "A nonempty typed admitted-input path is required."})
        if (
            not isinstance(expected_action, str)
            or not expected_action
            or not isinstance(expected_target, tuple)
            or len(expected_target) != 2
            or any(not isinstance(value, str) for value in expected_target)
            or expected_verdict not in {str(value) for value in Verdict.TERMINAL}
        ):
            raise ValidationError({"decision": "The consumer must declare exact action, target and terminal verdict."})
        try:
            actor_ref = to_subject_ref(actor)
        except NoActorResolvedError as error:
            raise PermissionDenied("Decision resolution requires an acting subject.") from error
        alias = self.db
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run_id),
            system_context(reason="workflows.decision.consume"),
        ):
            run, step_run = self._locked_ancestry(step_run_id, alias)
            if run.is_terminal or step_run.status != StepRunStatus.STARTED or step_run.current_attempt_id is None:
                raise ValidationError({"step_run": "Decision consumption requires the current active step."})
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=step_run.current_attempt_id)
            if (
                attempt.step_run_id != step_run.pk
                or attempt.lease_token != lease_token
                or attempt.started_at is None
                or attempt.result_recorded_at is not None
                or attempt.lease_revoked_at is not None
            ):
                raise ValidationError({"attempt": "Decision consumption requires the current admitted lease."})
            record_basis = tuple(
                required_record_access() if callable(required_record_access) else required_record_access
            )
            admitted_input, provenance, admitted_run, admitted_path = _admitted_decision_input(
                input_source=input_source,
                path=resolution_path,
                step_run=step_run,
                attempt=attempt,
                using=alias,
            )
            leaf, remaining = _decision_source_leaf(
                provenance,
                admitted_path,
                recovery_source_attempt_id=admitted_run.recovery_source_attempt_id,
            )
            source_attempt, source_step_run = _admitted_source_attempt(
                leaf,
                run=admitted_run,
                alias=alias,
                attempt_model=self.model,
            )
            if source_attempt.result_kind != str(AttemptResultKind.SUSPEND):
                raise ValidationError({"decision": "The admitted source is not a retained Decision gate."})
            decision_model = self.model._meta.apps.get_model("workflows", "Decision")
            source_decisions = list(
                system_queryset(decision_model, using=alias, lock=None)
                .filter(suspension_attempt_id=source_attempt.pk)
                .order_by("priority", "pk")
            )
            projected = retained_gate_output(source_attempt, source_decisions)
            if projected is None:
                raise ValidationError({"decision": "The retained suspension has no terminal gate projection."})
            if leaf.get("kind") == "step_output" and (
                source_step_run.status != StepRunStatus.SUCCEEDED
                or not source_step_run.output_present
                or source_step_run.output != projected
                or source_step_run.outcome != projected["outcome"]
            ):
                raise ValidationError({"decision": "The upstream gate projection does not match retained evidence."})
            selected_path = leaf.get("path", [])
            if not isinstance(selected_path, list) or any(type(part) not in {str, int} for part in selected_path):
                raise ValidationError({"decision": "The admitted gate source path is invalid."})
            retained_value = json_value_at_path(
                projected,
                (*selected_path, *remaining),
                field="resolution_path",
            )
            admitted_value = json_value_at_path(admitted_input, admitted_path, field="resolution_path")
            if not json_values_equal(admitted_value, retained_value):
                raise ValidationError({"decision": "The input resolution differs from its retained gate source."})
            try:
                resolution = DecisionResolution.model_validate_json(json.dumps(admitted_value, allow_nan=False))
            except (PydanticValidationError, TypeError, ValueError) as error:
                raise ValidationError(
                    {"decision": "The admitted gate resolution is not typed terminal evidence."}
                ) from error
            decision = instance_from_public_id(
                decision_model,
                resolution.decision_id,
                queryset=system_queryset(decision_model, using=alias, lock=None),
            )
            canonical_expected_target = _canonical_declared_target(*expected_target)
            if (
                decision is None
                or decision.pk not in {row.pk for row in source_decisions}
                or not isinstance(leaf.get("settled_decision_ids"), list)
                or decision.pk not in leaf["settled_decision_ids"]
                or decision.pk not in source_attempt.decision_settlement.get("decision_ids", ())
                or decision.suspension_attempt_id != source_attempt.pk
                or decision.action != expected_action
                or (decision.target_model, decision.target_id) != canonical_expected_target
                or str(decision.verdict) != expected_verdict
                or _decision_resolution(decision, index=decision.declaration_index) != resolution
            ):
                raise ValidationError({"decision": "Decision action, target, verdict or provenance differs."})
            if (
                not rebac_backend()
                .check_access(subject=actor_ref, action="read", resource=to_object_ref(decision))
                .allowed
            ):
                raise PermissionDenied("Decision resolution is not readable by this actor.")
            if record_basis:
                self._validate_consumed_record_access(
                    decision,
                    resolution,
                    record_basis,
                    using=alias,
                )
            return decision, resolution

    @staticmethod
    def _validate_consumed_record_access(
        decision: Any,
        resolution: DecisionResolution,
        required_records: Collection[models.Model],
        *,
        using: str,
    ) -> None:
        """Authorize locked records by current reads or this Decision's exact delegation."""

        required: dict[tuple[str, str], models.Model] = {}
        for record in required_records:
            if not isinstance(record, models.Model) or record.pk is None:
                raise ValidationError({"record_access": "Required Decision records must be retained rows."})
            ref = to_object_ref(record)
            key = (ref.resource_type, ref.resource_id)
            if not system_queryset(type(record), using=using, lock=("self",)).filter(pk=record.pk).exists():
                raise ValidationError({"record_access": "A required Decision record is unavailable."})
            required.setdefault(key, record)
        try:
            resolver_subject = canonical_subject_ref(resolution.resolved_by)
        except (TypeError, ValueError) as error:
            raise ValidationError({"record_access": "Decision record authority requires a human resolver."}) from error
        resolver = get_user_model().objects.active_person_for_subject(resolver_subject)
        if resolver is None:
            raise ValidationError({"record_access": "Decision record authority requires an active human resolver."})
        try:
            canonical_resolver = canonical_subject_ref(str(to_subject_ref(resolver)))
        except (NoActorResolvedError, TypeError, ValueError) as error:
            raise ValidationError({"record_access": "Decision resolver identity is not canonical."}) from error
        if canonical_resolver != resolver_subject:
            raise ValidationError({"record_access": "Decision resolver identity is not canonical."})

        if all(record.with_actor(resolver).has_access("read") for record in required.values()):
            return

        required_types = {resource_type for resource_type, _ in required}
        delegated = {
            (ref.resource_type, ref.resource_id)
            for ref in _retained_record_access_refs(decision)
            if ref.resource_type in required_types
        }
        if delegated != set(required):
            raise PermissionDenied("The Decision does not authorize the complete required record basis.")
        for record in required.values():
            if "pending_decision" not in type(record).get_rebac_grantable():
                raise ValidationError({"record_access": "A required record has no pending Decision delegation owner."})

    def recovery_plan(self, attempt: Any, *, actor: Any) -> RecoveryPlan:
        """Return the operation-owned recovery capability for authorized evidence."""

        readable_attempts = read_scoped_queryset(self.model, actor, action="read")
        row = (
            None
            if readable_attempts is None
            else readable_attempts.select_related("step_run__run__workflow", "step_run__step")
            .filter(pk=attempt.pk)
            .first()
        )
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
        subject_readable = subject is None or (
            (scoped := read_scoped_queryset(type(subject), actor, action="read")) is not None
            and scoped.filter(pk=subject.pk).exists()
        )
        admissible = (
            writable is not None
            and writable.filter(pk=workflow.pk).exists()
            and subject_readable
            and step_run.current_attempt_id == row.pk
            and step_run.status in {StepRunStatus.FAILED, StepRunStatus.CANCELED}
            and (
                (
                    row.applied_at is not None
                    and row.result_kind
                    in {
                        str(AttemptResultKind.ERROR),
                        str(AttemptResultKind.NO_RESULT),
                        str(AttemptResultKind.PREPARATION_ERROR),
                        str(AttemptResultKind.TRANSIENT_ERROR),
                    }
                )
                or (row.result_recorded_at is None and row.lease_revoked_at is not None)
            )
        )
        capability = (
            step_run.step.resolve_impl("step_class").recovery_capability(attempt=row)
            if admissible
            else RecoveryCapability(None, "This retained attempt is not an admissible failure.")
        )
        if capability.available and step_run.map_index >= 0:
            try:
                self._map_recovery_source(row.pk, alias=self.db, lock=None)
            except ValidationError:
                capability = RecoveryCapability(
                    None,
                    "Map recovery is unavailable until its exact retained expansion is aggregated.",
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
    def _fixture_source_summary(attempt: Any, role: FixtureRole) -> FixtureSourceSummary:
        step = attempt.step_run.step
        workflow = step.workflow
        return FixtureSourceSummary(
            attempt_id=attempt.sqid,
            run_id=attempt.step_run.run.sqid,
            workflow_id=workflow.sqid,
            workflow_revision=workflow.draft_revision,
            step_id=step.sqid,
            step_key=step.key,
            role=role,
            item_index=(
                attempt.map_item_index
                if role == FixtureRole.MAP_ITEM
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
        role: FixtureRole,
        step_key: str,
        item_index: int | None,
    ) -> Any:
        """Return authorized accepted evidence for one exact fixture slot."""

        if not isinstance(role, FixtureRole):
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
        queryset = (
            authorized.select_related("step_run__run", "step_run__step__workflow")
            .filter(
                result_kind=AttemptResultKind.DONE,
                result_recorded_at__isnull=False,
                applied_at__isnull=False,
                lease_revoked_at__isnull=True,
                step_run__step__isnull=False,
                step_run__step__key=step_key,
            )
            .filter(
                models.Q(step_run__step__workflow_id=head_id)
                | models.Q(step_run__step__workflow__published_from_id=head_id)
            )
        )
        if role == FixtureRole.OUTPUT:
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
        role: FixtureRole,
        step_key: str,
        item_index: int | None = None,
        after: str | None = None,
        first: int = 20,
    ) -> FixtureSourcePage:
        """Return a bounded page of summaries without retained payload values."""

        if type(first) is not int or not 1 <= first <= 50:
            raise ValidationError({"first": "Captured source pages contain between one and fifty rows."})
        queryset = (
            self._eligible_fixture_source_queryset(
                workflow,
                actor=actor,
                role=role,
                step_key=step_key,
                item_index=item_index,
            )
            .only(
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
            )
            .order_by("-pk")
        )
        if after is not None:
            cursor = queryset.filter(sqid=after).values_list("pk", flat=True).first()
            if cursor is None:
                raise ValidationError({"after": "Captured source cursor is unavailable."})
            queryset = queryset.filter(pk__lt=cursor)
        rows = list(queryset[: first + 1])
        visible = rows[:first]
        return FixtureSourcePage(
            items=tuple(self._fixture_source_summary(row, role) for row in visible),
            next_after=visible[-1].sqid if len(rows) > first else None,
        )

    def test_fixture_source(
        self,
        workflow: Any,
        *,
        actor: Any,
        attempt_id: str,
        role: FixtureRole,
        step_key: str,
        item_index: int | None = None,
    ) -> FixtureSource | None:
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
            if role == FixtureRole.OUTPUT
            else JsonPresence(True, copy.deepcopy(attempt.map_item))
        )
        return FixtureSource(self._fixture_source_summary(attempt, role), value)

    def _test_fixture_source_record(
        self,
        workflow: Any,
        *,
        actor: Any,
        attempt_id: str,
        role: FixtureRole,
        step_key: str,
        item_index: int | None,
    ) -> Any | None:
        """Resolve the exact persisted row used by both preview and admission."""

        return (
            self._eligible_fixture_source_queryset(
                workflow,
                actor=actor,
                role=role,
                step_key=step_key,
                item_index=item_index,
            )
            .filter(sqid=attempt_id)
            .first()
        )

    def record_test_fixture(self, fixture: Any, *, at: datetime, due_step_run_id: int | None = None) -> Any:
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
            if fixture.role != FixtureRole.OUTPUT:
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
                    operation=lambda: step_run.mark_started(heartbeat_at=None, claimed_deliveries=run.deliveries),
                )
                self._write_step_run(
                    step_run,
                    alias=alias,
                    operation=lambda: step_run.mark_succeeded(
                        output=copy.deepcopy(fixture.value) if fixture.value_present else None,
                        output_present=fixture.value_present,
                        outcome=fixture.outcome,
                    ),
                )
            return attempt

    @contextmanager
    def _write(self, alias: str, step_run_id: int) -> Iterator[None]:
        active = _attempt_write_session.get()
        if active is not None:
            if active.alias != alias or active.step_run_id != step_run_id:
                raise RuntimeError("An attempt write cannot span database connections or logical step runs.")
            yield
            return
        token = _attempt_write_session.set(_AttemptWriteSession(alias, step_run_id))
        try:
            yield
        finally:
            _attempt_write_session.reset(token)

    def active_database_command_input(
        self,
        step_run_id: int,
        *,
        actor: Any,
    ) -> tuple[Any, AttemptInput]:
        """Return the exact input of this active database-command invocation."""

        alias = self.db
        if not _attempt_write_active(alias, step_run_id):
            raise RuntimeError("Database-command input requires the active attempt write session.")
        try:
            actor_ref = to_subject_ref(actor)
        except NoActorResolvedError as error:
            raise PermissionDenied("Database-command input requires the admitted actor.") from error
        with system_context(reason="workflows.attempt.database_command.input"):
            run, step_run = self._locked_ancestry(step_run_id, alias)
            if step_run.current_attempt_id is None:
                raise ValidationError({"attempt": "Database-command input requires the current invocation."})
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=step_run.current_attempt_id)
        if (
            run.is_terminal
            or step_run.status != StepRunStatus.STARTED
            or step_run.current_attempt_id != attempt.pk
            or step_run.effect_key != attempt.effect_key
            or step_run.effect_generation != attempt.effect_generation
            or run.admission_actor_subject() != actor_ref
            or attempt.step_run_id != step_run.pk
            or attempt.started_at is None
            or attempt.lease_revoked_at is not None
            or attempt.result_recorded_at is not None
        ):
            raise ValidationError({"attempt": "Database-command input requires the current admitted invocation."})
        return step_run, AttemptInput(
            present=attempt.input_present,
            value=copy.deepcopy(attempt.input),
            provenance=copy.deepcopy(attempt.input_provenance),
        )

    def _locked_ancestry(self, step_run_id: int, alias: str) -> tuple[Any, Any]:
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        run_id = (
            system_queryset(step_run_model, using=alias, lock=None).values_list("run_id", flat=True).get(pk=step_run_id)
        )
        run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
        step_run = system_queryset(step_run_model, using=alias, lock=("self",)).get(pk=step_run_id)
        if step_run.run_id != run.pk:
            raise OperationalError("Step run ownership changed while its attempt was being locked.")
        return run, step_run

    def _save_attempt(self, attempt: Any, *, alias: str, **kwargs: Any) -> None:
        """Authorize one exact instance save and consume authority before signals run."""

        capability = _AttemptSaveCapability(
            atomic=_AtomicWriteCapability(alias, id(attempt)),
            step_run_id=attempt.step_run_id,
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

        capability = _StepRunSaveCapability(
            atomic=_AtomicWriteCapability(alias, id(step_run)),
            run_id=step_run.run_id,
            step_run_id=step_run.pk,
        )
        token = _step_run_save_capability.set(capability)
        try:
            result = operation()
            if not capability.atomic.consumed:
                raise RuntimeError("Retained StepRun projection operation did not save its exact row.")
            return result
        finally:
            _step_run_save_capability.reset(token)

    def validate_result(self, result: AttemptResult) -> AttemptResult:
        """Validate one physical result before any retained write begins."""

        self._validate_result(result)
        self._validated_artifacts(result)
        return result

    def _fresh_recovery_anchor(
        self,
        current_attempt_id: int | None,
        *,
        recovery_run_id: int,
        alias: str,
        lock: tuple[str, ...] | None,
        require_active: bool,
    ) -> tuple[Any, tuple[int, int] | None]:
        """Trace one same-slot continuation chain to its admitted FRESH attempt."""

        if current_attempt_id is None:
            raise ValidationError({"recovery": "FRESH recovery has no current attempt."})
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        recovery_run = system_queryset(run_model, using=alias, lock=lock).get(pk=recovery_run_id)
        step_run = system_queryset(step_run_model, using=alias, lock=lock).get(
            current_attempt_id=current_attempt_id,
            run_id=recovery_run.pk,
        )
        current = system_queryset(self.model, using=alias, lock=lock).get(
            pk=current_attempt_id,
            step_run_id=step_run.pk,
        )
        if require_active:
            eligible_current = (
                current.started_at is not None
                and current.result_recorded_at is None
                and current.lease_revoked_at is None
            )
        else:
            eligible_current = (current.result_recorded_at is not None and current.applied_at is not None) or (
                current.result_recorded_at is None and current.lease_revoked_at is not None
            )
        if not eligible_current:
            raise ValidationError({"recovery": "FRESH recovery current attempt is not retained."})
        lineage = current
        retained_target = (
            (current.external_content_type_id, current.external_object_id)
            if current.external_content_type_id is not None and current.external_object_id is not None
            else None
        )
        while lineage.cause == str(AttemptCause.CONTINUATION):
            previous = (
                system_queryset(self.model, using=alias, lock=lock)
                .filter(
                    step_run_id=step_run.pk,
                    ordinal=lineage.ordinal - 1,
                )
                .first()
            )
            if (
                previous is None
                or previous.result_kind != str(AttemptResultKind.SUSPEND)
                or previous.result_recorded_at is None
                or previous.applied_at is None
                or previous.lease_revoked_at is not None
                or previous.external_content_type_id is None
                or previous.external_object_id is None
                or previous.input_present != lineage.input_present
                or not json_values_equal(previous.input, lineage.input)
                or previous.effect_key != lineage.effect_key
                or previous.effect_generation != lineage.effect_generation
                or previous.map_expansion_id != lineage.map_expansion_id
                or previous.map_item_index != lineage.map_item_index
                or previous.map_item_present != lineage.map_item_present
                or not json_values_equal(previous.map_item, lineage.map_item)
            ):
                raise ValidationError({"recovery": "FRESH recovery continuation identity changed."})
            previous_target = (
                previous.external_content_type_id,
                previous.external_object_id,
            )
            if retained_target is not None and previous_target != retained_target:
                raise ValidationError({"recovery": "FRESH recovery continuation target changed."})
            retained_target = previous_target
            lineage = previous
        if (
            lineage.cause != str(AttemptCause.MANUAL_RETRY)
            or lineage.recovery_mode != str(RecoveryMode.FRESH)
            or lineage.recovery_source_attempt_id != recovery_run.recovery_source_attempt_id
        ):
            raise ValidationError({"recovery": "Attempt is not anchored to this exact FRESH recovery."})
        return lineage, retained_target

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
            run_rows = system_queryset(step_run_model, using=alias, lock=None).filter(run_id=run.pk)
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
                fixture_model = self.model._meta.apps.get_model("workflows", "WorkflowTestFixture")
                fixture_indexes = set(
                    system_queryset(fixture_model, using=alias, lock=None)
                    .filter(
                        run_id=run.pk,
                        step_id=plan.target_id,
                        role=FixtureRole.OUTPUT,
                        item_index__gte=0,
                        item_index__lt=len(plan.items),
                    )
                    .values_list("item_index", flat=True)
                )
            additional = sum(
                index not in fixture_indexes and existing_rows.get(index) != StepRunStatus.SCHEDULED
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
                operation=lambda: locked.mark_started(heartbeat_at=None, claimed_deliveries=run.deliveries),
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

    @staticmethod
    def _map_member_result(child: Any, item_attempt: Any, *, expansion_id: int) -> dict[str, Any]:
        """Project one exact terminal Map member through the shared aggregate owner."""

        output = None
        if child.status == StepRunStatus.SUCCEEDED:
            if (
                item_attempt is None
                or item_attempt.map_expansion_id != expansion_id
                or item_attempt.map_item_index != child.map_index
                or item_attempt.effect_key != child.effect_key
                or item_attempt.effect_generation != child.effect_generation
                or item_attempt.result_kind != str(AttemptResultKind.DONE)
                or item_attempt.applied_at is None
                or item_attempt.lease_revoked_at is not None
            ):
                raise ValidationError({"map": "Map member success lacks current retained DONE evidence."})
            if item_attempt.output_present:
                output = copy.deepcopy(item_attempt.output)
        return {
            "map_index": child.map_index,
            "status": str(child.status),
            "outcome": child.outcome,
            "output": output,
            "output_present": bool(item_attempt.output_present) if item_attempt is not None else False,
            "error": child.error,
        }

    @staticmethod
    def _map_aggregate_output(
        results: list[dict[str, Any]],
        *,
        error: Any = None,
    ) -> dict[str, Any]:
        """Build the one public ordered Map result shape from member projections."""

        successes = sum(
            result["status"] == str(StepRunStatus.SUCCEEDED)
            and result["outcome"] not in {"child_failed", "child_canceled"}
            for result in results
        )
        failures = sum(
            result["status"]
            in {
                str(StepRunStatus.FAILED),
                str(StepRunStatus.CANCELED),
            }
            or result["outcome"] in {"child_failed", "child_canceled"}
            for result in results
        )
        output = {
            "total": len(results),
            "successes": successes,
            "failures": failures,
            "results": results,
        }
        if error:
            output["error"] = error
        return output

    @staticmethod
    def _map_policy_outcome(controller: Any, output: dict[str, Any]) -> str:
        """Evaluate the configured Map policy for an ordinary or recovered join."""

        impl_class = controller.step.resolve_impl("step_class")
        return (
            "failed"
            if output.get("error")
            else "succeeded"
            if impl_class.policy_passes(controller.step.config, output)
            else "failed"
        )

    def _map_recovery_source(
        self,
        source_attempt_id: int,
        *,
        alias: str,
        lock: tuple[str, ...] | None,
        aggregate_attempt_id: int | None = None,
    ) -> tuple[Any, Any, Any, Any, Any, dict[str, Any]]:
        """Resolve an exact failed member and its already-applied retained Map join."""

        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        source = system_queryset(self.model, using=alias, lock=lock).get(pk=source_attempt_id)
        source_step = (
            system_queryset(step_run_model, using=alias, lock=lock)
            .select_related("run__workflow", "step")
            .get(pk=source.step_run_id)
        )
        if (
            source_step.current_attempt_id != source.pk
            or source_step.status not in {StepRunStatus.FAILED, StepRunStatus.CANCELED}
            or source_step.map_index < 0
            or source.map_expansion_id is None
            or source.map_item_index != source_step.map_index
            or source.effect_key != source_step.effect_key
            or source.effect_generation != source_step.effect_generation
        ):
            raise ValidationError({"attempt": "Map recovery requires one exact current failed member."})
        root_source = source
        source_runs_seen: set[int] = set()
        while root_source.step_run.run.origin == RunOrigin.RECOVERY:
            recovery_run_id = root_source.step_run.run_id
            if recovery_run_id in source_runs_seen:
                raise ValidationError({"attempt": "Map recovery source lineage is cyclic."})
            source_runs_seen.add(recovery_run_id)
            anchor, _target = self._fresh_recovery_anchor(
                root_source.pk,
                recovery_run_id=recovery_run_id,
                alias=alias,
                lock=lock,
                require_active=False,
            )
            if anchor.recovery_source_attempt_id is None:
                raise ValidationError({"attempt": "Map recovery source lineage is incomplete."})
            previous = system_queryset(self.model, using=alias, lock=lock).get(pk=anchor.recovery_source_attempt_id)
            if (
                previous.step_run.step_id != source_step.step_id
                or previous.step_run.map_index != source_step.map_index
                or previous.map_expansion_id != source.map_expansion_id
                or previous.map_item_index != source.map_item_index
                or previous.map_item_present != source.map_item_present
                or not json_values_equal(previous.map_item, source.map_item)
            ):
                raise ValidationError({"attempt": "Map recovery source lineage changed its retained item."})
            root_source = previous
        root_step = (
            system_queryset(step_run_model, using=alias, lock=lock)
            .select_related("run__workflow", "step")
            .get(pk=root_source.step_run_id)
        )
        if (
            root_step.current_attempt_id != root_source.pk
            or root_step.status not in {StepRunStatus.FAILED, StepRunStatus.CANCELED}
            or root_step.step_id != source_step.step_id
            or root_step.map_index != source_step.map_index
            or root_step.run.workflow_id != source_step.run.workflow_id
        ):
            raise ValidationError({"attempt": "Map recovery root evidence is no longer exact."})
        expansion = system_queryset(self.model, using=alias, lock=lock).get(pk=root_source.map_expansion_id)
        controller = (
            system_queryset(step_run_model, using=alias, lock=lock)
            .select_related("run__workflow", "step")
            .get(pk=expansion.step_run_id)
        )
        aggregate = (
            system_queryset(self.model, using=alias, lock=lock)
            .filter(pk=aggregate_attempt_id or controller.current_attempt_id)
            .first()
            if aggregate_attempt_id is not None or controller.current_attempt_id is not None
            else None
        )
        aggregate_controller = (
            system_queryset(step_run_model, using=alias, lock=lock)
            .select_related("run__workflow", "step")
            .get(pk=aggregate.step_run_id)
            if aggregate is not None
            else None
        )
        checkpoint = expansion.checkpoint if expansion.checkpoint_present else None
        map_state = checkpoint.get("map") if isinstance(checkpoint, dict) else None
        items = map_state.get("items") if isinstance(map_state, dict) else None
        output = aggregate.output if aggregate is not None and aggregate.output_present else None
        results = output.get("results") if isinstance(output, dict) else None
        if (
            controller.run_id != root_step.run_id
            or controller.run.workflow_id != source_step.run.workflow_id
            or controller.map_index != -1
            or controller.step_id is None
            or controller.step.step_class != "map"
            or aggregate is None
            or aggregate_controller is None
            or aggregate_controller.current_attempt_id != aggregate.pk
            or aggregate_controller.status != StepRunStatus.SUCCEEDED
            or aggregate_controller.run.workflow_id != controller.run.workflow_id
            or aggregate_controller.step_id != controller.step_id
            or aggregate_controller.map_index != -1
            or aggregate.cause != str(AttemptCause.MAP_ENGINE)
            or aggregate.result_kind != str(AttemptResultKind.DONE)
            or aggregate.applied_at is None
            or aggregate.lease_revoked_at is not None
            or aggregate.effect_key != aggregate_controller.effect_key
            or aggregate.effect_generation != aggregate_controller.effect_generation
            or expansion.cause != str(AttemptCause.MAP_ENGINE)
            or expansion.result_kind != str(AttemptResultKind.WAIT)
            or expansion.applied_at is None
            or expansion.lease_revoked_at is not None
            or not isinstance(map_state, dict)
            or not isinstance(items, list)
            or map_state.get("target_step_id") != source_step.step_id
            or source_step.map_index >= len(items)
            or not root_source.map_item_present
            or not json_values_equal(root_source.map_item, items[source_step.map_index])
            or not isinstance(results, list)
            or len(results) != len(items)
            or [result.get("map_index") if isinstance(result, dict) else None for result in results]
            != list(range(len(items)))
            or not json_values_equal(
                results[source_step.map_index],
                self._map_member_result(root_step, root_source, expansion_id=expansion.pk),
            )
        ):
            raise ValidationError(
                {"attempt": "Map recovery is unavailable until the exact retained expansion is aggregated."}
            )
        return source, source_step, expansion, controller, aggregate, map_state

    def record_recovery_map_aggregate(
        self,
        recovered_step_run_id: int,
        *,
        at: datetime,
    ) -> Any | None:
        """Overlay one recovered member onto its exact retained Map aggregate."""

        alias = self.db
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        with transaction.atomic(using=alias), system_context(reason="workflows.map.recovery_aggregate"):
            run_id = (
                system_queryset(step_run_model, using=alias, lock=None)
                .values_list("run_id", flat=True)
                .get(pk=recovered_step_run_id)
            )
            run = (
                system_queryset(run_model, using=alias, lock=("self",))
                .select_related("recovery_source_attempt")
                .get(pk=run_id)
            )
            recovered = (
                system_queryset(step_run_model, using=alias, lock=("self",))
                .select_related("step")
                .get(pk=recovered_step_run_id)
            )
            source_attempt_id = run.recovery_source_attempt_id
            if (
                run.origin != RunOrigin.RECOVERY
                or run.recovery_mode != str(RecoveryMode.FRESH)
                or source_attempt_id is None
                or recovered.run_id != run.pk
                or recovered.step_id != run.recovery_source_attempt.step_run.step_id
                or recovered.map_index != run.recovery_source_attempt.step_run.map_index
                or recovered.map_index < 0
            ):
                raise ValidationError({"run": "Map recovery aggregate requires its exact FRESH member."})
            if recovered.status != StepRunStatus.SUCCEEDED:
                return None
            current = (
                system_queryset(self.model, using=alias, lock=("self",)).filter(pk=recovered.current_attempt_id).first()
                if recovered.current_attempt_id is not None
                else None
            )
            recovery_anchor = None
            if current is not None:
                recovery_anchor, _recovery_target = self._fresh_recovery_anchor(
                    current.pk,
                    recovery_run_id=run.pk,
                    alias=alias,
                    lock=("self",),
                    require_active=False,
                )
            source, _source_step, expansion, source_controller, _root_aggregate, _map_state = self._map_recovery_source(
                source_attempt_id, alias=alias, lock=None
            )
            evidence_model = run._meta.apps.get_model("workflows", "WorkflowRecoveryEvidence")
            basis = (
                system_queryset(evidence_model, using=alias, lock=None)
                .filter(
                    run_id=run.pk,
                    step_id=source_controller.step_id,
                    map_index=-1,
                )
                .first()
            )
            if basis is None:
                raise ValidationError({"map": "Recovered Map aggregate basis was not admitted."})
            source, _source_step, expansion, source_controller, source_aggregate, map_state = self._map_recovery_source(
                source_attempt_id,
                alias=alias,
                lock=None,
                aggregate_attempt_id=basis.source_attempt_id,
            )
            if (
                current is None
                or current.step_run_id != recovered.pk
                or recovery_anchor is None
                or recovery_anchor.recovery_source_attempt_id != source.pk
                or current.map_expansion_id != expansion.pk
                or current.map_item_index != source.map_item_index
                or current.map_item_present != source.map_item_present
                or not json_values_equal(current.map_item, source.map_item)
                or current.effect_key != recovered.effect_key
                or current.effect_generation != recovered.effect_generation
                or current.result_kind != str(AttemptResultKind.DONE)
                or current.applied_at is None
                or current.lease_revoked_at is not None
                or run.workflow_id != source_controller.run.workflow_id
            ):
                raise ValidationError({"attempt": "Recovered Map member lacks exact retained DONE evidence."})

            retained_output = copy.deepcopy(source_aggregate.output)
            results = retained_output.get("results") if isinstance(retained_output, dict) else None
            if not isinstance(results, list) or source.map_item_index is None:
                raise ValidationError({"map": "Retained Map aggregate output is unavailable."})
            results[source.map_item_index] = self._map_member_result(
                recovered,
                current,
                expansion_id=expansion.pk,
            )
            expected_output = self._map_aggregate_output(
                results,
                error=map_state.get("error"),
            )

            controller, _ = step_run_model.objects.get_or_create(
                run=run,
                step=source_controller.step,
                map_index=-1,
                defaults={"status": StepRunStatus.SCHEDULED, "input": {}},
            )
            controller = (
                system_queryset(step_run_model, using=alias, lock=("self",))
                .select_related("run", "step")
                .get(pk=controller.pk)
            )
            expected_outcome = self._map_policy_outcome(controller, expected_output)
            if controller.current_attempt_id is not None:
                existing = system_queryset(self.model, using=alias, lock=("self",)).get(
                    pk=controller.current_attempt_id
                )
                if (
                    controller.status != StepRunStatus.SUCCEEDED
                    or existing.cause != str(AttemptCause.MAP_ENGINE)
                    or existing.result_kind != str(AttemptResultKind.DONE)
                    or existing.applied_at is None
                    or existing.lease_revoked_at is not None
                    or existing.output_present is not True
                    or not json_values_equal(existing.output, expected_output)
                    or existing.outcome != expected_outcome
                ):
                    raise ValidationError({"map": "Recovered Map aggregate identity changed."})
                return existing
            if controller.status != StepRunStatus.SCHEDULED or controller.is_retained:
                raise ValidationError({"map": "Recovered Map controller is not pristine."})
            with self._write(alias, controller.pk):
                aggregate = self._allocate_locked(
                    controller,
                    cause=AttemptCause.MAP_ENGINE,
                    input=AttemptInput(),
                    claimed_at=at,
                    alias=alias,
                )
                self._write_step_run(
                    controller,
                    alias=alias,
                    operation=lambda: controller.mark_started(
                        heartbeat_at=None,
                        claimed_deliveries=run.deliveries,
                    ),
                )
                aggregate.result_kind = str(AttemptResultKind.DONE)
                aggregate.result_recorded_at = at
                aggregate.output_present = True
                aggregate.output = copy.deepcopy(expected_output)
                aggregate.outcome = expected_outcome
                aggregate.applied_at = at
                self._save_attempt(aggregate, alias=alias)
                self._write_step_run(
                    controller,
                    alias=alias,
                    operation=lambda: controller.mark_succeeded(
                        output=expected_output,
                        outcome=expected_outcome,
                    ),
                )
                return aggregate

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
            run_id = (
                system_queryset(step_run_model, using=alias, lock=None)
                .values_list("run_id", flat=True)
                .get(pk=step_run_id)
            )
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
            locked_rows = list(
                system_queryset(step_run_model, using=alias, lock=("self",)).filter(run_id=run.pk).order_by("pk")
            )
            locked = next((row for row in locked_rows if row.pk == step_run_id), None)
            if locked is None:
                raise OperationalError("Map controller disappeared while locking membership.")
            expansion = system_queryset(self.model, using=alias, lock=("self",)).get(pk=expansion_attempt_id)
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
                (row for row in locked_rows if row.current_map_expansion_id == expansion.pk),
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
            attempt_ids = sorted(child.current_attempt_id for child in children if child.current_attempt_id is not None)
            attempts = {
                item.pk: item
                for item in system_queryset(self.model, using=alias, lock=("self",))
                .filter(pk__in=attempt_ids)
                .order_by("pk")
            }
            results: list[dict[str, Any]] = []
            for child in children:
                item_attempt = attempts.get(child.current_attempt_id)
                results.append(
                    self._map_member_result(
                        child,
                        item_attempt,
                        expansion_id=expansion.pk,
                    )
                )
            expected_output = self._map_aggregate_output(
                results,
                error=map_state.get("error"),
            )
            expected_outcome = self._map_policy_outcome(locked, expected_output)
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
                operation=lambda: locked.mark_succeeded(output=expected_output, outcome=expected_outcome),
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
                operation=lambda: locked.mark_started(heartbeat_at=None, claimed_deliveries=run.deliveries),
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
        external_recovery = (
            recovery_source is not None
            and locked.step.resolve_impl("step_class").execution_mode == StepExecutionMode.EXTERNAL_OPERATION
        )
        if recovery_source is not None and (locked.run.recovery_mode == "reconcile" or external_recovery):
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
            or locked.role != FixtureRole.MAP_ITEM
            or locked.item_index != step_run.map_index
            or step_run.current_map_expansion_id is not None
        ):
            raise ValidationError({"test_fixture": "Map item fixture does not match this test slot."})

    def admit_invocation(self, attempt_id: int, *, lease_token: uuid.UUID, at: datetime) -> InvocationAdmission:
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
                operation=lambda: step_run.save(using=alias, update_fields=["heartbeat_at", "updated_at"]),
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
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=step_run.current_attempt_id)
            if (
                attempt.result_kind == str(AttemptResultKind.SUSPEND)
                and attempt.applied_at is not None
                and attempt.lease_revoked_at is None
            ):
                decision_model = step_run.decisions.model
                decision_model.objects.expire_departing_suspension(step_run.pk, resolved_by="workflows/wake")
            self._write_step_run(step_run, alias=alias, operation=lambda: step_run.wake(at=at))
            return True

    def subscribe_external(
        self,
        attempt_id: int,
        *,
        lease_token: uuid.UUID,
        resources: Iterable[Any],
    ) -> None:
        """Commit the complete current attempt target set before its predicate read."""

        alias = self.db
        if connections[alias].in_atomic_block:
            raise RuntimeError("External subscription must commit before the domain predicate read.")
        identities: set[tuple[int, int]] = set()
        for resource in resources:
            target = canonical_record_target(resource)
            if not isinstance(target.object_id, int):
                raise ValidationError({"resources": "External subscriptions require integer record identities."})
            identities.add((target.content_type.pk, target.object_id))
        subscribed = tuple(sorted(identities))
        if not subscribed:
            raise ValidationError({"resources": "External subscription requires at least one target."})
        step_run_id = (
            system_queryset(self.model, using=alias, lock=None).values_list("step_run_id", flat=True).get(pk=attempt_id)
        )
        with (
            transaction.atomic(using=alias),
            self._write(alias, step_run_id),
            system_context(reason="workflows.attempt.subscribe_external"),
        ):
            run, step_run = self._locked_ancestry(step_run_id, alias)
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=attempt_id)
            if (
                run.is_terminal
                or step_run.status != StepRunStatus.STARTED
                or step_run.current_attempt_id != attempt.pk
                or step_run.effect_key != attempt.effect_key
                or step_run.effect_generation != attempt.effect_generation
                or attempt.lease_token != lease_token
                or attempt.started_at is None
                or attempt.lease_revoked_at is not None
                or attempt.result_recorded_at is not None
            ):
                raise ValidationError({"attempt": "External subscription requires the current started lease."})
            subscription_model = self.model._meta.apps.get_model("workflows", "StepExternalSubscription")
            existing = tuple(
                sorted(
                    subscription_model.objects.using(alias)
                    .filter(
                        attempt_id=attempt.pk,
                    )
                    .values_list("target_content_type_id", "target_object_id")
                )
            )
            if existing == subscribed:
                return
            if existing:
                raise ValidationError({"resources": "This attempt already retained a different external target set."})
            content_types = {
                row.pk: row
                for row in ContentType.objects.db_manager(alias).filter(
                    pk__in=[content_type_id for content_type_id, _ in subscribed]
                )
            }
            for content_type_id, object_id in subscribed:
                subscription_model(
                    attempt=attempt,
                    target_content_type=content_types[content_type_id],
                    target_object_id=object_id,
                ).save(using=alias, force_insert=True)

    def bind_call_child(self, attempt_id: int, *, lease_token: uuid.UUID, child: Any) -> None:
        """Bind the exact owned child while a fenced call command holds its parent."""

        alias = self.db
        if not _attempt_write_active(alias):
            raise RuntimeError("Child completion binding requires the fenced database-command transaction.")
        unresolved = system_queryset(self.model, using=alias, lock=None).get(pk=attempt_id)
        with self._write(alias, unresolved.step_run_id), system_context(reason="workflows.attempt.bind_call_child"):
            run, step_run = self._locked_ancestry(unresolved.step_run_id, alias)
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=attempt_id)
            child_model = self.model._meta.apps.get_model("workflows", "WorkflowRun")
            retained = (
                system_queryset(child_model, using=alias, lock=("self",))
                .select_related("parent_step_run__run")
                .get(pk=child.pk)
            )
            original = retained.parent_step_run
            if (
                run.is_terminal
                or step_run.status != StepRunStatus.STARTED
                or step_run.current_attempt_id != attempt.pk
                or attempt.lease_token != lease_token
                or attempt.started_at is None
                or attempt.lease_revoked_at is not None
                or attempt.result_recorded_at is not None
                or retained.parent_relation != ParentRelation.OWNED_CALL
                or original is None
                or original.step_id != step_run.step_id
                or original.map_index != step_run.map_index
                or original.run.execution_lineage_root_id() != run.execution_lineage_root_id()
            ):
                raise ValidationError({"child": "Call subscription does not match the current exact child slot."})
            target = canonical_record_target(retained)
            expected = (target.content_type.pk, target.object_id)
            existing = (attempt.external_content_type_id, attempt.external_object_id)
            if existing == expected:
                return
            if existing != (None, None):
                raise ValidationError({"child": "This call attempt is bound to a different child."})
            attempt.external_content_type_id, attempt.external_object_id = expected
            self._save_attempt(
                attempt,
                alias=alias,
                update_fields=["external_content_type", "external_object_id", "updated_at"],
            )

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
                attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=step_run.current_attempt_id)
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
                self._write_step_run(step_run, alias=alias, operation=step_run.mark_canceled)
            elif step_run.status in {StepRunStatus.SCHEDULED, StepRunStatus.WAITING}:
                self._write_step_run(step_run, alias=alias, operation=step_run.mark_canceled)
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
            attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=step_run.current_attempt_id)
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
                    error=(
                        "External request result is uncertain after heartbeat loss."
                        if step_run.step.resolve_impl("step_class").execution_mode
                        == StepExecutionMode.EXTERNAL_OPERATION
                        else "Step heartbeat timed out."
                    ),
                    stacktrace="",
                    outcome=(
                        "uncertain_external_result"
                        if step_run.step.resolve_impl("step_class").execution_mode
                        == StepExecutionMode.EXTERNAL_OPERATION
                        else "failed"
                    ),
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

    def _validate_map_item_owner(self, step_run: Any, map_item: MapItemSource | None, *, alias: str) -> None:
        if map_item is None:
            if step_run.current_map_expansion_id is not None:
                raise ValidationError({"map_item": "Current Map membership requires captured item provenance."})
            return
        if step_run.run.origin == RunOrigin.RECOVERY:
            source_id = step_run.run.recovery_source_attempt_id
            if source_id is None:
                raise ValidationError({"map_item": "Recovery Map item has no admitted source evidence."})
            source = system_queryset(self.model, using=alias, lock=None).select_related("step_run").get(pk=source_id)
            if source.step_run.step_id == step_run.step_id and source.step_run.map_index == step_run.map_index:
                if (
                    source.map_expansion_id == map_item.expansion_attempt_id
                    and source.map_item_index == map_item.index
                    and source.map_item_present is map_item.value.present
                    and json_values_equal(source.map_item, map_item.value.value)
                ):
                    return
                raise ValidationError({"map_item": "Recovery Map item does not match admitted source evidence."})
        if step_run.map_index != map_item.index or step_run.current_map_expansion_id != map_item.expansion_attempt_id:
            raise ValidationError({"map_item": "Map item source does not match current membership."})
        expansion = system_queryset(self.model, using=alias, lock=("self",)).get(pk=map_item.expansion_attempt_id)
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        controller = system_queryset(step_run_model, using=alias, lock=None).get(pk=expansion.step_run_id)
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
            raise ValidationError({"map_item": "Map item source is not current applied expansion evidence."})

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
        elif result.kind == AttemptResultKind.ERROR:
            if (
                not result.error
                or result.output_present
                or result.requested_until is not None
                or result.decisions
                or result.waiting_kind
            ):
                raise ValidationError({"result": "Errors require an error, cannot carry output, timers, or decisions."})
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
                    canonical_subject_ref(subject)
                if declaration.requester:
                    canonical_subject_ref(declaration.requester)
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
            or not json_values_equal(attempt.map_item, map_item.value.value if map_item else None)
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

    def execute_database_command(
        self,
        attempt_id: int,
        *,
        lease_token: uuid.UUID,
        command: Callable[[Any, Any], AttemptResult],
        recorded_at: datetime,
    ) -> AttemptFinalization | None:
        """Fence a current attempt, run its database command, then finalize it atomically.

        The caller keeps an outer transaction open while it schedules continuation
        dispatches. A command exception rolls back its domain writes with the
        result; the caller records failure evidence in a later transaction.
        """

        alias = self.db
        unresolved = system_queryset(self.model, using=alias, lock=None).get(pk=attempt_id)
        with (
            transaction.atomic(using=alias),
            self._write(alias, unresolved.step_run_id),
        ):
            with system_context(reason="workflows.attempt.database_command.fence"):
                run, step_run = self._locked_ancestry(unresolved.step_run_id, alias)
                attempt = system_queryset(self.model, using=alias, lock=("self",)).get(pk=attempt_id)
            current = (
                not run.is_terminal
                and step_run.status == StepRunStatus.STARTED
                and step_run.current_attempt_id == attempt.pk
                and step_run.effect_key == attempt.effect_key
                and step_run.effect_generation == attempt.effect_generation
                and attempt.lease_token == lease_token
                and attempt.started_at is not None
                and attempt.lease_revoked_at is None
                and attempt.result_recorded_at is None
            )
            if not current:
                return None
            result = command(step_run, attempt)
            if result.kind in {
                AttemptResultKind.ERROR,
                AttemptResultKind.TRANSIENT_ERROR,
                AttemptResultKind.NO_RESULT,
                AttemptResultKind.PREPARATION_ERROR,
            }:
                raise ValidationError({"result": "A failed database command cannot commit its domain effect."})
            with system_context(reason="workflows.attempt.database_command.finalize"):
                finalization = self.finalize(
                    attempt_id,
                    lease_token=lease_token,
                    result=result,
                    recorded_at=recorded_at,
                )
            if not finalization.recorded or not finalization.applied:
                raise ValidationError(
                    {"attempt": "The command result was not applied; its domain effect was rolled back."}
                )
            return finalization

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
                not run.is_terminal and step_run.current_attempt_id == attempt.pk and attempt.lease_revoked_at is None
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
                capability = _ArtifactBatchCapability(
                    _AtomicWriteCapability(alias, id(attempt)),
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
        impl = step_run.step.resolve_impl("step_class")
        if impl.execution_mode == StepExecutionMode.EXTERNAL_OPERATION:
            provider_policy = impl.external_operation_policy(attempt=attempt)
            if not isinstance(provider_policy, ExternalOperationPolicy):
                raise ValidationError({"operation": "External operation policy is invalid."})
            if provider_policy is not ExternalOperationPolicy.IDEMPOTENT_REQUEST:
                self._write_step_run(
                    step_run,
                    alias=alias,
                    operation=lambda: step_run.mark_failed(
                        error="External request result is uncertain; automatic replay is unavailable.",
                        stacktrace=result.stacktrace or "",
                        outcome="uncertain_external_result",
                    ),
                )
                return None
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
            and json_values_equal(attempt.result_decisions, serialize_decision_specs(result.decisions))
            and attempt.artifacts_present == result.artifacts_present
            and (
                not result.artifacts_present
                or tuple(
                    attempt.artifacts.order_by("declaration_index").values_list(
                        "target_content_type_id", "target_object_id", "label"
                    )
                )
                == StepAttemptManager._validated_artifacts(result)
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
                    output_present=result.output_present,
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
                    str(decision.pk): retained_decision_form_schema(spec.decision_schema)
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
        return super().get_queryset().system_context(reason="workflows.step_attempt.base_manager")


class StepExternalSubscriptionQuerySet(AngeeQuerySet[Any]):
    """Immutable attempt-owned targets registered before an external predicate read."""

    def update(self, **kwargs: Any) -> int:
        raise TypeError("External subscriptions are immutable attempt evidence.")

    def bulk_create(self, objs: Iterable[Any], *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("External subscriptions can only be recorded by StepAttemptManager.")

    def bulk_update(self, objs: Iterable[Any], fields: Iterable[str], batch_size: int | None = None) -> int:
        raise TypeError("External subscriptions are immutable attempt evidence.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise TypeError("External subscriptions are retained for their attempt lifecycle.")


class StepExternalSubscriptionManager(
    AngeeManager.from_queryset(StepExternalSubscriptionQuerySet)  # type: ignore[misc]
):
    """Read manager for the exact external targets retained by one invocation."""


class StepArtifactQuerySet(AngeeQuerySet[Any]):
    """Read-only collection of explicit retained result artifacts."""

    def for_runs(self, runs: models.QuerySet[Any]) -> Self:
        """Return retained outputs emitted by a bounded workflow-run selection."""

        return cast(
            Self,
            self.filter(
                attempt__step_run__run_id__in=models.Subquery(
                    runs.order_by().values("pk"),
                ),
            )
            .select_related("attempt__step_run__run")
            .order_by("created_at", "pk"),
        )

    def history_page(
        self,
        runs: models.QuerySet[Any],
        *,
        limit: int = 200,
    ) -> tuple[Self, bool]:
        """Return one newest row per target-and-label meaning before bounding history."""

        bounded = max(1, min(int(limit), 200))
        scope = self.for_runs(runs).order_by()
        representative = (
            scope.filter(
                target_content_type_id=models.OuterRef("target_content_type_id"),
                target_object_id=models.OuterRef("target_object_id"),
                label=models.OuterRef("label"),
            )
            .order_by("-created_at", "-pk")
            .values("pk")[:1]
        )
        candidates = (
            scope.annotate(
                _history_representative_id=models.Subquery(representative),
            )
            .filter(pk=models.F("_history_representative_id"))
            .order_by("-created_at", "-pk")
        )
        candidate_ids = list(candidates.values_list("pk", flat=True)[: bounded + 1])
        page = (
            self.filter(pk__in=candidate_ids[:bounded])
            .select_related(
                "attempt__step_run__run",
            )
            .order_by("-created_at", "-pk")
        )
        return cast(Self, page), len(candidate_ids) > bounded

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
        batch_capability = _artifact_batch_capability.get()
        if (
            batch_capability is None
            or batch_capability.attempt_id != attempt.pk
            or batch_capability.rows != rows
            or not _attempt_write_active(using, attempt.step_run_id)
            or attempt.result_recorded_at is None
            or not attempt.artifacts_present
            or not batch_capability.atomic.consume(using, attempt)
        ):
            raise RuntimeError("Artifact recording requires the exact attempt finalization transaction.")
        created: list[Any] = []
        for index, (content_type_id, object_id, label) in enumerate(rows):
            artifact = self.model(
                attempt=attempt,
                declaration_index=index,
                target_content_type_id=content_type_id,
                target_object_id=object_id,
                label=label,
            )
            write_capability = _ArtifactWriteCapability(
                _AtomicWriteCapability(using, id(artifact)),
                attempt.pk,
                index,
            )
            token = _artifact_write_capability.set(write_capability)
            try:
                artifact.save(using=using, force_insert=True)
            finally:
                _artifact_write_capability.reset(token)
            created.append(artifact)
        return tuple(created)


class DecisionQuerySet(AngeeQuerySet[Any]):
    """Decision reads with protected retained-suspension provenance."""

    _PROTECTED_FIELDS = frozenset(
        {
            "suspension_attempt",
            "suspension_attempt_id",
            "declaration_index",
            "priority",
            "action",
            "payload",
            "max_attempts",
            "expires_at",
            "escalate_at",
            "target_model",
            "target_id",
            "target_tab",
            "target_authority_decision",
            "target_authority_decision_id",
            "record_access",
            "verdict",
            "resolution",
            "resolved_by",
            "attempts",
            "resolved_at",
        }
    )

    def with_context_projection(self) -> Self:
        """Annotate the complete context before materializing a Decision list."""

        decision_model = cast(Any, self.model)
        return cast(Self, self.annotate(**decision_model.context_projection_annotation()))

    def update(self, **kwargs: Any) -> int:
        if self._PROTECTED_FIELDS.intersection(kwargs):
            raise TypeError("Decision suspension provenance is owned by DecisionManager.")
        return super().update(**kwargs)

    def bulk_create(self, objs: Iterable[Any], *args: Any, **kwargs: Any) -> list[Any]:
        rows = list(objs)
        retained = any(row.suspension_attempt_id is not None or row.declaration_index is not None for row in rows)
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

    def retained_record_access(
        self,
        decision: Any,
        *,
        actor: Any,
    ) -> tuple[models.Model, ...]:
        """Resolve one canonical Decision's immutable delegated record rows.

        This does not authorize the resolver to use the rows. Consumers pass the
        exact returned basis back to ``consume_decision_resolution``, which owns
        resolver identity and pending-delegation validation.
        """

        readable = read_scoped_queryset(self.model, actor, action="read")
        if not isinstance(decision, self.model) or decision.pk is None or (decision._state.db or self.db) != self.db:
            raise PermissionDenied("The retained Decision is unavailable.")
        canonical = None if readable is None else readable.using(self.db).filter(pk=decision.pk).first()
        if canonical is None:
            raise PermissionDenied("The retained Decision is unavailable.")
        references = _retained_record_access_refs(canonical)
        rows: list[models.Model] = []
        with system_context(reason="workflows.decision.retained_record_access"):
            for ref in references:
                model = model_for_resource_type(ref.resource_type)
                if model is None:
                    raise ValidationError({"record_access": "A retained Decision record type is not installed."})
                row = (
                    system_queryset(model, using=self.db, lock=None)
                    .filter(**{resource_id_attr(model): ref.resource_id})
                    .first()
                )
                if row is None or to_object_ref(row) != ref:
                    raise ValidationError({"record_access": "A retained Decision record is unavailable."})
                rows.append(row)
        return tuple(rows)

    def ensure_sequential_turn(self, decision: Any) -> None:
        """Enforce system-owned priority after the public action check.

        Decision visibility cannot define gate order: hidden sibling slots remain
        part of the declared suspension and therefore must participate.
        """

        if not decision.step_run.decision_gate.is_sequential:
            return
        current_id = (
            system_queryset(self.model, using=self.db, lock=None)
            .filter(
                step_run_id=decision.step_run_id,
                suspension_attempt_id=decision.suspension_attempt_id,
                verdict=Verdict.PENDING,
            )
            .order_by("priority", "pk")
            .values_list("pk", flat=True)
            .first()
        )
        if current_id != decision.pk:
            raise ValidationError({"decision": "Sequential decisions must resolve in priority order."})

    @contextmanager
    def _resolution_owner(self, decision_id: int) -> Iterator[None]:
        """Require one retained resolution to finish its logical projection."""

        alias = self.db
        session = _DecisionResolutionSession(alias, decision_id)
        token = _decision_resolution_session.set(session)
        try:
            yield
            if not session.completed:
                raise RuntimeError("Retained decision resolution did not complete its projection.")
        finally:
            _decision_resolution_session.reset(token)

    def _require_resolution_owner(self, decision_id: int) -> _DecisionResolutionSession:
        session = _decision_resolution_session.get()
        if session is None or session.completed or session.alias != self.db or session.decision_id != decision_id:
            raise RuntimeError("Retained decision writes require their exact transition owner.")
        return session

    def complete_retained_resolution(self, decision_id: int) -> None:
        """Mark the exact retained resolution's projection and dispatch complete."""

        self._require_resolution_owner(decision_id).completed = True

    def _write_retained(self, decision: Any, operation: Any) -> Any:
        """Authorize one exact retained Decision save and spend it before signals."""

        capability = _DecisionSaveCapability(
            _AtomicWriteCapability(self.db, id(decision)),
            decision.pk,
        )
        token = _decision_save_capability.set(capability)
        try:
            result = operation()
            if not capability.atomic.consumed:
                raise RuntimeError("Retained Decision transition did not save its exact row.")
            if decision.is_terminal:
                self._remove_pending_record_access(decision)
            return result
        finally:
            _decision_save_capability.reset(token)

    def _lock_retained_resolution(self, decision_id: int, *, using: str) -> Any | None:
        """Lock and validate one retained decision in canonical ancestry order."""

        discovered = (
            system_queryset(self.model, using=using, lock=None)
            .filter(pk=decision_id)
            .values("step_run_id", "suspension_attempt_id")
            .first()
        )
        if discovered is None or discovered["suspension_attempt_id"] is None:
            return None
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        attempt_model = self.model._meta.get_field("suspension_attempt").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        run_id = (
            system_queryset(step_run_model, using=using, lock=None)
            .values_list("run_id", flat=True)
            .get(pk=discovered["step_run_id"])
        )
        run = system_queryset(run_model, using=using, lock=("self",)).get(pk=run_id)
        step_run = system_queryset(step_run_model, using=using, lock=("self",)).get(pk=discovered["step_run_id"])
        attempt = system_queryset(attempt_model, using=using, lock=("self",)).get(
            pk=discovered["suspension_attempt_id"]
        )
        decision = (
            system_queryset(self.model, using=using, lock=("self",))
            .select_related("step_run__run", "step_run__step")
            .get(pk=decision_id)
        )
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
            self.ensure_sequential_turn(decision)
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
            self.ensure_sequential_turn(decision)
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
                system_queryset(self.model, using=alias, lock=("self",))
                .filter(
                    suspension_attempt_id=decision.suspension_attempt_id,
                    verdict=Verdict.PENDING,
                )
                .order_by("priority", "pk")
            )
            for pending_decision in pending:
                self._write_retained(
                    pending_decision,
                    lambda pending_decision=pending_decision: pending_decision.resolve(
                        Verdict.EXPIRED, resolution={}, resolved_by=resolved_by
                    ),
                )
            return decision

    def expire_departing_suspension(self, step_run_id: int, *, resolved_by: str) -> int:
        """Expire the exact current suspension before its step becomes runnable again."""

        return self._expire_exact_suspension(step_run_id, resolved_by=resolved_by, transition="depart")

    def _expire_exact_suspension(
        self, step_run_id: int, *, resolved_by: str, transition: Literal["depart", "cancel"]
    ) -> int:
        """Expire one applied current suspension under its exact lifecycle transition."""

        alias = self.db
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        attempt_model = self.model._meta.get_field("suspension_attempt").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        with transaction.atomic(using=alias), system_context(reason=f"workflows.decision.{transition}"):
            run_id = (
                system_queryset(step_run_model, using=alias, lock=None)
                .values_list("run_id", flat=True)
                .get(pk=step_run_id)
            )
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
            step_run = system_queryset(step_run_model, using=alias, lock=("self",)).get(pk=step_run_id)
            expected_status = StepRunStatus.WAITING if transition == "depart" else StepRunStatus.CANCELED
            transition_label = "departure" if transition == "depart" else "cancellation"
            if step_run.run_id != run.pk or step_run.status != expected_status:
                raise ValidationError(
                    {"step_run": f"Decision {transition_label} requires a {expected_status} retained slot."}
                )
            if transition == "depart" and run.is_terminal:
                raise ValidationError({"step_run": "Decision departure requires a current waiting slot."})
            if step_run.current_attempt_id is None:
                return 0
            attempt = system_queryset(attempt_model, using=alias, lock=("self",)).get(pk=step_run.current_attempt_id)
            if (
                attempt.step_run_id != step_run.pk
                or attempt.result_kind != str(AttemptResultKind.SUSPEND)
                or attempt.applied_at is None
                or attempt.lease_revoked_at is not None
            ):
                raise ValidationError(
                    {"attempt": f"Decision {transition_label} requires the applied current suspension."}
                )
            pending = list(
                system_queryset(self.model, using=alias, lock=("self",))
                .filter(
                    suspension_attempt=attempt,
                    verdict=Verdict.PENDING,
                )
                .order_by("pk")
            )
            for decision in pending:
                self._write_retained(
                    decision,
                    lambda decision=decision: decision.resolve(Verdict.EXPIRED, resolution={}, resolved_by=resolved_by),
                )
            return len(pending)

    def expire_orphaned_suspensions(self, step_run_id: int, *, resolved_by: str) -> int:
        """Expire pending retained Decisions that no longer own an active suspension."""

        alias = self.db
        step_run_model = self.model._meta.get_field("step_run").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        with transaction.atomic(using=alias), system_context(reason="workflows.decision.expire_orphans"):
            run_id = (
                system_queryset(step_run_model, using=alias, lock=None)
                .values_list("run_id", flat=True)
                .get(pk=step_run_id)
            )
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
            step_run = system_queryset(step_run_model, using=alias, lock=("self",)).get(pk=step_run_id)
            pending = list(
                system_queryset(self.model, using=alias, lock=("self",))
                .filter(
                    step_run=step_run,
                    suspension_attempt__isnull=False,
                    verdict=Verdict.PENDING,
                )
                .select_related("suspension_attempt")
                .order_by("pk")
            )
            expired = 0
            for decision in pending:
                attempt = decision.suspension_attempt
                current_active = (
                    not run.is_terminal
                    and step_run.status == StepRunStatus.WAITING
                    and step_run.current_attempt_id == attempt.pk
                    and attempt.result_kind == str(AttemptResultKind.SUSPEND)
                    and attempt.applied_at is not None
                    and attempt.lease_revoked_at is None
                )
                if current_active:
                    continue
                self._write_retained(
                    decision,
                    lambda decision=decision: decision.resolve(Verdict.EXPIRED, resolution={}, resolved_by=resolved_by),
                )
                expired += 1
            return expired

    def expire_canceled_suspension(self, step_run_id: int, *, resolved_by: str) -> int:
        """Expire pending decisions only after their retained suspension was canceled."""

        return self._expire_exact_suspension(step_run_id, resolved_by=resolved_by, transition="cancel")

    def timer_intents_for(self, *, attempt: Any, using: str) -> tuple[DecisionTimerIntent, ...]:
        """Reconstruct deterministic post-commit work for an applied suspension."""

        decisions = (
            system_queryset(self.model, using=using, lock=None)
            .filter(suspension_attempt=attempt)
            .order_by("declaration_index")
        )
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
        if not isinstance(rebac_backend(), LocalBackend):
            raise ValidationError(
                {"rebac": "Decision-scoped review access requires the transactional local REBAC adapter."}
            )
        if not _attempt_write_active(using, step_run.pk):
            raise RuntimeError("Retained decisions require an active StepAttemptManager transaction.")

        declarations = deserialize_decision_specs(serialize_decision_specs(declarations))

        prepared_rows: list[tuple[Any, ...]] = []
        for spec in declarations:
            delegated_actor, target_authority = self._target_actor(
                spec,
                step_run=step_run,
                attempt=attempt,
                using=using,
            )
            grant_actor = step_run.run.admission_actor()
            if grant_actor is None:
                raise ValidationError({"actor": "Decision delegation requires the admitted run actor."})
            prepared_rows.append(
                (
                    spec,
                    tuple(canonical_subject_ref(subject) for subject in spec.assignees),
                    canonical_subject_ref(spec.requester) if spec.requester else None,
                    tuple(canonical_subject_ref(subject) for subject in spec.escalation),
                    self._validated_target(spec, actor=delegated_actor or grant_actor),
                    self._validated_record_access(
                        spec,
                        actor=grant_actor,
                        step_run=step_run,
                        attempt=attempt,
                        using=using,
                    ),
                    grant_actor,
                    target_authority,
                )
            )
        prepared = tuple(prepared_rows)
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
            manager = self.db_manager(using)
            for index, (
                spec,
                assignees,
                requester,
                escalation,
                target,
                record_access,
                grant_actor,
                target_authority,
            ) in enumerate(prepared):
                contract = compile_decision_action_schema(spec.decision_schema)
                if contract is not None:
                    contract.validate_context(spec.payload)
                token = _decision_write_session.set(_DecisionWriteSession(using, step_run.pk, attempt.pk, index))
                try:
                    decision = manager.create(
                        step_run=step_run,
                        suspension_attempt=attempt,
                        declaration_index=index,
                        priority=spec.priority,
                        action=spec.action,
                        payload=spec.payload,
                        target_model=target[0],
                        target_id=target[1],
                        target_tab=spec.target_tab,
                        target_authority_decision=target_authority,
                        record_access=[
                            {
                                "resource_type": ref.resource_type,
                                "resource_id": ref.resource_id,
                                **({"authority_decision_id": prior.sqid} if prior is not None else {}),
                            }
                            for _, ref, _, prior in record_access
                        ],
                        max_attempts=spec.max_attempts,
                        expires_at=spec.expires_at,
                        escalate_at=spec.escalate_at,
                    )
                finally:
                    _decision_write_session.reset(token)
                resource = to_object_ref(decision)
                relationships = [
                    RelationshipTuple(resource=resource, relation="assignee", subject=subject) for subject in assignees
                ]
                if requester is not None:
                    relationships.append(RelationshipTuple(resource=resource, relation="requester", subject=requester))
                relationships.extend(
                    RelationshipTuple(resource=resource, relation="escalation", subject=subject)
                    for subject in escalation
                )
                if relationships:
                    write_relationships(relationships)
                decision_subject = self._pending_decision_subject(decision)
                for record, _, record_actor, _ in record_access:
                    with actor_context(record_actor):
                        record.grant_record_access("pending_decision", decision_subject)
                decisions.append(decision)
                if spec.escalate_at is not None:
                    timer_intents.append(
                        DecisionTimerIntent(
                            DecisionTimerKind.ESCALATE, decision.pk, decision.attempts, spec.escalate_at
                        )
                    )
                if spec.expires_at is not None:
                    timer_intents.append(
                        DecisionTimerIntent(DecisionTimerKind.EXPIRE, decision.pk, decision.attempts, spec.expires_at)
                    )
        return tuple(decisions), tuple(timer_intents)

    @staticmethod
    def _pending_decision_subject(decision: Any) -> SubjectRef:
        ref = to_object_ref(decision)
        return SubjectRef.of(ref.resource_type, ref.resource_id)

    @staticmethod
    def _validated_record_access(
        spec: DecisionSpec,
        *,
        actor: Any,
        step_run: Any,
        attempt: Any,
        using: str,
    ) -> tuple[tuple[Any, Any, Any, Any | None], ...]:
        """Resolve and deduplicate the exact opting-in review records."""

        selected: dict[tuple[str, str], tuple[Any, Any, Any, Any | None]] = {}
        for declared in spec.record_access:
            record_actor, prior = (
                DecisionManager._resolve_admitted_input_authority(
                    input_source=declared.authority_input.source,
                    authority_path=declared.authority_input.path,
                    proposal_gate_path=declared.authority_input.proposal_gate_path,
                    step_run=step_run,
                    attempt=attempt,
                    using=using,
                )
                if declared.authority_input is not None
                else (actor, None)
            )
            if record_actor is None:
                raise ValidationError({"record_access": "Decision review delegation has no admitted actor."})
            try:
                model = apps.get_model(declared.model)
            except (LookupError, ValueError) as error:
                raise ValidationError({"record_access": "Decision review record model is not installed."}) from error
            queryset = read_scoped_queryset(model, record_actor, action="read")
            if queryset is None:
                raise PermissionDenied("Decision review record is not readable by its admitted delegating actor.")
            record = instance_from_public_id(model, declared.id, queryset=queryset)
            if record is None:
                raise ValidationError({"record_access": "Decision review record was not found."})
            if "pending_decision" not in type(record).get_rebac_grantable():
                raise ValidationError(
                    {"record_access": "Decision review record has no pending_decision delegation owner."}
                )
            ref = to_object_ref(record)
            key = (ref.resource_type, ref.resource_id)
            existing = selected.get(key)
            actor_ref = canonical_subject_ref(str(to_subject_ref(record_actor)))
            prior_id = None if prior is None else prior.pk
            if existing is not None and (
                canonical_subject_ref(str(to_subject_ref(existing[2]))) != actor_ref
                or (None if existing[3] is None else existing[3].pk) != prior_id
            ):
                raise ValidationError(
                    {"record_access": "One record cannot use competing Decision delegation authority."}
                )
            with actor_context(record_actor):
                record.validate_record_access_target()
                record._require_record_access(type(record).record_access_permission("pending_decision"))
            selected[key] = (record, ref, record_actor, prior)
        return tuple(selected[key] for key in sorted(selected))

    @classmethod
    def _remove_pending_record_access(cls, decision: Any) -> None:
        """Remove only tuples retained for this terminal Decision, inside its verdict transaction."""

        subject = cls._pending_decision_subject(decision)
        for resource in _retained_record_access_refs(decision):
            delete_relationship(
                RelationshipTuple(
                    resource=resource,
                    relation="pending_decision",
                    subject=subject,
                )
            )

    @staticmethod
    def _target_actor(spec: DecisionSpec, *, step_run: Any, attempt: Any, using: str) -> tuple[Any | None, Any | None]:
        return DecisionManager._resolve_admitted_input_authority(
            input_source="attempt_input",
            authority_path=spec.target_authority_path,
            proposal_gate_path=spec.target_authority_gate_path,
            step_run=step_run,
            attempt=attempt,
            using=using,
        )

    @staticmethod
    def _resolve_admitted_input_authority(
        *,
        input_source: DecisionInputSource,
        authority_path: JsonPath,
        proposal_gate_path: tuple[str | int, ...],
        step_run: Any,
        attempt: Any,
        using: str,
    ) -> tuple[Any | None, Any | None]:
        """Prove one explicitly forwarded, exact settled gate Decision for target read."""

        if not authority_path:
            return None, None
        admitted_input, provenance, admitted_run, admitted_path = _admitted_decision_input(
            input_source=input_source,
            path=authority_path,
            step_run=step_run,
            attempt=attempt,
            using=using,
        )
        selected_id = json_value_at_path(admitted_input, admitted_path, field="authority_path")
        if not isinstance(selected_id, str) or not selected_id:
            raise ValidationError({"target": "Admitted target authority ID is missing."})
        leaf, remaining = _decision_source_leaf(
            provenance,
            admitted_path,
            recovery_source_attempt_id=admitted_run.recovery_source_attempt_id,
        )
        producer_attempt, producer_step = _admitted_source_attempt(
            leaf,
            run=admitted_run,
            alias=using,
            attempt_model=type(attempt),
        )
        source_path = leaf.get("path", [])
        if not isinstance(source_path, list) or any(type(part) not in {str, int} for part in source_path):
            raise ValidationError({"target": "Admitted target authority source path is invalid."})
        if producer_attempt.result_kind == str(AttemptResultKind.SUSPEND):
            if proposal_gate_path:
                raise ValidationError({"target": "Direct gate authority cannot name a proposal gate path."})
            gate_attempt = producer_attempt
            gate_decisions = list(
                system_queryset(
                    step_run._meta.apps.get_model("workflows", "Decision"),
                    using=using,
                    lock=None,
                )
                .filter(suspension_attempt=gate_attempt)
                .order_by("declaration_index")
            )
            gate_output = retained_gate_output(gate_attempt, gate_decisions)
            if gate_output is None or not json_values_equal(
                selected_id,
                json_value_at_path(gate_output, (*source_path, *remaining), field="authority_path"),
            ):
                raise ValidationError({"target": "Direct target authority differs from its settled gate."})
        elif producer_attempt.result_kind == str(AttemptResultKind.DONE):
            if not proposal_gate_path:
                raise ValidationError({"target": "Proposal authority needs its admitted gate path."})
            if leaf.get("kind") == "step_output" and (
                not producer_step.output_present or not json_values_equal(producer_step.output, producer_attempt.output)
            ):
                raise ValidationError({"target": "Current proposal output differs from retained evidence."})
            if not producer_attempt.output_present or not json_values_equal(
                selected_id,
                json_value_at_path(
                    producer_attempt.output,
                    (*source_path, *remaining),
                    field="authority_path",
                ),
            ):
                raise ValidationError({"target": "Proposal did not retain the selected authority ID."})
            gate_leaf, gate_remaining = _decision_source_leaf(
                producer_attempt.input_provenance,
                proposal_gate_path,
                recovery_source_attempt_id=producer_step.run.recovery_source_attempt_id,
            )
            gate_attempt, _ = _admitted_source_attempt(
                gate_leaf,
                run=producer_step.run,
                alias=using,
                attempt_model=type(attempt),
            )
            gate_decisions = list(
                system_queryset(
                    step_run._meta.apps.get_model("workflows", "Decision"),
                    using=using,
                    lock=None,
                )
                .filter(suspension_attempt=gate_attempt)
                .order_by("declaration_index")
            )
            gate_output = retained_gate_output(gate_attempt, gate_decisions)
            gate_path = gate_leaf.get("path", [])
            admitted_gate_value = json_value_at_path(
                producer_attempt.input,
                proposal_gate_path,
                field="authority_gate_path",
            )
            forwarded_id = (
                admitted_gate_value.get("decision_id") if isinstance(admitted_gate_value, dict) else admitted_gate_value
            )
            if (
                gate_output is None
                or not isinstance(gate_path, list)
                or any(type(part) not in {str, int} for part in gate_path)
                or forwarded_id != selected_id
                or not json_values_equal(
                    admitted_gate_value,
                    json_value_at_path(
                        gate_output,
                        (*gate_path, *gate_remaining),
                        field="authority_gate_path",
                    ),
                )
            ):
                raise ValidationError({"target": "Proposal did not admit the original settled gate."})
        else:
            raise ValidationError({"target": "Target authority needs a retained gate or one proposal producer."})
        decision_model = step_run._meta.apps.get_model("workflows", "Decision")
        prior = (
            system_queryset(decision_model, using=using, lock=None)
            .filter(
                sqid=selected_id,
                suspension_attempt=gate_attempt,
                verdict=Verdict.COMPLETED,
            )
            .first()
        )
        if (
            prior is None
            or prior.pk not in gate_attempt.decision_settlement.get("decision_ids", ())
            or prior.step_run_id == step_run.pk
        ):
            raise ValidationError({"target": "Selected target authority is not this exact completed gate."})
        try:
            subject = canonical_subject_ref(prior.resolved_by)
        except (TypeError, ValueError) as error:
            raise ValidationError({"target": "Decision target authority requires a human resolver."}) from error
        if get_user_model().objects.active_person_for_subject(subject) is None:
            raise ValidationError({"target": "Decision target authority requires a human resolver."})
        return subject, prior

    @staticmethod
    def _validated_target(spec: DecisionSpec, *, actor: Any | None = None) -> tuple[str, str]:
        """Resolve one declared related record through the execution actor's read scope."""

        return _canonical_decision_target(
            spec.target_model,
            spec.target_id,
            actor=actor,
        )


class WorkflowDispatchQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet[Any]):
    """Read durable delivery intents without exposing collection mutation bypasses."""

    def immutable_error(self, operation: str) -> Exception:
        """Keep the dispatch owner's collection-update and retention errors."""

        if operation in {"delete", "_raw_delete"}:
            return TypeError("Workflow dispatches are durable delivery evidence and cannot be deleted.")
        return TypeError("Workflow dispatches do not support collection updates.")

    def bulk_create(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise TypeError("Workflow dispatches do not support bulk_create().")

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        raise TypeError("Workflow dispatches do not support bulk_update().")


class WorkflowDispatchManager(AngeeManager.from_queryset(WorkflowDispatchQuerySet)):  # type: ignore[misc]
    """Schedule intents and own bounded publication telemetry."""

    @staticmethod
    def _advance_error_prefix(dispatch: Any) -> str:
        return f"Workflow advancement {dispatch.sqid} could not continue: "

    def _save(self, dispatch: Any, *, alias: str, **kwargs: Any) -> None:
        capability = _DispatchSaveCapability(
            _AtomicWriteCapability(alias, id(dispatch)),
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

        unresolved = system_queryset(self.model, using=using, lock=None).get(pk=dispatch_id)
        if unresolved.kind == WorkflowDispatchKind.ADVANCE:
            run_model = self.model._meta.get_field("run").remote_field.model
            locked_run = system_queryset(run_model, using=using, lock=("self",)).get(pk=unresolved.run_id)
            if locked_run.pk != unresolved.run_id:
                raise OperationalError("Advance dispatch ancestry changed while locking.")
        elif unresolved.kind == WorkflowDispatchKind.RUN_CANCEL:
            run_model = self.model._meta.get_field("run").remote_field.model
            locked_run = system_queryset(run_model, using=using, lock=("self",)).get(pk=unresolved.run_id)
            if locked_run.pk != unresolved.run_id:
                raise OperationalError("Run cancellation target changed while locking.")
        elif unresolved.kind == WorkflowDispatchKind.CHILD_CANCEL:
            run_model = self.model._meta.get_field("run").remote_field.model
            ancestry = (
                system_queryset(run_model, using=using, lock=None)
                .values(
                    "parent_step_run_id",
                    "parent_step_run__run_id",
                )
                .get(pk=unresolved.run_id)
            )
            if ancestry["parent_step_run_id"] is None or ancestry["parent_step_run__run_id"] is None:
                raise OperationalError("Owned-child cancellation lost its original parent slot.")
            parent = system_queryset(run_model, using=using, lock=("self",)).get(pk=ancestry["parent_step_run__run_id"])
            step_run_model = run_model._meta.apps.get_model("workflows", "StepRun")
            slot = system_queryset(step_run_model, using=using, lock=("self",)).get(pk=ancestry["parent_step_run_id"])
            child = system_queryset(run_model, using=using, lock=("self",)).get(pk=unresolved.run_id)
            if slot.run_id != parent.pk or child.parent_step_run_id != slot.pk:
                raise OperationalError("Owned-child cancellation ancestry changed while locking.")
        elif unresolved.kind == WorkflowDispatchKind.EXECUTE:
            attempt_model = self.model._meta.get_field("step_attempt").remote_field.model
            ancestry = (
                system_queryset(attempt_model, using=using, lock=None)
                .values("step_run_id", "step_run__run_id")
                .get(pk=unresolved.step_attempt_id)
            )
            step_run_model = attempt_model._meta.get_field("step_run").remote_field.model
            run_model = step_run_model._meta.get_field("run").remote_field.model
            locked_run = system_queryset(run_model, using=using, lock=("self",)).get(pk=ancestry["step_run__run_id"])
            locked_step_run = system_queryset(step_run_model, using=using, lock=("self",)).get(
                pk=ancestry["step_run_id"]
            )
            locked_attempt = system_queryset(attempt_model, using=using, lock=("self",)).get(
                pk=unresolved.step_attempt_id
            )
            if locked_step_run.run_id != locked_run.pk or locked_attempt.step_run_id != locked_step_run.pk:
                raise OperationalError("Execution dispatch ancestry changed while locking.")
        elif unresolved.kind == WorkflowDispatchKind.ARTIFACT_DELIVERY:
            # The sender inserts only this intent while holding domain locks.
            # Delivery scans subscribed runs after the sender commits.
            system_queryset(self.model, using=using, lock=("self",)).get(pk=dispatch_id)
        else:
            decision_model = self.model._meta.get_field("decision").remote_field.model
            ancestry = (
                system_queryset(decision_model, using=using, lock=None)
                .values("step_run_id", "step_run__run_id", "suspension_attempt_id")
                .get(pk=unresolved.decision_id)
            )
            step_run_model = decision_model._meta.get_field("step_run").remote_field.model
            run_model = step_run_model._meta.get_field("run").remote_field.model
            locked_run = system_queryset(run_model, using=using, lock=("self",)).get(pk=ancestry["step_run__run_id"])
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
                or (locked_attempt is not None and locked_attempt.step_run_id != locked_step_run.pk)
            ):
                raise OperationalError("Decision dispatch ancestry changed while locking.")
        dispatch = (
            system_queryset(self.model, using=using, lock=None).select_related("step_attempt").get(pk=dispatch_id)
        )
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
        if session is None or session.alias != alias or session.consumed or session.dispatch_id != dispatch_id:
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
        if dispatch.kind == WorkflowDispatchKind.ADVANCE and not fenced:
            run_model = self.model._meta.get_field("run").remote_field.model
            run = system_queryset(run_model, using=alias, lock=None).get(pk=dispatch.run_id)
            if run.status not in {RunStatus.FAILED, RunStatus.CANCELED} and run.error.startswith(
                self._advance_error_prefix(dispatch)
            ):
                run.error = ""
                run.save(using=alias, update_fields=["error", "updated_at"])
        dispatch.consumed_at = at
        self._save(dispatch, alias=alias, update_fields=["consumed_at", "updated_at"])
        return DispatchConsumption.FENCED if fenced else DispatchConsumption.CONSUMED

    def record_advance_error(self, dispatch_id: int, *, error: Exception) -> bool:
        """Expose one failed ADVANCE while preserving its durable retry intent."""

        alias = self.db
        unresolved = system_queryset(self.model, using=alias, lock=None).values("kind", "run_id").get(pk=dispatch_id)
        if unresolved["kind"] != WorkflowDispatchKind.ADVANCE or unresolved["run_id"] is None:
            raise ValidationError({"dispatch": "Advance errors require an ADVANCE intent."})
        run_model = self.model._meta.get_field("run").remote_field.model
        with transaction.atomic(using=alias), system_context(reason="workflows.dispatch.advance_error"):
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=unresolved["run_id"])
            dispatch = system_queryset(self.model, using=alias, lock=("self",)).get(pk=dispatch_id)
            if dispatch.kind != WorkflowDispatchKind.ADVANCE or dispatch.run_id != run.pk:
                raise OperationalError("Advance dispatch ancestry changed while locking.")
            if dispatch.consumed_at is not None or run.is_terminal:
                return False
            prefix = self._advance_error_prefix(dispatch)
            if run.error and not run.error.startswith(prefix):
                return False
            detail = " ".join(str(error).split()) or type(error).__name__
            message = f"{prefix}{detail}"[:2000]
            if run.error == message:
                return False
            run.error = message
            run.save(using=alias, update_fields=["error", "updated_at"])
            return True

    def schedule_advance(self, run: Any, *, available_at: datetime) -> Any:
        """Create one independent run advance after locking its owner."""

        alias = self.db
        run_model = self.model._meta.get_field("run").remote_field.model
        with transaction.atomic(using=alias), system_context(reason="workflows.dispatch.schedule_advance"):
            locked = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run.pk)
            dispatch = self.model(kind=WorkflowDispatchKind.ADVANCE, run=locked, available_at=available_at)
            self._save(dispatch, alias=alias, force_insert=True)
            return dispatch

    def schedule_artifact_delivery(self, resource: Any, *, available_at: datetime | None = None) -> Any:
        """Retain a domain-change intent without taking any Workflow run lock."""

        alias = self.db
        if not connections[alias].in_atomic_block:
            raise RuntimeError("Artifact delivery must share the domain transition transaction.")
        target = canonical_record_target(resource)
        if not isinstance(target.object_id, int):
            raise ValidationError({"resource": "Artifact delivery requires an integer record identity."})
        dispatch = self.model(
            kind=WorkflowDispatchKind.ARTIFACT_DELIVERY,
            artifact_content_type_id=target.content_type.pk,
            artifact_object_id=target.object_id,
            available_at=available_at or timezone.now(),
        )
        with system_context(reason="workflows.dispatch.schedule_artifact_delivery"):
            self._save(dispatch, alias=alias, force_insert=True)
        transaction.on_commit(enqueue_dispatch_publisher, using=alias)
        return dispatch

    def schedule_child_cancel(self, child: Any, *, available_at: datetime | None = None) -> tuple[Any, bool]:
        """Retain one owned-child cancellation while the canceling parent is locked."""

        alias = self.db
        if not connections[alias].in_atomic_block:
            raise RuntimeError("Owned-child cancellation must share the parent cancellation transaction.")
        run_model = self.model._meta.get_field("run").remote_field.model
        with system_context(reason="workflows.dispatch.child_cancel"):
            retained = system_queryset(run_model, using=alias, lock=None).get(pk=child.pk)
            if retained.parent_relation != ParentRelation.OWNED_CALL or retained.parent_step_run_id is None:
                raise ValidationError({"child": "Cancellation target is not an owned child call."})
            existing = (
                system_queryset(self.model, using=alias, lock=None)
                .filter(
                    kind=WorkflowDispatchKind.CHILD_CANCEL,
                    run_id=retained.pk,
                )
                .first()
            )
            if existing is not None:
                return existing, False
            dispatch = self.model(
                kind=WorkflowDispatchKind.CHILD_CANCEL,
                run=retained,
                available_at=available_at or timezone.now(),
            )
            self._save(dispatch, alias=alias, force_insert=True)
            transaction.on_commit(enqueue_dispatch_publisher, using=alias)
            return dispatch, True

    def schedule_run_cancel(
        self,
        step_run_id: int,
        run: Any,
        *,
        actor: Any,
        available_at: datetime | None = None,
    ) -> tuple[Any, bool]:
        """Retain one cross-run cancellation from an exact database command."""

        alias = self.db
        run_model = self.model._meta.get_field("run").remote_field.model
        attempt_model = run_model._meta.apps.get_model("workflows", "StepAttempt")
        owner_step, _input = attempt_model.objects.active_database_command_input(
            step_run_id,
            actor=actor,
        )
        with system_context(reason="workflows.dispatch.run_cancel"):
            retained = system_queryset(run_model, using=alias, lock=None).get(pk=run.pk)
            if retained.pk == owner_step.run_id:
                raise ValidationError({"run": "A workflow cannot defer cancellation of itself."})
            existing = (
                system_queryset(self.model, using=alias, lock=None)
                .filter(
                    kind=WorkflowDispatchKind.RUN_CANCEL,
                    run_id=retained.pk,
                )
                .first()
            )
            if existing is not None:
                return existing, False
            dispatch = self.model(
                kind=WorkflowDispatchKind.RUN_CANCEL,
                run=retained,
                available_at=available_at or timezone.now(),
            )
            try:
                with transaction.atomic(using=alias):
                    self._save(dispatch, alias=alias, force_insert=True)
            except IntegrityError:
                existing = (
                    system_queryset(self.model, using=alias, lock=None)
                    .filter(
                        kind=WorkflowDispatchKind.RUN_CANCEL,
                        run_id=retained.pk,
                    )
                    .first()
                )
                if existing is None:
                    raise
                return existing, False
            transaction.on_commit(enqueue_dispatch_publisher, using=alias)
            return dispatch, True

    def schedule_execute(self, attempt: Any) -> tuple[Any, bool]:
        """Ensure one execution intent using the attempt's immutable availability."""

        alias = self.db
        attempt_model = self.model._meta.get_field("step_attempt").remote_field.model
        row = system_queryset(attempt_model, using=alias, lock=None).values("step_run_id").get(pk=attempt.pk)
        step_run_model = attempt_model._meta.get_field("step_run").remote_field.model
        run_model = step_run_model._meta.get_field("run").remote_field.model
        run_id = (
            system_queryset(step_run_model, using=alias, lock=None)
            .values_list("run_id", flat=True)
            .get(pk=row["step_run_id"])
        )
        with transaction.atomic(using=alias), system_context(reason="workflows.dispatch.schedule_execute"):
            run = system_queryset(run_model, using=alias, lock=("self",)).get(pk=run_id)
            step_run = system_queryset(step_run_model, using=alias, lock=("self",)).get(pk=row["step_run_id"])
            locked = system_queryset(attempt_model, using=alias, lock=("self",)).get(pk=attempt.pk)
            if locked.step_run_id != step_run.pk or step_run.run_id != run.pk:
                raise OperationalError("Execution dispatch ancestry changed while locking.")
            existing = (
                system_queryset(self.model, using=alias, lock=("self",))
                .filter(kind=WorkflowDispatchKind.EXECUTE, step_attempt=locked)
                .first()
            )
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
        ancestry = (
            system_queryset(decision_model, using=alias, lock=None)
            .values("step_run_id", "step_run__run_id", "suspension_attempt_id")
            .get(pk=decision.pk)
        )
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
            if (
                locked.step_run_id != step_run.pk
                or step_run.run_id != run.pk
                or (attempt is not None and attempt.step_run_id != step_run.pk)
            ):
                raise OperationalError("Decision dispatch ancestry changed while locking.")
            existing = (
                system_queryset(self.model, using=alias, lock=("self",))
                .filter(kind=kind, decision=locked, generation=locked.attempts)
                .first()
            )
            if existing is not None:
                return existing, False
            available_at = locked.expires_at if kind == WorkflowDispatchKind.DECISION_EXPIRE else locked.escalate_at
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
                )
                .select_related("step_attempt")
                .order_by("next_send_at", "available_at", "pk")[:limit]
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
