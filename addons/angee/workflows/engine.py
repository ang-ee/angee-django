"""Runtime engine for workflow runs.

This module is the single owner of workflow advancement. It creates and replays
the step-run journal, evaluates join rules, routes outcomes, claims work, and
records cancellation. Step implementations run only through ``execute()``, never
inside ``advance()``.
"""

from __future__ import annotations

import traceback
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, Literal, cast

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from procrastinate import exceptions as procrastinate_exceptions
from pydantic import ValidationError as PydanticValidationError
from pydantic import create_model
from rebac import PermissionDenied, SubjectRef, current_actor, system_context
from rebac.actors import NoActorResolvedError, to_subject_ref
from rebac.backends import backend as rebac_backend
from rebac.relationships import write_relationships
from rebac.resources import to_object_ref
from rebac.types import RelationshipTuple

from angee.base.actors import actor_user_id
from angee.workflows.models import JoinRule, RunStatus, StepRunStatus, Verdict, WorkflowStatus
from angee.workflows.steps import DecisionSpec, StepResult

RUN_TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED}
STEP_TERMINAL = {
    StepRunStatus.SUCCEEDED,
    StepRunStatus.FAILED,
    StepRunStatus.CANCELED,
    StepRunStatus.SKIPPED,
}
STEP_ACTIVE = {StepRunStatus.SCHEDULED, StepRunStatus.STARTED, StepRunStatus.WAITING}
VERDICT_PENDING = cast(Verdict, Verdict.PENDING)
VERDICT_COMPLETED = cast(Verdict, Verdict.COMPLETED)
VERDICT_REJECTED = cast(Verdict, Verdict.REJECTED)
VERDICT_ESCALATED = cast(Verdict, Verdict.ESCALATED)
VERDICT_EXPIRED = cast(Verdict, Verdict.EXPIRED)
VERDICT_TERMINAL = {VERDICT_COMPLETED, VERDICT_REJECTED, VERDICT_ESCALATED, VERDICT_EXPIRED}
DECISION_VERBS: dict[str, Verdict] = {
    "complete": VERDICT_COMPLETED,
    "completed": VERDICT_COMPLETED,
    "reject": VERDICT_REJECTED,
    "rejected": VERDICT_REJECTED,
    "escalate": VERDICT_ESCALATED,
    "escalated": VERDICT_ESCALATED,
}


def start(
    workflow: Any,
    subject: Any,
    actor: Any,
    *,
    trigger: Any = None,
    parent_step_run: Any = None,
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

        subject_content_type, subject_object_id = _object_ref(subject)
        dedup_key = _dedup_key(trigger, subject_content_type, subject_object_id)
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
        if dedup_key:
            run, created = run_model.objects.get_or_create(dedup_key=dedup_key, defaults=attrs)
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
        run = run_model.objects.select_for_update().select_related("workflow").get(pk=run_id)
        if run.status in RUN_TERMINAL:
            return {"claimed": 0}

        _activate_run_if_needed(run, timestamp=timestamp)
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
        step_run = (
            step_run_model.objects.select_related("run", "step", "run__workflow").filter(pk=step_run_id).first()
        )
        if step_run is None or step_run.status in STEP_TERMINAL:
            return {"executed": 0}
        if step_run.status != StepRunStatus.STARTED or step_run.run.status in RUN_TERMINAL:
            return {"executed": 0}
        impl_class = step_run.step.resolve_impl("step_class", default="handler")
        setattr(step_run, "_engine_now", timestamp)

    result: StepResult | None = None
    error = ""
    stack = ""
    try:
        result = cast(Any, impl_class)().run(step_run)
    except Exception as exc:  # noqa: BLE001 - impl failure is journaled as a step result.
        error = str(exc)
        stack = traceback.format_exc()

    wait_until: datetime | None = None
    run_id: int | None = None
    with system_context(reason="workflows.engine.execute.persist"), transaction.atomic():
        locked = step_run_model.objects.select_for_update().select_related("run").get(pk=step_run_id)
        run_id = locked.run_id
        if locked.status != StepRunStatus.STARTED or locked.run.status in RUN_TERMINAL:
            return {"executed": 0}
        if error:
            locked.mark_failed(error=error, stacktrace=stack)
        elif result is None:
            locked.mark_failed(error="Step implementation returned no result.", stacktrace="")
        elif result.kind == "done":
            locked.mark_succeeded(output=result.output, outcome=result.outcome)
        elif result.kind == "wait":
            locked.mark_waiting(until=result.until, event=result.event, resume_state=result.resume_state)
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
        locked = run_model.objects.select_for_update().get(pk=run_id)
        if locked.status in RUN_TERMINAL:
            return
        child_ids = list(
            run_model.objects.filter(parent_step_run__run=locked).values_list("pk", flat=True).order_by("pk")
        )
        for step_run in step_run_model.objects.select_for_update().filter(run=locked).order_by("pk"):
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


def decide(decision: Any, verdict: str, *, payload: Any = None, actor: Any = None) -> Any:
    """Resolve one pending decision as an actor after checking ``act``."""

    target = _verdict_for_verb(verdict)
    actor_ref = _actor_ref(actor)
    decision_model = _model("Decision")
    decision_id = decision.pk if hasattr(decision, "pk") else int(decision)
    with system_context(reason="workflows.engine.decide.load"):
        current = decision_model.objects.get(pk=decision_id)
    _check_decision_act(current, actor_ref)

    run_id: int | None = None
    with system_context(reason="workflows.engine.decide"), transaction.atomic():
        locked = (
            decision_model.objects.select_for_update()
            .select_related("step_run", "step_run__run", "step_run__step")
            .get(pk=decision_id)
        )
        if locked.verdict != VERDICT_PENDING:
            return locked
        _ensure_sequential_turn(locked)
        try:
            resolution = _validate_resolution(locked, payload)
        except ValidationError as error:
            _record_invalid_resolution(locked, error)
            run_id = locked.step_run.run_id
        else:
            _mark_decision(locked, target, resolution=resolution, resolved_by=str(actor_ref))
            _apply_decision_policy(locked.step_run)
            run_id = locked.step_run.run_id
        transaction.on_commit(lambda run_id=run_id: enqueue_advance(cast(int, run_id)))
    return locked


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


def override_run(run: Any, next_steps: Iterable[Any], *, actor: Any) -> Any:
    """Cancel active rows, insert an override journal row, and schedule next steps."""

    run_model = _model("WorkflowRun")
    step_run_model = _model("StepRun")
    run_id = run.pk if hasattr(run, "pk") else int(run)
    actor_ref = _actor_ref(actor)
    actor_id = actor_user_id(actor_ref)
    step_ids = [step.pk if hasattr(step, "pk") else int(step) for step in next_steps]

    with system_context(reason="workflows.engine.override"), transaction.atomic():
        locked = run_model.objects.select_for_update().get(pk=run_id)
        for step_run in step_run_model.objects.select_for_update().filter(run=locked, status__in=list(STEP_ACTIVE)):
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
            row = step_run_model.objects.create(
                run=locked,
                step_id=step_id,
                map_index=-1,
                status=StepRunStatus.SCHEDULED,
                input={},
            )
            row.previous.set([override])
        if locked.status == RunStatus.WAITING:
            locked.resume()
        transaction.on_commit(lambda run_id=locked.pk: enqueue_advance(run_id))
    return override


def enqueue_advance(run_id: int) -> None:
    """Enqueue a deduped advance job after the current transaction commits."""

    def defer() -> None:
        # The task wrapper imports this module to call the engine; import here to
        # keep that adapter cycle out of app loading.
        from angee.workflows.tasks import advance_workflow_run

        try:
            advance_workflow_run.configure(queueing_lock=_advance_lock(run_id)).defer(run_id=run_id)
        except procrastinate_exceptions.AlreadyEnqueued:
            return

    transaction.on_commit(defer)


def enqueue_advance_at(run_id: int, when: datetime) -> None:
    """Enqueue a deferred advance job for a durable timer wake."""

    def defer() -> None:
        from angee.workflows.tasks import advance_workflow_run

        try:
            advance_workflow_run.configure(
                lock=_advance_lock(run_id),
                queueing_lock=f"{_advance_lock(run_id)}:wake:{when.isoformat()}",
                schedule_at=when,
            ).defer(run_id=run_id)
        except procrastinate_exceptions.AlreadyEnqueued:
            return

    transaction.on_commit(defer)


def enqueue_decision_escalation_at(decision_id: int, attempt: int, when: datetime) -> None:
    """Enqueue a deferred escalation timer for one decision attempt."""

    def defer() -> None:
        from angee.workflows.tasks import escalate_workflow_decision

        try:
            escalate_workflow_decision.configure(
                lock=f"workflows.decision:{decision_id}",
                queueing_lock=f"workflows.decision.escalate:{decision_id}:{attempt}",
                schedule_at=when,
            ).defer(decision_id=decision_id, attempt=attempt)
        except procrastinate_exceptions.AlreadyEnqueued:
            return

    transaction.on_commit(defer)


def enqueue_decision_expiry_at(decision_id: int, attempt: int, when: datetime) -> None:
    """Enqueue a deferred expiry timer for one decision attempt."""

    def defer() -> None:
        from angee.workflows.tasks import expire_workflow_decision

        try:
            expire_workflow_decision.configure(
                lock=f"workflows.decision:{decision_id}",
                queueing_lock=f"workflows.decision.expire:{decision_id}:{attempt}",
                schedule_at=when,
            ).defer(decision_id=decision_id, attempt=attempt)
        except procrastinate_exceptions.AlreadyEnqueued:
            return

    transaction.on_commit(defer)


def enqueue_execute(step_run_id: int) -> None:
    """Enqueue one step execution job after the current transaction commits."""

    def defer() -> None:
        from angee.workflows.tasks import execute_workflow_step

        try:
            execute_workflow_step.configure(queueing_lock=f"workflows.execute:{step_run_id}").defer(
                step_run_id=step_run_id
            )
        except procrastinate_exceptions.AlreadyEnqueued:
            return

    transaction.on_commit(defer)


def _model(name: str) -> type[Any]:
    """Return a concrete workflows model from the Django app registry."""

    return apps.get_model("workflows", name)


def _suspend_step_run(step_run: Any, result: StepResult) -> None:
    """Persist a suspended result and create its awaited decisions."""

    resume_state = dict(result.resume_state or {})
    decisions = tuple(result.decisions)
    if decisions:
        resume_state["_decision_specs"] = {
            str(index): dict(spec.decision_schema) for index, spec in enumerate(decisions) if spec.decision_schema
        }
    step_run.mark_waiting(resume_state=resume_state)
    for index, spec in enumerate(decisions):
        decision = _create_decision(step_run, spec)
        if spec.decision_schema:
            state = dict(step_run.resume_state)
            schemas = dict(state.get("_decision_schemas", {}))
            schemas[str(decision.pk)] = dict(spec.decision_schema)
            state["_decision_schemas"] = schemas
            step_run.resume_state = state
            step_run.save(update_fields=["resume_state", "updated_at"])


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
        enqueue_decision_escalation_at(decision.pk, decision.attempts, decision.escalate_at)
    if decision.expires_at is not None:
        enqueue_decision_expiry_at(decision.pk, decision.attempts, decision.expires_at)


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

    try:
        return DECISION_VERBS[str(verb)]
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


def _validate_resolution(decision: Any, payload: Any) -> dict[str, Any]:
    """Validate a decision resolution against its step-owned schema."""

    resolution = payload if payload is not None else {}
    if not isinstance(resolution, dict):
        raise ValidationError({"payload": "Decision payload must be a JSON object."})

    schema = _schema_for_decision(decision)
    if schema is None:
        return dict(resolution)
    if isinstance(schema, dict):
        return _validate_mapping_schema(schema, resolution)
    if hasattr(schema, "model_validate"):
        try:
            parsed = schema.model_validate(resolution)
        except PydanticValidationError as error:
            raise ValidationError({"payload": str(error)}) from error
        dumped = parsed.model_dump()
        return cast(dict[str, Any], dumped)
    return dict(resolution)


def _schema_for_decision(decision: Any) -> Any | None:
    """Return the resolution schema owned by the suspended step."""

    schemas = dict(decision.step_run.resume_state.get("_decision_schemas", {}))
    if str(decision.pk) in schemas:
        return schemas[str(decision.pk)]
    gate = decision.step_run.resume_state.get("gate")
    if isinstance(gate, dict) and isinstance(gate.get("decision_schema"), dict):
        return gate["decision_schema"]
    if decision.step_run.step_id is None:
        return None
    impl_class = decision.step_run.step.resolve_impl("step_class", default="handler")
    return getattr(impl_class, "decision_schema", None)


def _validate_mapping_schema(schema: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
    """Validate a JSON-authored decision schema through a pydantic model."""

    if not schema:
        return dict(resolution)
    if schema.get("type", "object") != "object":
        raise ValidationError({"payload": "Decision schema root type must be object."})
    required = set(schema.get("required", ()))
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValidationError({"payload": "Decision schema properties must be an object."})
    fields: dict[str, tuple[Any, Any]] = {}
    for name, spec in properties.items():
        field_schema = spec if isinstance(spec, dict) else {}
        annotation = _annotation_for_field_schema(field_schema)
        default = ... if name in required else None
        fields[str(name)] = (annotation, default)
    for name in required:
        fields.setdefault(str(name), (Any, ...))
    model_factory = cast(Any, create_model)
    model = model_factory("DecisionResolution", **fields)
    try:
        parsed = model.model_validate(resolution)
    except PydanticValidationError as error:
        raise ValidationError({"payload": str(error)}) from error
    return cast(dict[str, Any], parsed.model_dump(exclude_none=False))


def _annotation_for_field_schema(schema: dict[str, Any]) -> Any:
    """Return a pydantic annotation for the supported decision-schema subset."""

    if "const" in schema:
        return Literal.__getitem__((schema["const"],))
    if "enum" in schema and isinstance(schema["enum"], list):
        return Literal.__getitem__(tuple(schema["enum"]))
    field_type = schema.get("type", "any")
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict[str, Any],
        "array": list[Any],
        "any": Any,
    }.get(str(field_type), Any)


def _record_invalid_resolution(decision: Any, error: ValidationError) -> None:
    """Re-open an invalid decision attempt or fail the suspended step at max."""

    decision.record_invalid_resolution()
    if decision.max_attempts is not None and decision.attempts >= decision.max_attempts:
        decision.step_run.mark_failed(error=f"Decision resolution failed validation: {error}", stacktrace="")
        return
    _schedule_decision_timers(decision)


def _mark_decision(decision: Any, verdict: Verdict, *, resolution: dict[str, Any], resolved_by: str) -> None:
    """Apply a terminal verdict transition to one decision."""

    if verdict == VERDICT_COMPLETED:
        decision.mark_completed(resolution=resolution, resolved_by=resolved_by)
    elif verdict == VERDICT_REJECTED:
        decision.mark_rejected(resolution=resolution, resolved_by=resolved_by)
    elif verdict == VERDICT_ESCALATED:
        decision.mark_escalated(resolution=resolution, resolved_by=resolved_by)
    elif verdict == VERDICT_EXPIRED:
        decision.mark_expired(resolution=resolution, resolved_by=resolved_by)
    else:
        raise ValidationError({"verdict": "Decision verdict must be terminal."})


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

    terminal = [decision for decision in decisions if decision.verdict in VERDICT_TERMINAL]
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
            decision_model.objects.select_for_update()
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
        _mark_decision(decision, verdict, resolution={}, resolved_by=resolved_by)
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

    for decision in step_run.decisions.select_for_update().filter(verdict=VERDICT_PENDING):
        decision.mark_expired(resolution={}, resolved_by=resolved_by)


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
        .filter(status__in=list(STEP_TERMINAL))
        .filter(step__isnull=False)
        .order_by("pk")
    )


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
    for edge in step_run.step.outgoing_edges.select_related("target").filter(condition="").order_by("pk"):
        _maybe_schedule_target(run, edge.target)


def _maybe_schedule_target(run: Any, target: Any) -> Any | None:
    step_run_model = _model("StepRun")
    existing = step_run_model.objects.filter(run=run, step=target, map_index=-1).first()
    if existing is not None:
        return existing
    upstream = _upstream_rows(run, target)
    decision = _join_decision(target.join_rule, upstream)
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


def _join_decision(rule: Any, upstream: list[Any | None]) -> str:
    statuses = [row.status if row is not None else None for row in upstream]
    if not statuses:
        return "run"

    terminal = [status in STEP_TERMINAL for status in statuses]
    has_missing_or_active = any(status is None or status not in STEP_TERMINAL for status in statuses)
    has_success = any(status == StepRunStatus.SUCCEEDED for status in statuses)
    has_done = any(status in STEP_TERMINAL for status in statuses)
    has_failed = any(status in {StepRunStatus.FAILED, StepRunStatus.CANCELED} for status in statuses)

    if rule == JoinRule.ALL_SUCCESS:
        if all(status == StepRunStatus.SUCCEEDED for status in statuses):
            return "run"
        if any(status in STEP_TERMINAL and status != StepRunStatus.SUCCEEDED for status in statuses):
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


def _claim_due_steps(run: Any, *, timestamp: datetime) -> list[int]:
    due = list(
        run.step_runs.select_for_update()
        .filter(
            models.Q(status=StepRunStatus.SCHEDULED)
            | models.Q(status=StepRunStatus.WAITING, wait_until__isnull=False, wait_until__lte=timestamp)
        )
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
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _update_run_status(run: Any, *, timestamp: datetime) -> None:
    if run.status in RUN_TERMINAL:
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

    failed = run.step_runs.filter(status__in=[StepRunStatus.FAILED, StepRunStatus.CANCELED]).order_by("pk").first()
    if failed is not None:
        if run.status == RunStatus.PENDING:
            run.mark_running()
        run.mark_failed(failed.error or f"Step {failed.pk} ended as {failed.status}.")
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


def _object_ref(value: Any) -> tuple[Any | None, Any | None]:
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


def _advance_lock(run_id: int) -> str:
    return f"workflows.advance:{run_id}"
