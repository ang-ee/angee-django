"""Runtime engine for workflow runs.

This module is the single owner of workflow advancement. It creates and replays
the step-run journal, evaluates join rules, routes outcomes, claims work, and
records cancellation. Step implementations run only through retained
``execute_dispatch()`` deliveries, never inside ``advance_dispatch()``.
"""

from __future__ import annotations

import json
import logging
import re
import traceback
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import JsonValue, create_model
from pydantic import ValidationError as PydanticValidationError
from rebac import PermissionDenied, SubjectRef, current_actor, system_context
from rebac.actors import to_subject_ref
from rebac.backends import backend as rebac_backend
from rebac.resources import to_object_ref

from angee.base.actors import actor_user_id
from angee.base.identity import instance_from_public_id
from angee.base.refs import CanonicalRecordTarget, canonical_record_target
from angee.base.scoping import read_scoped_queryset
from angee.workflows.attempts import (
    AttemptCause,
    AttemptInput,
    AttemptResult,
    AttemptResultKind,
    DecisionInputSource,
    DecisionResolution,
    ExternalOperationPolicy,
    ExternalOperationRequest,
    InvocationAdmission,
    JsonPresence,
    MapItemSource,
    RecoveryMode,
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
from angee.workflows.decision_actions import compile_decision_action_schema
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
from angee.workflows.steps import MapStep, StepExecutionMode, TransientStepError
from angee.workflows.testing import FixtureRole, WorkflowScope

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
class DecisionAttemptResult:
    """Outcome of one authorized decision attempt and any shape validation failure."""

    decision: Any
    validation_error: ValidationError | None = None


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
) -> Any:
    """Start the current published version after validating its subject declaration.

    An empty subject declaration accepts any subject for backwards compatibility.
    A declared workflow raises ``ValidationError`` before creating a run when the
    subject's concrete model differs. ``validate_new`` is the manager-owned,
    side-effect-free new-admission check; callers must authorize its inputs before
    entering this system transaction.
    """

    run_model = apps.get_model("workflows", "WorkflowRun")
    return run_model.objects.start(
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
    )


def recover(
    source_attempt: Any, *, request_key: str, actor: Any, prior_recovery: Any = None,
) -> Any:
    """Start or recover one exact same-revision retained recovery request."""

    return apps.get_model("workflows", "WorkflowRun").objects.start_recovery(
        source_attempt,
        request_key=request_key,
        actor=actor,
        prior_recovery=prior_recovery,
    )


def deliver(run_id: int, *, now: datetime | None = None) -> dict[str, int]:
    """Deliver an external event by waking this run's parked journal rows.

    This is the workflow engine's event-delivery seam. Every delivery advances
    the run-scoped generation under the same short row lock as :func:`advance`,
    even when no row is waiting. A step that parks after observing an older
    generation is made immediately due by the retained dispatch path.
    """

    timestamp = now or timezone.now()
    run_model = apps.get_model("workflows", "WorkflowRun")
    step_run_model = apps.get_model("workflows", "StepRun")
    woken = 0
    with system_context(reason="workflows.engine.deliver"), transaction.atomic():
        run = run_model.objects.lock_if_supported().get(pk=run_id)
        if run.status in RunStatus.TERMINAL:
            return {"woken": 0}
        run.deliveries += 1
        run.save(update_fields=["deliveries", "updated_at"])
        waiting = list(
            step_run_model.objects.lock_if_supported()
            .filter(run=run, status=StepRunStatus.WAITING)
            .order_by("pk")
        )
        for step_run in waiting:
            apps.get_model("workflows", "StepAttempt").objects.wake_current(step_run.pk, at=timestamp)
            woken += 1
        if run.status == RunStatus.WAITING and waiting:
            run.resume()
        apps.get_model("workflows", "WorkflowDispatch").objects.schedule_advance(run, available_at=timestamp)
        transaction.on_commit(enqueue_dispatch_publisher)
    return {"woken": woken}


def subscribe_external(step_run: Any, resources: Iterable[Any]) -> None:
    """Commit this invocation's complete domain target set before its predicate read."""

    lease_token = getattr(step_run, "_workflow_invocation_lease_token", None)
    if step_run.current_attempt_id is None or not isinstance(lease_token, uuid.UUID):
        raise RuntimeError("External subscription requires a retained invocation lease.")
    apps.get_model("workflows", "StepAttempt").objects.subscribe_external(
        step_run.current_attempt_id, lease_token=lease_token, resources=resources,
    )


def schedule_artifact_delivery(resource: Any) -> Any:
    """Keep a domain event in its native transaction without locking a run."""

    return apps.get_model("workflows", "WorkflowDispatch").objects.schedule_artifact_delivery(resource)


def deliver_artifact(resource: Any, *, now: datetime | None = None) -> dict[str, int]:
    """Wake exact external waits whose current attempt retained ``resource``.

    Resource owners use this seam when one durable domain fact changes.  Unlike
    :func:`deliver`, it does not wake unrelated approval or timer rows that happen
    to share a workflow run with the external dependency.
    """

    timestamp = now or timezone.now()
    target = resource if isinstance(resource, CanonicalRecordTarget) else canonical_record_target(resource)
    run_model = apps.get_model("workflows", "WorkflowRun")
    step_run_model = apps.get_model("workflows", "StepRun")
    artifact_model = apps.get_model("workflows", "StepArtifact")
    subscription_model = apps.get_model("workflows", "StepExternalSubscription")
    woken = 0
    delivered_run_ids: list[int] = []
    with system_context(reason="workflows.engine.deliver_artifact"), transaction.atomic():
        artifact_step_ids = list(artifact_model.objects.filter(
            target_content_type_id=target.content_type.pk,
            target_object_id=target.object_id,
            attempt__step_run__current_attempt_id=models.F("attempt_id"),
            attempt__step_run__status=StepRunStatus.WAITING,
            attempt__step_run__waiting_kind=WaitingKind.EXTERNAL,
        ).order_by().values_list("attempt__step_run_id", flat=True).distinct())
        attempt_model = apps.get_model("workflows", "StepAttempt")
        subscribed_attempt_ids = set(subscription_model.objects.filter(
            target_content_type_id=target.content_type.pk,
            target_object_id=target.object_id,
            attempt__step_run__current_attempt_id=models.F("attempt_id"),
            attempt__step_run__status__in=[StepRunStatus.STARTED, StepRunStatus.WAITING],
            attempt__lease_revoked_at__isnull=True,
        ).order_by().values_list("attempt_id", flat=True))
        bound_attempt_ids = set(attempt_model.objects.filter(
            external_content_type_id=target.content_type.pk,
            external_object_id=target.object_id,
            step_run__current_attempt_id=models.F("pk"),
            step_run__status__in=[StepRunStatus.STARTED, StepRunStatus.WAITING],
            lease_revoked_at__isnull=True,
        ).order_by().values_list("pk", flat=True))
        subscribed_step_ids = list(attempt_model.objects.filter(
            pk__in=subscribed_attempt_ids | bound_attempt_ids,
        ).order_by().values_list("step_run_id", flat=True))
        candidate_step_ids = sorted(set(artifact_step_ids).union(subscribed_step_ids))
        candidate_run_ids = list(step_run_model.objects.filter(
            pk__in=candidate_step_ids,
        ).order_by().values_list("run_id", flat=True).distinct())
        runs = {
            run.pk: run for run in run_model.objects.lock_if_supported().filter(
                pk__in=candidate_run_ids,
            ).order_by("pk")
        }
        step_runs = list(step_run_model.objects.lock_if_supported().filter(
            pk__in=candidate_step_ids,
            status__in=[StepRunStatus.STARTED, StepRunStatus.WAITING],
        ).select_related("current_attempt").order_by("run_id", "pk"))
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
            for attempt in attempt_model.objects.lock_if_supported().filter(
                pk__in=attempt_ids
            ).order_by("pk")
        }
        retained_attempt_ids = set(
            artifact_model.objects.filter(
                attempt_id__in=attempt_ids,
                target_content_type_id=target.content_type.pk,
                target_object_id=target.object_id,
            ).order_by().values_list("attempt_id", flat=True).distinct()
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
            retained_artifact = (
                step_run.status == StepRunStatus.WAITING
                and attempt.pk in retained_attempt_ids
            )
            if not (subscribed or retained_artifact):
                continue
            if step_run.status == StepRunStatus.WAITING:
                if step_run.waiting_kind != WaitingKind.EXTERNAL:
                    continue
                attempt_model.objects.wake_current(step_run.pk, at=timestamp)
                woken += 1
                touched.add(run.pk)
            elif not subscribed or attempt.result_recorded_at is not None:
                continue
            touched.add(run.pk)
        for run_id in sorted(touched):
            run = runs[run_id]
            run.deliveries += 1
            run.save(update_fields=["deliveries", "updated_at"])
            if run.status == RunStatus.WAITING:
                run.resume()
            delivered_run_ids.append(run_id)
            apps.get_model("workflows", "WorkflowDispatch").objects.schedule_advance(run, available_at=timestamp)
        if delivered_run_ids:
            transaction.on_commit(enqueue_dispatch_publisher)
    return {"runs": len(delivered_run_ids), "woken": woken}


def deliver_artifact_dispatch(dispatch_id: int, *, now: datetime | None = None) -> dict[str, int]:
    """Consume one committed domain intent and deliver to current subscribers."""

    timestamp = now or timezone.now()
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    with system_context(reason="workflows.engine.deliver_artifact_dispatch"), transaction.atomic():
        with dispatch_model.objects._owner_transition(
            dispatch_id=dispatch_id, lease_token=None, at=timestamp, using=dispatch_model.objects.db,
        ) as preflight:
            if preflight.disposition != DispatchPreflightDisposition.READY:
                return {"runs": 0, "woken": 0}
            if (
                preflight.envelope.kind != WorkflowDispatchKind.ARTIFACT_DELIVERY
                or preflight.envelope.target_id != dispatch_id
            ):
                raise ValidationError({"dispatch": "Artifact delivery envelope is invalid."})
            dispatch = dispatch_model.objects.select_related("artifact_content_type").get(pk=dispatch_id)
            target = CanonicalRecordTarget(
                dispatch.artifact_content_type, dispatch.artifact_object_id,
            )
            outcome = deliver_artifact(target, now=timestamp)
            dispatch_model.objects._consume_locked(dispatch_id, at=timestamp)
    return outcome


def cancel_child_dispatch(dispatch_id: int, *, expected_child_id: int | None = None) -> dict[str, int]:
    """Deliver an owned-child cancel after the parent is terminal, with parent-first locks."""

    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    canceled = 0
    with system_context(reason="workflows.engine.child_cancel_dispatch"), transaction.atomic():
        with dispatch_model.objects._owner_transition(
            dispatch_id=dispatch_id, lease_token=None, at=timezone.now(), using=dispatch_model.objects.db,
        ) as preflight:
            if preflight.disposition != DispatchPreflightDisposition.READY:
                return {"canceled": 0}
            envelope = preflight.envelope
            if envelope.kind != WorkflowDispatchKind.CHILD_CANCEL or (
                expected_child_id is not None and expected_child_id != envelope.target_id
            ):
                raise ValidationError({"dispatch": "Owned-child cancellation envelope changed."})
            child = apps.get_model("workflows", "WorkflowRun").objects.get(pk=envelope.target_id)
            if child.parent_relation != "owned_call":
                raise ValidationError({"child": "Cancellation target is no longer an owned child."})
            if child.status not in RunStatus.TERMINAL:
                cancel(child)
                canceled = 1
            dispatch_model.objects._consume_locked(dispatch_id, at=timezone.now(), fenced=not canceled)
    return {"canceled": canceled}


def schedule_run_cancel(
    step_run: Any,
    run: Any,
    *,
    actor: Any,
) -> tuple[Any, bool]:
    """Retain one cross-run cancellation from this fenced database command."""

    return apps.get_model("workflows", "WorkflowDispatch").objects.schedule_run_cancel(
        step_run.pk,
        run,
        actor=actor,
    )


def cancel_run_dispatch(dispatch_id: int, *, expected_run_id: int | None = None) -> dict[str, int]:
    """Cancel one exact run, then publish its committed terminal state."""

    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    canceled = 0
    timestamp = timezone.now()
    with system_context(reason="workflows.engine.run_cancel_dispatch"), transaction.atomic():
        with dispatch_model.objects._owner_transition(
            dispatch_id=dispatch_id,
            lease_token=None,
            at=timestamp,
            using=dispatch_model.objects.db,
        ) as preflight:
            if preflight.disposition != DispatchPreflightDisposition.READY:
                return {"canceled": 0}
            envelope = preflight.envelope
            if envelope.kind != WorkflowDispatchKind.RUN_CANCEL or (
                expected_run_id is not None and expected_run_id != envelope.target_id
            ):
                raise ValidationError({"dispatch": "Run cancellation envelope changed."})
            run = apps.get_model("workflows", "WorkflowRun").objects.get(pk=envelope.target_id)
            if run.status not in RunStatus.TERMINAL:
                cancel(run)
                canceled = 1
            dispatch_model.objects.schedule_artifact_delivery(run)
            dispatch_model.objects._consume_locked(dispatch_id, at=timestamp)
    return {"canceled": canceled}


def advance(run_id: int, *, now: datetime | None = None) -> dict[str, int]:
    """Create and synchronously consume one durable orchestration pulse."""

    timestamp = now or timezone.now()
    run_model = apps.get_model("workflows", "WorkflowRun")
    with system_context(reason="workflows.engine.advance.schedule"), transaction.atomic():
        run = run_model.objects.get(pk=run_id)
        dispatch = apps.get_model("workflows", "WorkflowDispatch").objects.schedule_advance(
            run, available_at=timestamp
        )
    return advance_dispatch(dispatch.pk, expected_run_id=run_id, now=timestamp)


def advance_dispatch(
    dispatch_id: int, *, expected_run_id: int | None = None, now: datetime | None = None
) -> dict[str, int]:
    """Apply one durable ADVANCE delivery through its exact owner preflight."""

    timestamp = now or timezone.now()
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    admitted = False
    try:
        with system_context(reason="workflows.engine.advance_dispatch"), transaction.atomic():
            with dispatch_model.objects._owner_transition(
                dispatch_id=dispatch_id, lease_token=None, at=timestamp, using=dispatch_model.objects.db
            ) as preflight:
                if preflight.disposition != DispatchPreflightDisposition.READY:
                    return {"claimed": 0}
                if expected_run_id is not None and preflight.envelope.target_id != expected_run_id:
                    raise ValidationError({"dispatch": "ADVANCE envelope target does not match its durable intent."})
                if preflight.envelope.kind != WorkflowDispatchKind.ADVANCE:
                    raise ValidationError({"dispatch": "ADVANCE envelope kind does not match its durable intent."})
                admitted = True
                run = apps.get_model("workflows", "WorkflowRun").objects.select_related("workflow").get(
                    pk=preflight.envelope.target_id
                )
                claimed_ids: list[int] = []
                if run.status not in RunStatus.TERMINAL:
                    _activate_run_if_needed(run, timestamp=timestamp)
                    _route_completed_steps(run)
                    _process_recovery_map_aggregate(run, timestamp=timestamp)
                    _route_completed_steps(run)
                    if _process_map_steps(run, timestamp=timestamp):
                        _route_completed_steps(run)
                        if not _fail_if_budget_exceeded(run):
                            claimed_ids = _claim_due_steps(run, timestamp=timestamp)
                            _update_run_status(run, timestamp=timestamp)
                dispatch_model.objects._consume_locked(dispatch_id, at=timestamp)
    except Exception as error:
        if admitted:
            try:
                dispatch_model.objects.record_advance_error(dispatch_id, error=error)
            except Exception:  # noqa: BLE001 - preserve the original advancement failure.
                logger.exception("Could not retain workflow ADVANCE failure visibility.")
        raise
    return {"claimed": len(claimed_ids)}


def external_operation_request(step_run: Any) -> ExternalOperationRequest:
    """Return the exact retained request identity for the current provider call."""

    request = getattr(step_run, "_workflow_external_operation_request", None)
    if not isinstance(request, ExternalOperationRequest):
        raise RuntimeError("External operation requests are available only inside their admitted invocation.")
    return request


def consume_decision_resolution(
    consumer_step_run: Any,
    resolution_path: tuple[str | int, ...],
    *,
    input_source: DecisionInputSource = "attempt_input",
    expected_action: str,
    expected_target: tuple[str, str],
    expected_verdict: str,
    actor: Any,
    required_record_access: Collection[models.Model] = (),
) -> tuple[Any, DecisionResolution]:
    """Consume one exact gate value from this invocation's admitted input."""

    lease_token = getattr(consumer_step_run, "_workflow_invocation_lease_token", None)
    if not isinstance(lease_token, uuid.UUID):
        raise RuntimeError("Decision consumption requires the active fenced invocation lease.")
    return apps.get_model("workflows", "StepAttempt").objects.consume_decision_resolution(
        consumer_step_run.pk,
        resolution_path,
        lease_token=lease_token,
        input_source=input_source,
        expected_action=expected_action,
        expected_target=expected_target,
        expected_verdict=expected_verdict,
        actor=actor,
        required_record_access=required_record_access,
    )


def admitted_continuation_child(
    consumer_step_run: Any, child_id_path: tuple[str | int, ...],
    *, expected_starter_class: str,
) -> Any:
    """Resolve a continuation run from one exact admitted starter output."""

    lease_token = getattr(consumer_step_run, "_workflow_invocation_lease_token", None)
    if not isinstance(lease_token, uuid.UUID):
        raise RuntimeError("Continuation result requires the active fenced invocation lease.")
    return apps.get_model("workflows", "StepAttempt").objects.admitted_continuation_child(
        consumer_step_run.pk, lease_token=lease_token,
        child_id_path=child_id_path, expected_starter_class=expected_starter_class,
    )


def admitted_continuation_completion(
    consumer_step_run: Any,
    child_id_path: tuple[str | int, ...],
    *,
    expected_starter_class: str,
    actor: Any,
) -> tuple[Any, Any | None]:
    """Resolve an admitted child and its one accepted successful recovery."""

    lease_token = getattr(consumer_step_run, "_workflow_invocation_lease_token", None)
    if not isinstance(lease_token, uuid.UUID):
        raise RuntimeError("Continuation completion requires the active fenced invocation lease.")
    return apps.get_model("workflows", "StepAttempt").objects.admitted_continuation_completion(
        consumer_step_run.pk,
        lease_token=lease_token,
        child_id_path=child_id_path,
        expected_starter_class=expected_starter_class,
        actor=actor,
    )


def target_read_authority(
    consumer_step_run: Any, authority_path: tuple[str | int, ...],
    *, proposal_gate_path: tuple[str | int, ...] = (),
) -> tuple[Any, Any]:
    """Return one human resolver and Decision proven by this admitted input."""

    lease_token = getattr(consumer_step_run, "_workflow_invocation_lease_token", None)
    if not isinstance(lease_token, uuid.UUID):
        raise RuntimeError("Target authority requires the active fenced invocation lease.")
    return apps.get_model("workflows", "StepAttempt").objects.target_read_authority(
        consumer_step_run.pk, lease_token=lease_token,
        authority_path=authority_path, proposal_gate_path=proposal_gate_path,
    )


def execute_dispatch(
    dispatch_id: int, attempt_id: int, lease_token: Any, *, now: datetime | None = None
) -> dict[str, int]:
    """Execute one exact retained attempt after atomic dispatch and lease admission."""

    timestamp = now or timezone.now()
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    attempt_model = apps.get_model("workflows", "StepAttempt")
    with system_context(reason="workflows.engine.execute_dispatch.admit"), transaction.atomic():
        with dispatch_model.objects._owner_transition(
            dispatch_id=dispatch_id,
            lease_token=lease_token,
            at=timestamp,
            using=dispatch_model.objects.db,
        ) as preflight:
            if preflight.disposition != DispatchPreflightDisposition.READY:
                return {"executed": 0}
            if (
                preflight.envelope.kind != WorkflowDispatchKind.EXECUTE
                or preflight.envelope.target_id != attempt_id
            ):
                raise ValidationError({"dispatch": "EXECUTE envelope does not match its durable intent."})
            admission = attempt_model.objects.admit_invocation(
                attempt_id, lease_token=lease_token, at=timestamp
            )
            dispatch_model.objects._consume_locked(
                dispatch_id, at=timestamp, fenced=admission != InvocationAdmission.FIRST_START
            )
            if admission != InvocationAdmission.FIRST_START:
                return {"executed": 0}

    with system_context(reason="workflows.engine.execute_dispatch.load"):
        attempt = attempt_model.objects.select_related(
            "step_run__run", "step_run__step", "recovery_source_attempt"
        ).get(pk=attempt_id)
        step_run = attempt.step_run
        impl_class = step_run.step.resolve_impl("step_class")

    def invoke(owned_step_run: Any, owned_attempt: Any) -> AttemptResult:
        owned_step_run.input = owned_attempt.input if owned_attempt.input_present else None
        owned_step_run._workflow_invocation_lease_token = owned_attempt.lease_token
        if impl_class.execution_mode == StepExecutionMode.EXTERNAL_OPERATION:
            policy = impl_class.external_operation_policy(attempt=owned_attempt)
            if not isinstance(policy, ExternalOperationPolicy):
                raise ValidationError({
                    "operation": "External operation policy must be a declared provider capability."
                })
            source = owned_attempt.recovery_source_attempt
            owned_step_run._workflow_external_operation_request = ExternalOperationRequest(
                request_key=str(source.effect_key if source is not None else owned_attempt.effect_key),
                attempt_id=owned_attempt.pk,
                input_present=owned_attempt.input_present,
                input=owned_attempt.input,
                recovery_source_attempt_id=None if source is None else source.pk,
                uncertainty_acknowledged=bool(owned_step_run.run.recovery_uncertainty_ack),
            )
        implementation = cast(Any, impl_class)()
        if owned_attempt.cause == AttemptCause.MANUAL_RETRY:
            recovery_mode = RecoveryMode(owned_attempt.recovery_mode)
            capability = impl_class.recovery_capability(
                attempt=owned_attempt.recovery_source_attempt
            )
            if capability.mode is not recovery_mode:
                raise ValidationError(
                    {"recovery": "The operation's recovery capability changed after admission."}
                )
            step_result = implementation.run_recovery(
                owned_step_run,
                now=timestamp,
                source_attempt=owned_attempt.recovery_source_attempt,
                mode=recovery_mode,
            )
        else:
            step_result = implementation.run(owned_step_run, now=timestamp)
        result = (
            step_result.to_attempt_result()
            if step_result is not None
            else AttemptResult(AttemptResultKind.NO_RESULT)
        )
        attempt_model.objects.validate_result(result)
        return result

    def schedule_result(finalization: Any) -> None:
        if finalization.retry_intent is not None:
            successor = attempt_model.objects.get(pk=finalization.retry_intent.attempt_id)
            dispatch_model.objects.schedule_execute(successor)
        for intent in finalization.timer_intents:
            decision = apps.get_model("workflows", "Decision").objects.get(pk=intent.decision_id)
            kind = (
                WorkflowDispatchKind.DECISION_ESCALATE
                if intent.kind.value == "escalate"
                else WorkflowDispatchKind.DECISION_EXPIRE
            )
            dispatch_model.objects.schedule_decision(kind, decision)
        if finalization.recorded and finalization.applied and finalization.retry_intent is None:
            projected = apps.get_model("workflows", "StepRun").objects.select_related("run").get(pk=attempt.step_run_id)
            dispatch_model.objects.schedule_advance(projected.run, available_at=timezone.now())
            if projected.status == StepRunStatus.WAITING and projected.wait_until is not None:
                dispatch_model.objects.schedule_advance(
                    projected.run, available_at=projected.wait_until
                )
        transaction.on_commit(enqueue_dispatch_publisher)

    mode = impl_class.execution_mode
    try:
        if mode == StepExecutionMode.DATABASE_COMMAND:
            with transaction.atomic():
                finalization = attempt_model.objects.execute_database_command(
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

    with system_context(reason="workflows.engine.execute_dispatch.finalize"), transaction.atomic():
        finalization = attempt_model.objects.finalize(
            attempt_id,
            lease_token=lease_token,
            result=result,
            recorded_at=timezone.now(),
        )
        schedule_result(finalization)
    return {"executed": 1}


def cancel(run: Any) -> None:
    """Cancel a run and commit durable intents for its owned active children."""

    run_model = apps.get_model("workflows", "WorkflowRun")
    step_run_model = apps.get_model("workflows", "StepRun")
    run_id = run.pk if hasattr(run, "pk") else int(run)
    with system_context(reason="workflows.engine.cancel"), transaction.atomic():
        locked = run_model.objects.lock_if_supported().get(pk=run_id)
        if locked.status in RunStatus.TERMINAL:
            return
        owned_children = list(run_model.objects.filter(
            parent_step_run__run=locked,
            parent_relation="owned_call",
            status__in=[RunStatus.PENDING, RunStatus.RUNNING, RunStatus.WAITING],
        ).order_by("pk"))
        for step_run in step_run_model.objects.lock_if_supported().filter(run=locked).order_by("pk"):
            was_waiting = step_run.status == StepRunStatus.WAITING
            apps.get_model("workflows", "StepAttempt").objects.cancel_current(step_run.pk, at=timezone.now())
            if was_waiting:
                apps.get_model("workflows", "Decision").objects.expire_canceled_suspension(
                    step_run.pk, resolved_by="workflows/cancel"
                )
        locked.mark_canceled()
        dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
        for child in owned_children:
            dispatch_model.objects.schedule_child_cancel(child)
        if owned_children:
            transaction.on_commit(enqueue_dispatch_publisher)


def expire_pending_decisions(run: Any, *, resolved_by: str) -> int:
    """Expire every pending decision for ``run`` through the engine owner."""

    run_model = apps.get_model("workflows", "WorkflowRun")
    step_run_model = apps.get_model("workflows", "StepRun")
    run_id = run.pk if hasattr(run, "pk") else int(run)
    expired = 0
    with system_context(reason="workflows.engine.expire_pending_decisions"), transaction.atomic():
        locked_run = run_model.objects.lock_if_supported().get(pk=run_id)
        step_runs = step_run_model.objects.lock_if_supported().filter(run=locked_run).order_by("pk")
        for step_run in step_runs:
            expired += _expire_pending_decisions(step_run, resolved_by=resolved_by)
            expired += apps.get_model("workflows", "Decision").objects.expire_orphaned_suspensions(
                step_run.pk, resolved_by=resolved_by
            )
    return expired


def expire_orphaned_decisions(run: Any, *, resolved_by: str) -> int:
    """Expire only pending retained Decisions whose suspension is no longer active."""

    run_model = apps.get_model("workflows", "WorkflowRun")
    step_run_model = apps.get_model("workflows", "StepRun")
    run_id = run.pk if hasattr(run, "pk") else int(run)
    expired = 0
    with system_context(reason="workflows.engine.expire_orphaned_decisions"), transaction.atomic():
        locked_run = run_model.objects.lock_if_supported().get(pk=run_id)
        step_runs = step_run_model.objects.lock_if_supported().filter(run=locked_run).order_by("pk")
        for step_run in step_runs:
            expired += apps.get_model("workflows", "Decision").objects.expire_orphaned_suspensions(
                step_run.pk, resolved_by=resolved_by
            )
    return expired


def sweep(*, now: datetime | None = None) -> dict[str, int]:
    """Advance runs whose durable wake time is due."""

    timestamp = now or timezone.now()
    run_model = apps.get_model("workflows", "WorkflowRun")
    with system_context(reason="workflows.engine.sweep"):
        run_ids = list(
            run_model.objects.filter(wake_at__lte=timestamp)
            .filter(status__in=[RunStatus.RUNNING, RunStatus.WAITING])
            .order_by("pk")
            .values_list("pk", flat=True)
        )
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    dispatch_ids: list[int] = []
    with system_context(reason="workflows.engine.sweep.schedule"), transaction.atomic():
        for run_id in run_ids:
            run = run_model.objects.get(pk=run_id)
            dispatch = dispatch_model.objects.schedule_advance(run, available_at=timestamp)
            dispatch_ids.append(dispatch.pk)
        if run_ids:
            transaction.on_commit(enqueue_dispatch_publisher)
    for dispatch_id in dispatch_ids:
        advance_dispatch(dispatch_id, now=timestamp)
    return {"runs": len(run_ids)}


def reap(*, now: datetime | None = None) -> dict[str, int]:
    """Fail started step-runs whose heartbeat is past the configured deadline."""

    timestamp = now or timezone.now()
    deadline = timestamp - _heartbeat_timeout()
    step_run_model = apps.get_model("workflows", "StepRun")
    reaped = 0
    with system_context(reason="workflows.engine.reap.discover"):
        stale_ids = list(
            step_run_model.objects
            .filter(status=StepRunStatus.STARTED)
            .filter(
                models.Q(current_attempt__isnull=False, current_attempt__heartbeat_at__lt=deadline)
                | models.Q(current_attempt__isnull=True, heartbeat_at__lt=deadline)
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )
    for step_run_id in stale_ids:
        with system_context(reason="workflows.engine.reap"), transaction.atomic():
            step_run = step_run_model.objects.select_related("run").get(pk=step_run_id)
            if apps.get_model("workflows", "StepAttempt").objects.timeout_current(
                step_run.pk, heartbeat_before=deadline, at=timestamp
            ):
                apps.get_model("workflows", "WorkflowDispatch").objects.schedule_advance(
                    step_run.run, available_at=timestamp
                )
                transaction.on_commit(enqueue_dispatch_publisher)
                reaped += 1
    return {"reaped": reaped}


def decide(decision: Any, verdict: str, *, payload: Any = None, actor: Any = None) -> DecisionAttemptResult:
    """Attempt one actor-authorized resolution and return its validation outcome."""

    target = _verdict_for_verb(verdict)
    actor_ref = _actor_ref(actor)
    decision_model = apps.get_model("workflows", "Decision")
    decision_id = decision.pk if hasattr(decision, "pk") else int(decision)
    with system_context(reason="workflows.engine.decide.load"):
        current = decision_model.objects.get(pk=decision_id)
    _check_decision_act(current, actor_ref)

    if current.suspension_attempt_id is None:
        raise ValidationError({"decision": "Decision resolution requires retained suspension evidence."})
    validation_error: ValidationError | None = None
    with (
        system_context(reason="workflows.engine.decide.retained"),
        transaction.atomic(),
        decision_model.objects._resolution_owner(decision_id),
    ):
        try:
            resolution = _validate_resolution(current, payload, actor=actor_ref)
            _assert_selected_action_verdict(current, resolution, target)
        except ValidationError as resolution_error:
            locked, exhausted = decision_model.objects.record_invalid_retained(decision_id)
            validation_error = resolution_error
            if exhausted:
                locked.step_run.__class__.objects.fail_retained_decisions(
                    locked.step_run_id,
                    error=f"Decision resolution failed validation: {resolution_error}",
                )
            else:
                _schedule_decision_timers(locked)
        else:
            locked = decision_model.objects.resolve_retained(
                decision_id,
                verdict=target,
                resolution=resolution,
                resolved_by=str(actor_ref),
                at=timezone.now(),
            )
            if locked is None:
                decision_model.objects.complete_retained_resolution(decision_id)
                return DecisionAttemptResult(current)
            _apply_decision_policy(locked.step_run)
        apps.get_model("workflows", "WorkflowDispatch").objects.schedule_advance(
            locked.step_run.run, available_at=timezone.now()
        )
        transaction.on_commit(enqueue_dispatch_publisher)
        decision_model.objects.complete_retained_resolution(decision_id)
    return DecisionAttemptResult(locked, validation_error)


def escalate_decision_dispatch(
    dispatch_id: int, *, expected_decision_id: int | None = None,
    expected_generation: int | None = None, now: datetime | None = None
) -> dict[str, int]:
    """Consume one exact durable escalation timer."""

    return _consume_decision_dispatch(
        dispatch_id, WorkflowDispatchKind.DECISION_ESCALATE,
        expected_decision_id=expected_decision_id, expected_generation=expected_generation, now=now
    )


def expire_decision_dispatch(
    dispatch_id: int, *, expected_decision_id: int | None = None,
    expected_generation: int | None = None, now: datetime | None = None
) -> dict[str, int]:
    """Consume one exact durable expiry timer."""

    return _consume_decision_dispatch(
        dispatch_id, WorkflowDispatchKind.DECISION_EXPIRE,
        expected_decision_id=expected_decision_id, expected_generation=expected_generation, now=now
    )


def _consume_decision_dispatch(
    dispatch_id: int, kind: WorkflowDispatchKind, *, expected_decision_id: int | None,
    expected_generation: int | None, now: datetime | None
) -> dict[str, int]:
    timestamp = now or timezone.now()
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    with system_context(reason="workflows.engine.decision_dispatch"), transaction.atomic():
        with dispatch_model.objects._owner_transition(
            dispatch_id=dispatch_id, lease_token=None, at=timestamp, using=dispatch_model.objects.db
        ) as preflight:
            if preflight.disposition != DispatchPreflightDisposition.READY:
                return {"resolved": 0}
            if (
                (expected_decision_id is not None and preflight.envelope.target_id != expected_decision_id)
                or (expected_generation is not None and preflight.envelope.generation != expected_generation)
            ):
                raise ValidationError({"dispatch": "Decision envelope does not match its durable intent."})
            if preflight.envelope.kind != kind or preflight.envelope.generation is None:
                raise ValidationError({"dispatch": "Decision envelope kind does not match its durable intent."})
            verdict = VERDICT_ESCALATED if kind == WorkflowDispatchKind.DECISION_ESCALATE else VERDICT_EXPIRED
            result = _resolve_timed_decision(
                preflight.envelope.target_id,
                preflight.envelope.generation,
                verdict,
                resolved_by=f"workflows/timer:{kind.value}",
                timestamp=timestamp,
            )
            dispatch_model.objects._consume_locked(
                dispatch_id, at=timestamp, fenced=result["resolved"] == 0
            )
            return result


def sweep_decisions(*, now: datetime | None = None) -> dict[str, int]:
    """Retain and consume dispatches for pending decisions whose deadlines are due."""

    timestamp = now or timezone.now()
    decision_model = apps.get_model("workflows", "Decision")
    dispatches: list[tuple[int, WorkflowDispatchKind, int, int]] = []
    with system_context(reason="workflows.engine.decision_sweep"), transaction.atomic():
        expired = list(
            decision_model.objects.filter(verdict=VERDICT_PENDING, expires_at__lte=timestamp)
            .order_by("pk")
            .select_related("step_run__run")
        )
        escalated = list(
            decision_model.objects.filter(verdict=VERDICT_PENDING, escalate_at__lte=timestamp)
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
                dispatch, _created = dispatch_model.objects.schedule_decision(kind, decision)
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
        )
        if kind == WorkflowDispatchKind.DECISION_EXPIRE:
            expired_count += result["resolved"]
        else:
            escalated_count += result["resolved"]
    return {"expired": expired_count, "escalated": escalated_count}


def override_run(run: Any, next_steps: Iterable[Any], *, actor: Any) -> Any:
    """Cancel active rows, insert an override journal row, and schedule next steps."""

    run_model = apps.get_model("workflows", "WorkflowRun")
    step_run_model = apps.get_model("workflows", "StepRun")
    run_id = run.pk if hasattr(run, "pk") else int(run)
    actor_ref = _actor_ref(actor)
    actor_id = actor_user_id(actor_ref)
    step_ids = [step.pk if hasattr(step, "pk") else int(step) for step in next_steps]

    with system_context(reason="workflows.engine.override"), transaction.atomic():
        locked = run_model.objects.lock_if_supported().get(pk=run_id)
        if locked.status in RunStatus.TERMINAL:
            raise ValidationError({"run": "A terminal workflow run cannot be overridden."})
        for step_run in step_run_model.objects.lock_if_supported().filter(
            run=locked,
            status__in=list(StepRunStatus.ACTIVE),
        ):
            if step_run.step_id in step_ids:
                step_run_model.objects.reschedule_for_override(step_run.pk, input={}, at=timezone.now())
            else:
                apps.get_model("workflows", "StepAttempt").objects.cancel_current(step_run.pk, at=timezone.now())
        override = step_run_model.objects.create(
            run=locked,
            step=None,
            system_kind="override",
            status=StepRunStatus.SUCCEEDED,
            output={"next_steps": step_ids},
            outcome="override",
            created_by_id=actor_id,
            updated_by_id=actor_id,
        )
        for step_id in step_ids:
            row = step_run_model.objects.filter(run=locked, step_id=step_id, map_index=-1).first()
            if row is None:
                row = step_run_model.objects.create(
                    run=locked,
                    step_id=step_id,
                    map_index=-1,
                    status=StepRunStatus.SCHEDULED,
                    input={},
                )
            elif row.status in StepRunStatus.TERMINAL:
                row = step_run_model.objects.reschedule_for_override(
                    row.pk, input={}, at=timezone.now()
                )
            row.previous.set([override])
        if locked.status == RunStatus.WAITING:
            locked.resume()
        dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
        dispatch_model.objects.schedule_advance(locked, available_at=timezone.now())
        transaction.on_commit(enqueue_dispatch_publisher)
    return override


def enqueue_advance(run_id: int) -> None:
    """Retain an immediate advance and request transport publication."""

    enqueue_advance_at(run_id, timezone.now())


def enqueue_advance_at(run_id: int, when: datetime) -> None:
    """Retain a timer wake before asking the transport to publish it."""

    with system_context(reason="workflows.engine.schedule_advance"), transaction.atomic():
        run = apps.get_model("workflows", "WorkflowRun").objects.filter(pk=run_id).first()
        if run is None:
            return
        apps.get_model("workflows", "WorkflowDispatch").objects.schedule_advance(run, available_at=when)
        transaction.on_commit(enqueue_dispatch_publisher)


def _schedule_decision_timers(decision: Any) -> None:
    """Schedule deadline jobs for the decision's current attempt."""

    if decision.suspension_attempt_id is None:
        raise ValidationError({"decision": "Decision timers require retained suspension evidence."})
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    if decision.escalate_at is not None:
        dispatch_model.objects.schedule_decision(WorkflowDispatchKind.DECISION_ESCALATE, decision)
    if decision.expires_at is not None:
        dispatch_model.objects.schedule_decision(WorkflowDispatchKind.DECISION_EXPIRE, decision)
    transaction.on_commit(enqueue_dispatch_publisher)


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


def _check_decision_act(decision: Any, actor: SubjectRef) -> None:
    """Raise when ``actor`` cannot act on ``decision``."""

    result = rebac_backend().check_access(subject=actor, action="act", resource=to_object_ref(decision))
    if not result.allowed:
        raise PermissionDenied(f"Denied: {actor} cannot act on workflows/decision:{decision.sqid}")


def _validate_resolution(decision: Any, payload: Any, *, actor: Any = None) -> dict[str, Any]:
    """Validate a decision resolution against its step-owned schema."""

    resolution = payload if payload is not None else {}
    if not isinstance(resolution, dict):
        raise ValidationError({"payload": "Decision payload must be a JSON object."})

    schema = _schema_for_decision(decision)
    if schema is None:
        return dict(resolution)
    if isinstance(schema, dict):
        return _validate_mapping_schema(schema, resolution, actor=actor)
    if hasattr(schema, "model_validate"):
        try:
            parsed = schema.model_validate(resolution)
        except PydanticValidationError as error:
            raise _resolution_validation_error(error) from error
        dumped = parsed.model_dump()
        return cast(dict[str, Any], dumped)
    return dict(resolution)


def _assert_selected_action_verdict(decision: Any, resolution: dict[str, Any], verdict: Verdict) -> None:
    """Prevent an independent mutation verdict from overriding the selected action."""

    schema = _schema_for_decision(decision)
    contract = compile_decision_action_schema(schema) if isinstance(schema, dict) else None
    if contract is not None and contract.verdict_for(resolution.get("action")) != str(verdict):
        raise ValidationError({"verdict": "The selected action maps to a different native verdict."})


def _schema_for_decision(decision: Any) -> Any | None:
    """Return the resolution schema owned by the suspended step."""

    form_schema = decision.form_schema
    if form_schema is not None:
        return form_schema
    if decision.step_run.step_id is None:
        return None
    impl_class = decision.step_run.step.resolve_impl("step_class")
    return getattr(impl_class, "decision_schema", None)


def _validate_mapping_schema(
    schema: dict[str, Any],
    resolution: dict[str, Any],
    *,
    actor: Any = None,
) -> dict[str, Any]:
    """Normalize a JSON-authored resolution, then enforce its full schema."""

    if not schema:
        return dict(resolution)
    contract = compile_decision_action_schema(schema)
    if contract is not None:
        submitted_context = set(contract.context_fields).intersection(resolution)
        if submitted_context:
            raise ValidationError({
                name: "Decision context cannot be submitted as a resolution."
                for name in sorted(submitted_context)
            })
        selected = resolution.get("action")
        branch = contract.branches.get(selected) if isinstance(selected, str) else None
        if branch is None:
            raise ValidationError({"action": "Choose one declared Decision action."})
        extraneous = set(resolution) - set(branch["properties"])
        if extraneous:
            raise ValidationError({
                name: "This field is not permitted for the selected action."
                for name in sorted(extraneous)
            })
        try:
            parsed = _mapping_schema_model(branch, name="DecisionActionResolution").model_validate(
                resolution
            )
        except PydanticValidationError as error:
            raise _resolution_validation_error(error) from error
        submitted = cast(dict[str, Any], parsed.model_dump(exclude_unset=True))
        schema_errors: dict[str, list[str]] = {}
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(submitted),
            key=lambda item: (tuple(str(part) for part in item.path), item.message),
        ):
            field = ".".join(str(component) for component in error.path) or "payload"
            schema_errors.setdefault(field, []).append(error.message)
        if schema_errors:
            raise ValidationError(schema_errors)
        _validate_relation_fields(schema, submitted, actor)
        return submitted
    if schema.get("type", "object") != "object":
        raise ValidationError({"payload": "Decision schema root type must be object."})
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValidationError({"payload": "Decision schema properties must be an object."})
    context_fields = {
        str(field_name)
        for field_name, field_schema in properties.items()
        if isinstance(field_schema, dict) and field_schema.get("layout") == "context"
    }
    submitted_context = context_fields.intersection(resolution)
    if submitted_context:
        raise ValidationError({
            field_name: "Decision context cannot be submitted as a resolution."
            for field_name in sorted(submitted_context)
        })
    resolution_schema = dict(schema)
    resolution_schema["properties"] = {
        field_name: field_schema
        for field_name, field_schema in properties.items()
        if str(field_name) not in context_fields
    }
    if isinstance(schema.get("required"), list):
        resolution_schema["required"] = [
            field_name for field_name in schema["required"]
            if str(field_name) not in context_fields
        ]
    # Human-paced decisions rebuild per attempt; memoize by schema-dict hash before bulk or programmatic reuse.
    model = _mapping_schema_model(resolution_schema, name="DecisionResolution")
    try:
        parsed = model.model_validate(resolution)
    except PydanticValidationError as error:
        raise _resolution_validation_error(error) from error
    submitted = cast(dict[str, Any], parsed.model_dump(exclude_unset=True))
    schema_errors: dict[str, list[str]] = {}
    for error in sorted(
        Draft202012Validator(resolution_schema).iter_errors(submitted),
        key=lambda item: (tuple(str(part) for part in item.path), item.message),
    ):
        field = ".".join(str(component) for component in error.path) or "payload"
        schema_errors.setdefault(field, []).append(error.message)
    if schema_errors:
        raise ValidationError(schema_errors)
    _validate_relation_fields(resolution_schema, submitted, actor)
    return submitted


def _validate_relation_fields(schema: dict[str, Any], resolution: dict[str, Any], actor: Any) -> None:
    """Re-check every submitted relation id against the resolving actor's access.

    Decision resolution is the only point that still holds the acting subject —
    the execute worker runs detached with no actor — so the ``relation`` facts a
    schema declares are enforced here: the id must address a row of the declared
    resource that ``actor`` may ``write``, checked through the model's own
    REBAC-scoped manager. Models without a REBAC row policy degrade to an
    existence check.
    """

    errors: dict[str, list[str]] = {}
    _collect_relation_errors(schema, resolution, actor, path="", errors=errors)
    if errors:
        raise ValidationError(errors)


def _collect_relation_errors(
    schema: dict[str, Any],
    value: Any,
    actor: Any,
    *,
    path: str,
    errors: dict[str, list[str]],
) -> None:
    """Walk one object schema level and collect relation failures by dotted path."""

    properties = schema.get("properties")
    if not isinstance(properties, dict) or not isinstance(value, dict):
        return
    for field_name, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        field_path = f"{path}.{field_name}" if path else str(field_name)
        field_value = value.get(field_name)
        relation = spec.get("relation")
        if isinstance(relation, dict):
            message = _relation_error(relation, field_value, actor)
            if message:
                errors.setdefault(field_path, []).append(message)
            continue
        field_type = spec.get("type")
        items = spec.get("items")
        if field_type == "object":
            _collect_relation_errors(spec, field_value, actor, path=field_path, errors=errors)
        elif field_type == "array" and isinstance(items, dict) and isinstance(field_value, list):
            item_relation = items.get("relation")
            for index, item in enumerate(field_value):
                item_path = f"{field_path}.{index}"
                if isinstance(item_relation, dict):
                    message = _relation_error(item_relation, item, actor)
                    if message:
                        errors.setdefault(item_path, []).append(message)
                    continue
                _collect_relation_errors(items, item, actor, path=item_path, errors=errors)


_RELATION_PERMISSION = re.compile(r"^[a-z][a-z0-9_]*$")


def _relation_error(relation: dict[str, Any], value: Any, actor: Any) -> str | None:
    """Return the field error for one submitted relation id, or None when valid.

    Unknown ids, wrong-model ids, and ids outside the declared permission scope
    share one message so the response does not disclose which records exist.
    """

    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return "Relation value must be a record id."
    resource = str(relation.get("resource") or "")
    app_label, separator, model_name = resource.partition(".")
    if not separator:
        return "Relation resource must be an app_label.Model string."
    try:
        model = apps.get_model(app_label, model_name)
    except (LookupError, ValueError):
        return f"Relation resource {resource!r} is not installed."
    permission = relation.get("permission", "write")
    if not isinstance(permission, str) or _RELATION_PERMISSION.fullmatch(permission) is None:
        return "Relation value must reference a permitted record."
    scoped = read_scoped_queryset(model, actor, action=permission)
    instance = instance_from_public_id(model, value, queryset=scoped)
    if instance is None:
        return (
            "Relation value must reference a record you can write."
            if permission == "write"
            else "Relation value must reference a permitted record."
        )
    return None


def _resolution_validation_error(error: PydanticValidationError) -> ValidationError:
    """Translate pydantic failures into field-keyed Django validation errors."""

    field_errors: dict[str, list[str]] = {}
    for detail in error.errors(include_url=False):
        field = ".".join(str(component) for component in detail["loc"]) or "payload"
        field_errors.setdefault(field, []).append(str(detail["msg"]))
    return ValidationError(field_errors)


def _mapping_schema_model(schema: dict[str, Any], *, name: str) -> Any:
    """Compile one object schema and its nested properties into a pydantic model."""

    required = set(schema.get("required", ()))
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValidationError({"payload": "Decision schema properties must be an object."})
    fields: dict[str, tuple[Any, Any]] = {}
    for field_name, spec in properties.items():
        field_schema = spec if isinstance(spec, dict) else {}
        annotation = _annotation_for_field_schema(field_schema)
        default = ... if field_name in required else None
        fields[str(field_name)] = (annotation, default)
    for field_name in required:
        fields.setdefault(str(field_name), (Any, ...))
    model_factory = cast(Any, create_model)
    return model_factory(name, **fields)


def _annotation_for_field_schema(schema: dict[str, Any]) -> Any:
    """Return a pydantic annotation for the supported decision-schema subset."""

    if "const" in schema:
        return Literal.__getitem__((schema["const"],))
    if "enum" in schema and isinstance(schema["enum"], list):
        return Literal.__getitem__(tuple(schema["enum"]))
    field_type = schema.get("type", "any")
    if field_type == "object":
        if isinstance(schema.get("properties"), dict):
            return _mapping_schema_model(schema, name="DecisionResolutionObject")
        return dict[str, Any]
    if field_type == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            return list.__class_getitem__(_annotation_for_field_schema(items))
        return list[Any]
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "any": Any,
    }.get(str(field_type), Any)


def _apply_decision_policy(step_run: Any) -> None:
    """Complete ``step_run`` when its decision collection satisfies its policy."""

    if step_run.status != StepRunStatus.WAITING:
        return
    decisions = list(
        step_run.decisions.filter(suspension_attempt_id=step_run.current_attempt_id)
        .order_by("priority", "pk")
    )
    outcome = step_run.decision_gate.outcome(decisions)
    if outcome is None:
        return
    apps.get_model("workflows", "StepRun").objects.settle_retained_decisions(
        step_run.pk,
        outcome=outcome,
        decision_ids=tuple(decision.pk for decision in decisions),
        at=timezone.now(),
    )


def _resolve_timed_decision(
    decision_id: int,
    attempt: int,
    verdict: Verdict,
    *,
    resolved_by: str,
    timestamp: datetime,
) -> dict[str, int]:
    """Resolve a deadline decision if the attempt and deadline are still current."""

    decision_model = apps.get_model("workflows", "Decision")
    with system_context(reason="workflows.engine.decision_timer"), transaction.atomic():
        discovered = decision_model.objects.filter(pk=decision_id).first()
        if discovered is None:
            return {"resolved": 0}
        if discovered.suspension_attempt_id is None:
            raise ValidationError({"decision": "Decision timers require retained suspension evidence."})
        with decision_model.objects._resolution_owner(decision_id):
            deadline = "escalate_at" if verdict == VERDICT_ESCALATED else "expires_at"
            decision = decision_model.objects.resolve_retained(
                decision_id,
                verdict=verdict,
                resolution={},
                resolved_by=resolved_by,
                at=timestamp,
                expected_attempts=attempt,
                deadline=deadline,
            )
            if decision is None:
                decision_model.objects.complete_retained_resolution(decision_id)
                return {"resolved": 0}
            _apply_decision_policy(decision.step_run)
            apps.get_model("workflows", "WorkflowDispatch").objects.schedule_advance(
                decision.step_run.run, available_at=timestamp
            )
            transaction.on_commit(enqueue_dispatch_publisher)
            decision_model.objects.complete_retained_resolution(decision_id)
            return {"resolved": 1}


def _expire_pending_decisions(step_run: Any, *, resolved_by: str) -> int:
    """Expire pending decisions attached to one step-run."""

    decision_model = apps.get_model("workflows", "Decision")
    pending = decision_model.objects.filter(
        step_run_id=step_run.pk,
        suspension_attempt_id=step_run.current_attempt_id,
        verdict=VERDICT_PENDING,
    )
    pending_count = pending.count()
    pending_id = pending.order_by("priority", "pk").values_list("pk", flat=True).first()
    if pending_id is None:
        return 0
    with decision_model.objects._resolution_owner(pending_id):
        decision = decision_model.objects.expire_retained_suspension(
            pending_id, resolved_by=resolved_by
        )
        if decision is None:
            decision_model.objects.complete_retained_resolution(pending_id)
            return 0
        _apply_decision_policy(decision.step_run)
        apps.get_model("workflows", "WorkflowDispatch").objects.schedule_advance(
            decision.step_run.run, available_at=timezone.now()
        )
        transaction.on_commit(enqueue_dispatch_publisher)
        decision_model.objects.complete_retained_resolution(pending_id)
    return pending_count


def _activate_run_if_needed(run: Any, *, timestamp: datetime) -> None:
    if run.status == RunStatus.PENDING:
        run.mark_running()
    elif run.status == RunStatus.WAITING and _has_due_wait(run, timestamp=timestamp):
        run.resume()


def _has_due_wait(run: Any, *, timestamp: datetime) -> bool:
    return run.step_runs.filter(
        status=StepRunStatus.WAITING,
        wait_until__isnull=False,
        wait_until__lte=timestamp,
    ).exists()


def _route_completed_steps(run: Any) -> None:
    if run.origin == RunOrigin.TEST and run.test_scope == WorkflowScope.NODE:
        return
    for step_run in _terminal_step_runs(run):
        if step_run.step_id is None:
            continue
        if step_run.status == StepRunStatus.SUCCEEDED:
            _route_success(run, step_run)
        elif step_run.status == StepRunStatus.SKIPPED:
            _route_skip(run, step_run)
        elif step_run.status in {StepRunStatus.FAILED, StepRunStatus.CANCELED}:
            _route_done(run, step_run)


def _terminal_step_runs(run: Any) -> Iterable[Any]:
    return (
        run.step_runs.select_related("step")
        .filter(status__in=list(StepRunStatus.TERMINAL))
        .filter(map_index=-1)
        .filter(step__isnull=False)
        .order_by("pk")
    )


def _process_recovery_map_aggregate(run: Any, *, timestamp: datetime) -> None:
    """Project a successful FRESH Map member through the ordinary Map join owner."""

    if run.origin != RunOrigin.RECOVERY or run.recovery_source_attempt_id is None:
        return
    source = run.recovery_source_attempt
    source_step_run = source.step_run
    if source_step_run.map_index < 0 or source.map_expansion_id is None:
        return
    recovered = run.step_runs.filter(
        step_id=source_step_run.step_id,
        map_index=source_step_run.map_index,
    ).first()
    if recovered is None or recovered.status != StepRunStatus.SUCCEEDED:
        return
    apps.get_model("workflows", "StepAttempt").objects.record_recovery_map_aggregate(
        recovered.pk, at=timestamp,
    )


def _process_map_steps(run: Any, *, timestamp: datetime) -> bool:
    locked_rows = list(
        run.step_runs.lock_if_supported()
        .select_related("step")
        .order_by("pk")
    )
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
                run.test_fixtures.filter(
                    step_id=step_run.step_id,
                    role=FixtureRole.OUTPUT,
                    item_index__isnull=True,
                ).first()
                if run.origin == RunOrigin.TEST
                else None
            )
            if fixture is not None:
                apps.get_model("workflows", "StepAttempt").objects.record_test_fixture(
                    fixture, at=timestamp, due_step_run_id=step_run.pk
                )
                apps.get_model("workflows", "WorkflowDispatch").objects.schedule_advance(
                    run, available_at=timestamp
                )
                continue
            if not _expand_retained_map_step(run, step_run, timestamp=timestamp):
                return False
        if step_run.status == StepRunStatus.WAITING:
            if not _complete_retained_map_step_if_ready(run, step_run, timestamp=timestamp):
                return False
    return True


def _expand_retained_map_step(run: Any, step_run: Any, *, timestamp: datetime) -> bool:
    """Retain one Map expansion generation before exposing any body slot."""

    recorded = apps.get_model("workflows", "StepAttempt").objects.record_map_expansion(step_run, at=timestamp)
    if recorded is None:
        return False
    expansion, plan = recorded
    target = apps.get_model("workflows", "Step").objects.get(pk=plan.target_id) if plan.target_id is not None else None
    items = plan.items
    if target is not None:
        _ensure_map_children(run, step_run, target=target, items=items)
        apps.get_model("workflows", "StepRun").objects.bind_map_membership(
            run_id=run.pk,
            target_id=target.pk,
            expansion_attempt_id=expansion.pk,
            item_count=len(items),
            at=timestamp,
        )
    if not items:
        return _complete_retained_map_step_if_ready(
            run, step_run, timestamp=timestamp, expansion_attempt_id=expansion.pk
        )
    return True


def _complete_retained_map_step_if_ready(
    run: Any,
    step_run: Any,
    *,
    timestamp: datetime,
    expansion_attempt_id: int | None = None,
) -> bool:
    """Aggregate only the exact members of the current retained expansion."""

    del run
    expansion_id = expansion_attempt_id or step_run.current_attempt_id
    if expansion_id is None:
        raise ValidationError({"attempt": "Retained Map controller has no current expansion."})
    expansion = apps.get_model("workflows", "StepAttempt").objects.get(pk=expansion_id)
    checkpoint = expansion.checkpoint if expansion.checkpoint_present else None
    map_state = checkpoint.get("map") if isinstance(checkpoint, dict) else None
    if isinstance(map_state, dict):
        target_id = map_state.get("target_step_id")
        items = map_state.get("items")
        if target_id is not None and isinstance(items, list):
            target = apps.get_model("workflows", "Step").objects.get(pk=target_id)
            # The membership owner validates the exact current expansion before
            # recovery is allowed to materialize any missing child rows.
            apps.get_model("workflows", "StepRun").objects.bind_map_membership(
                run_id=step_run.run_id,
                target_id=target.pk,
                expansion_attempt_id=expansion_id,
                item_count=len(items),
                at=timestamp,
            )
            _ensure_map_children(step_run.run, step_run, target=target, items=items)
            apps.get_model("workflows", "StepRun").objects.bind_map_membership(
                run_id=step_run.run_id,
                target_id=target.pk,
                expansion_attempt_id=expansion_id,
                item_count=len(items),
                at=timestamp,
            )
    apps.get_model("workflows", "StepAttempt").objects.record_map_aggregate(
        step_run.pk,
        expansion_attempt_id=expansion_id,
        at=timestamp,
    )
    return True


def _map_capacity_allows(run: Any, *, target: Any, items: list[Any]) -> bool:
    """Reserve journal capacity for one Map expansion before creating children."""

    step_runs = run.step_runs.lock_if_supported()
    existing_indexes = set(
        step_runs.filter(step=target, map_index__gte=0, map_index__lt=len(items))
        .values_list("map_index", flat=True)
    )
    substituted_indexes = set()
    if run.origin == RunOrigin.TEST:
        substituted_indexes = set(
            run.test_fixtures.filter(
                step=target,
                role=FixtureRole.OUTPUT,
                item_index__gte=0,
                item_index__lt=len(items),
            ).values_list("item_index", flat=True)
        )
    missing_children = len(set(range(len(items))) - existing_indexes - substituted_indexes)
    if _workflow_capacity_allows(run, additional=missing_children):
        return True
    return False


def _workflow_capacity_allows(run: Any, *, additional: int = 0) -> bool:
    """Fail before executing work that cannot fit the run journal budget."""

    scheduled = list(
        run.step_runs.filter(status=StepRunStatus.SCHEDULED).values_list(
            "step_id", "map_index"
        )
    )
    substituted: set[tuple[int, int | None]] = set()
    if run.origin == RunOrigin.TEST:
        substituted = {
            (fixture.step_id, fixture.item_index)
            for fixture in run.test_fixtures.filter(role=FixtureRole.OUTPUT)
        }
    admitted = sum(
        (step_id, None if map_index == -1 else map_index) not in substituted
        for step_id, map_index in scheduled
    )
    if run.steps_taken + admitted + additional <= run.workflow.max_steps:
        return True
    run.mark_failed(f"Workflow exceeded max_steps={run.workflow.max_steps}.")
    return False


def _ensure_map_children(run: Any, step_run: Any, *, target: Any, items: list[Any]) -> None:
    step_run_model = apps.get_model("workflows", "StepRun")
    for index, item in enumerate(items):
        child, _ = step_run_model.objects.get_or_create(
            run=run,
            step=target,
            map_index=index,
            defaults={
                "status": StepRunStatus.SCHEDULED,
                "input": _map_child_input(item),
            },
        )
        child.previous.add(step_run)


def _map_child_input(item: Any) -> Any:
    if isinstance(item, Mapping):
        return dict(item)
    return {"item": item}


def _map_output(children: list[Any]) -> dict[str, Any]:
    results = [
        {
            "map_index": child.map_index,
            "status": str(child.status),
            "outcome": child.outcome,
            "output": child.output,
            "output_present": child.output_present,
            "error": child.error,
        }
        for child in children
    ]
    successes = sum(1 for child in children if child.status == StepRunStatus.SUCCEEDED
                    and child.outcome not in {"child_failed", "child_canceled"})
    failures = sum(1 for child in children if child.status in {StepRunStatus.FAILED, StepRunStatus.CANCELED}
                   or child.outcome in {"child_failed", "child_canceled"})
    return {
        "total": len(children),
        "successes": successes,
        "failures": failures,
        "results": results,
    }


def _route_success(run: Any, step_run: Any) -> None:
    outgoing = list(step_run.step.outgoing_edges.select_related("target").order_by("pk"))
    by_target: dict[int, tuple[Any, list[Any]]] = {}
    for edge in outgoing:
        target, edges = by_target.setdefault(edge.target_id, (edge.target, []))
        edges.append(edge)
    for target, edges in by_target.values():
        if any(not edge.condition or edge.condition == step_run.outcome for edge in edges):
            _maybe_schedule_target(run, target)
        elif target.incoming_edges.exclude(source_id=step_run.step_id).exists():
            _maybe_schedule_target(run, target)
        else:
            _ensure_skipped(run, target, previous=[step_run])


def _route_skip(run: Any, step_run: Any) -> None:
    outgoing = list(step_run.step.outgoing_edges.select_related("target").order_by("pk"))
    by_target: dict[int, tuple[Any, list[Any]]] = {}
    for edge in outgoing:
        target, edges = by_target.setdefault(edge.target_id, (edge.target, []))
        edges.append(edge)
    for target, edges in by_target.values():
        if any(not edge.condition for edge in edges) or target.incoming_edges.exclude(
            source_id=step_run.step_id
        ).exists():
            _maybe_schedule_target(run, target)
        else:
            _ensure_skipped(run, target, previous=[step_run])


def _route_done(run: Any, step_run: Any) -> None:
    for edge in step_run.step.outgoing_edges.select_related("target").order_by("pk"):
        if edge.condition and edge.condition != step_run.outcome:
            continue
        _maybe_schedule_target(run, edge.target, routed_row=step_run)


def _maybe_schedule_target(run: Any, target: Any, *, routed_row: Any | None = None) -> Any | None:
    step_run_model = apps.get_model("workflows", "StepRun")
    existing = step_run_model.objects.filter(run=run, step=target, map_index=-1).first()
    if existing is not None:
        return existing
    previous, statuses = _upstream_join_state(run, target, routed_row=routed_row)
    if not statuses:
        return None
    decision = _join_decision(target.join_rule, statuses)
    if decision == "skip":
        return _ensure_skipped(run, target, previous=previous)
    if decision != "run":
        return None
    step_run = step_run_model.objects.create(
        run=run,
        step=target,
        map_index=-1,
        status=StepRunStatus.SCHEDULED,
        input=_input_from_previous(previous),
    )
    step_run.previous.set(previous)
    return step_run


def _ensure_skipped(run: Any, step: Any, *, previous: list[Any]) -> Any:
    step_run_model = apps.get_model("workflows", "StepRun")
    step_run = step_run_model.objects.filter(run=run, step=step, map_index=-1).first()
    if step_run is None:
        step_run = step_run_model.objects.create(
            run=run,
            step=step,
            map_index=-1,
            status=StepRunStatus.SKIPPED,
            input=_input_from_previous(previous),
        )
        step_run.previous.set(previous)
    elif step_run.status in {StepRunStatus.SCHEDULED, StepRunStatus.WAITING}:
        step_run.mark_skipped()
    else:
        return step_run

    _route_skip(run, step_run)
    return step_run


def _upstream_join_state(
    run: Any, target: Any, *, routed_row: Any | None = None,
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
    for edge in target.incoming_edges.select_related("source").order_by("pk"):
        by_source.setdefault(edge.source_id, []).append(edge)
    previous: list[Any] = []
    statuses: list[Any | None] = []
    for source_id, edges in by_source.items():
        row = step_run_model.objects.filter(
            run=run, step_id=source_id, map_index=-1,
        ).first()
        if row is None:
            statuses.append(None)
            continue
        effective_status = (
            StepRunStatus.SUCCEEDED if _same_step_run(row, routed_row) else row.status
        )
        if effective_status in StepRunStatus.TERMINAL:
            route_matches = any(
                not edge.condition or edge.condition == row.outcome for edge in edges
            )
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


def _claim_due_steps(run: Any, *, timestamp: datetime) -> list[int]:
    locked_rows = list(
        run.step_runs.lock_if_supported()
        .select_related("step", "current_attempt", "current_map_expansion")
        .order_by("pk")
    )
    due = [
        row
        for row in locked_rows
        if (
            row.status == StepRunStatus.SCHEDULED
            or (
                row.status == StepRunStatus.WAITING
                and row.wait_until is not None
                and row.wait_until <= timestamp
            )
        )
        and not (
            row.step_id is not None
            and row.step.step_class == MapStep.key
            and row.map_index == -1
        )
        and run.allows_test_step(row.step)
    ]
    if not due:
        return []
    fixture_by_slot: dict[tuple[int, int | None], Any] = {}
    if run.origin == RunOrigin.TEST:
        fixture_by_slot = {
            (fixture.step_id, fixture.item_index): fixture
            for fixture in run.test_fixtures.filter(role=FixtureRole.OUTPUT)
        }
    substituted = [
        row
        for row in due
        if (row.step_id, None if row.map_index == -1 else row.map_index) in fixture_by_slot
    ]
    physical_due = [row for row in due if row not in substituted]
    if run.steps_taken + len(physical_due) > run.workflow.max_steps:
        run.mark_failed(f"Workflow exceeded max_steps={run.workflow.max_steps}.")
        return []

    claimed: list[int] = []
    for step_run in substituted:
        fixture = fixture_by_slot[(step_run.step_id, None if step_run.map_index == -1 else step_run.map_index)]
        apps.get_model("workflows", "StepAttempt").objects.record_test_fixture(
            fixture, at=timestamp, due_step_run_id=step_run.pk
        )
        apps.get_model("workflows", "WorkflowDispatch").objects.schedule_advance(run, available_at=timestamp)
        claimed.append(step_run.pk)
    due = physical_due
    if not due:
        if claimed:
            transaction.on_commit(enqueue_dispatch_publisher)
        return claimed

    preparations = [
        _prepare_attempt_input(run, step_run, source_rows=locked_rows)
        for step_run in due
    ]
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
            apps.get_model("workflows", "StepAttempt").objects.fail_preparation(
                step_run,
                cause=cause,
                input=preparation.input,
                map_item=preparation.map_item,
                test_fixture=preparation.test_fixture,
                result=preparation.failure,
                claimed_at=timestamp,
                recorded_at=timestamp,
            )
            apps.get_model("workflows", "WorkflowDispatch").objects.schedule_advance(run, available_at=timestamp)
            claimed.append(step_run.pk)
            continue
        claim = apps.get_model("workflows", "StepAttempt").objects.claim(
            step_run,
            cause=cause,
            input=preparation.input,
            map_item=preparation.map_item,
            test_fixture=preparation.test_fixture,
            claimed_at=timestamp,
        )
        apps.get_model("workflows", "WorkflowDispatch").objects.schedule_execute(claim.attempt)
        claimed.append(step_run.pk)
    if claimed:
        transaction.on_commit(enqueue_dispatch_publisher)
    return claimed


def _prepare_attempt_input(
    run: Any, step_run: Any, *, source_rows: list[Any]
) -> _AttemptPreparation:
    """Resolve one immutable ordinary-step input before any physical invocation."""

    if (
        run.origin == RunOrigin.RECOVERY
        and run.recovery_source_attempt_id is not None
        and run.recovery_source_attempt.step_run.step_id == step_run.step_id
        and run.recovery_source_attempt.step_run.map_index == step_run.map_index
        and not (
            run.recovery_mode == str(RecoveryMode.FRESH)
            and run.recovery_source_attempt.result_kind
            == str(AttemptResultKind.PREPARATION_ERROR)
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
            AttemptInput(source.input_present, source.input, {
                "kind": "recovery_input",
                "attempt_id": source.pk,
                "source_provenance": source.input_provenance,
            }),
            map_item=map_item,
        )

    if step_run.status == StepRunStatus.WAITING and step_run.current_attempt_id is not None:
        previous = apps.get_model("workflows", "StepAttempt").objects.get(pk=step_run.current_attempt_id)
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
        fixture = run.test_fixtures.filter(
            step_id=step_run.step_id,
            role=FixtureRole.MAP_ITEM,
            item_index=step_run.map_index,
        ).first()
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
        for evidence in run.recovery_evidence.select_related(
            "step", "source_attempt"
        ).order_by("pk"):
            source_attempt = evidence.source_attempt
            gate_decisions = (
                list(source_attempt.decisions.order_by("priority", "pk"))
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
                    **({"settled_decision_ids": source_attempt.decision_settlement["decision_ids"]}
                       if gate_output is not None else {}),
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
                source.decisions.filter(suspension_attempt_id=attempt.pk)
                .order_by("priority", "pk")
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
                {"settled_decision_ids": attempt.decision_settlement["decision_ids"]}
                if valid_settled_decisions
                else {}
            ),
        }
        sources.setdefault(source.step.key, (
            SourceValue(
                JsonPresence(
                    True if valid_settled_decisions else attempt.output_present,
                    source.output if valid_settled_decisions else attempt.output,
                ),
                provenance,
            )
            if valid_done or valid_settled_decisions
            else UnavailableSource("source_unavailable", "The referenced retained output is unavailable.", provenance)
        ))
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
        return _AttemptPreparation(
            AttemptInput(False, None, provenance), failure, map_item_source, fixture
        )
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
        error.errors(include_url=False)
        if isinstance(error, PydanticValidationError)
        else [{"message": str(error)}]
    )
    return AttemptResult(
        AttemptResultKind.PREPARATION_ERROR,
        error=message,
        stacktrace=json.dumps(details, sort_keys=True, default=str),
        outcome="failed",
    )


def _fail_if_budget_exceeded(run: Any) -> bool:
    """Fail ``run`` when its top-level numeric budget spend exceeds a limit."""

    budget = run.workflow.budget if isinstance(run.workflow.budget, Mapping) else {}
    spent = run.budget_spent if isinstance(run.budget_spent, Mapping) else {}
    for key, limit in _numeric_budget_items(budget):
        spent_value = _numeric_budget_value(spent.get(key))
        if spent_value is None or spent_value <= limit:
            continue
        run.mark_failed(f"Workflow exceeded budget {key}={limit:g} (spent {spent_value:g}).")
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
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _update_run_status(run: Any, *, timestamp: datetime) -> None:
    if run.status in RunStatus.TERMINAL:
        return
    rows = run.step_runs.select_related("step").all()
    if run.origin == RunOrigin.TEST and run.test_scope == WorkflowScope.NODE:
        rows = [row for row in rows if row.step_id is not None and run.allows_test_step(row.step)]
        active_without_wait = any(
            row.status in {StepRunStatus.SCHEDULED, StepRunStatus.STARTED} for row in rows
        )
    else:
        active_without_wait = rows.filter(
            status__in=[StepRunStatus.SCHEDULED, StepRunStatus.STARTED]
        ).exists()
    if active_without_wait:
        if run.status == RunStatus.PENDING:
            run.mark_running()
        elif run.status == RunStatus.WAITING:
            run.resume()
        if run.wake_at is not None:
            run.wake_at = None
            run.save(update_fields=["wake_at", "updated_at"])
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
            run.mark_waiting(wake_at=wake_at)
        elif run.status == RunStatus.WAITING and run.wake_at != wake_at:
            run.wake_at = wake_at
            run.save(update_fields=["wake_at", "updated_at"])
        return

    failed = (
        run.step_runs.filter(status__in=[StepRunStatus.FAILED, StepRunStatus.CANCELED], map_index=-1)
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
        failed = run.step_runs.filter(
            step_id=source_step_run.step_id,
            map_index=source_step_run.map_index,
            status__in=[StepRunStatus.FAILED, StepRunStatus.CANCELED],
        ).first()
    if failed is not None:
        if run.status == RunStatus.PENDING:
            run.mark_running()
        _fail_run(run, failed.error or f"Step {failed.pk} ended as {failed.status}.", failed_step_run=failed)
        return

    if run.step_runs.exists():
        if run.status == RunStatus.PENDING:
            run.mark_running()
        _finish_run_result(run)
        return

    if run.status == RunStatus.RUNNING and run.wake_at is not None and run.wake_at <= timestamp:
        run.wake_at = None
        run.save(update_fields=["wake_at", "updated_at"])


def _finish_run_result(run: Any) -> None:
    """Select exactly one declared terminal rule and retain its typed run result."""

    rules = run.workflow.result_rules
    terminals = list(
        run.step_runs.select_related("step").filter(
            map_index=-1, status=StepRunStatus.SUCCEEDED, step__isnull=False,
        ).order_by("pk")
    )
    outgoing_routes = list(run.workflow.edges.values_list("source_id", "condition"))
    unhandled_calls = [
        row for row in terminals
        if not any(source_id == row.step_id and condition in {"", row.outcome}
                   for source_id, condition in outgoing_routes)
        and row.step.step_class == "call_workflow"
        and row.outcome in {"child_failed", "child_canceled"}
    ]
    if unhandled_calls:
        if any(row.outcome == "child_failed" for row in unhandled_calls):
            run.mark_failed("An unhandled child workflow failed.")
        else:
            run.mark_canceled()
        return
    if not rules:
        run.mark_succeeded(outcome="completed", output={})
        return
    matches = [
        (rule, producer)
        for rule in rules
        for producer in terminals
        if producer.step.key == rule["producer"] and producer.outcome == rule["when_outcome"]
    ]
    if len(matches) != 1:
        run.mark_failed(workflow_result_terminal_match_error(len(matches)))
        return
    rule, producer = matches[0]
    context = BindingContext(
        workflow_input=SourceValue(
            JsonPresence(run.input_present, run.input), {"kind": "workflow_input", "run_id": run.pk}
        ),
        step_outputs={producer.step.key: SourceValue(
            JsonPresence(producer.output_present, producer.output),
            {"kind": "step_output", "step_run_id": producer.pk},
        )},
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
        run.mark_failed(f"Workflow result contract failed: {error}")
        return
    run.mark_succeeded(outcome=rule["outcome"], output=output)


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


def _heartbeat_timeout() -> timedelta:
    """Return the configured heartbeat timeout as a timedelta."""

    configured = getattr(settings, "ANGEE_WORKFLOWS_HEARTBEAT_TIMEOUT", 300)
    if isinstance(configured, timedelta):
        return configured
    return timedelta(seconds=float(configured))


def _fail_run(run: Any, error: str, *, failed_step_run: Any) -> None:
    """Mark ``run`` failed and start its linked error workflow once."""

    run.mark_failed(error)
    _start_error_workflow(run, failed_step_run=failed_step_run)


def _start_error_workflow(run: Any, *, failed_step_run: Any) -> None:
    """Start the pinned workflow's error workflow for ``failed_step_run``."""

    if _is_error_workflow_run(run):
        return
    lineage = getattr(run.workflow, "error_workflow", None)
    if lineage is None:
        return
    start(
        lineage,
        subject=run,
        actor=None,
        parent_step_run=failed_step_run,
        parent_relation="continuation",
        origin=cast(RunOrigin, RunOrigin.ERROR_WORKFLOW),
    )


def _is_error_workflow_run(run: Any) -> bool:
    """Return whether ``run`` was started by an error-workflow failure path."""

    return run.origin == RunOrigin.ERROR_WORKFLOW
