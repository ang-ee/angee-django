"""Workflow step implementation backed by the agents inference catalogue.

``AgentStepImpl`` is the one-shot activity counterpart to workflow gates: it
renders one prompt from a minimal Django-template context (``subject``, ``run``,
``step``), sends one non-streaming chat request through the selected inference
provider backend, journals a bounded request/response summary on the step-run,
and debits token usage onto the run budget ledger. Operator-provisioned service
mode is reserved but intentionally not implemented in this slice.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.template import Context, Engine
from rebac import system_context

from angee.agents.backends import InferenceRequest, InferenceResponse
from angee.workflows.steps import StepImpl, StepResult

AGENT_STEP_JOURNAL_MAX_BYTES = 4096
"""Maximum UTF-8 JSON bytes stored in one agent step-run output journal."""

AGENT_STEP_TRUNCATION_MARKER = "[truncated: workflows_agents.AgentStepImpl journal exceeded 4096 bytes]"
"""Marker appended when an agent request/response journal is shortened."""

SERVICE_MODE_NOT_IMPLEMENTED = (
    "Agent service mode is reserved for operator-provisioned agents and is not implemented in this slice."
)
"""Clear runtime error for the reserved service-mode config key."""

_ONE_SHOT_MODE = "one_shot"
_SERVICE_MODE = "service"
_TEMPLATE_ENGINE = Engine(debug=False)


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
        """Validate one-shot or reserved service-mode agent step config."""

        super().validate_config(config)
        mode = str(config.get("mode", _ONE_SHOT_MODE) or _ONE_SHOT_MODE)
        if mode not in {_ONE_SHOT_MODE, _SERVICE_MODE}:
            raise ValidationError({"config": "Agent step mode must be one_shot or service."})
        if mode == _SERVICE_MODE:
            return

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

    def run(self, step_run: Any) -> StepResult:
        """Execute one one-shot inference request and return a routing outcome."""

        config = dict(step_run.step.config)
        mode = str(config.get("mode", _ONE_SHOT_MODE) or _ONE_SHOT_MODE)
        if mode == _SERVICE_MODE:
            raise NotImplementedError(SERVICE_MODE_NOT_IMPLEMENTED)

        request: InferenceRequest | None = None
        try:
            prompt = _render_prompt(str(config["prompt_template"]), step_run)
            target = _resolve_target(config)
            request = _request_for(config, target=target, prompt=prompt)
            response = target.provider.backend.chat(request)
            _debit_budget(step_run.run, _usage_delta(response.usage))
            return StepResult.done(
                output=_bounded_summary(_success_summary(target=target, request=request, response=response)),
                outcome="completed",
            )
        except Exception as error:  # noqa: BLE001 - backend/config failure is a workflow outcome.
            return StepResult.done(
                output=_bounded_summary(_failure_summary(request=request, error=error)),
                outcome="failed",
            )


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
            None
            if config.get("temperature") in (None, "")
            else _float(config.get("temperature"), name="temperature")
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


def _debit_budget(run: Any, delta: Mapping[str, int]) -> None:
    """Atomically add usage deltas to ``WorkflowRun.budget_spent``."""

    if not delta:
        return
    run_model = apps.get_model("workflows", "WorkflowRun")
    with system_context(reason="workflows_agents.agent_step.budget"), transaction.atomic():
        locked = run_model.objects.select_for_update().get(pk=run.pk)
        spent = dict(locked.budget_spent or {})
        for key, value in delta.items():
            spent[key] = int(spent.get(key, 0) or 0) + int(value)
        locked.budget_spent = spent
        locked.save(update_fields=["budget_spent", "updated_at"])


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
    except (TypeError, ValueError):
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
    available = AGENT_STEP_JOURNAL_MAX_BYTES - _json_size(
        {**wrapper, "summary": AGENT_STEP_TRUNCATION_MARKER}
    )
    wrapper["summary"] = f"{text[:max(0, available)]}{AGENT_STEP_TRUNCATION_MARKER}"
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
