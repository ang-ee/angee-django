"""Opt-in pytest fixtures for real workflow commits and captured task transport."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context
from rebac.roles import grant as grant_role

from angee.jobs.enqueue import celery_app
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import RunFactory


@pytest.fixture
def execution(composed_tables: None, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[Any]]:
    """Provide an acting administrator and sends captured after real commits."""

    sent: list[Any] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kwargs: sent.append((name, kwargs)))
    with system_context(reason="workflows.testing execution actor"):
        actor = get_user_model().objects.create_user(username="workflow-runner")
        grant_role(actor=actor, role="angee/role:admin")
    return actor, sent


@pytest.fixture
def register_step(settings: Any, monkeypatch: pytest.MonkeyPatch) -> Callable[[type[Step[Any, Any, Any]]], None]:
    """Contribute a step through trusted settings, including function-local classes."""

    def register(step: type[Step[Any, Any, Any]]) -> None:
        monkeypatch.setattr(sys.modules[step.__module__], step.__name__, step, raising=False)
        settings.ANGEE_WORKFLOW_STEP_CLASSES = {
            **settings.ANGEE_WORKFLOW_STEP_CLASSES,
            step.key: f"{step.__module__}.{step.__name__}",
        }

    return register


@pytest.fixture
def run_factory(execution: tuple[Any, list[Any]]) -> Callable[..., RunFactory]:
    """Bind ``run_factory(workflow, actor=...).at(node, status=...)`` to a workflow."""

    default_actor, _sent = execution

    def factory(workflow: Any, *, actor: Any = default_actor) -> RunFactory:
        return RunFactory(workflow, actor)

    return factory
