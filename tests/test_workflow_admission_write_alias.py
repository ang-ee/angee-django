"""Unsupported upstream routing stops at the workflow operation owner."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import router
from django.utils import timezone

from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows_agents import steps as agent_steps
from tests.test_transitions import TransitionRouter
from tests.workflows import StepRun, WorkflowRun


@pytest.mark.parametrize("entry", ["admission_actor", "execution_admission_actor"])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("subject", ["", "auth/user:17"])
def test_admission_rejects_unsupported_alias_before_resolution_or_lineage_reads(
    monkeypatch: pytest.MonkeyPatch, entry: str, explicit: bool, subject: str
) -> None:
    """No missing-actor or recovery branch can bypass the upstream capability check."""

    run = WorkflowRun(pk=17, admitted_actor_ref=subject)
    run._state.adding = False
    run._state.db = "default" if explicit else "writer"

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Admission reached database-backed resolution before rejecting its alias.")

    monkeypatch.setattr(workflow_models, "resolve_subjects", unexpected)
    monkeypatch.setattr(WorkflowRun, "delivery_target", unexpected)
    monkeypatch.setattr(router, "routers", [TransitionRouter("default")])

    with pytest.raises(ValidationError, match=r"django-zed-rebac resolve_subjects\(\).*writer"):
        getattr(run, entry)(**({"using": "writer"} if explicit else {}))


@pytest.mark.parametrize("entry", ["admission_actor", "execution_admission_actor"])
def test_default_admission_preserves_native_resolver(monkeypatch: pytest.MonkeyPatch, entry: str) -> None:
    """The supported database still resolves through the upstream subject owner."""

    run = WorkflowRun(pk=17, admitted_actor_ref="auth/user:17")
    run._state.adding = False
    run._state.db = "default"
    subject = run.admission_actor_subject()
    actor = object()
    calls: list[tuple[Any, ...]] = []

    def resolve_subjects(refs: tuple[Any, ...]) -> dict[Any, Any]:
        calls.append(refs)
        return {subject: actor}

    monkeypatch.setattr(workflow_models, "resolve_subjects", resolve_subjects)
    assert getattr(run, entry)() is actor
    assert calls == [(subject,)]


def test_actor_resolution_pins_explicit_alias_before_no_keyword_admission_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retained default-bound run cannot make an explicit writer resolve on default."""

    run = WorkflowRun(pk=17, admitted_actor_ref="auth/user:17")
    run._state.adding = False
    run._state.db = "default"
    original = WorkflowRun.admission_actor
    aliases: list[str | None] = []

    def admission_actor(self: WorkflowRun) -> Any:
        aliases.append(self._state.db)
        return original(self)

    monkeypatch.setattr(WorkflowRun, "admission_actor", admission_actor)
    with pytest.raises(ValidationError, match="resolve_subjects"):
        engine.resolve_workflow_actor(run, using="writer")
    assert aliases == ["writer"]


@pytest.mark.parametrize("implementation", [agent_steps.InferStepImpl, agent_steps.AgentSessionStepImpl])
def test_agent_steps_reject_nondefault_before_provider_or_session_work(
    monkeypatch: pytest.MonkeyPatch, implementation: type[Any]
) -> None:
    """The step instance alias wins over a conflicting default write router."""

    step_run = StepRun(pk=17)
    step_run._state.adding = False
    step_run._state.db = "writer"

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Unsupported workflow alias entered a provider or session owner.")

    monkeypatch.setattr(router, "routers", [TransitionRouter("default")])
    monkeypatch.setattr(agent_steps, "_resolve_inference_model", unexpected)
    monkeypatch.setattr(agent_steps, "_session_for_step", unexpected)

    with pytest.raises(ValidationError, match="default database until agents"):
        implementation().run(step_run, now=timezone.now())
