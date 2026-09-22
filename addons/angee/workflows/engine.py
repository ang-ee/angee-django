"""Runtime engine for workflow runs.

This module is the single owner of workflow advancement. It creates and replays
the step-run journal, evaluates join rules, routes outcomes, claims work, and
records cancellation. Step implementations run only through retained
``execute_dispatch()`` deliveries, never inside ``advance_dispatch()``.
"""

from __future__ import annotations

import json
import logging
import traceback
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from jsonschema import Draft202012Validator
from pydantic import JsonValue
from pydantic import ValidationError as PydanticValidationError
from rebac import PermissionDenied, SubjectRef, current_actor, system_context
from rebac.actors import to_subject_ref

from angee.base.actors import actor_user_id
from angee.base.db import get_write_alias, related_on
from angee.base.identity import canonical_subject_ref
from angee.base.refs import CanonicalRecordTarget, canonical_record_target
from angee.workflows.attempts import (
    AttemptCause,
    AttemptInput,
    AttemptResult,
    AttemptResultKind,
    DecisionAttemptResult,
    DecisionSubmission,
    ExternalOperationPolicy,
    ExternalOperationRequest,
    FixtureRole,
    InvocationAdmission,
    JsonPresence,
    MapItemSource,
    RecoveryMode,
    WorkflowScope,
    map_child_input,
    workflow_result_terminal_match_error,
)
from angee.workflows.bindings import (
    BindingContext,
    SourceValue,
    UnavailableSource,
    binding_error_details,
    evaluate_binding,
    parse_binding,
)
from angee.workflows.dispatch import (
    DispatchPreflightDisposition,
    WorkflowDispatchKind,
    enqueue_dispatch_publisher,
)
from angee.workflows.managers import retained_gate_output
from angee.workflows.models import (
    JoinRule,
    RunOrigin,
    RunStatus,
    StepRunStatus,
    Verdict,
    WaitingKind,
)
from angee.workflows.steps import MapStep, StepExecutionMode, TransientStepError, heartbeat_timeout

VERDICT_PENDING = cast(Verdict, Verdict.PENDING)
VERDICT_COMPLETED = cast(Verdict, Verdict.COMPLETED)
VERDICT_REJECTED = cast(Verdict, Verdict.REJECTED)
VERDICT_ESCALATED = cast(Verdict, Verdict.ESCALATED)
VERDICT_EXPIRED = cast(Verdict, Verdict.EXPIRED)
DECISION_VERBS: dict[str, Verdict] = {
    "complete": VERDICT_COMPLETED,
    "reject": VERDICT_REJECTED,
    "escalate": VERDICT_ESCALATED,
}
logger = logging.getLogger(__name__)


def _exception_message(error: BaseException) -> str:
    """Preserve an exception's message or fall back to its concrete class."""

    message = str(error)
    return message if message.strip() else type(error).__name__


@dataclass(frozen=True, slots=True)
class WorkflowActorResolution:
    """One canonical actor, assignee, or requester and its REBAC subject."""

    actor: Any | None
    subject: SubjectRef


@dataclass(frozen=True, slots=True)
class _AttemptPreparation:
    input: AttemptInput
    failure: AttemptResult | None = None
    map_item: MapItemSource | None = None
    test_fixture: Any = None


def start(
    workflow: Any,
    subject: Any,
    actor: Any,
    *,
    trigger: Any = None,
    parent_step_run: Any = None,
    parent_relation: str = "",
    dedup_key: str | None = None,
    origin: RunOrigin | None = None,
    input: JsonPresence = JsonPresence(),
    validate_new: Callable[[], None] | None = None,
    using: str | None = None,
) -> Any:
    """Start the current published version after validating its subject declaration.

    An empty subject declaration accepts any subject. A declared workflow raises
    ``ValidationError`` when the subject's concrete model differs. The manager
    checks the explicit actor's workflow permission before persisting engine
    rows; ``validate_new`` supplies an additional domain admission check.
    """

    alias = get_write_alias(apps.get_model("workflows", "WorkflowRun"), using=using, instance=workflow)

    run_model = apps.get_model("workflows", "WorkflowRun")
    return run_model.objects.db_manager(alias).start(
        workflow,
        subject,
        actor,
        trigger=trigger,
        parent_step_run=parent_step_run,
        parent_relation=parent_relation,
        dedup_key=dedup_key,
        origin=origin,
        input=input,
        validate_new=validate_new,
        using=alias,
    )


def recover(
    source_attempt: Any, *, request_key: str, actor: Any, prior_recovery: Any = None, using: str | None = None
) -> Any:
    """Start or recover one exact same-revision retained recovery request."""

    alias = get_write_alias(apps.get_model("workflows", "WorkflowRun"), using=using, instance=source_attempt)

    return (
        apps.get_model("workflows", "WorkflowRun")
        .objects.db_manager(alias)
        .start_recovery(
            source_attempt,
            request_key=request_key,
            actor=actor,
            prior_recovery=prior_recovery,
        )
    )


def deliver(run_id: int, *, now: datetime | None = None, using: str | None = None) -> dict[str, int]:
    """Deliver an external event by waking this run's parked journal rows.

    This is the workflow engine's event-delivery seam. Every delivery advances
    the run-scoped generation under the same short row lock as :func:`advance`,
    even when no row is waiting. A step that parks after observing an older
    generation is made immediately due by the retained dispatch path.
    """

    alias = get_write_alias(apps.get_model("workflows", "WorkflowRun"), using=using)

    timestamp = now or timezone.now()
    run_model = apps.get_model("workflows", "WorkflowRun")
    step_run_model = apps.get_model("workflows", "StepRun")
    woken = 0
    with system_context(reason="workflows.engine.deliver"), transaction.atomic(using=alias):
        run = run_model.objects.db_manager(alias).lock_execution_ancestry((run_id,))[run_id]
        if run.status in RunStatus.TERMINAL:
            return {"woken": 0}
        run.deliveries += 1
        run.save(update_fields=["deliveries", "updated_at"], using=alias)
        waiting = list(
            step_run_model.objects.db_manager(alias)
            .lock_if_supported()
            .filter(run=run, status=StepRunStatus.WAITING)
            .order_by("pk")
        )
        for step_run in waiting:
            apps.get_model("workflows", "StepAttempt").objects.db_manager(alias).wake_current(step_run.pk, at=timestamp)
            woken += 1
        if run.status == RunStatus.WAITING and waiting:
            run.resume(using=alias)
        apps.get_model("workflows", "WorkflowDispatch").objects.db_manager(alias).schedule_advance(
            run, available_at=timestamp
        )
        transaction.on_commit(lambda: enqueue_dispatch_publisher(using=alias), using=alias)
    return {"woken": woken}


def subscribe_external(step_run: Any, resources: Iterable[Any], *, using: str | None = None) -> None:
    """Commit this invocation's complete domain target set before its predicate read."""

    alias = get_write_alias(apps.get_model("workflows", "StepAttempt"), using=using, instance=step_run)

    attempt: Any = related_on(step_run, "current_attempt", using=alias)
    lease_token = attempt.lease_token if attempt is not None else None
    if step_run.current_attempt_id is None or not isinstance(lease_token, uuid.UUID):
        raise RuntimeError("External subscription requires a retained invocation lease.")
    apps.get_model("workflows", "StepAttempt").objects.db_manager(alias).subscribe_external(
        step_run.current_attempt_id,
        lease_token=lease_token,
        resources=resources,
    )


def schedule_artifact_delivery(resource: Any, *, using: str | None = None) -> Any:
    """Keep a domain event in its native transaction without locking a run."""

    alias = get_write_alias(
        apps.get_model("workflows", "WorkflowDispatch"),
        using=using,
        instance=resource.content_type if isinstance(resource, CanonicalRecordTarget) else resource,
    )

    return (
        apps.get_model("workflows", "WorkflowDispatch").objects.db_manager(alias).schedule_artifact_delivery(resource)
    )


def deliver_artifact(resource: Any, *, now: datetime | None = None, using: str | None = None) -> dict[str, int]:
    """Wake exact external waits whose current attempt retained ``resource``.

    Resource owners use this seam when one durable domain fact changes.  Unlike
    :func:`deliver`, it does not wake unrelated approval or timer rows that happen
    to share a workflow run with the external dependency.
    """

    timestamp = now or timezone.now()
    anchor = resource.content_type if isinstance(resource, CanonicalRecordTarget) else resource
    alias = get_write_alias(apps.get_model("workflows", "WorkflowRun"), using=using, instance=anchor)
    target = resource if isinstance(resource, CanonicalRecordTarget) else canonical_record_target(resource, using=alias)
    run_model = apps.get_model("workflows", "WorkflowRun")
    step_run_model = apps.get_model("workflows", "StepRun")
    artifact_model = apps.get_model("workflows", "StepArtifact")
    subscription_model = apps.get_model("workflows", "StepExternalSubscription")
    woken = 0
    delivered_run_ids: list[int] = []
    artifact_owner = artifact_model.objects.db_manager(alias)
    subscription_owner = subscription_model.objects.db_manager(alias)
    attempt_owner = apps.get_model("workflows", "StepAttempt").objects.db_manager(alias)
    step_run_owner = step_run_model.objects.db_manager(alias)
    run_owner = run_model.objects.db_manager(alias)
    with system_context(reason="workflows.engine.deliver_artifact"), transaction.atomic(using=alias):
        artifact_step_ids = list(
            artifact_owner.filter(
                target_content_type_id=target.content_type.pk,
                target_object_id=target.object_id,
                attempt__step_run__current_attempt_id=models.F("attempt_id"),
                attempt__step_run__status=StepRunStatus.WAITING,
                attempt__step_run__waiting_kind=WaitingKind.EXTERNAL,
            )
            .order_by()
            .values_list("attempt__step_run_id", flat=True)
            .distinct()
        )
        subscribed_attempt_ids = set(
            subscription_owner.filter(
                target_content_type_id=target.content_type.pk,
                target_object_id=target.object_id,
                attempt__step_run__current_attempt_id=models.F("attempt_id"),
                attempt__step_run__status__in=[StepRunStatus.STARTED, StepRunStatus.WAITING],
                attempt__lease_revoked_at__isnull=True,
            )
            .order_by()
            .values_list("attempt_id", flat=True)
        )
        bound_attempt_ids = set(
            attempt_owner.filter(
                external_content_type_id=target.content_type.pk,
                external_object_id=target.object_id,
                step_run__current_attempt_id=models.F("pk"),
                step_run__status__in=[StepRunStatus.STARTED, StepRunStatus.WAITING],
                lease_revoked_at__isnull=True,
            )
            .order_by()
            .values_list("pk", flat=True)
        )
        subscribed_step_ids = list(
            attempt_owner.filter(
                pk__in=subscribed_attempt_ids | bound_attempt_ids,
            )
            .order_by()
            .values_list("step_run_id", flat=True)
        )
        candidate_step_ids = sorted(set(artifact_step_ids).union(subscribed_step_ids))
        candidate_run_ids = list(
            step_run_owner.filter(
                pk__in=candidate_step_ids,
            )
            .order_by()
            .values_list("run_id", flat=True)
            .distinct()
        )
        runs = run_owner.lock_execution_ancestry(candidate_run_ids)
        step_runs = list(
            step_run_owner.lock_if_supported()
            .filter(
                pk__in=candidate_step_ids,
                status__in=[StepRunStatus.STARTED, StepRunStatus.WAITING],
            )
            .select_related("current_attempt")
            .order_by("run_id", "pk")
        )
        eligible_step_runs = [
            step_run
            for step_run in step_runs
            if (run := runs.get(step_run.run_id)) is not None
            and run.status not in RunStatus.TERMINAL
            and step_run.current_attempt_id is not None
        ]
        attempt_ids = {step_run.current_attempt_id for step_run in eligible_step_runs}
        attempts = {
            attempt.pk: attempt
            for attempt in attempt_owner.lock_if_supported().filter(pk__in=attempt_ids).order_by("pk")
        }
        retained_attempt_ids = set(
            artifact_owner.filter(
                attempt_id__in=attempt_ids,
                target_content_type_id=target.content_type.pk,
                target_object_id=target.object_id,
            )
            .order_by()
            .values_list("attempt_id", flat=True)
            .distinct()
        )
        touched: set[int] = set()
        for step_run in eligible_step_runs:
            run = runs[step_run.run_id]
            attempt = attempts.get(step_run.current_attempt_id)
            if attempt is None:
                continue
            subscribed = (
                attempt.pk in subscribed_attempt_ids or attempt.pk in bound_attempt_ids
            ) and attempt.lease_revoked_at is None
            retained_artifact = step_run.status == StepRunStatus.WAITING and attempt.pk in retained_attempt_ids
            if not (subscribed or retained_artifact):
                continue
            if step_run.status == StepRunStatus.WAITING:
                if step_run.waiting_kind != WaitingKind.EXTERNAL:
                    continue
                attempt_owner.wake_current(step_run.pk, at=timestamp)
                woken += 1
                touched.add(run.pk)
            elif not subscribed or attempt.result_recorded_at is not None:
                continue
            touched.add(run.pk)
        for run_id in sorted(touched):
            run = runs[run_id]
            run.deliveries += 1
            run.save(using=alias, update_fields=["deliveries", "updated_at"])
            if run.status == RunStatus.WAITING:
                run.resume(using=alias)
            delivered_run_ids.append(run_id)
            apps.get_model("workflows", "WorkflowDispatch").objects.db_manager(alias).schedule_advance(
                run, available_at=timestamp
            )
        if delivered_run_ids:
            transaction.on_commit(lambda: enqueue_dispatch_publisher(using=alias), using=alias)
    return {"runs": len(delivered_run_ids), "woken": woken}


def deliver_artifact_dispatch(
    dispatch_id: int, *, now: datetime | None = None, using: str | None = None
) -> dict[str, int]:
    """Consume one committed domain intent and deliver to current subscribers."""

    timestamp = now or timezone.now()
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    alias = get_write_alias(dispatch_model, using=using)
    dispatch_owner = dispatch_model.objects.db_manager(alias)
    with system_context(reason="workflows.engine.deliver_artifact_dispatch"), transaction.atomic(using=alias):
        with dispatch_owner._owner_transition(
            dispatch_id=dispatch_id,
            lease_token=None,
            at=timestamp,
            using=alias,
        ) as preflight:
            if preflight.disposition != DispatchPreflightDisposition.READY:
                return {"runs": 0, "woken": 0}
            if (
                preflight.envelope.kind != WorkflowDispatchKind.ARTIFACT_DELIVERY
                or preflight.envelope.target_id != dispatch_id
            ):
                raise ValidationError({"dispatch": "Artifact delivery envelope is invalid."})
            dispatch = dispatch_owner.select_related("artifact_content_type").get(pk=dispatch_id)
            target = CanonicalRecordTarget(
                dispatch.artifact_content_type,
                dispatch.artifact_object_id,
            )
            outcome = deliver_artifact(target, now=timestamp, using=alias)
            dispatch_owner._consume_locked(dispatch_id, envelope=preflight.envelope, at=timestamp, alias=alias)
    return outcome


def cancel_child_dispatch(
    dispatch_id: int, *, expected_child_id: int | None = None, using: str | None = None
) -> dict[str, int]:
    """Deliver one persisted owned-child cancellation through the run owner."""

    alias = get_write_alias(apps.get_model("workflows", "WorkflowRun"), using=using)

    return (
        apps.get_model("workflows", "WorkflowRun")
        .objects.db_manager(alias)
        .cancel_from_dispatch(
            dispatch_id,
            kind=WorkflowDispatchKind.CHILD_CANCEL,
            expected_run_id=expected_child_id,
        )
    )


def schedule_run_cancel(step_run: Any, run: Any, *, actor: Any, using: str | None = None) -> tuple[Any, bool]:
    """Retain one cross-run cancellation from this fenced database command."""

    alias = get_write_alias(apps.get_model("workflows", "WorkflowDispatch"), using=using, instance=step_run)
    attempt: Any = related_on(step_run, "current_attempt", using=alias)
    if attempt is None:
        raise apps.get_model("workflows", "StepAttempt").DoesNotExist("StepRun has no current attempt.")

    return (
        apps.get_model("workflows", "WorkflowDispatch")
        .objects.db_manager(alias)
        .schedule_run_cancel(
            step_run.pk,
            run,
            actor=actor,
            lease_token=attempt.lease_token,
        )
    )


def cancel_run_dispatch(
    dispatch_id: int, *, expected_run_id: int | None = None, using: str | None = None
) -> dict[str, int]:
    """Deliver one persisted cross-run cancellation through the run owner."""

    alias = get_write_alias(apps.get_model("workflows", "WorkflowRun"), using=using)

    return (
        apps.get_model("workflows", "WorkflowRun")
        .objects.db_manager(alias)
        .cancel_from_dispatch(
            dispatch_id,
            kind=WorkflowDispatchKind.RUN_CANCEL,
            expected_run_id=expected_run_id,
        )
    )


def settle_run_dispatch(
    dispatch_id: int, *, expected_run_id: int | None = None, using: str | None = None
) -> dict[str, int]:
    """Deliver a terminal subject settlement through the retained run owner."""

    model = apps.get_model("workflows", "WorkflowRun")
    alias = get_write_alias(model, using=using)
    return model.objects.db_manager(alias).settle_from_dispatch(
        dispatch_id, expected_run_id=expected_run_id, using=alias,
    )


def advance(run_id: int, *, now: datetime | None = None, using: str | None = None) -> dict[str, int]:
    """Create and synchronously consume one durable orchestration pulse."""

    timestamp = now or timezone.now()
    run_model = apps.get_model("workflows", "WorkflowRun")
    alias = get_write_alias(run_model, using=using)
    with system_context(reason="workflows.engine.advance.schedule"), transaction.atomic(using=alias):
        run = run_model.objects.db_manager(alias).get(pk=run_id)
        dispatch = (
            apps.get_model("workflows", "WorkflowDispatch")
            .objects.db_manager(alias)
            .schedule_advance(run, available_at=timestamp)
        )
    return advance_dispatch(dispatch.pk, expected_run_id=run_id, now=timestamp, using=alias)


def advance_dispatch(
    dispatch_id: int, *, expected_run_id: int | None = None, now: datetime | None = None, using: str | None = None
) -> dict[str, int]:
    """Apply one durable ADVANCE delivery through its exact owner preflight."""

    timestamp = now or timezone.now()
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    alias = get_write_alias(dispatch_model, using=using)
    dispatch_owner = dispatch_model.objects.db_manager(alias)
    admitted = False
    try:
        with system_context(reason="workflows.engine.advance_dispatch"), transaction.atomic(using=alias):
            with dispatch_owner._owner_transition(
                dispatch_id=dispatch_id, lease_token=None, at=timestamp, using=alias
            ) as preflight:
                if preflight.disposition != DispatchPreflightDisposition.READY:
                    return {"claimed": 0}
                if expected_run_id is not None and preflight.envelope.target_id != expected_run_id:
                    raise ValidationError({"dispatch": "ADVANCE envelope target does not match its durable intent."})
                if preflight.envelope.kind != WorkflowDispatchKind.ADVANCE:
                    raise ValidationError({"dispatch": "ADVANCE envelope kind does not match its durable intent."})
                admitted = True
                run = (
                    apps.get_model("workflows", "WorkflowRun")
                    .objects.db_manager(alias)
                    .select_related("workflow__error_workflow", "recovery_source_attempt__step_run")
                    .get(pk=preflight.envelope.target_id)
                )
                claimed_ids: list[int] = []
                if run.status not in RunStatus.TERMINAL:
                    _activate_run_if_needed(run, timestamp=timestamp, alias=alias)
                    _route_completed_steps(run, alias=alias)
                    _process_recovery_map_aggregate(run, timestamp=timestamp, alias=alias)
                    _route_completed_steps(run, alias=alias)
                    if _process_map_steps(run, timestamp=timestamp, alias=alias):
                        _route_completed_steps(run, alias=alias)
                        if not _fail_if_budget_exceeded(run, alias=alias):
                            claimed_ids = _claim_due_steps(run, timestamp=timestamp, alias=alias)
                            _update_run_status(run, timestamp=timestamp, alias=alias)
                dispatch_owner._consume_locked(dispatch_id, envelope=preflight.envelope, at=timestamp, alias=alias)
    except Exception as error:
        if admitted:
            try:
                dispatch_owner.record_advance_error(dispatch_id, error=error)
            except Exception:  # noqa: BLE001 - preserve the original advancement failure.
                logger.exception("Could not retain workflow ADVANCE failure visibility.")
        raise
    return {"claimed": len(claimed_ids)}


def external_operation_request(step_run: Any, *, using: str | None = None) -> ExternalOperationRequest:
    """Return the exact retained request identity for the current provider call."""

    alias = get_write_alias(apps.get_model("workflows", "StepAttempt"), using=using, instance=step_run)

    attempt: Any = related_on(
        step_run,
        "current_attempt",
        using=alias,
        select_related=("recovery_source_attempt", "step_run__run"),
    )
    if attempt is None or attempt.started_at is None:
        raise RuntimeError("External operations require a started retained attempt.")
    source = attempt.recovery_source_attempt
    return ExternalOperationRequest(
        request_key=str(source.effect_key if source is not None else attempt.effect_key),
        attempt_id=attempt.pk,
        input_present=attempt.input_present,
        input=attempt.input,
        recovery_source_attempt_id=None if source is None else source.pk,
        uncertainty_acknowledged=bool(attempt.step_run.run.recovery_uncertainty_ack),
    )


def resolve_workflow_actor(
    source: Any, *, require_person: bool = False, using: str | None = None
) -> WorkflowActorResolution:
    """Resolve run admission, actor, assignee, or requester through one owner."""

    alias = get_write_alias(
        get_user_model(), using=using, instance=source if isinstance(source, models.Model) else None
    )

    if hasattr(source, "admission_actor") and callable(source.admission_actor):
        if isinstance(source, models.Model):
            source._state.db = alias
        actor = source.admission_actor()
        if actor is None:
            raise ValidationError({"actor": "Workflow execution requires its admitted actor."})
        subject = to_subject_ref(actor)
    elif isinstance(source, str | SubjectRef):
        try:
            subject = canonical_subject_ref(str(source))
        except (TypeError, ValueError) as error:
            raise ValidationError({"actor": "Workflow execution requires a canonical subject."}) from error
        actor = None
    else:
        try:
            subject = to_subject_ref(source)
        except Exception as error:  # noqa: BLE001 - the REBAC adapter owns accepted actor types.
            raise ValidationError({"actor": "Workflow execution actor is invalid."}) from error
        actor = source
    if require_person:
        person = get_user_model().objects.db_manager(alias).active_person_for_subject(subject)
        if person is None:
            raise ValidationError({"actor": "Workflow Decision requires an active human actor."})
        actor = person
    return WorkflowActorResolution(actor=actor, subject=subject)


def execute_dispatch(
    dispatch_id: int, attempt_id: int, lease_token: Any, *, now: datetime | None = None, using: str | None = None
) -> dict[str, int]:
    """Execute one exact retained attempt after atomic dispatch and lease admission."""

    timestamp = now or timezone.now()
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    attempt_model = apps.get_model("workflows", "StepAttempt")
    alias = get_write_alias(dispatch_model, using=using)
    dispatch_owner = dispatch_model.objects.db_manager(alias)
    with system_context(reason="workflows.engine.execute_dispatch.admit"), transaction.atomic(using=alias):
        with dispatch_owner._owner_transition(
            dispatch_id=dispatch_id,
            lease_token=lease_token,
            at=timestamp,
            using=alias,
        ) as preflight:
            if preflight.disposition != DispatchPreflightDisposition.READY:
                return {"executed": 0}
            if preflight.envelope.kind != WorkflowDispatchKind.EXECUTE or preflight.envelope.target_id != attempt_id:
                raise ValidationError({"dispatch": "EXECUTE envelope does not match its durable intent."})
            admission = attempt_model.objects.db_manager(alias).admit_invocation(
                attempt_id, lease_token=lease_token, at=timestamp
            )
            dispatch_owner._consume_locked(
                dispatch_id,
                envelope=preflight.envelope,
                at=timestamp,
                fenced=admission != InvocationAdmission.FIRST_START,
                alias=alias,
            )
            if admission != InvocationAdmission.FIRST_START:
                return {"executed": 0}

    with system_context(reason="workflows.engine.execute_dispatch.load"):
        attempt = (
            attempt_model.objects.db_manager(alias)
            .select_related("step_run__run", "step_run__step", "recovery_source_attempt")
            .get(pk=attempt_id)
        )
        step_run = attempt.step_run
        impl_class = step_run.step.resolve_impl("step_class")

    def invoke(owned_step_run: Any, owned_attempt: Any) -> AttemptResult:
        # Overrides retain their public signature and inherit the admitted writer.
        owned_step_run._state.db = alias
        owned_step_run.run._state.db = alias
        owned_step_run.input = owned_attempt.input if owned_attempt.input_present else None
        owned_step_run.current_attempt = owned_attempt
        if impl_class.execution_mode == StepExecutionMode.EXTERNAL_OPERATION:
            policy = impl_class.external_operation_policy(attempt=owned_attempt)
            if not isinstance(policy, ExternalOperationPolicy):
                raise ValidationError(
                    {"operation": "External operation policy must be a declared provider capability."}
                )
        implementation = cast(Any, impl_class)()
        if owned_attempt.cause == AttemptCause.MANUAL_RETRY:
            recovery_mode = RecoveryMode(owned_attempt.recovery_mode)
            capability = impl_class.recovery_capability(attempt=owned_attempt.recovery_source_attempt)
            if capability.mode is not recovery_mode:
                raise ValidationError({"recovery": "The operation's recovery capability changed after admission."})
            step_result = implementation.run_recovery(
                owned_step_run,
                now=timestamp,
                source_attempt=owned_attempt.recovery_source_attempt,
                mode=recovery_mode,
            )
        else:
            step_result = implementation.run(owned_step_run, now=timestamp)
        result = (
            step_result.to_attempt_result() if step_result is not None else AttemptResult(AttemptResultKind.NO_RESULT)
        )
        attempt_model.objects.db_manager(alias).validate_result(result)
        return result

    def schedule_result(finalization: Any) -> None:
        if finalization.retry_intent is not None:
            successor = attempt_model.objects.db_manager(alias).get(pk=finalization.retry_intent.attempt_id)
            dispatch_model.objects.db_manager(alias).schedule_execute(successor)
        for intent in finalization.timer_intents:
            decision = apps.get_model("workflows", "Decision").objects.db_manager(alias).get(pk=intent.decision_id)
            kind = (
                WorkflowDispatchKind.DECISION_ESCALATE
                if intent.kind.value == "escalate"
                else WorkflowDispatchKind.DECISION_EXPIRE
            )
            dispatch_model.objects.db_manager(alias).schedule_decision(kind, decision)
        if finalization.recorded and finalization.applied and finalization.retry_intent is None:
            projected: Any = related_on(attempt, "step_run", using=alias, select_related=("run",))
            dispatch_model.objects.db_manager(alias).schedule_advance(projected.run, available_at=timezone.now())
            if projected.status == StepRunStatus.WAITING and projected.wait_until is not None:
                dispatch_model.objects.db_manager(alias).schedule_advance(
                    projected.run, available_at=projected.wait_until
                )
        transaction.on_commit(lambda: enqueue_dispatch_publisher(using=alias), using=alias)

    mode = impl_class.execution_mode
    try:
        if mode == StepExecutionMode.DATABASE_COMMAND:
            with transaction.atomic(using=alias):
                finalization = attempt_model.objects.db_manager(alias).execute_database_command(
                    attempt_id,
                    lease_token=lease_token,
                    command=invoke,
                    recorded_at=timezone.now(),
                )
                if finalization is None:
                    return {"executed": 0}
                with system_context(reason="workflows.engine.execute_dispatch.database_command.dispatch"):
                    schedule_result(finalization)
            return {"executed": 1}
        result = invoke(step_run, attempt)
    except TransientStepError as error:
        result = AttemptResult(
            AttemptResultKind.TRANSIENT_ERROR,
            error=_exception_message(error),
            stacktrace=traceback.format_exc(),
        )
    except Exception as error:  # noqa: BLE001 - implementation failure is retained evidence.
        result = AttemptResult(
            AttemptResultKind.ERROR,
            error=_exception_message(error),
            stacktrace=traceback.format_exc(),
        )

    with system_context(reason="workflows.engine.execute_dispatch.finalize"), transaction.atomic(using=alias):
        finalization = attempt_model.objects.db_manager(alias).finalize(
            attempt_id,
            lease_token=lease_token,
            result=result,
            recorded_at=timezone.now(),
        )
        schedule_result(finalization)
    return {"executed": 1}


def cancel(run: Any, *, actor: Any, using: str | None = None) -> None:
    """Dispatch an explicitly authorized cancellation to the run owner."""

    alias = get_write_alias(
        apps.get_model("workflows", "WorkflowRun"), using=using, instance=run if isinstance(run, models.Model) else None
    )

    apps.get_model("workflows", "WorkflowRun").objects.db_manager(alias).cancel(run, actor=actor)


def expire_pending_decisions(run: Any, *, resolved_by: str, using: str | None = None) -> int:
    """Expire every pending decision for ``run`` through the engine owner."""

    alias = get_write_alias(
        apps.get_model("workflows", "WorkflowRun"), using=using, instance=run if isinstance(run, models.Model) else None
    )

    run_model = apps.get_model("workflows", "WorkflowRun")
    step_run_model = apps.get_model("workflows", "StepRun")
    run_id = run.pk if hasattr(run, "pk") else int(run)
    expired = 0
    with system_context(reason="workflows.engine.expire_pending_decisions"), transaction.atomic(using=alias):
        locked_run = run_model.objects.db_manager(alias).lock_execution_ancestry((run_id,))[run_id]
        step_runs = step_run_model.objects.db_manager(alias).lock_if_supported().filter(run=locked_run).order_by("pk")
        for step_run in step_runs:
            expired += (
                apps.get_model("workflows", "Decision")
                .objects.db_manager(alias)
                .expire_pending(step_run.pk, resolved_by=resolved_by)
            )
            expired += (
                apps.get_model("workflows", "Decision")
                .objects.db_manager(alias)
                .expire_orphaned_suspensions(step_run.pk, resolved_by=resolved_by)
            )
    return expired


def expire_orphaned_decisions(run: Any, *, resolved_by: str, using: str | None = None) -> int:
    """Expire only pending retained Decisions whose suspension is no longer active."""

    alias = get_write_alias(
        apps.get_model("workflows", "WorkflowRun"), using=using, instance=run if isinstance(run, models.Model) else None
    )

    run_model = apps.get_model("workflows", "WorkflowRun")
    step_run_model = apps.get_model("workflows", "StepRun")
    run_id = run.pk if hasattr(run, "pk") else int(run)
    expired = 0
    with system_context(reason="workflows.engine.expire_orphaned_decisions"), transaction.atomic(using=alias):
        locked_run = run_model.objects.db_manager(alias).lock_execution_ancestry((run_id,))[run_id]
        step_runs = step_run_model.objects.db_manager(alias).lock_if_supported().filter(run=locked_run).order_by("pk")
        for step_run in step_runs:
            expired += (
                apps.get_model("workflows", "Decision")
                .objects.db_manager(alias)
                .expire_orphaned_suspensions(step_run.pk, resolved_by=resolved_by)
            )
    return expired


def sweep(*, now: datetime | None = None, using: str | None = None) -> dict[str, int]:
    """Advance runs whose durable wake time is due."""

    alias = get_write_alias(apps.get_model("workflows", "WorkflowRun"), using=using)

    timestamp = now or timezone.now()
    run_model = apps.get_model("workflows", "WorkflowRun")
    with system_context(reason="workflows.engine.sweep"):
        run_ids = list(
            run_model.objects.db_manager(alias)
            .filter(wake_at__lte=timestamp)
            .filter(status__in=[RunStatus.RUNNING, RunStatus.WAITING])
            .order_by("pk")
            .values_list("pk", flat=True)
        )
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    dispatch_ids: list[int] = []
    with system_context(reason="workflows.engine.sweep.schedule"), transaction.atomic(using=alias):
        for run_id in run_ids:
            run = run_model.objects.db_manager(alias).get(pk=run_id)
            dispatch = dispatch_model.objects.db_manager(alias).schedule_advance(run, available_at=timestamp)
            dispatch_ids.append(dispatch.pk)
        if run_ids:
            transaction.on_commit(lambda: enqueue_dispatch_publisher(using=alias), using=alias)
    for dispatch_id in dispatch_ids:
        advance_dispatch(dispatch_id, now=timestamp, using=alias)
    return {"runs": len(run_ids)}


def reap(*, now: datetime | None = None, using: str | None = None) -> dict[str, int]:
    """Fail started step-runs whose heartbeat is past the configured deadline."""

    alias = get_write_alias(apps.get_model("workflows", "StepRun"), using=using)

    timestamp = now or timezone.now()
    deadline = timestamp - heartbeat_timeout()
    step_run_model = apps.get_model("workflows", "StepRun")
    reaped = 0
    with system_context(reason="workflows.engine.reap.discover"):
        stale_ids = list(
            step_run_model.objects.db_manager(alias)
            .filter(status=StepRunStatus.STARTED)
            .filter(
                models.Q(current_attempt__isnull=False, current_attempt__heartbeat_at__lt=deadline)
                | models.Q(current_attempt__isnull=True, heartbeat_at__lt=deadline)
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )
    for step_run_id in stale_ids:
        with system_context(reason="workflows.engine.reap"), transaction.atomic(using=alias):
            step_run = step_run_model.objects.db_manager(alias).select_related("run").get(pk=step_run_id)
            if (
                apps.get_model("workflows", "StepAttempt")
                .objects.db_manager(alias)
                .timeout_current(step_run.pk, heartbeat_before=deadline, at=timestamp)
            ):
                apps.get_model("workflows", "WorkflowDispatch").objects.db_manager(alias).schedule_advance(
                    step_run.run, available_at=timestamp
                )
                transaction.on_commit(lambda: enqueue_dispatch_publisher(using=alias), using=alias)
                reaped += 1
    return {"reaped": reaped}


def decide(
    decision: Any, verdict: str, *, payload: Any = None, actor: Any = None, using: str | None = None
) -> DecisionAttemptResult:
    """Dispatch a public decision submission to its complete manager operation."""

    alias = get_write_alias(
        apps.get_model("workflows", "Decision"),
        using=using,
        instance=decision if isinstance(decision, models.Model) else None,
    )

    decision_id = decision.pk if hasattr(decision, "pk") else int(decision)
    return (
        apps.get_model("workflows", "Decision")
        .objects.db_manager(alias)
        .decide(
            decision_id,
            actor=_actor_ref(actor),
            resolution=DecisionSubmission(verdict=str(_verdict_for_verb(verdict)), payload=payload),
        )
    )


def escalate_decision_dispatch(
    dispatch_id: int,
    *,
    expected_decision_id: int | None = None,
    expected_generation: int | None = None,
    now: datetime | None = None,
    using: str | None = None,
) -> dict[str, int]:
    """Consume one exact durable escalation timer."""

    alias = get_write_alias(apps.get_model("workflows", "WorkflowDispatch"), using=using)

    return _consume_decision_dispatch(
        dispatch_id,
        WorkflowDispatchKind.DECISION_ESCALATE,
        expected_decision_id=expected_decision_id,
        expected_generation=expected_generation,
        now=now,
        alias=alias,
    )


def expire_decision_dispatch(
    dispatch_id: int,
    *,
    expected_decision_id: int | None = None,
    expected_generation: int | None = None,
    now: datetime | None = None,
    using: str | None = None,
) -> dict[str, int]:
    """Consume one exact durable expiry timer."""

    alias = get_write_alias(apps.get_model("workflows", "WorkflowDispatch"), using=using)

    return _consume_decision_dispatch(
        dispatch_id,
        WorkflowDispatchKind.DECISION_EXPIRE,
        expected_decision_id=expected_decision_id,
        expected_generation=expected_generation,
        now=now,
        alias=alias,
    )


def _consume_decision_dispatch(
    dispatch_id: int,
    kind: WorkflowDispatchKind,
    *,
    expected_decision_id: int | None,
    expected_generation: int | None,
    now: datetime | None,
    alias: str,
) -> dict[str, int]:
    timestamp = now or timezone.now()
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    dispatch_owner = dispatch_model.objects.db_manager(alias)
    with system_context(reason="workflows.engine.decision_dispatch"), transaction.atomic(using=alias):
        with dispatch_owner._owner_transition(
            dispatch_id=dispatch_id, lease_token=None, at=timestamp, using=alias
        ) as preflight:
            if preflight.disposition != DispatchPreflightDisposition.READY:
                return {"resolved": 0}
            if (expected_decision_id is not None and preflight.envelope.target_id != expected_decision_id) or (
                expected_generation is not None and preflight.envelope.generation != expected_generation
            ):
                raise ValidationError({"dispatch": "Decision envelope does not match its durable intent."})
            if preflight.envelope.kind != kind or preflight.envelope.generation is None:
                raise ValidationError({"dispatch": "Decision envelope kind does not match its durable intent."})
            verdict = VERDICT_ESCALATED if kind == WorkflowDispatchKind.DECISION_ESCALATE else VERDICT_EXPIRED
            resolved = (
                apps.get_model("workflows", "Decision")
                .objects.db_manager(alias)
                .resolve_timed(
                    preflight.envelope.target_id,
                    generation=preflight.envelope.generation,
                    verdict=verdict,
                    at=timestamp,
                )
            )
            dispatch_owner._consume_locked(
                dispatch_id, envelope=preflight.envelope, at=timestamp, fenced=not resolved, alias=alias
            )
            return {"resolved": int(resolved)}


def sweep_decisions(*, now: datetime | None = None, using: str | None = None) -> dict[str, int]:
    """Retain and consume dispatches for pending decisions whose deadlines are due."""

    alias = get_write_alias(apps.get_model("workflows", "Decision"), using=using)

    timestamp = now or timezone.now()
    decision_model = apps.get_model("workflows", "Decision")
    dispatches: list[tuple[int, WorkflowDispatchKind, int, int]] = []
    with system_context(reason="workflows.engine.decision_sweep"), transaction.atomic(using=alias):
        expired = list(
            decision_model.objects.db_manager(alias)
            .filter(verdict=VERDICT_PENDING, expires_at__lte=timestamp)
            .order_by("pk")
            .select_related("step_run__run")
        )
        escalated = list(
            decision_model.objects.db_manager(alias)
            .filter(verdict=VERDICT_PENDING, escalate_at__lte=timestamp)
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timestamp))
            .order_by("pk")
            .select_related("step_run__run")
        )
        dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
        for kind, decisions in (
            (WorkflowDispatchKind.DECISION_EXPIRE, expired),
            (WorkflowDispatchKind.DECISION_ESCALATE, escalated),
        ):
            for decision in decisions:
                if decision.suspension_attempt_id is None:
                    raise ValidationError({"decision": "Decision timers require retained suspension evidence."})
                dispatch, _created = dispatch_model.objects.db_manager(alias).schedule_decision(kind, decision)
                dispatches.append((dispatch.pk, kind, decision.pk, decision.attempts))
    expired_count = 0
    escalated_count = 0
    for dispatch_id, kind, decision_id, generation in dispatches:
        result = _consume_decision_dispatch(
            dispatch_id,
            kind,
            expected_decision_id=decision_id,
            expected_generation=generation,
            now=timestamp,
            alias=alias,
        )
        if kind == WorkflowDispatchKind.DECISION_EXPIRE:
            expired_count += result["resolved"]
        else:
            escalated_count += result["resolved"]
    return {"expired": expired_count, "escalated": escalated_count}


def override_run(run: Any, next_steps: Iterable[Any], *, actor: Any, using: str | None = None) -> Any:
    """Cancel active rows, insert an override journal row, and schedule next steps."""

    alias = get_write_alias(
        apps.get_model("workflows", "WorkflowRun"), using=using, instance=run if isinstance(run, models.Model) else None
    )

    run_model = apps.get_model("workflows", "WorkflowRun")
    step_run_model = apps.get_model("workflows", "StepRun")
    run_id = run.pk if hasattr(run, "pk") else int(run)
    actor_ref = _actor_ref(actor)
    actor_id = actor_user_id(actor_ref)
    step_ids = [step.pk if hasattr(step, "pk") else int(step) for step in next_steps]

    with system_context(reason="workflows.engine.override"), transaction.atomic(using=alias):
        locked = run_model.objects.db_manager(alias).lock_execution_ancestry((run_id,))[run_id]
        if locked.status in RunStatus.TERMINAL:
            raise ValidationError({"run": "A terminal workflow run cannot be overridden."})
        for step_run in (
            step_run_model.objects.db_manager(alias)
            .lock_if_supported()
            .filter(
                run=locked,
                status__in=list(StepRunStatus.ACTIVE),
            )
        ):
            if step_run.step_id in step_ids:
                step_run_model.objects.db_manager(alias).reschedule_for_override(
                    step_run.pk, input={}, at=timezone.now()
                )
            else:
                apps.get_model("workflows", "StepAttempt").objects.db_manager(alias).cancel_current(
                    step_run.pk, at=timezone.now()
                )
        override = step_run_model.objects.db_manager(alias).create(
            run_id=locked.pk,
            step=None,
            system_kind="override",
            status=StepRunStatus.SUCCEEDED,
            output={"next_steps": step_ids},
            outcome="override",
            created_by_id=actor_id,
            updated_by_id=actor_id,
        )
        for step_id in step_ids:
            row = step_run_model.objects.db_manager(alias).filter(run=locked, step_id=step_id, map_index=-1).first()
            if row is None:
                row = step_run_model.objects.db_manager(alias).create(
                    run_id=locked.pk,
                    step_id=step_id,
                    map_index=-1,
                    status=StepRunStatus.SCHEDULED,
                    input={},
                )
            elif row.status in StepRunStatus.TERMINAL:
                row = step_run_model.objects.db_manager(alias).reschedule_for_override(
                    row.pk, input={}, at=timezone.now()
                )
            step_run_model.objects.update_previous(row, [override], replace=True, using=alias)
        if locked.status == RunStatus.WAITING:
            locked.resume(using=alias)
        dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
        dispatch_model.objects.db_manager(alias).schedule_advance(locked, available_at=timezone.now())
        transaction.on_commit(lambda: enqueue_dispatch_publisher(using=alias), using=alias)
    return override


def enqueue_advance(run_id: int, *, using: str | None = None) -> None:
    """Retain an immediate advance and request transport publication."""

    alias = get_write_alias(apps.get_model("workflows", "WorkflowRun"), using=using)

    enqueue_advance_at(run_id, timezone.now(), using=alias)


def enqueue_advance_at(run_id: int, when: datetime, *, using: str | None = None) -> None:
    """Retain a timer wake before asking the transport to publish it."""

    alias = get_write_alias(apps.get_model("workflows", "WorkflowRun"), using=using)

    with system_context(reason="workflows.engine.schedule_advance"), transaction.atomic(using=alias):
        run = apps.get_model("workflows", "WorkflowRun").objects.db_manager(alias).filter(pk=run_id).first()
        if run is None:
            return
        apps.get_model("workflows", "WorkflowDispatch").objects.db_manager(alias).schedule_advance(
            run, available_at=when
        )
        transaction.on_commit(lambda: enqueue_dispatch_publisher(using=alias), using=alias)


def _actor_ref(actor: Any) -> SubjectRef:
    """Return the explicit actor for a resolution path."""

    if actor is None:
        actor = current_actor()
    if actor is None:
        raise PermissionDenied("Authentication required.")
    return actor if isinstance(actor, SubjectRef) else to_subject_ref(actor)


def _verdict_for_verb(verb: str) -> Verdict:
    """Return the stored terminal verdict for a public resolution verb."""

    value = str(getattr(verb, "value", verb)).lower()
    try:
        return DECISION_VERBS[value]
    except KeyError as error:
        raise ValidationError({"verdict": "Verdict must be complete, reject, or escalate."}) from error


def _activate_run_if_needed(run: Any, *, timestamp: datetime, alias: str) -> None:
    if run.status == RunStatus.PENDING:
        run.mark_running(using=alias)
    elif run.status == RunStatus.WAITING and _has_due_wait(run, timestamp=timestamp, alias=alias):
        run.resume(using=alias)


def _has_due_wait(run: Any, *, timestamp: datetime, alias: str) -> bool:
    return (
        run.step_runs.db_manager(alias)
        .filter(
            status=StepRunStatus.WAITING,
            wait_until__isnull=False,
            wait_until__lte=timestamp,
        )
        .exists()
    )


def _route_completed_steps(run: Any, *, alias: str) -> None:
    if run.origin == RunOrigin.TEST and run.test_scope == WorkflowScope.NODE:
        return
    for step_run in _terminal_step_runs(run, alias=alias):
        if step_run.step_id is None:
            continue
        if step_run.status == StepRunStatus.SUCCEEDED:
            _route_success(run, step_run, alias=alias)
        elif step_run.status == StepRunStatus.SKIPPED:
            _route_skip(run, step_run, alias=alias)
        elif step_run.status in {StepRunStatus.FAILED, StepRunStatus.CANCELED}:
            _route_done(run, step_run, alias=alias)


def _terminal_step_runs(run: Any, *, alias: str) -> Iterable[Any]:
    return (
        run.step_runs.db_manager(alias)
        .select_related("step")
        .filter(status__in=list(StepRunStatus.TERMINAL))
        .filter(map_index=-1)
        .filter(step__isnull=False)
        .order_by("pk")
    )


def _process_recovery_map_aggregate(run: Any, *, timestamp: datetime, alias: str) -> None:
    """Project a successful FRESH Map member through the ordinary Map join owner."""

    if run.origin != RunOrigin.RECOVERY or run.recovery_source_attempt_id is None:
        return
    source = run.recovery_source_attempt
    source_step_run = source.step_run
    if source_step_run.map_index < 0 or source.map_expansion_id is None:
        return
    recovered = (
        run.step_runs.db_manager(alias)
        .filter(
            step_id=source_step_run.step_id,
            map_index=source_step_run.map_index,
        )
        .first()
    )
    if recovered is None or recovered.status != StepRunStatus.SUCCEEDED:
        return
    apps.get_model("workflows", "StepAttempt").objects.db_manager(alias).record_recovery_map_aggregate(
        recovered.pk,
        at=timestamp,
    )


def _process_map_steps(run: Any, *, timestamp: datetime, alias: str) -> bool:
    locked_rows = list(run.step_runs.db_manager(alias).lock_if_supported().select_related("step").order_by("pk"))
    map_rows = [
        row
        for row in locked_rows
        if row.step_id is not None
        and row.step.step_class == "map"
        and row.map_index == -1
        and row.status in {StepRunStatus.SCHEDULED, StepRunStatus.WAITING}
    ]
    for step_run in map_rows:
        if step_run.status == StepRunStatus.SCHEDULED:
            fixture = (
                run.test_fixtures.db_manager(alias)
                .filter(
                    step_id=step_run.step_id,
                    role=FixtureRole.OUTPUT,
                    item_index__isnull=True,
                )
                .first()
                if run.origin == RunOrigin.TEST
                else None
            )
            if fixture is not None:
                apps.get_model("workflows", "StepAttempt").objects.db_manager(alias).record_test_fixture(
                    fixture, at=timestamp, due_step_run_id=step_run.pk
                )
                apps.get_model("workflows", "WorkflowDispatch").objects.db_manager(alias).schedule_advance(
                    run, available_at=timestamp
                )
                continue
            if not _expand_retained_map_step(run, step_run, timestamp=timestamp, alias=alias):
                return False
        if step_run.status == StepRunStatus.WAITING:
            if not _complete_retained_map_step_if_ready(run, step_run, timestamp=timestamp, alias=alias):
                return False
    return True


def _expand_retained_map_step(run: Any, step_run: Any, *, timestamp: datetime, alias: str) -> bool:
    """Retain one Map expansion generation before exposing any body slot."""

    recorded = (
        apps.get_model("workflows", "StepAttempt")
        .objects.db_manager(alias)
        .record_map_expansion(step_run, at=timestamp)
    )
    if recorded is None:
        return False
    expansion, plan = recorded
    target = (
        apps.get_model("workflows", "Step").objects.db_manager(alias).get(pk=plan.target_id)
        if plan.target_id is not None
        else None
    )
    items = plan.items
    if target is not None:
        _ensure_map_children(run, step_run, target=target, items=items, alias=alias)
        apps.get_model("workflows", "StepRun").objects.db_manager(alias).bind_map_membership(
            run_id=run.pk,
            target_id=target.pk,
            expansion_attempt_id=expansion.pk,
            item_count=len(items),
            at=timestamp,
        )
    if not items:
        return _complete_retained_map_step_if_ready(
            run, step_run, timestamp=timestamp, expansion_attempt_id=expansion.pk, alias=alias
        )
    return True


def _complete_retained_map_step_if_ready(
    run: Any, step_run: Any, *, timestamp: datetime, expansion_attempt_id: int | None = None, alias: str
) -> bool:
    """Aggregate only the exact members of the current retained expansion."""

    expansion_id = expansion_attempt_id or step_run.current_attempt_id
    if expansion_id is None:
        raise ValidationError({"attempt": "Retained Map controller has no current expansion."})
    expansion: Any = (
        apps.get_model("workflows", "StepAttempt").objects.db_manager(alias).get(pk=expansion_attempt_id)
        if expansion_attempt_id
        else related_on(step_run, "current_attempt", using=alias)
    )
    checkpoint = expansion.checkpoint if expansion.checkpoint_present else None
    map_state = checkpoint.get("map") if isinstance(checkpoint, dict) else None
    if isinstance(map_state, dict):
        target_id = map_state.get("target_step_id")
        items = map_state.get("items")
        if target_id is not None and isinstance(items, list):
            target = apps.get_model("workflows", "Step").objects.db_manager(alias).get(pk=target_id)
            # The membership owner validates the exact current expansion before
            # recovery is allowed to materialize any missing child rows.
            apps.get_model("workflows", "StepRun").objects.db_manager(alias).bind_map_membership(
                run_id=step_run.run_id,
                target_id=target.pk,
                expansion_attempt_id=expansion_id,
                item_count=len(items),
                at=timestamp,
            )
            _ensure_map_children(run, step_run, target=target, items=items, alias=alias)
            apps.get_model("workflows", "StepRun").objects.db_manager(alias).bind_map_membership(
                run_id=step_run.run_id,
                target_id=target.pk,
                expansion_attempt_id=expansion_id,
                item_count=len(items),
                at=timestamp,
            )
    apps.get_model("workflows", "StepAttempt").objects.db_manager(alias).record_map_aggregate(
        step_run.pk,
        expansion_attempt_id=expansion_id,
        at=timestamp,
    )
    return True


def _ensure_map_children(run: Any, step_run: Any, *, target: Any, items: list[Any], alias: str) -> None:
    step_run_model = apps.get_model("workflows", "StepRun")
    for index, item in enumerate(items):
        child, _ = step_run_model.objects.db_manager(alias).get_or_create(
            run_id=run.pk,
            step_id=target.pk,
            map_index=index,
            defaults={
                "status": StepRunStatus.SCHEDULED,
                "input": map_child_input(item),
            },
        )
        step_run_model.objects.update_previous(child, [step_run], using=alias)


def _route_success(run: Any, step_run: Any, *, alias: str) -> None:
    outgoing = list(
        apps.get_model("workflows", "Edge").objects.db_manager(alias)
        .filter(source_id=step_run.step_id).select_related("target").order_by("pk")
    )
    by_target: dict[int, tuple[Any, list[Any]]] = {}
    for edge in outgoing:
        target, edges = by_target.setdefault(edge.target_id, (edge.target, []))
        edges.append(edge)
    for target, edges in by_target.values():
        if any(not edge.condition or edge.condition == step_run.outcome for edge in edges):
            _maybe_schedule_target(run, target, alias=alias)
        elif target.incoming_edges.db_manager(alias).exclude(source_id=step_run.step_id).exists():
            _maybe_schedule_target(run, target, alias=alias)
        else:
            _ensure_skipped(run, target, previous=[step_run], alias=alias)


def _route_skip(run: Any, step_run: Any, *, alias: str) -> None:
    outgoing = list(
        apps.get_model("workflows", "Edge").objects.db_manager(alias)
        .filter(source_id=step_run.step_id).select_related("target").order_by("pk")
    )
    by_target: dict[int, tuple[Any, list[Any]]] = {}
    for edge in outgoing:
        target, edges = by_target.setdefault(edge.target_id, (edge.target, []))
        edges.append(edge)
    for target, edges in by_target.values():
        if (
            any(not edge.condition for edge in edges)
            or target.incoming_edges.db_manager(alias).exclude(source_id=step_run.step_id).exists()
        ):
            _maybe_schedule_target(run, target, alias=alias)
        else:
            _ensure_skipped(run, target, previous=[step_run], alias=alias)


def _route_done(run: Any, step_run: Any, *, alias: str) -> None:
    for edge in (
        apps.get_model("workflows", "Edge").objects.db_manager(alias)
        .filter(source_id=step_run.step_id).select_related("target").order_by("pk")
    ):
        if edge.condition and edge.condition != step_run.outcome:
            continue
        _maybe_schedule_target(run, edge.target, routed_row=step_run, alias=alias)


def _maybe_schedule_target(run: Any, target: Any, *, routed_row: Any | None = None, alias: str) -> Any | None:
    step_run_model = apps.get_model("workflows", "StepRun")
    existing = step_run_model.objects.db_manager(alias).filter(run=run, step=target, map_index=-1).first()
    if existing is not None:
        return existing
    previous, statuses = _upstream_join_state(run, target, routed_row=routed_row, alias=alias)
    if not statuses:
        return None
    decision = _join_decision(target.join_rule, statuses)
    if decision == "skip":
        return _ensure_skipped(run, target, previous=previous, alias=alias)
    if decision != "run":
        return None
    step_run = step_run_model.objects.db_manager(alias).create(
        run_id=run.pk,
        step_id=target.pk,
        map_index=-1,
        status=StepRunStatus.SCHEDULED,
        input=_input_from_previous(previous),
    )
    step_run_model.objects.update_previous(step_run, previous, replace=True, using=alias)
    return step_run


def _ensure_skipped(run: Any, step: Any, *, previous: list[Any], alias: str) -> Any:
    step_run_model = apps.get_model("workflows", "StepRun")
    step_run = (
        step_run_model.objects.db_manager(alias).select_related("step").filter(run=run, step=step, map_index=-1).first()
    )
    if step_run is None:
        step_run = step_run_model.objects.db_manager(alias).create(
            run_id=run.pk,
            step_id=step.pk,
            map_index=-1,
            status=StepRunStatus.SKIPPED,
            input=_input_from_previous(previous),
        )
        step_run_model.objects.update_previous(step_run, previous, replace=True, using=alias)
    elif step_run.status in {StepRunStatus.SCHEDULED, StepRunStatus.WAITING}:
        step_run.mark_skipped(using=alias)
    else:
        return step_run

    _route_skip(run, step_run, alias=alias)
    return step_run


def _upstream_join_state(
    run: Any, target: Any, *, routed_row: Any | None = None, alias: str
) -> tuple[list[Any], list[Any | None]]:
    """Return one conditional route contribution per predecessor step.

    Multiple edges from the same predecessor are alternatives.  A completed
    predecessor contributes only when at least one of those edges matches its
    outcome; an unmatched conditional route is absent rather than a skipped
    target.  Missing or active predecessors remain pending because their
    eventual outcome can still select the route.
    """

    step_run_model = apps.get_model("workflows", "StepRun")
    by_source: dict[int, list[Any]] = {}
    for edge in (
        apps.get_model("workflows", "Edge").objects.db_manager(alias)
        .filter(target_id=target.pk).select_related("source").order_by("pk")
    ):
        by_source.setdefault(edge.source_id, []).append(edge)
    previous: list[Any] = []
    statuses: list[Any | None] = []
    for source_id, edges in by_source.items():
        row = (
            step_run_model.objects.db_manager(alias)
            .select_related("step")
            .filter(
                run=run,
                step_id=source_id,
                map_index=-1,
            )
            .first()
        )
        if row is None:
            statuses.append(None)
            continue
        effective_status = StepRunStatus.SUCCEEDED if _same_step_run(row, routed_row) else row.status
        if effective_status in StepRunStatus.TERMINAL:
            route_matches = any(not edge.condition or edge.condition == row.outcome for edge in edges)
            if not route_matches:
                continue
        previous.append(row)
        statuses.append(effective_status)
    return previous, statuses


def _join_decision(rule: Any, statuses: list[Any | None]) -> str:
    if not statuses:
        return "run"

    terminal = [status in StepRunStatus.TERMINAL for status in statuses]
    has_missing_or_active = any(status is None or status not in StepRunStatus.TERMINAL for status in statuses)
    has_success = any(status == StepRunStatus.SUCCEEDED for status in statuses)
    has_done = any(status in StepRunStatus.TERMINAL for status in statuses)
    has_failed = any(status in {StepRunStatus.FAILED, StepRunStatus.CANCELED} for status in statuses)

    if rule == JoinRule.ALL_SUCCESS:
        if all(status == StepRunStatus.SUCCEEDED for status in statuses):
            return "run"
        if any(status in StepRunStatus.TERMINAL and status != StepRunStatus.SUCCEEDED for status in statuses):
            return "skip"
        return "wait"
    if rule == JoinRule.ONE_SUCCESS:
        if has_success:
            return "run"
        return "wait" if has_missing_or_active else "none"
    if rule == JoinRule.ONE_DONE:
        return "run" if has_done else "wait"
    if rule == JoinRule.ALL_DONE:
        return "run" if all(terminal) else "wait"
    if rule == JoinRule.NONE_FAILED:
        if has_failed:
            return "none"
        return "run" if not has_missing_or_active else "wait"
    if rule == JoinRule.NONE_FAILED_MIN_ONE_SUCCESS:
        if has_failed:
            return "none"
        if has_missing_or_active:
            return "wait"
        return "run" if has_success else "none"
    if rule == JoinRule.ALWAYS:
        return "run" if not has_missing_or_active else "wait"
    return "wait"


def _same_step_run(left: Any | None, right: Any | None) -> bool:
    """Return whether two optional step-run rows identify the same journal row."""

    return left is not None and right is not None and left.pk == right.pk


def _claim_due_steps(run: Any, *, timestamp: datetime, alias: str) -> list[int]:
    locked_rows = list(
        run.step_runs.db_manager(alias)
        .lock_if_supported()
        .select_related("step", "current_attempt", "current_map_expansion")
        .order_by("pk")
    )
    due = [
        row
        for row in locked_rows
        if (
            row.status == StepRunStatus.SCHEDULED
            or (row.status == StepRunStatus.WAITING and row.wait_until is not None and row.wait_until <= timestamp)
        )
        and not (row.step_id is not None and row.step.step_class == MapStep.key and row.map_index == -1)
        and run.allows_test_step(row.step, using=alias)
    ]
    if not due:
        return []
    fixture_by_slot: dict[tuple[int, int | None], Any] = {}
    if run.origin == RunOrigin.TEST:
        fixture_by_slot = {
            (fixture.step_id, fixture.item_index): fixture
            for fixture in run.test_fixtures.db_manager(alias).filter(role=FixtureRole.OUTPUT)
        }
    substituted = [
        row for row in due if (row.step_id, None if row.map_index == -1 else row.map_index) in fixture_by_slot
    ]
    physical_due = [row for row in due if row not in substituted]
    if run.steps_taken + len(physical_due) > run.workflow.max_steps:
        run.mark_failed(f"Workflow exceeded max_steps={run.workflow.max_steps}.", using=alias)
        return []

    claimed: list[int] = []
    for step_run in substituted:
        fixture = fixture_by_slot[(step_run.step_id, None if step_run.map_index == -1 else step_run.map_index)]
        apps.get_model("workflows", "StepAttempt").objects.db_manager(alias).record_test_fixture(
            fixture, at=timestamp, due_step_run_id=step_run.pk
        )
        apps.get_model("workflows", "WorkflowDispatch").objects.db_manager(alias).schedule_advance(
            run, available_at=timestamp
        )
        claimed.append(step_run.pk)
    due = physical_due
    if not due:
        if claimed:
            transaction.on_commit(lambda: enqueue_dispatch_publisher(using=alias), using=alias)
        return claimed

    preparations = [_prepare_attempt_input(run, step_run, source_rows=locked_rows, alias=alias) for step_run in due]
    for step_run, preparation in zip(due, preparations, strict=True):
        cause = (
            AttemptCause.CONTINUATION
            if step_run.status == StepRunStatus.WAITING
            else (
                AttemptCause.MANUAL_RETRY
                if run.origin == RunOrigin.RECOVERY
                and run.recovery_source_attempt_id is not None
                and run.recovery_source_attempt.step_run.step_id == step_run.step_id
                else AttemptCause.INITIAL
            )
        )
        if preparation.failure is not None:
            apps.get_model("workflows", "StepAttempt").objects.db_manager(alias).fail_preparation(
                step_run,
                cause=cause,
                input=preparation.input,
                map_item=preparation.map_item,
                test_fixture=preparation.test_fixture,
                result=preparation.failure,
                claimed_at=timestamp,
                recorded_at=timestamp,
            )
            apps.get_model("workflows", "WorkflowDispatch").objects.db_manager(alias).schedule_advance(
                run, available_at=timestamp
            )
            claimed.append(step_run.pk)
            continue
        claim = (
            apps.get_model("workflows", "StepAttempt")
            .objects.db_manager(alias)
            .claim(
                step_run,
                cause=cause,
                input=preparation.input,
                map_item=preparation.map_item,
                test_fixture=preparation.test_fixture,
                claimed_at=timestamp,
            )
        )
        apps.get_model("workflows", "WorkflowDispatch").objects.db_manager(alias).schedule_execute(claim.attempt)
        claimed.append(step_run.pk)
    if claimed:
        transaction.on_commit(lambda: enqueue_dispatch_publisher(using=alias), using=alias)
    return claimed


def _prepare_attempt_input(run: Any, step_run: Any, *, source_rows: list[Any], alias: str) -> _AttemptPreparation:
    """Resolve one immutable ordinary-step input before any physical invocation."""

    if (
        run.origin == RunOrigin.RECOVERY
        and run.recovery_source_attempt_id is not None
        and run.recovery_source_attempt.step_run.step_id == step_run.step_id
        and run.recovery_source_attempt.step_run.map_index == step_run.map_index
        and not (
            run.recovery_mode == str(RecoveryMode.FRESH)
            and run.recovery_source_attempt.result_kind == str(AttemptResultKind.PREPARATION_ERROR)
        )
    ):
        source = run.recovery_source_attempt
        map_item = (
            MapItemSource(
                source.map_expansion_id,
                source.map_item_index,
                JsonPresence(source.map_item_present, source.map_item),
            )
            if source.map_expansion_id is not None and source.map_item_index is not None
            else None
        )
        return _AttemptPreparation(
            AttemptInput(
                source.input_present,
                source.input,
                {
                    "kind": "recovery_input",
                    "attempt_id": source.pk,
                    "source_provenance": source.input_provenance,
                },
            ),
            map_item=map_item,
        )

    if step_run.status == StepRunStatus.WAITING and step_run.current_attempt_id is not None:
        previous: Any = related_on(step_run, "current_attempt", using=alias, select_related=("test_fixture",))
        map_item = (
            MapItemSource(
                previous.map_expansion_id,
                previous.map_item_index,
                JsonPresence(previous.map_item_present, previous.map_item),
            )
            if previous.map_expansion_id is not None and previous.map_item_index is not None
            else None
        )
        return _AttemptPreparation(
            AttemptInput(previous.input_present, previous.input, previous.input_provenance),
            map_item=map_item,
            test_fixture=previous.test_fixture,
        )
    fixture = None
    if run.origin == RunOrigin.TEST and step_run.map_index >= 0:
        fixture = (
            run.test_fixtures.db_manager(alias)
            .filter(
                step_id=step_run.step_id,
                role=FixtureRole.MAP_ITEM,
                item_index=step_run.map_index,
            )
            .first()
        )
    if step_run.step.input_binding is None:
        if fixture is not None:
            provenance = {
                "kind": "test_fixture",
                "fixture_id": fixture.sqid,
                "role": str(FixtureRole.MAP_ITEM),
                "step_id": fixture.step_id,
                "item_index": fixture.item_index,
            }
            return _AttemptPreparation(
                AttemptInput(
                    fixture.value_present,
                    map_child_input(fixture.value) if fixture.value_present else None,
                    provenance,
                ),
                test_fixture=fixture,
            )
        map_item = None
        expansion = step_run.current_map_expansion
        if step_run.map_index >= 0 and expansion is not None:
            checkpoint = expansion.checkpoint if expansion.checkpoint_present else None
            map_state = checkpoint.get("map") if isinstance(checkpoint, dict) else None
            items = map_state.get("items") if isinstance(map_state, dict) else None
            if isinstance(items, list) and step_run.map_index < len(items):
                map_item = MapItemSource(
                    expansion.pk,
                    step_run.map_index,
                    JsonPresence(True, items[step_run.map_index]),
                )
        return _AttemptPreparation(
            AttemptInput(True, step_run.input, {"kind": "automatic"}),
            map_item=map_item,
        )
    try:
        binding = parse_binding(step_run.step.input_binding)
    except PydanticValidationError as error:
        diagnostics: list[dict[str, JsonValue]] = [
            {"path": list(path), "message": message}
            for path, message in binding_error_details(step_run.step.input_binding, error)
        ]
        failure = AttemptResult(
            AttemptResultKind.PREPARATION_ERROR,
            error="Input binding is invalid.",
            stacktrace=json.dumps(diagnostics, sort_keys=True),
            outcome="failed",
        )
        return _AttemptPreparation(
            AttemptInput(False, None, {"kind": "binding", "diagnostics": diagnostics}),
            failure,
        )

    workflow_input = SourceValue(
        JsonPresence(run.input_present, run.input),
        {"kind": "workflow_input", "run_id": run.pk},
    )
    sources: dict[str, Any] = {}
    if run.origin == RunOrigin.RECOVERY:
        for evidence in run.recovery_evidence.db_manager(alias).select_related("step", "source_attempt").order_by("pk"):
            source_attempt = evidence.source_attempt
            gate_decisions = (
                list(source_attempt.decisions.db_manager(alias).order_by("priority", "pk"))
                if source_attempt.result_kind == str(AttemptResultKind.SUSPEND)
                else []
            )
            gate_output = retained_gate_output(source_attempt, gate_decisions) if gate_decisions else None
            sources[evidence.step.key] = SourceValue(
                JsonPresence(
                    True if gate_output is not None else source_attempt.output_present,
                    gate_output if gate_output is not None else source_attempt.output,
                ),
                {
                    "kind": "recovery_evidence",
                    "evidence_id": evidence.sqid,
                    "attempt_id": source_attempt.pk,
                    "step_key": evidence.step.key,
                    "map_index": evidence.map_index,
                    **(
                        {"settled_decision_ids": source_attempt.decision_settlement["decision_ids"]}
                        if gate_output is not None
                        else {}
                    ),
                },
            )
    for source in source_rows:
        if source.step_id is None or source.pk == step_run.pk or source.map_index != -1:
            continue
        attempt = source.current_attempt
        valid_attempt = (
            source.status == StepRunStatus.SUCCEEDED
            and attempt is not None
            and attempt.step_run_id == source.pk
            and attempt.effect_key == source.effect_key
            and attempt.effect_generation == source.effect_generation
            and attempt.applied_at is not None
            and attempt.lease_revoked_at is None
        )
        settled_decisions: list[Any] = []
        if valid_attempt and attempt.result_kind == str(AttemptResultKind.SUSPEND):
            settled_decisions = list(
                source.decisions.db_manager(alias).filter(suspension_attempt_id=attempt.pk).order_by("priority", "pk")
            )
        settled_output = retained_gate_output(attempt, settled_decisions) if settled_decisions else None
        valid_done = valid_attempt and attempt.result_kind == str(AttemptResultKind.DONE)
        valid_settled_decisions = (
            bool(settled_decisions)
            and settled_output is not None
            and source.output == settled_output
            and settled_output["outcome"] == source.outcome
        )
        provenance = {
            "kind": "step_output",
            "step_key": source.step.key,
            "step_run_id": source.pk,
            "attempt_id": attempt.pk if attempt is not None else None,
            "effect_generation": source.effect_generation,
            **(
                {"settled_decision_ids": attempt.decision_settlement["decision_ids"]} if valid_settled_decisions else {}
            ),
        }
        sources.setdefault(
            source.step.key,
            (
                SourceValue(
                    JsonPresence(
                        True if valid_settled_decisions else attempt.output_present,
                        source.output if valid_settled_decisions else attempt.output,
                    ),
                    provenance,
                )
                if valid_done or valid_settled_decisions
                else UnavailableSource(
                    "source_unavailable", "The referenced retained output is unavailable.", provenance
                )
            ),
        )
    map_item_source: MapItemSource | None = None
    map_binding_source: SourceValue | UnavailableSource
    expansion = step_run.current_map_expansion
    if fixture is not None:
        presence = JsonPresence(fixture.value_present, fixture.value)
        map_binding_source = SourceValue(
            presence,
            {
                "kind": "test_fixture",
                "fixture_id": fixture.sqid,
                "role": str(FixtureRole.MAP_ITEM),
                "step_id": fixture.step_id,
                "item_index": fixture.item_index,
                "path": [],
            },
        )
    elif step_run.map_index >= 0 and expansion is not None:
        checkpoint = expansion.checkpoint if expansion.checkpoint_present else None
        map_state = checkpoint.get("map") if isinstance(checkpoint, dict) else None
        items = map_state.get("items") if isinstance(map_state, dict) else None
        if (
            expansion.result_kind == str(AttemptResultKind.WAIT)
            and expansion.applied_at is not None
            and isinstance(items, list)
            and step_run.map_index < len(items)
        ):
            presence = JsonPresence(True, items[step_run.map_index])
            provenance = {
                "kind": "map_item",
                "expansion_attempt_id": expansion.pk,
                "map_index": step_run.map_index,
                "path": [],
            }
            map_item_source = MapItemSource(expansion.pk, step_run.map_index, presence)
            map_binding_source = SourceValue(presence, provenance)
        else:
            map_binding_source = UnavailableSource(
                "map_item_unavailable",
                "The retained Map item is outside the current expansion.",
                {"kind": "map_item", "map_index": step_run.map_index},
            )
    else:
        map_binding_source = UnavailableSource(
            "map_item_unavailable",
            "Retained Map item provenance is not available for this execution generation.",
            {"kind": "map_item"},
        )
    context = BindingContext(
        workflow_input=workflow_input,
        step_outputs=sources,
        map_item=map_binding_source,
    )
    evaluation = evaluate_binding(binding, context)
    if evaluation.diagnostics or evaluation.value is None:
        diagnostics = [
            {
                "code": item.code,
                "path": list(item.path),
                "message": item.message,
                "source": item.source,
                "source_path": list(item.source_path),
            }
            for item in evaluation.diagnostics
        ]
        failure = AttemptResult(
            AttemptResultKind.PREPARATION_ERROR,
            error="Workflow input binding could not be resolved.",
            stacktrace=json.dumps(diagnostics, sort_keys=True),
            outcome="failed",
            checkpoint_present=False,
            checkpoint=None,
        )
        provenance = dict(evaluation.provenance or {"kind": "binding"})
        provenance["diagnostics"] = diagnostics
        return _AttemptPreparation(AttemptInput(False, None, provenance), failure, map_item_source, fixture)
    candidate = evaluation.value
    impl_class = step_run.step.resolve_impl("step_class")
    if impl_class.input_model is not None:
        try:
            impl_class.validate_input(candidate.value)
        except (PydanticValidationError, TypeError, ValueError) as error:
            failure = _preparation_error("Workflow step input is invalid.", error)
            return _AttemptPreparation(
                AttemptInput(candidate.present, candidate.value, evaluation.provenance),
                failure,
                map_item_source,
                fixture,
            )
    return _AttemptPreparation(
        AttemptInput(candidate.present, candidate.value, evaluation.provenance),
        map_item=map_item_source,
        test_fixture=fixture,
    )


def _preparation_error(message: str, error: Exception) -> AttemptResult:
    details = (
        error.errors(include_url=False) if isinstance(error, PydanticValidationError) else [{"message": str(error)}]
    )
    return AttemptResult(
        AttemptResultKind.PREPARATION_ERROR,
        error=message,
        stacktrace=json.dumps(details, sort_keys=True, default=str),
        outcome="failed",
    )


def _fail_if_budget_exceeded(run: Any, *, alias: str) -> bool:
    """Fail ``run`` when its top-level numeric budget spend exceeds a limit."""

    budget = run.workflow.budget if isinstance(run.workflow.budget, Mapping) else {}
    spent = run.budget_spent if isinstance(run.budget_spent, Mapping) else {}
    for key, limit in _numeric_budget_items(budget):
        spent_value = _numeric_budget_value(spent.get(key))
        if spent_value is None or spent_value <= limit:
            continue
        run.mark_failed(f"Workflow exceeded budget {key}={limit:g} (spent {spent_value:g}).", using=alias)
        return True
    return False


def _numeric_budget_items(budget: Mapping[str, Any]) -> Iterable[tuple[str, float]]:
    """Yield numeric top-level budget limits in deterministic key order."""

    for key in sorted(budget):
        value = _numeric_budget_value(budget[key])
        if value is not None:
            yield str(key), value


def _numeric_budget_value(value: Any) -> float | None:
    """Return ``value`` as a non-negative budget number, or ``None``."""

    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = float(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed >= 0 else None


def _update_run_status(run: Any, *, timestamp: datetime, alias: str) -> None:
    if run.status in RunStatus.TERMINAL:
        return
    rows = run.step_runs.db_manager(alias).select_related("step").all()
    if run.origin == RunOrigin.TEST and run.test_scope == WorkflowScope.NODE:
        rows = [row for row in rows if row.step_id is not None and run.allows_test_step(row.step, using=alias)]
        active_without_wait = any(row.status in {StepRunStatus.SCHEDULED, StepRunStatus.STARTED} for row in rows)
    else:
        active_without_wait = rows.filter(status__in=[StepRunStatus.SCHEDULED, StepRunStatus.STARTED]).exists()
    if active_without_wait:
        if run.status == RunStatus.PENDING:
            run.mark_running(using=alias)
        elif run.status == RunStatus.WAITING:
            run.resume(using=alias)
        if run.wake_at is not None:
            run.wake_at = None
            run.save(update_fields=["wake_at", "updated_at"], using=alias)
        return

    waiting_rows = (
        [row for row in rows if row.status == StepRunStatus.WAITING]
        if isinstance(rows, list)
        else rows.filter(status=StepRunStatus.WAITING)
    )
    if waiting_rows:
        wake_at = (
            min((row.wait_until for row in waiting_rows if row.wait_until is not None), default=None)
            if isinstance(waiting_rows, list)
            else waiting_rows.filter(wait_until__isnull=False)
            .order_by("wait_until")
            .values_list("wait_until", flat=True)
            .first()
        )
        if run.status == RunStatus.RUNNING:
            run.mark_waiting(wake_at=wake_at, using=alias)
        elif run.status == RunStatus.WAITING and run.wake_at != wake_at:
            run.wake_at = wake_at
            run.save(update_fields=["wake_at", "updated_at"], using=alias)
        return

    failed = (
        run.step_runs.db_manager(alias)
        .filter(status__in=[StepRunStatus.FAILED, StepRunStatus.CANCELED], map_index=-1)
        .order_by("-pk")
        .first()
    )
    if (
        failed is None
        and run.origin == RunOrigin.RECOVERY
        and run.recovery_source_attempt_id is not None
        and run.recovery_source_attempt.step_run.map_index >= 0
    ):
        source_step_run = run.recovery_source_attempt.step_run
        failed = (
            run.step_runs.db_manager(alias)
            .filter(
                step_id=source_step_run.step_id,
                map_index=source_step_run.map_index,
                status__in=[StepRunStatus.FAILED, StepRunStatus.CANCELED],
            )
            .first()
        )
    if failed is not None:
        if run.status == RunStatus.PENDING:
            run.mark_running(using=alias)
        _fail_run(
            run, failed.error or f"Step {failed.pk} ended as {failed.status}.", failed_step_run=failed, alias=alias
        )
        return

    if run.step_runs.db_manager(alias).exists():
        if run.status == RunStatus.PENDING:
            run.mark_running(using=alias)
        _finish_run_result(run, alias=alias)
        return

    if run.status == RunStatus.RUNNING and run.wake_at is not None and run.wake_at <= timestamp:
        run.wake_at = None
        run.save(update_fields=["wake_at", "updated_at"], using=alias)


def _finish_run_result(run: Any, *, alias: str) -> None:
    """Select exactly one declared terminal rule and retain its typed run result."""

    rules = run.workflow.result_rules
    terminals = list(
        run.step_runs.db_manager(alias)
        .select_related("step")
        .filter(
            map_index=-1,
            status=StepRunStatus.SUCCEEDED,
            step__isnull=False,
        )
        .order_by("pk")
    )
    outgoing_routes = list(run.workflow.edges.db_manager(alias).values_list("source_id", "condition"))
    unhandled_calls = [
        row
        for row in terminals
        if not any(
            source_id == row.step_id and condition in {"", row.outcome} for source_id, condition in outgoing_routes
        )
        and row.step.step_class == "call_workflow"
        and row.outcome in {"child_failed", "child_canceled"}
    ]
    if unhandled_calls:
        if any(row.outcome == "child_failed" for row in unhandled_calls):
            run.mark_failed("An unhandled child workflow failed.", using=alias)
        else:
            run.mark_canceled(using=alias)
        return
    if not rules:
        run.mark_succeeded(outcome="completed", output={}, using=alias)
        return
    matches = [
        (rule, producer)
        for rule in rules
        for producer in terminals
        if producer.step.key == rule["producer"] and producer.outcome == rule["when_outcome"]
    ]
    if len(matches) != 1:
        run.mark_failed(workflow_result_terminal_match_error(len(matches)), using=alias)
        return
    rule, producer = matches[0]
    context = BindingContext(
        workflow_input=SourceValue(
            JsonPresence(run.input_present, run.input), {"kind": "workflow_input", "run_id": run.pk}
        ),
        step_outputs={
            producer.step.key: SourceValue(
                JsonPresence(producer.output_present, producer.output),
                {"kind": "step_output", "step_run_id": producer.pk},
            )
        },
        map_item=UnavailableSource("source_unavailable", "Result rules cannot read Map items.", {"kind": "map_item"}),
    )
    try:
        evaluated = evaluate_binding(parse_binding(rule["binding"]), context)
        if evaluated.diagnostics or evaluated.value is None or not evaluated.value.present:
            raise ValidationError({"result": "Terminal binding did not produce a complete output."})
        output = evaluated.value.value
        errors = list(Draft202012Validator(run.workflow.output_schema).iter_errors(output))
        if errors:
            raise ValidationError({"result": "Terminal output does not satisfy the published workflow schema."})
    except (PydanticValidationError, ValidationError) as error:
        run.mark_failed(f"Workflow result contract failed: {error}", using=alias)
        return
    run.mark_succeeded(outcome=rule["outcome"], output=output, using=alias)


def _input_from_previous(previous: list[Any]) -> Any:
    if not previous:
        return {}
    if len(previous) == 1:
        return previous[0].output if previous[0].output_present else {}
    return {_step_key(row): row.output for row in previous if row.output_present}


def _step_key(step_run: Any) -> str:
    if step_run.step_id is not None:
        return str(step_run.step.key)
    return step_run.system_kind or str(step_run.pk)


def _fail_run(run: Any, error: str, *, failed_step_run: Any, alias: str) -> None:
    """Mark ``run`` failed and start its linked error workflow once."""

    run.mark_failed(error, using=alias)
    _start_error_workflow(run, failed_step_run=failed_step_run, alias=alias)


def _start_error_workflow(run: Any, *, failed_step_run: Any, alias: str) -> None:
    """Start the pinned workflow's error workflow for ``failed_step_run``."""

    if _is_error_workflow_run(run):
        return
    lineage = getattr(run.workflow, "error_workflow", None)
    if lineage is None:
        return
    start(
        lineage,
        subject=run,
        actor=run.admission_actor_subject(),
        parent_step_run=failed_step_run,
        parent_relation="continuation",
        origin=cast(RunOrigin, RunOrigin.ERROR_WORKFLOW),
        using=alias,
    )


def _is_error_workflow_run(run: Any) -> bool:
    """Return whether ``run`` was started by an error-workflow failure path."""

    return run.origin == RunOrigin.ERROR_WORKFLOW
