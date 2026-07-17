"""Runtime engine for workflow runs.

This module is the single owner of workflow advancement. It creates and replays
the step-run journal, evaluates join rules, routes outcomes, claims work, and
records cancellation. Step implementations run only through ``execute()``, never
inside ``advance()``.
"""

from __future__ import annotations

import traceback
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError
from pydantic import create_model
from rebac import PermissionDenied, SubjectRef, current_actor, system_context
from rebac.actors import NoActorResolvedError, to_subject_ref
from rebac.backends import backend as rebac_backend
from rebac.relationships import write_relationships
from rebac.resources import to_object_ref
from rebac.types import RelationshipTuple

from angee.base.actors import actor_user_id
from angee.base.models import instance_from_public_id, read_scoped_queryset
from angee.tasks.enqueue import enqueue_task
from angee.workflows.models import JoinRule, RunStatus, StepRunStatus, Verdict, WorkflowStatus
from angee.workflows.steps import DecisionSpec, MapStep, StepResult, TransientStepError

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


@dataclass(frozen=True, slots=True)
class DecisionAttemptResult:
    """Outcome of one authorized decision attempt and any shape validation failure."""

    decision: Any
    validation_error: ValidationError | None = None


def start(
    workflow: Any,
    subject: Any,
    actor: Any,
    *,
    trigger: Any = None,
    parent_step_run: Any = None,
    dedup_key: str | None = None,
) -> Any:
    """Start the current published version for ``workflow`` and enqueue advancement."""

    workflow_model = _model("Workflow")
    run_model = _model("WorkflowRun")
    step_run_model = _model("StepRun")
    with system_context(reason="workflows.engine.start"), transaction.atomic():
        version = workflow_model.objects.current_published_for(workflow)
        if version is None:
            raise ValidationError({"workflow": "Workflow has no published version to start."})
        if version.status != WorkflowStatus.PUBLISHED:
            raise ValidationError({"workflow": "Workflow runs must pin a published version."})

        subject_content_type, subject_object_id = _subject_gfk(subject)
        run_dedup_key = dedup_key or _dedup_key(trigger, subject_content_type, subject_object_id)
        owner_id = _owner_id(actor=actor, trigger=trigger, workflow=version)

        attrs = {
            "workflow": version,
            "trigger": trigger,
            "parent_step_run": parent_step_run,
            "subject_content_type": subject_content_type,
            "subject_object_id": subject_object_id,
            "created_by_id": owner_id,
            "updated_by_id": owner_id,
        }
        if parent_step_run is not None:
            run, created = run_model.objects.get_or_create(parent_step_run=parent_step_run, defaults=attrs)
            if not created:
                return run
        elif run_dedup_key:
            run, created = run_model.objects.get_or_create(dedup_key=run_dedup_key, defaults=attrs)
            if not created:
                return run
        else:
            run = run_model.objects.create(**attrs)

        entries = list(version.steps.filter(is_entry=True).order_by("pk"))
        if len(entries) != 1:
            raise ValidationError({"workflow": "Workflow version must have exactly one entry step."})
        step_run_model.objects.create(
            run=run,
            step=entries[0],
            map_index=-1,
            status=StepRunStatus.SCHEDULED,
            input={},
        )
        transaction.on_commit(lambda: enqueue_advance(run.pk))
    return run


def advance(run_id: int, *, now: datetime | None = None) -> dict[str, int]:
    """Advance one workflow run under a short row lock, without running impls."""

    timestamp = now or timezone.now()
    run_model = _model("WorkflowRun")
    claimed_ids: list[int] = []

    with system_context(reason="workflows.engine.advance"), transaction.atomic():
        run = run_model.objects.lock_if_supported().select_related("workflow").get(pk=run_id)
        if run.status in RunStatus.TERMINAL:
            return {"claimed": 0}

        _activate_run_if_needed(run, timestamp=timestamp)
        _route_completed_steps(run)
        _process_map_steps(run, timestamp=timestamp)
        _route_completed_steps(run)
        if _fail_if_budget_exceeded(run):
            return {"claimed": 0}
        claimed_ids = _claim_due_steps(run, timestamp=timestamp)
        _update_run_status(run, timestamp=timestamp)
        for step_run_id in claimed_ids:
            transaction.on_commit(lambda step_run_id=step_run_id: enqueue_execute(step_run_id))

    return {"claimed": len(claimed_ids)}


def execute(step_run_id: int, *, now: datetime | None = None) -> dict[str, int]:
    """Run one claimed StepRun outside any advance lock and enqueue replay."""

    step_run_model = _model("StepRun")
    timestamp = now or timezone.now()
    with system_context(reason="workflows.engine.execute.load"):
        step_run = step_run_model.objects.select_related("run", "step", "run__workflow").filter(pk=step_run_id).first()
        if step_run is None or step_run.status in StepRunStatus.TERMINAL:
            return {"executed": 0}
        if step_run.status != StepRunStatus.STARTED or step_run.run.status in RunStatus.TERMINAL:
            return {"executed": 0}
        impl_class = step_run.step.resolve_impl("step_class")
    with system_context(reason="workflows.engine.execute.attempt"), transaction.atomic():
        locked = step_run_model.objects.lock_if_supported().select_related("run").get(pk=step_run_id)
        if locked.status != StepRunStatus.STARTED or locked.run.status in RunStatus.TERMINAL:
            return {"executed": 0}
        locked.record_attempt(heartbeat_at=timestamp)
        step_run.attempt = locked.attempt
        step_run.heartbeat_at = locked.heartbeat_at

    result: StepResult | None = None
    error = ""
    stack = ""
    try:
        result = cast(Any, impl_class)().run(step_run, now=timestamp)
    except TransientStepError:
        raise
    except Exception as exc:  # noqa: BLE001 - impl failure is journaled as a step result.
        error = str(exc)
        stack = traceback.format_exc()

    wait_until: datetime | None = None
    run_id: int | None = None
    with system_context(reason="workflows.engine.execute.persist"), transaction.atomic():
        locked = step_run_model.objects.lock_if_supported().select_related("run").get(pk=step_run_id)
        run_id = locked.run_id
        if locked.status != StepRunStatus.STARTED or locked.run.status in RunStatus.TERMINAL:
            return {"executed": 0}
        if error:
            locked.mark_failed(error=error, stacktrace=stack)
        elif result is None:
            locked.mark_failed(error="Step implementation returned no result.", stacktrace="")
        elif result.kind == "done":
            locked.mark_succeeded(output=result.output, outcome=result.outcome)
        elif result.kind == "wait":
            locked.mark_waiting(until=result.until, resume_state=result.resume_state)
            wait_until = result.until
        elif result.kind == "suspend":
            _suspend_step_run(locked, result)
        else:
            locked.mark_failed(error=f"Unknown step result kind {result.kind!r}.", stacktrace="")
        transaction.on_commit(lambda run_id=run_id: enqueue_advance(cast(int, run_id)))
        if wait_until is not None:
            transaction.on_commit(
                lambda run_id=run_id, wait_until=wait_until: enqueue_advance_at(cast(int, run_id), wait_until)
            )

    return {"executed": 1}


def cancel(run: Any) -> None:
    """Cancel a run, its durable waits, scheduled rows, and child runs."""

    run_model = _model("WorkflowRun")
    step_run_model = _model("StepRun")
    run_id = run.pk if hasattr(run, "pk") else int(run)
    child_ids: list[int] = []
    with system_context(reason="workflows.engine.cancel"), transaction.atomic():
        locked = run_model.objects.lock_if_supported().get(pk=run_id)
        if locked.status in RunStatus.TERMINAL:
            return
        child_ids = list(
            run_model.objects.filter(parent_step_run__run=locked).values_list("pk", flat=True).order_by("pk")
        )
        for step_run in step_run_model.objects.lock_if_supported().filter(run=locked).order_by("pk"):
            if step_run.status == StepRunStatus.SCHEDULED:
                step_run.mark_canceled()
            elif step_run.status == StepRunStatus.WAITING:
                _expire_pending_decisions(step_run, resolved_by="workflows/cancel")
                step_run.mark_canceled()
            elif step_run.status == StepRunStatus.STARTED:
                state = dict(step_run.resume_state)
                state["cancel_requested"] = True
                step_run.resume_state = state
                step_run.error = "Cancellation requested; running worker result will be ignored."
                step_run.save(update_fields=["resume_state", "error", "updated_at"])
        locked.mark_canceled()

    for child_id in child_ids:
        cancel(child_id)


def sweep(*, now: datetime | None = None) -> dict[str, int]:
    """Advance runs whose durable wake time is due."""

    timestamp = now or timezone.now()
    run_model = _model("WorkflowRun")
    with system_context(reason="workflows.engine.sweep"):
        run_ids = list(
            run_model.objects.filter(wake_at__lte=timestamp)
            .filter(status__in=[RunStatus.RUNNING, RunStatus.WAITING])
            .order_by("pk")
            .values_list("pk", flat=True)
        )
    for run_id in run_ids:
        advance(run_id, now=timestamp)
    return {"runs": len(run_ids)}


def reap(*, now: datetime | None = None) -> dict[str, int]:
    """Fail started step-runs whose heartbeat is past the configured deadline."""

    timestamp = now or timezone.now()
    deadline = timestamp - _heartbeat_timeout()
    step_run_model = _model("StepRun")
    run_ids: list[int] = []
    with system_context(reason="workflows.engine.reap"), transaction.atomic():
        stale_rows = list(
            step_run_model.objects.lock_if_supported()
            .filter(status=StepRunStatus.STARTED, heartbeat_at__lt=deadline)
            .order_by("pk")
        )
        for step_run in stale_rows:
            message = "Step heartbeat timed out."
            if step_run.resume_state.get("cancel_requested"):
                message = "Cancellation requested; heartbeat timed out."
            step_run.mark_failed(error=message, stacktrace="")
            run_ids.append(step_run.run_id)
    for run_id in sorted(set(run_ids)):
        enqueue_advance(run_id)
    return {"reaped": len(run_ids)}


def decide(decision: Any, verdict: str, *, payload: Any = None, actor: Any = None) -> DecisionAttemptResult:
    """Attempt one actor-authorized resolution and return its validation outcome."""

    target = _verdict_for_verb(verdict)
    actor_ref = _actor_ref(actor)
    decision_model = _model("Decision")
    decision_id = decision.pk if hasattr(decision, "pk") else int(decision)
    with system_context(reason="workflows.engine.decide.load"):
        current = decision_model.objects.get(pk=decision_id)
    _check_decision_act(current, actor_ref)

    run_id: int | None = None
    validation_error: ValidationError | None = None
    with system_context(reason="workflows.engine.decide"), transaction.atomic():
        locked = (
            decision_model.objects.lock_if_supported()
            .select_related("step_run", "step_run__run", "step_run__step")
            .get(pk=decision_id)
        )
        if locked.verdict != VERDICT_PENDING:
            return DecisionAttemptResult(locked)
        _ensure_sequential_turn(locked)
        try:
            resolution = _validate_resolution(locked, payload, actor=actor_ref)
        except ValidationError as error:
            _record_invalid_resolution(locked, error)
            validation_error = error
            run_id = locked.step_run.run_id
        else:
            locked.resolve(target, resolution=resolution, resolved_by=str(actor_ref))
            _apply_decision_policy(locked.step_run)
            run_id = locked.step_run.run_id
        transaction.on_commit(lambda run_id=run_id: enqueue_advance(cast(int, run_id)))
    return DecisionAttemptResult(locked, validation_error)


def escalate_decision(decision_id: int, attempt: int, *, now: datetime | None = None) -> dict[str, int]:
    """Resolve a pending decision as escalated when its timer is still current."""

    return _resolve_timed_decision(
        decision_id,
        attempt,
        VERDICT_ESCALATED,
        resolved_by="workflows/timer:escalate",
        timestamp=now or timezone.now(),
    )


def expire_decision(decision_id: int, attempt: int, *, now: datetime | None = None) -> dict[str, int]:
    """Resolve a pending decision as expired when its timer is still current."""

    return _resolve_timed_decision(
        decision_id,
        attempt,
        VERDICT_EXPIRED,
        resolved_by="workflows/timer:expire",
        timestamp=now or timezone.now(),
    )


def sweep_decisions(*, now: datetime | None = None) -> dict[str, int]:
    """Resolve pending decisions whose durable deadlines are due."""

    timestamp = now or timezone.now()
    decision_model = _model("Decision")
    with system_context(reason="workflows.engine.decision_sweep"):
        expired = list(
            decision_model.objects.filter(verdict=VERDICT_PENDING, expires_at__lte=timestamp)
            .order_by("pk")
            .values_list("pk", "attempts")
        )
        escalated = list(
            decision_model.objects.filter(verdict=VERDICT_PENDING, escalate_at__lte=timestamp)
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timestamp))
            .order_by("pk")
            .values_list("pk", "attempts")
        )
    expired_count = sum(expire_decision(pk, attempt, now=timestamp)["resolved"] for pk, attempt in expired)
    escalated_count = sum(escalate_decision(pk, attempt, now=timestamp)["resolved"] for pk, attempt in escalated)
    return {"expired": expired_count, "escalated": escalated_count}


def override_run(run: Any, next_steps: Iterable[Any], *, actor: Any) -> Any:
    """Cancel active rows, insert an override journal row, and schedule next steps."""

    run_model = _model("WorkflowRun")
    step_run_model = _model("StepRun")
    run_id = run.pk if hasattr(run, "pk") else int(run)
    actor_ref = _actor_ref(actor)
    actor_id = actor_user_id(actor_ref)
    step_ids = [step.pk if hasattr(step, "pk") else int(step) for step in next_steps]

    with system_context(reason="workflows.engine.override"), transaction.atomic():
        locked = run_model.objects.lock_if_supported().get(pk=run_id)
        for step_run in step_run_model.objects.lock_if_supported().filter(
            run=locked,
            status__in=list(StepRunStatus.ACTIVE),
        ):
            step_run.mark_canceled()
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
                row.reschedule_for_override(input={})
            row.previous.set([override])
        if locked.status == RunStatus.WAITING:
            locked.resume()
        transaction.on_commit(lambda run_id=locked.pk: enqueue_advance(run_id))
    return override


def enqueue_advance(run_id: int) -> None:
    """Enqueue an advance job."""

    _defer("workflows.advance", run_id=run_id)


def enqueue_advance_at(run_id: int, when: datetime) -> None:
    """Enqueue a deferred advance job for a durable timer wake."""

    _defer(
        "workflows.advance",
        schedule_at=when,
        run_id=run_id,
    )


def enqueue_decision_escalation_at(decision_id: int, attempt: int, when: datetime) -> None:
    """Enqueue a deferred escalation timer for one decision attempt."""

    _defer(
        "workflows.decision_escalate",
        schedule_at=when,
        decision_id=decision_id,
        attempt=attempt,
    )


def enqueue_decision_expiry_at(decision_id: int, attempt: int, when: datetime) -> None:
    """Enqueue a deferred expiry timer for one decision attempt."""

    _defer(
        "workflows.decision_expire",
        schedule_at=when,
        decision_id=decision_id,
        attempt=attempt,
    )


def enqueue_execute(step_run_id: int) -> None:
    """Enqueue one step execution job."""

    _defer("workflows.execute", step_run_id=step_run_id)


def _defer(
    task_name: str,
    *,
    schedule_at: datetime | None = None,
    **kwargs: Any,
) -> None:
    """Send one Celery task by registered name."""

    enqueue_task(task_name, kwargs=kwargs, eta=schedule_at)


def _model(name: str) -> type[Any]:
    """Return a concrete workflows model from the Django app registry."""

    return apps.get_model("workflows", name)


def _suspend_step_run(step_run: Any, result: StepResult) -> None:
    """Persist a suspended result and create its awaited decisions."""

    resume_state = dict(result.resume_state or {})
    decisions = tuple(result.decisions)
    decision_schemas: dict[str, dict[str, Any]] = {}
    for spec in decisions:
        decision = _create_decision(step_run, spec)
        if spec.decision_schema:
            decision_schemas[str(decision.pk)] = dict(spec.decision_schema)
    if decision_schemas:
        resume_state["_decision_schemas"] = decision_schemas
    step_run.mark_waiting(resume_state=resume_state)


def _create_decision(step_run: Any, spec: DecisionSpec) -> Any:
    """Create one decision row and its explicit REBAC relationship tuples."""

    decision_model = _model("Decision")
    decision = decision_model.objects.create(
        step_run=step_run,
        priority=spec.priority,
        action=spec.action,
        payload=spec.payload,
        max_attempts=spec.max_attempts,
        expires_at=spec.expires_at,
        escalate_at=spec.escalate_at,
    )
    _write_decision_relationships(
        decision,
        assignees=spec.assignees,
        requester=spec.requester,
        escalation=spec.escalation,
    )
    _schedule_decision_timers(decision)
    return decision


def _write_decision_relationships(
    decision: Any,
    *,
    assignees: Iterable[str | SubjectRef] = (),
    requester: str | SubjectRef = "",
    escalation: Iterable[str | SubjectRef] = (),
) -> None:
    """Write explicit decision relationship tuples through django-zed-rebac."""

    resource = to_object_ref(decision)
    tuples: list[RelationshipTuple] = []
    for subject in assignees:
        tuples.append(RelationshipTuple(resource=resource, relation="assignee", subject=_subject_ref(subject)))
    if requester:
        tuples.append(RelationshipTuple(resource=resource, relation="requester", subject=_subject_ref(requester)))
    for subject in escalation:
        tuples.append(RelationshipTuple(resource=resource, relation="escalation", subject=_subject_ref(subject)))
    if tuples:
        write_relationships(tuples)


def _schedule_decision_timers(decision: Any) -> None:
    """Schedule deadline jobs for the decision's current attempt."""

    if decision.escalate_at is not None:
        transaction.on_commit(
            lambda decision_id=decision.pk, attempt=decision.attempts, when=decision.escalate_at: (
                enqueue_decision_escalation_at(decision_id, attempt, when)
            )
        )
    if decision.expires_at is not None:
        transaction.on_commit(
            lambda decision_id=decision.pk, attempt=decision.attempts, when=decision.expires_at: (
                enqueue_decision_expiry_at(decision_id, attempt, when)
            )
        )


def _subject_ref(subject: str | SubjectRef) -> SubjectRef:
    """Return a REBAC subject ref from a stored subject spelling."""

    if isinstance(subject, SubjectRef):
        return subject
    return SubjectRef.parse(str(subject))


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


def _ensure_sequential_turn(decision: Any) -> None:
    """Enforce priority order for sequential gate slots."""

    if _policy_for(decision.step_run) != "sequential":
        return
    current = (
        decision.step_run.decisions.filter(verdict=VERDICT_PENDING)
        .order_by("priority", "pk")
        .values_list("pk", flat=True)
        .first()
    )
    if current != decision.pk:
        raise ValidationError({"decision": "Sequential decisions must resolve in priority order."})


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
    """Validate a JSON-authored decision schema through a pydantic model."""

    if not schema:
        return dict(resolution)
    if schema.get("type", "object") != "object":
        raise ValidationError({"payload": "Decision schema root type must be object."})
    # Human-paced decisions rebuild per attempt; memoize by schema-dict hash before bulk or programmatic reuse.
    model = _mapping_schema_model(schema, name="DecisionResolution")
    try:
        parsed = model.model_validate(resolution)
    except PydanticValidationError as error:
        raise _resolution_validation_error(error) from error
    validated = cast(dict[str, Any], parsed.model_dump(exclude_none=False))
    _validate_relation_fields(schema, validated, actor)
    return validated


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


def _relation_error(relation: dict[str, Any], value: Any, actor: Any) -> str | None:
    """Return the field error for one submitted relation id, or None when valid.

    Unknown ids, wrong-model ids, and ids the actor may not write share one
    message so the response does not disclose which records exist.
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
    scoped = read_scoped_queryset(model, actor, action="write")
    instance = instance_from_public_id(model, value, queryset=scoped)
    if instance is None:
        return "Relation value must reference a record you can write."
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


def _record_invalid_resolution(decision: Any, error: ValidationError) -> None:
    """Re-open an invalid decision attempt or fail the suspended step at max."""

    decision.record_invalid_resolution()
    if decision.max_attempts is not None and decision.attempts >= decision.max_attempts:
        decision.step_run.mark_failed(error=f"Decision resolution failed validation: {error}", stacktrace="")
        return
    _schedule_decision_timers(decision)


def _apply_decision_policy(step_run: Any) -> None:
    """Complete ``step_run`` when its decision collection satisfies its policy."""

    if step_run.status != StepRunStatus.WAITING:
        return
    decisions = list(step_run.decisions.order_by("priority", "pk"))
    outcome = _decision_policy_outcome(_policy_for(step_run), decisions)
    if outcome is None:
        return
    step_run.mark_succeeded(
        output={"decisions": [decision.sqid for decision in decisions]},
        outcome=outcome,
    )


def _policy_for(step_run: Any) -> str:
    """Return the gate aggregation policy for a suspended step."""

    gate = step_run.resume_state.get("gate")
    if isinstance(gate, dict):
        return str(gate.get("policy", "one_done") or "one_done")
    return "one_done"


def _decision_policy_outcome(policy: str, decisions: list[Any]) -> str | None:
    """Return the aggregate outcome for ``decisions`` under ``policy``."""

    terminal = [decision for decision in decisions if decision.verdict in Verdict.TERMINAL]
    if not decisions or not terminal:
        return None
    if policy == "one_done":
        return str(terminal[0].verdict.value)
    if policy == "all_success":
        for verdict in (VERDICT_REJECTED, VERDICT_ESCALATED, VERDICT_EXPIRED):
            if any(decision.verdict == verdict for decision in terminal):
                return str(verdict.value)
        return "completed" if len(terminal) == len(decisions) else None
    if policy == "majority":
        for verdict in (VERDICT_ESCALATED, VERDICT_EXPIRED):
            if any(decision.verdict == verdict for decision in terminal):
                return str(verdict.value)
        threshold = len(decisions) // 2 + 1
        completed = sum(1 for decision in terminal if decision.verdict == VERDICT_COMPLETED)
        rejected = sum(1 for decision in terminal if decision.verdict == VERDICT_REJECTED)
        if completed >= threshold:
            return "completed"
        if rejected >= threshold:
            return "rejected"
        return "rejected" if len(terminal) == len(decisions) else None
    if policy == "sequential":
        for decision in terminal:
            if decision.verdict != VERDICT_COMPLETED:
                return str(decision.verdict.value)
        return "completed" if len(terminal) == len(decisions) else None
    return None


def _resolve_timed_decision(
    decision_id: int,
    attempt: int,
    verdict: Verdict,
    *,
    resolved_by: str,
    timestamp: datetime,
) -> dict[str, int]:
    """Resolve a deadline decision if the attempt and deadline are still current."""

    decision_model = _model("Decision")
    with system_context(reason="workflows.engine.decision_timer"), transaction.atomic():
        decision = (
            decision_model.objects.lock_if_supported()
            .select_related("step_run", "step_run__run", "step_run__step")
            .filter(pk=decision_id)
            .first()
        )
        if decision is None or decision.verdict != VERDICT_PENDING or decision.attempts != attempt:
            return {"resolved": 0}
        if verdict == VERDICT_ESCALATED and (decision.escalate_at is None or decision.escalate_at > timestamp):
            return {"resolved": 0}
        if verdict == VERDICT_EXPIRED and (decision.expires_at is None or decision.expires_at > timestamp):
            return {"resolved": 0}
        if verdict == VERDICT_ESCALATED:
            _write_decision_relationships(decision, escalation=_escalation_subjects(decision))
        decision.resolve(verdict, resolution={}, resolved_by=resolved_by)
        _apply_decision_policy(decision.step_run)
        run_id = decision.step_run.run_id
        transaction.on_commit(lambda run_id=run_id: enqueue_advance(run_id))
    return {"resolved": 1}


def _escalation_subjects(decision: Any) -> tuple[str, ...]:
    """Return escalation subject refs from the suspended gate config."""

    gate = decision.step_run.resume_state.get("gate")
    if not isinstance(gate, dict):
        return ()
    return tuple(str(subject) for subject in gate.get("escalation", ()) if str(subject))


def _expire_pending_decisions(step_run: Any, *, resolved_by: str) -> None:
    """Expire pending decisions attached to a canceled waiting step-run."""

    decision_model = _model("Decision")
    pending = decision_model.objects.lock_if_supported().filter(step_run=step_run, verdict=VERDICT_PENDING)
    for decision in pending:
        decision.resolve(VERDICT_EXPIRED, resolution={}, resolved_by=resolved_by)


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


def _process_map_steps(run: Any, *, timestamp: datetime) -> None:
    map_rows = list(
        run.step_runs.lock_if_supported()
        .select_related("step")
        .filter(**MapStep.engine_expanded_filter(), status__in=[StepRunStatus.SCHEDULED, StepRunStatus.WAITING])
        .order_by("pk")
    )
    for step_run in map_rows:
        if step_run.status == StepRunStatus.SCHEDULED:
            _expand_map_step(run, step_run, timestamp=timestamp)
        if step_run.status == StepRunStatus.WAITING:
            _complete_map_step_if_ready(run, step_run)


def _expand_map_step(run: Any, step_run: Any, *, timestamp: datetime) -> None:
    try:
        target = MapStep.target_step(step_run)
        items = MapStep.items(step_run)
    except ValidationError as error:
        step_run.mark_started(heartbeat_at=timestamp)
        output = {"error": str(error), "total": 0, "successes": 0, "failures": 0}
        step_run.mark_succeeded(output=output, outcome="failed")
        run.steps_taken += 1
        run.save(update_fields=["steps_taken", "updated_at"])
        return

    state = dict(step_run.resume_state)
    state["map"] = {
        "target_step_key": target.key,
        "target_step_id": target.pk,
        "items": items,
    }
    step_run.mark_started(heartbeat_at=timestamp)
    step_run.resume_state = state
    step_run.save(update_fields=["resume_state", "updated_at"])
    run.steps_taken += 1
    run.save(update_fields=["steps_taken", "updated_at"])
    _ensure_map_children(run, step_run, target=target, items=items)
    if not items:
        _complete_map_step_if_ready(run, step_run)
    else:
        step_run.mark_waiting(resume_state=state)


def _complete_map_step_if_ready(run: Any, step_run: Any) -> None:
    state = dict(step_run.resume_state.get("map", {}))
    target_id = state.get("target_step_id")
    items = list(state.get("items", ()))
    if target_id is None:
        return
    target = _model("Step").objects.get(pk=target_id)
    children = list(run.step_runs.lock_if_supported().filter(step=target, map_index__gte=0).order_by("map_index"))
    if len(children) < len(items):
        _ensure_map_children(run, step_run, target=target, items=items)
        return
    if any(child.status not in StepRunStatus.TERMINAL for child in children):
        return

    output = _map_output(children)
    outcome = "succeeded" if MapStep.policy_passes(step_run.step.config, output) else "failed"
    updated_state = dict(step_run.resume_state)
    map_state = dict(updated_state.get("map", {}))
    map_state["results"] = output["results"]
    updated_state["map"] = map_state
    step_run.resume_state = updated_state
    step_run.save(update_fields=["resume_state", "updated_at"])
    step_run.mark_succeeded(output=output, outcome=outcome)


def _ensure_map_children(run: Any, step_run: Any, *, target: Any, items: list[Any]) -> None:
    step_run_model = _model("StepRun")
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
            "error": child.error,
        }
        for child in children
    ]
    successes = sum(1 for child in children if child.status == StepRunStatus.SUCCEEDED)
    failures = sum(1 for child in children if child.status in {StepRunStatus.FAILED, StepRunStatus.CANCELED})
    return {
        "total": len(children),
        "successes": successes,
        "failures": failures,
        "results": results,
    }


def _route_success(run: Any, step_run: Any) -> None:
    outgoing = list(step_run.step.outgoing_edges.select_related("target").order_by("pk"))
    for edge in outgoing:
        if edge.condition and edge.condition != step_run.outcome:
            _ensure_skipped(run, edge.target, previous=[step_run])
        else:
            _maybe_schedule_target(run, edge.target)


def _route_skip(run: Any, step_run: Any) -> None:
    for edge in step_run.step.outgoing_edges.select_related("target").order_by("pk"):
        if edge.target.join_rule == JoinRule.ALL_SUCCESS:
            _ensure_skipped(run, edge.target, previous=[step_run])
        else:
            _maybe_schedule_target(run, edge.target)


def _route_done(run: Any, step_run: Any) -> None:
    for edge in step_run.step.outgoing_edges.select_related("target").order_by("pk"):
        if edge.condition and edge.condition != step_run.outcome:
            continue
        _maybe_schedule_target(run, edge.target, routed_row=step_run)


def _maybe_schedule_target(run: Any, target: Any, *, routed_row: Any | None = None) -> Any | None:
    step_run_model = _model("StepRun")
    existing = step_run_model.objects.filter(run=run, step=target, map_index=-1).first()
    if existing is not None:
        return existing
    upstream = _upstream_rows(run, target)
    decision = _join_decision(target.join_rule, upstream, routed_row=routed_row)
    previous = [row for row in upstream if row is not None]
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
    step_run_model = _model("StepRun")
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

    for edge in step.outgoing_edges.select_related("target").order_by("pk"):
        if edge.target.join_rule == JoinRule.ALL_SUCCESS:
            _ensure_skipped(run, edge.target, previous=[step_run])
        else:
            _maybe_schedule_target(run, edge.target)
    return step_run


def _upstream_rows(run: Any, target: Any) -> list[Any | None]:
    step_run_model = _model("StepRun")
    rows: list[Any | None] = []
    for edge in target.incoming_edges.select_related("source").order_by("pk"):
        rows.append(step_run_model.objects.filter(run=run, step=edge.source, map_index=-1).first())
    return rows


def _join_decision(rule: Any, upstream: list[Any | None], *, routed_row: Any | None = None) -> str:
    statuses = [
        StepRunStatus.SUCCEEDED if _same_step_run(row, routed_row) else row.status if row is not None else None
        for row in upstream
    ]
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
    due = list(
        run.step_runs.lock_if_supported()
        .filter(
            models.Q(status=StepRunStatus.SCHEDULED)
            | models.Q(status=StepRunStatus.WAITING, wait_until__isnull=False, wait_until__lte=timestamp)
        )
        .exclude(**MapStep.engine_expanded_filter())
        .order_by("pk")
    )
    if not due:
        return []
    if run.steps_taken + len(due) > run.workflow.max_steps:
        run.mark_failed(f"Workflow exceeded max_steps={run.workflow.max_steps}.")
        return []

    claimed: list[int] = []
    for step_run in due:
        step_run.mark_started(heartbeat_at=timestamp)
        claimed.append(step_run.pk)
    run.steps_taken += len(claimed)
    run.save(update_fields=["steps_taken", "updated_at"])
    return claimed


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
    except TypeError, ValueError:
        return None
    return parsed if parsed >= 0 else None


def _update_run_status(run: Any, *, timestamp: datetime) -> None:
    if run.status in RunStatus.TERMINAL:
        return
    active_without_wait = run.step_runs.filter(status__in=[StepRunStatus.SCHEDULED, StepRunStatus.STARTED]).exists()
    if active_without_wait:
        if run.status == RunStatus.PENDING:
            run.mark_running()
        elif run.status == RunStatus.WAITING:
            run.resume()
        if run.wake_at is not None:
            run.wake_at = None
            run.save(update_fields=["wake_at", "updated_at"])
        return

    waiting_rows = run.step_runs.filter(status=StepRunStatus.WAITING)
    if waiting_rows.exists():
        wake_at = (
            waiting_rows.filter(wait_until__isnull=False)
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
    if failed is not None:
        if run.status == RunStatus.PENDING:
            run.mark_running()
        _fail_run(run, failed.error or f"Step {failed.pk} ended as {failed.status}.", failed_step_run=failed)
        return

    if run.step_runs.exists():
        if run.status == RunStatus.PENDING:
            run.mark_running()
        run.mark_succeeded()
        return

    if run.status == RunStatus.RUNNING and run.wake_at is not None and run.wake_at <= timestamp:
        run.wake_at = None
        run.save(update_fields=["wake_at", "updated_at"])


def _input_from_previous(previous: list[Any]) -> Any:
    if not previous:
        return {}
    if len(previous) == 1:
        return previous[0].output
    return {_step_key(row): row.output for row in previous}


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
    start(lineage, subject=run, actor=None, parent_step_run=failed_step_run)


def _is_error_workflow_run(run: Any) -> bool:
    """Return whether ``run`` was started by an error-workflow failure path."""

    parent = getattr(run, "parent_step_run", None)
    return parent is not None


def _subject_gfk(value: Any) -> tuple[Any | None, Any | None]:
    """Return the ``(ContentType, pk)`` a run stores for its subject — kind-exact by design.

    The subject round-trips verbatim through ``WorkflowRun.subject`` (a GenericForeignKey)
    and feeds the trigger dedup key, so the content type stays the subject's own
    (proxy-aware) model — deliberately *not* canonicalized to an MTI ancestor the way a
    polymorphic edge is (:func:`angee.base.refs.canonical_record_target`), which would
    rehydrate the run against the wrong model and fuse distinct subjects under one key.
    """

    if value is None:
        return None, None
    content_type = ContentType.objects.get_for_model(value, for_concrete_model=False)
    return content_type, value.pk


def _dedup_key(trigger: Any, content_type: Any | None, object_id: Any | None) -> str:
    if trigger is None:
        return ""
    subject = "none" if content_type is None or object_id is None else f"{content_type.pk}:{object_id}"
    return f"trigger:{trigger.pk}:subject:{subject}"


def _owner_id(*, actor: Any, trigger: Any, workflow: Any) -> Any | None:
    if actor is not None:
        try:
            user_id = actor_user_id(to_subject_ref(actor))
        except NoActorResolvedError:
            user_id = None
        if user_id is not None:
            return user_id
    if trigger is not None and getattr(trigger, "created_by_id", None) is not None:
        return trigger.created_by_id
    lineage = getattr(workflow, "published_from", None)
    if lineage is not None and getattr(lineage, "created_by_id", None) is not None:
        return lineage.created_by_id
    return getattr(workflow, "created_by_id", None)
