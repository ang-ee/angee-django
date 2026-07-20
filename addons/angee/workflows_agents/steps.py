"""Workflow step implementation backed by the agents inference catalogue.

``AgentStepImpl`` is the one-shot activity counterpart to workflow gates: it
renders one prompt from a minimal Django-template context (``subject``, ``run``,
``step``), sends one non-streaming chat request through the selected inference
provider backend, journals a bounded request/response summary on the step-run,
and debits token usage onto the run budget ledger.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.template import Context, Engine
from django.utils import timezone
from rebac import actor_context, system_context, to_subject_ref

from angee.agents.backends import InferenceRequest, InferenceResponse
from angee.agents.models import SessionStatus, TurnStatus
from angee.agents.runners import TurnOutcome
from angee.workflows.models import RunStatus, StepRunStatus, Verdict
from angee.workflows.steps import DecisionSpec, StepImpl, StepResult, TransientStepError
from angee.workflows_agents.sessions import close_session

AGENT_STEP_JOURNAL_MAX_BYTES = 4096
"""Maximum UTF-8 JSON bytes stored in one agent step-run output journal."""

AGENT_STEP_TRUNCATION_MARKER = "[truncated: workflows_agents.AgentStepImpl journal exceeded 4096 bytes]"
"""Marker appended when an agent request/response journal is shortened."""

_ONE_SHOT_MODE = "one_shot"
_TEMPLATE_ENGINE = Engine(debug=False)
SESSION_PARKED_UNTIL = datetime.max.replace(tzinfo=UTC)
"""Far-future durable wait used because ``StepResult.wait`` requires a due time."""

SESSION_UPDATE_FLUSH_SECONDS = 0.25
"""Minimum interval between streamed turn-row saves."""


@dataclass(frozen=True, slots=True)
class _ResolvedAgentTarget:
    """Resolved one-shot inference target for an agent workflow step."""

    agent: Any | None
    provider: Any
    model: Any
    system: str


class AgentStepImpl(StepImpl):
    """One-shot workflow activity that calls an agents inference backend."""

    key = "agent"
    label = "Agent"
    category = "Activity"
    deterministic = False

    @classmethod
    def validate_config(cls, config: Any) -> None:
        """Validate one-shot agent step config."""

        super().validate_config(config)
        mode = str(config.get("mode", _ONE_SHOT_MODE) or _ONE_SHOT_MODE)
        if mode != _ONE_SHOT_MODE:
            raise ValidationError({"config": "Agent step mode must be one_shot."})

        prompt_template = config.get("prompt_template")
        if not isinstance(prompt_template, str) or not prompt_template.strip():
            raise ValidationError({"config": "Agent steps require a prompt_template string."})

        has_agent = bool(str(config.get("agent", "") or "").strip())
        has_provider = bool(str(config.get("provider", "") or "").strip())
        has_model = bool(str(config.get("model", "") or "").strip())
        if has_agent and (has_provider or has_model):
            raise ValidationError({"config": "Agent steps use either agent or provider plus model, not both."})
        if not has_agent and not (has_provider and has_model):
            raise ValidationError({"config": "Agent steps require agent or provider plus model."})

        if "max_tokens" in config:
            _positive_int(config["max_tokens"], name="max_tokens")
        if "temperature" in config and config["temperature"] not in (None, ""):
            _float(config["temperature"], name="temperature")
        if "options" in config and not isinstance(config["options"], Mapping):
            raise ValidationError({"config": "Agent step options must be a JSON object."})

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Execute one one-shot inference request and return a routing outcome."""

        del now
        config = dict(step_run.step.config)
        request: InferenceRequest | None = None
        try:
            prompt = _render_prompt(str(config["prompt_template"]), step_run)
            target = _resolve_target(config)
            request = _request_for(config, target=target, prompt=prompt)
            response = target.provider.backend.chat(request)
            with system_context(reason="workflows_agents.agent_step.budget"), transaction.atomic():
                step_run.run.debit_budget(_usage_delta(response.usage))
            return StepResult.done(
                output=_bounded_summary(_success_summary(target=target, request=request, response=response)),
                outcome="completed",
            )
        except TransientStepError:
            raise
        except Exception as error:  # noqa: BLE001 - backend/config failure is a workflow outcome.
            if _is_retryable_provider_error(error):
                raise TransientStepError(str(error)) from error
            return StepResult.done(
                output=_bounded_summary(_failure_summary(request=request, error=error)),
                outcome="failed",
            )


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
    deterministic = False

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Run the oldest active/pending turn or park an idle session."""

        del now
        with system_context(reason="workflows_agents.session_step.claim"), transaction.atomic():
            session = _session_for_step(step_run)
            if session.status != SessionStatus.CLOSED:
                turn, resumed = _claim_turn(session)
                if turn is None:
                    if session.status != SessionStatus.IDLE:
                        session.mark_idle()
                    return _park_session()
                deferred_results = _deferred_results(step_run) if resumed else []

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
        except TransientStepError:
            raise
        except Exception as error:  # noqa: BLE001 - provider/runtime failures become turn outcomes.
            if _is_retryable_provider_error(error):
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


def _deferred_results(step_run: Any) -> list[dict[str, Any]]:
    """Project this suspension's resolved workflow decisions for the runtime."""

    decision_ids = step_run.resume_state.get("_decision_ids")
    if not isinstance(decision_ids, list):
        return []
    decisions = step_run.decisions.filter(pk__in=decision_ids).order_by("priority", "pk")
    return [
        {
            **dict(decision.payload or {}),
            "approved": decision.verdict == Verdict.COMPLETED,
            "verdict": str(decision.verdict),
            "resolution": dict(decision.resolution or {}),
        }
        for decision in decisions
    ]


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
                result = StepResult.suspend(
                    resume_state={
                        "_resume_after_decisions": True,
                        "gate": {"policy": "all_done"},
                        "turn": locked_turn.sqid,
                        "approval_requests": outcome.approval_requests,
                    },
                    decisions=_approval_decisions(locked_session, outcome.approval_requests),
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


def _approval_decisions(session: Any, requests: list[dict[str, Any]]) -> tuple[DecisionSpec, ...]:
    """Build one owner-assigned workflow decision per deferred tool call."""

    assignee = str(to_subject_ref(session.owner))
    schema = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "label": "Decision note",
                "widget": "textarea",
            }
        },
    }
    return tuple(
        DecisionSpec(
            assignees=(assignee,),
            action="approve_tool",
            payload=dict(request),
            priority=index,
            max_attempts=3,
            decision_schema=schema,
        )
        for index, request in enumerate(requests)
    )


def _park_session() -> StepResult:
    """Return the far-future wait woken only by explicit event delivery."""

    return StepResult.wait(until=SESSION_PARKED_UNTIL, resume_state={})


def _continue_or_park(session: Any) -> StepResult:
    """Keep an already-delivered queued turn due, otherwise park the session."""

    if session.turns.filter(status=TurnStatus.PENDING).exists():
        return StepResult.wait(until=timezone.now(), resume_state={})
    return _park_session()


def _render_prompt(template: str, step_run: Any) -> str:
    """Render ``template`` with the documented minimal step context."""

    context = Context(
        {
            "subject": step_run.run.subject,
            "run": step_run.run,
            "step": step_run.step,
        },
        autoescape=False,
    )
    return _TEMPLATE_ENGINE.from_string(template).render(context)


def _resolve_target(config: Mapping[str, Any]) -> _ResolvedAgentTarget:
    """Resolve an ``agent`` or ``provider`` + ``model`` config into catalogue rows."""

    with system_context(reason="workflows_agents.agent_step.resolve"):
        agent_ref = str(config.get("agent", "") or "").strip()
        if agent_ref:
            agent_model = apps.get_model("agents", "Agent")
            agent = _by_public_id(
                agent_model.objects.select_related("model", "model__provider"),
                agent_ref,
                label="agent",
            )
            model = getattr(agent, "model", None)
            if model is None:
                raise ValidationError({"config": "Agent step agent must have an inference model."})
            return _ResolvedAgentTarget(
                agent=agent,
                provider=model.provider,
                model=model,
                system=str(config.get("system", "") or agent.instructions or ""),
            )

        provider_model = apps.get_model("agents", "InferenceProvider")
        inference_model = apps.get_model("agents", "InferenceModel")
        provider = _by_public_id(
            provider_model.objects.all(),
            str(config.get("provider", "") or "").strip(),
            label="provider",
        )
        try:
            model = inference_model.objects.select_related("provider").get(
                provider=provider,
                name=str(config.get("model", "") or "").strip(),
            )
        except ObjectDoesNotExist as error:
            raise ValidationError({"config": "Agent step model was not found for provider."}) from error
        return _ResolvedAgentTarget(
            agent=None,
            provider=provider,
            model=model,
            system=str(config.get("system", "") or ""),
        )


def _by_public_id(queryset: Any, value: str, *, label: str) -> Any:
    """Return one row by Angee public id, or raise a config validation error."""

    row = queryset.from_public_id(value)
    if row is None:
        raise ValidationError({"config": f"Agent step {label} was not found."})
    return row


def _request_for(config: Mapping[str, Any], *, target: _ResolvedAgentTarget, prompt: str) -> InferenceRequest:
    """Build the provider-neutral one-shot chat request."""

    return InferenceRequest(
        model=str(target.model.name),
        messages=[{"role": "user", "content": prompt}],
        system=target.system,
        max_tokens=_positive_int(config.get("max_tokens", _default_max_tokens(target.model)), name="max_tokens"),
        temperature=(
            None if config.get("temperature") in (None, "") else _float(config.get("temperature"), name="temperature")
        ),
        tools=tuple(config.get("tools", ()) or ()),
        options=dict(config.get("options") or {}),
    )


def _default_max_tokens(model: Any) -> int:
    """Return the model's declared output cap or the inference seam default."""

    configured = getattr(model, "max_output_tokens", None)
    if configured:
        return int(configured)
    return 1024


def _positive_int(value: Any, *, name: str) -> int:
    """Return ``value`` as a positive integer config field."""

    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError({"config": f"Agent step {name} must be an integer."}) from error
    if parsed < 1:
        raise ValidationError({"config": f"Agent step {name} must be positive."})
    return parsed


def _float(value: Any, *, name: str) -> float:
    """Return ``value`` as a float config field."""

    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValidationError({"config": f"Agent step {name} must be a number."}) from error


def _usage_delta(usage: Mapping[str, Any]) -> dict[str, int]:
    """Return normalized token usage deltas from provider-neutral backend usage."""

    delta: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens"):
        value = _int_usage(usage.get(key))
        if value:
            delta[key] = value

    total = _int_usage(usage.get("total_tokens"))
    if total is None:
        total = sum(delta.values()) or None
    if total:
        delta["tokens"] = total
    return delta


def _int_usage(value: Any) -> int | None:
    """Return a non-negative integer usage value, ignoring absent/non-numeric facts."""

    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = int(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed >= 0 else None


def _success_summary(
    *,
    target: _ResolvedAgentTarget,
    request: InferenceRequest,
    response: InferenceResponse,
) -> dict[str, Any]:
    """Return the structured request/response summary for a completed call."""

    agent_ref = getattr(target.agent, "sqid", "") if target.agent is not None else ""
    return {
        "agent": {
            "agent": agent_ref,
            "provider": getattr(target.provider, "sqid", ""),
            "model": getattr(target.model, "sqid", ""),
            "model_name": request.model,
        },
        "request": _request_summary(request),
        "response": {
            "text": response.text,
            "content": response.content,
            "usage": response.usage,
        },
    }


def _failure_summary(request: InferenceRequest | None, *, error: Exception) -> dict[str, Any]:
    """Return a structured failure summary for routing on the ``failed`` outcome."""

    return {
        "request": None if request is None else _request_summary(request),
        "error": {
            "type": type(error).__name__,
            "message": str(error),
        },
    }


def _is_retryable_provider_error(error: Exception) -> bool:
    """Return whether an SDK/provider exception represents a transient failure."""

    status = getattr(error, "status_code", None)
    if status in {408, 409, 425, 429, 500, 502, 503, 504, 529}:
        return True
    error_type = type(error).__name__.lower()
    message = str(error).lower()
    retryable_terms = (
        "ratelimit",
        "rate_limit",
        "rate limit",
        "overload",
        "overloaded",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "try again",
    )
    return any(term in error_type or term in message for term in retryable_terms)


def _request_summary(request: InferenceRequest) -> dict[str, Any]:
    """Return the JSON-safe request facts worth journaling."""

    return {
        "model": request.model,
        "messages": list(request.messages),
        "system": request.system,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "tools": list(request.tools),
        "options": dict(request.options),
    }


def _bounded_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``summary`` capped to ``AGENT_STEP_JOURNAL_MAX_BYTES`` when encoded."""

    safe = _json_safe(summary)
    if _json_size(safe) <= AGENT_STEP_JOURNAL_MAX_BYTES:
        return safe

    text = json.dumps(safe, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
    wrapper: dict[str, Any] = {
        "truncated": True,
        "limit_bytes": AGENT_STEP_JOURNAL_MAX_BYTES,
        "summary": "",
    }
    available = AGENT_STEP_JOURNAL_MAX_BYTES - _json_size({**wrapper, "summary": AGENT_STEP_TRUNCATION_MARKER})
    wrapper["summary"] = f"{text[: max(0, available)]}{AGENT_STEP_TRUNCATION_MARKER}"
    while _json_size(wrapper) > AGENT_STEP_JOURNAL_MAX_BYTES and wrapper["summary"]:
        shortened = str(wrapper["summary"])[: -min(128, len(str(wrapper["summary"])))]
        wrapper["summary"] = f"{shortened}{AGENT_STEP_TRUNCATION_MARKER}"
    return wrapper


def _json_size(value: Any) -> int:
    """Return the UTF-8 JSON byte size for ``value``."""

    return len(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8"))


def _json_safe(value: Any) -> Any:
    """Return a value suitable for JSONField storage."""

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
