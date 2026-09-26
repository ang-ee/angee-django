"""Workflow steps backed by the agents inference catalogue.

``InferStepImpl`` is the typed one-shot boundary over
``agents.InferenceModel.infer``. ``AgentSessionStepImpl`` remains the distinct
multi-turn session runner with tools and approvals.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse
from rebac import actor_context, system_context

from angee.agents.models import (
    SessionStatus,
    TurnStatus,
)
from angee.agents.runners import TurnOutcome
from angee.workflows import engine
from angee.workflows.decision_actions import ReviewAction, ReviewFact, build_decision_action
from angee.workflows.models import RunStatus, StepRunStatus
from angee.workflows.steps import (
    GateStep,
    StepEffect,
    StepImpl,
    StepOutcome,
    StepResult,
    TransientStepError,
    retry_policy_from_config,
)
from angee.workflows_agents.inference import InferenceCallError, InferRequest, call_inference
from angee.workflows_agents.sessions import close_session

SESSION_PARKED_UNTIL = datetime.max.replace(tzinfo=UTC)
"""Far-future durable wait used because ``StepResult.wait`` requires a due time."""

SESSION_UPDATE_FLUSH_SECONDS = 0.25
"""Minimum interval between streamed turn-row saves."""


class InferInput(BaseModel):
    """One role-approved catalogue model plus its one-shot request."""

    model_config = ConfigDict(extra="forbid")
    model: Annotated[str, Field(min_length=1)]
    role: Annotated[str, Field(min_length=1)]
    request: InferRequest
    timeout: float = Field(default=60, gt=0, description="Provider timeout in seconds for this step invocation.")

    @field_validator("request")
    @classmethod
    def validate_request_timeout(cls, request: InferRequest) -> InferRequest:
        """Keep the infer step's timeout on its one declared input field."""

        if "timeout" in request.settings:
            raise ValueError("timeout belongs to the infer step input, not request.settings.")
        return request


class InferOutput(BaseModel):
    """Native response evidence and normalized workflow budget usage.

    Adapter timestamps and provider ids remain retained evidence. They are not a
    stable reuse key and callers must not hash this projection for replay.
    """

    model_config = ConfigDict(extra="forbid")
    response: ModelResponse | None = None
    output: dict[str, Any] | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    error: dict[str, str] | None = None


class InferStepImpl(StepImpl):
    """Make one typed inference request and debit its normalized usage once."""

    key = "infer"
    label = "Infer"
    category = "Activity"
    description = "Run one native structured or multimodal inference request."
    input_model = InferInput
    output_model = InferOutput
    outcomes = (
        StepOutcome("completed", "Completed"),
        StepOutcome("failed", "Failed"),
    )
    effect = StepEffect.EXTERNAL
    effect_description = "Calls the configured inference provider."
    idempotent = False
    deterministic = False

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Invoke the shared authorization, provider and accounting boundary."""

        del now
        value = self.validate_input(step_run.input)
        request = value.request.model_copy(update={"settings": {**value.request.settings, "timeout": value.timeout}})
        try:
            result = call_inference(step_run, value.model, request, role=value.role)
        except InferenceCallError as error:
            return StepResult.done(
                output={
                    "response": _response_projection(error.response) if error.response is not None else None,
                    "output": None,
                    "usage": error.usage,
                    "error": {"type": type(error.error).__name__, "message": str(error.error)},
                },
                outcome="failed",
            )
        try:
            response = _response_projection(result.response)
        except (TypeError, ValueError) as error:
            return StepResult.done(
                output={
                    "response": None,
                    "output": None,
                    "usage": result.usage,
                    "error": {"type": type(error).__name__, "message": str(error)},
                },
                outcome="failed",
            )
        return StepResult.done(
            output={"response": response, "output": result.output, "usage": result.usage, "error": None},
            outcome="completed",
        )


def _response_projection(response: ModelResponse) -> dict[str, Any]:
    """Serialize one native ModelResponse through pydantic-ai's message adapter."""

    return cast(dict[str, Any], ModelMessagesTypeAdapter.dump_python([response], mode="json")[0])


class AgentSessionStepImpl(StepImpl):
    """Multi-turn agent session whose bounded turns run on workflow workers.

    Idle sessions park at :data:`SESSION_PARKED_UNTIL` and wake only through
    ``workflows.engine.deliver``. The update sink flushes ACP payloads at a
    bounded cadence while the runtime refreshes the step heartbeat independently
    every minute. Worker death mid-turn still fails the whole run in v1; durable
    mid-turn replay is intentionally deferred.
    """

    key = "agent_session"
    label = "Agent session"
    category = "Activity"
    description = "Internal multi-turn agent-session operation."
    selectable = False
    deterministic = False

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Claim and execute the session's next turn."""

        del now
        with system_context(reason="workflows_agents.session_step.claim"), transaction.atomic():
            session = _session_for_step(step_run)
            if session.status != SessionStatus.CLOSED:
                turn, resumed = _claim_turn(session)
                if turn is None:
                    if session.status != SessionStatus.IDLE:
                        session.mark_idle()
                    return _park_session()
                if resumed:
                    resumption = GateStep.resumption(step_run)
                    if resumption is None or resumption.state.get("turn") != turn.sqid:
                        raise ValidationError({"gate": "Agent approval state does not match its active turn."})
                    deferred_results = list(resumption.slots)
                else:
                    deferred_results = []

        if session.status == SessionStatus.CLOSED:
            close_session(session)
            return StepResult.done(output={"session": session.sqid}, outcome="closed")

        sink = _TurnUpdateSink(turn=turn)
        try:
            runner = session.agent.runtime_backend.session_runner()
            with actor_context(session.agent.principal_subject()):
                outcome = runner.run_turn(
                    session,
                    turn,
                    deferred_results=deferred_results,
                    emit=sink,
                    heartbeat=lambda: self.heartbeat(step_run),
                )
        except TransientStepError as error:
            # A transient raise hands the retry to the engine — but retry
            # exhaustion there fails the whole run, and a session must survive
            # a failed turn. Re-raise only while the step's retry budget has
            # attempts left; the last attempt becomes a failed turn outcome.
            if _attempts_remaining(step_run):
                raise
            outcome = TurnOutcome(
                kind="failed",
                error=str(error),
                replay_state=session.replay_state,
            )
        except Exception as error:  # noqa: BLE001 - provider/runtime failures become turn outcomes.
            if _attempts_remaining(step_run) and session.agent.is_transient_inference_error(error):
                raise TransientStepError(str(error)) from error
            outcome = TurnOutcome(
                kind="failed",
                error=str(error),
                replay_state=session.replay_state,
            )
        finally:
            sink.flush()
        return _persist_turn_outcome(step_run, session, turn, outcome)


@dataclass(slots=True)
class _TurnUpdateSink:
    """Bounded ACP update flusher."""

    turn: Any
    pending: list[dict[str, Any]] = field(default_factory=list)
    last_flush: float = 0.0

    def __post_init__(self) -> None:
        self.last_flush = time.monotonic()

    def __call__(self, update: dict[str, Any]) -> None:
        """Buffer one ACP payload and flush when the cadence bound elapsed."""

        self.pending.append(dict(update))
        if time.monotonic() - self.last_flush >= SESSION_UPDATE_FLUSH_SECONDS:
            self.flush()

    def flush(self) -> None:
        """Append buffered updates to the persisted turn."""

        if not self.pending:
            return
        turn_model = type(self.turn)
        with system_context(reason="workflows_agents.session_step.emit"), transaction.atomic():
            locked = turn_model.objects.lock_if_supported().get(pk=self.turn.pk)
            locked.updates = [*(locked.updates or []), *self.pending]
            locked.save(update_fields=["updates", "updated_at"])
            self.turn.updates = locked.updates
        self.pending.clear()
        self.last_flush = time.monotonic()


def _session_for_step(step_run: Any) -> Any:
    """Resolve and lock the declared AgentSession workflow subject."""

    subject = step_run.run.subject
    if subject is None or subject._meta.label_lower != "agents.agentsession":
        raise ValidationError({"run": "Agent session steps require an agents.AgentSession subject."})
    session_model = apps.get_model("agents", "AgentSession")
    return (
        session_model.objects.lock_if_supported()
        .select_related(
            "owner",
            "agent",
            "agent__model",
            "agent__model__provider",
            "agent__model__provider__credential",
            "agent__inference_credential",
        )
        .get(pk=subject.pk)
    )


def _claim_turn(session: Any) -> tuple[Any | None, bool]:
    """Claim the session's active retry/resume turn, else its oldest pending one."""

    active = (
        session.turns.lock_if_supported()
        .filter(status__in=[TurnStatus.RUNNING, TurnStatus.AWAITING_APPROVAL])
        .order_by("index")
        .first()
    )
    resumed = active is not None and active.status == TurnStatus.AWAITING_APPROVAL
    if active is not None and active.status == TurnStatus.RUNNING:
        active.updates = []
        active.save(update_fields=["updates", "updated_at"])
    turn = active or session.turns.lock_if_supported().filter(status=TurnStatus.PENDING).order_by("index").first()
    if turn is None:
        return None, False
    turn.mark_running()
    session.mark_running()
    return turn, resumed


def _persist_turn_outcome(step_run: Any, session: Any, turn: Any, outcome: TurnOutcome) -> StepResult:
    """Persist one runtime outcome under system authority and map it to the engine."""

    session_model = type(session)
    turn_model = type(turn)
    step_run_model = type(step_run)
    run_model = type(step_run.run)
    with system_context(reason="workflows_agents.session_step.persist"), transaction.atomic():
        locked_run = run_model.objects.lock_if_supported().get(pk=step_run.run_id)
        locked_step_run = step_run_model.objects.lock_if_supported().get(pk=step_run.pk)
        locked_session = session_model.objects.lock_if_supported().select_related("owner").get(pk=session.pk)
        locked_turn = turn_model.objects.lock_if_supported().get(pk=turn.pk)
        cancel_requested = bool((locked_step_run.resume_state or {}).get("cancel_requested"))
        if (
            locked_step_run.status != StepRunStatus.STARTED
            or locked_run.status in RunStatus.TERMINAL
            or cancel_requested
        ):
            if locked_turn.status in {
                TurnStatus.PENDING,
                TurnStatus.RUNNING,
                TurnStatus.AWAITING_APPROVAL,
            }:
                locked_turn.cancel()
            return StepResult.done(output={"session": locked_session.sqid}, outcome="canceled")

        locked_run.debit_budget(outcome.usage)
        # Budget debit saves a separate locked run; copy its committed value into the session.
        locked_run.refresh_from_db(fields=["budget_spent"])
        locked_session.replay_state = outcome.replay_state
        locked_session.usage = dict(locked_run.budget_spent or {})

        if outcome.kind == "completed":
            locked_turn.mark_completed(text=outcome.text, usage=outcome.usage)
            if locked_session.status != SessionStatus.CLOSED:
                locked_session.mark_idle()
            result = (
                StepResult.done(output={"session": locked_session.sqid}, outcome="closed")
                if locked_session.status == SessionStatus.CLOSED
                else _continue_or_park(locked_session)
            )
        elif outcome.kind == "needs_approval":
            if not outcome.approval_requests:
                locked_turn.mark_failed("The runtime requested approval without any tool calls.")
                locked_session.mark_error(locked_turn.error)
                result = _park_session()
            elif locked_session.status == SessionStatus.CLOSED:
                locked_turn.cancel()
                result = StepResult.done(output={"session": locked_session.sqid}, outcome="closed")
            else:
                locked_turn.mark_awaiting_approval()
                locked_session.mark_awaiting_approval()
                result = GateStep.gate_result(
                    locked_step_run,
                    config=_approval_gate_config(locked_session, outcome.approval_requests),
                    retained_state={"turn": locked_turn.sqid},
                )
        else:
            locked_turn.mark_failed(outcome.error or "Agent runtime failed.")
            if locked_session.status != SessionStatus.CLOSED:
                locked_session.mark_error(locked_turn.error)
            result = (
                StepResult.done(output={"session": locked_session.sqid}, outcome="closed")
                if locked_session.status == SessionStatus.CLOSED
                else _continue_or_park(locked_session)
            )

        locked_session.save(update_fields=["replay_state", "usage", "updated_at"])
    return result


def _approval_gate_config(session: Any, requests: list[dict[str, Any]]) -> dict[str, Any]:
    """Build one resumable built-in gate with a dynamic slot per deferred tool call."""

    assignee = str(engine.resolve_workflow_actor(session.owner).subject)
    slots = []
    decision_schema: dict[str, Any] | None = None
    for index, request in enumerate(requests):
        review = build_decision_action(
            actions=(
                ReviewAction(
                    value="approve",
                    label="Approve tool request",
                    verdict="COMPLETE",
                    fields=("reason",),
                ),
                ReviewAction(
                    value="reject",
                    label="Reject tool request",
                    verdict="REJECT",
                    fields=("reason",),
                    required=("reason",),
                    variant="destructive",
                ),
            ),
            properties={
                "reason": {
                    "type": "string",
                    "label": "Decision note",
                    "widget": "textarea",
                    "minLength": 1,
                }
            },
            payload=request,
            facts=(
                ReviewFact(
                    pointer=f"/approval_requests/{index}",
                    label="Requested tool call",
                    value=dict(request),
                    authority="unverified",
                ),
            ),
        )
        decision_schema = review.decision_schema
        slots.append(
            {
                "assignees": [assignee],
                "priority": index,
                "payload": review.payload,
            }
        )
    return {
        "policy": "all_done",
        "action": "approve_tool",
        "slots": slots,
        "decision_schema": decision_schema or {},
        "max_attempts": 3,
        "resume": True,
    }


def _attempts_remaining(step_run: Any) -> bool:
    """Return whether the step's declared retry budget has attempts left.

    ``step_run.attempt`` counts executions (the engine's ``record_attempt``
    runs once per claim), and the budget is the step config's ``retry`` policy
    — the same one the engine's exhaustion path consults, so the impl converts
    the final attempt into a turn outcome instead of letting the engine fail
    the run.
    """

    return step_run.attempt < retry_policy_from_config(step_run.step.config).max_attempts


def _park_session() -> StepResult:
    """Return the far-future wait woken only by explicit event delivery."""

    return StepResult.wait(until=SESSION_PARKED_UNTIL, resume_state={}, waiting_kind="external")


def _continue_or_park(session: Any) -> StepResult:
    """Keep an already-delivered queued turn due, otherwise park the session."""

    if session.turns.filter(status=TurnStatus.PENDING).exists():
        return StepResult.wait(until=timezone.now(), resume_state={})
    return _park_session()
